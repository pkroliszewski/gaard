from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import threading
from typing import Any, Literal
from uuid import uuid4

import httpx2 as httpx
from sqlalchemy.orm import Session

from gaard_core.errors import GaardError

from gaard_api.admin.models import AdminSetting, DatasourceConnector
from gaard_api.core.settings import settings
from gaard_api.tls_http import http_error_summary, post as tls_post


logger = logging.getLogger(__name__)

LicensePlan = Literal["community", "data_analyst", "enterprise"]
LicenseValidationKind = Literal["success", "invalid", "transient", "configuration_error"]

LICENSE_KEY_SETTING = "gaard_license_key"
LICENSE_CACHE_SETTING = "gaard_license_cache"
INSTANCE_ID_SETTING = "gaard_instance_id"
LICENSE_EDITION_SETTING = "license_edition"
PRODUCT_NAME = "gaard"
SQL_SOURCE_TYPES = {
    "sqlite",
    "postgresql",
    "mysql",
    "oracle",
    "mssql",
    "ibm_db2",
    "teradata",
}
FEATURE_KEYS = (
    "sql_sources",
    "non_sql_sources",
    "multi_source",
    "multiple_models",
    "extract_jobs",
    "identity_management",
    "unlimited_machine_consumers",
)
LIMIT_KEYS = ("human_users", "machine_consumers", "dashboards", "sources")


class LicenseAccessError(GaardError):
    code = "LICENSE_ENTITLEMENT_REQUIRED"
    status_code = 403


@dataclass(frozen=True)
class PlanEntitlements:
    features: dict[str, bool]
    limits: dict[str, int | None]


@dataclass(frozen=True)
class LicenseState:
    plan: LicensePlan
    status: str
    valid: bool
    features: dict[str, bool]
    limits: dict[str, int | None]
    current_period_end: datetime | None = None
    grace_until: datetime | None = None
    last_checked_at: datetime | None = None
    next_check_at: datetime | None = None
    message: str | None = None
    source: str = "community"

    def serialize_status(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "status": self.status,
            "valid": self.valid,
            "current_period_end": serialize_datetime(self.current_period_end),
            "grace_until": serialize_datetime(self.grace_until),
            "last_checked_at": serialize_datetime(self.last_checked_at),
            "next_check_at": serialize_datetime(self.next_check_at),
            "message": self.message,
        }


@dataclass(frozen=True)
class LicenseValidationResult:
    kind: LicenseValidationKind
    state: LicenseState | None = None
    response_payload: dict[str, Any] | None = None
    status_code: int | None = None
    message: str | None = None


PLAN_ENTITLEMENTS: dict[LicensePlan, PlanEntitlements] = {
    "community": PlanEntitlements(
        features={
            "sql_sources": True,
            "non_sql_sources": False,
            "multi_source": False,
            "multiple_models": False,
            "extract_jobs": False,
            "identity_management": False,
            "unlimited_machine_consumers": False,
        },
        limits={
            "human_users": 1,
            "machine_consumers": 1,
            "dashboards": 1,
            "sources": 1,
        },
    ),
    "data_analyst": PlanEntitlements(
        features={
            "sql_sources": True,
            "non_sql_sources": True,
            "multi_source": True,
            "multiple_models": True,
            "extract_jobs": False,
            "identity_management": False,
            "unlimited_machine_consumers": False,
        },
        limits={
            "human_users": 1,
            "machine_consumers": 1,
            "dashboards": 1,
            "sources": None,
        },
    ),
    "enterprise": PlanEntitlements(
        features={
            "sql_sources": True,
            "non_sql_sources": True,
            "multi_source": True,
            "multiple_models": True,
            "extract_jobs": True,
            "identity_management": True,
            "unlimited_machine_consumers": True,
        },
        limits={
            "human_users": None,
            "machine_consumers": None,
            "dashboards": None,
            "sources": None,
        },
    ),
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def normalize_plan(value: Any) -> LicensePlan | None:
    if value in PLAN_ENTITLEMENTS:
        return value
    return None


def entitlement_for_plan(plan: LicensePlan) -> PlanEntitlements:
    return PLAN_ENTITLEMENTS[plan]


def build_entitlements(
    plan: LicensePlan,
    response_features: dict[str, Any] | None = None,
    response_limits: dict[str, Any] | None = None,
) -> PlanEntitlements:
    plan_entitlements = entitlement_for_plan(plan)
    features = dict(plan_entitlements.features)
    limits = dict(plan_entitlements.limits)

    for key, value in (response_features or {}).items():
        if key in FEATURE_KEYS:
            features[key] = bool(value)

    for key, value in (response_limits or {}).items():
        if key not in LIMIT_KEYS:
            continue
        if value is None:
            limits[key] = None
            continue
        try:
            limits[key] = max(0, int(value))
        except (TypeError, ValueError):
            continue

    return PlanEntitlements(features=features, limits=limits)


def community_state(status: str = "missing", message: str | None = None) -> LicenseState:
    entitlements = entitlement_for_plan("community")
    return LicenseState(
        plan="community",
        status=status,
        valid=False,
        features=dict(entitlements.features),
        limits=dict(entitlements.limits),
        message=message,
        source="community",
    )


def fingerprint_license_key(license_key: str) -> str:
    return hashlib.sha256(license_key.encode("utf-8")).hexdigest()


def redact_license_key(license_key: str) -> str:
    normalized = license_key.strip()
    if not normalized:
        return ""
    if len(normalized) <= 8:
        return "****"
    return f"{normalized[:14]}..."


def is_sql_source_type(database_type: str) -> bool:
    return database_type in SQL_SOURCE_TYPES


def normalize_model_names(model: str) -> list[str]:
    names = [
        item.strip()
        for chunk in str(model or "").splitlines()
        for item in chunk.split(",")
        if item.strip()
    ]
    return list(dict.fromkeys(names))


class LicenseService:
    def __init__(self) -> None:
        self._state = community_state()
        self._state_lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._consecutive_transient_failures = 0
        self._http_post = tls_post

    @property
    def state(self) -> LicenseState:
        with self._state_lock:
            return self._state

    def set_http_post_for_tests(self, http_post: Any) -> None:
        self._http_post = http_post

    def reset_for_tests(self) -> None:
        self.stop()
        with self._state_lock:
            self._state = community_state()
            self._consecutive_transient_failures = 0
        self._http_post = tls_post

    def start(self) -> None:
        self.refresh(force=True)

        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._background_loop,
                name="gaard-license-checker",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        self._thread = None

    def refresh_if_due(self) -> LicenseState:
        now = utc_now()
        state = self.state
        if state.next_check_at is None or state.next_check_at > now:
            return state
        return self.refresh(force=False)

    def refresh(self, *, force: bool = False) -> LicenseState:
        if not self._refresh_lock.acquire(blocking=force):
            return self.state

        try:
            now = utc_now()
            current_state = self.state
            if not force and current_state.next_check_at and current_state.next_check_at > now:
                return current_state

            with self._session() as session:
                instance_id = self._get_or_create_instance_id(session)
                license_key = self._effective_license_key(session)
                if not license_key:
                    state = community_state(
                        message="No license key is configured; running in Community mode."
                    )
                    self._set_state(session, state)
                    session.commit()
                    return state

                result = self._validate_online(
                    license_key=license_key,
                    instance_id=instance_id,
                    checked_at=now,
                )

                if result.kind == "success" and result.state is not None:
                    self._consecutive_transient_failures = 0
                    state = result.state
                    self._save_license_cache(
                        session=session,
                        license_key=license_key,
                        payload=result.response_payload or {},
                        checked_at=now,
                    )
                    self._set_state(session, state)
                    session.commit()
                    logger.info(
                        "GAARD license validated: key=%s plan=%s status=%s",
                        redact_license_key(license_key),
                        state.plan,
                        state.status,
                    )
                    return state

                if result.kind in {"invalid", "configuration_error"}:
                    self._consecutive_transient_failures = 0
                    self._clear_license_cache(session)
                    state = community_state(
                        status=result.state.status if result.state else "invalid",
                        message=result.message or "License is not valid.",
                    )
                    state = self._with_check_times(
                        state,
                        last_checked_at=now,
                        next_check_at=self._next_regular_check(now),
                    )
                    self._set_state(session, state)
                    session.commit()
                    logger.warning(
                        "GAARD license rejected: key=%s status=%s message=%s",
                        redact_license_key(license_key),
                        state.status,
                        state.message,
                    )
                    return state

                self._consecutive_transient_failures += 1
                cached = self._load_cached_state(session, license_key, now)
                next_check_at = self._next_backoff_check(now)
                if cached is not None:
                    state = self._with_check_times(
                        cached,
                        next_check_at=next_check_at,
                        message=(
                            result.message
                            or "Online license validation failed; using cached license."
                        ),
                    )
                else:
                    state = self._with_check_times(
                        community_state(
                            status="missing",
                            message=(
                                result.message
                                or "Online license validation failed and no valid cache is available."
                            ),
                        ),
                        last_checked_at=now,
                        next_check_at=next_check_at,
                    )
                self._set_state(session, state)
                session.commit()
                logger.warning(
                    "GAARD license validation temporarily failed: key=%s status_code=%s message=%s",
                    redact_license_key(license_key),
                    result.status_code,
                    result.message,
                )
                return state
        finally:
            self._refresh_lock.release()

    def status(self) -> dict[str, Any]:
        return self.refresh_if_due().serialize_status()

    def set_license_key(self, license_key: str, actor: str) -> LicenseState:
        normalized = license_key.strip()
        if not normalized:
            raise ValueError("License key is required.")

        with self._session() as session:
            self._set_setting(session, LICENSE_KEY_SETTING, normalized, actor)
            self._clear_license_cache(session)
            session.commit()

        return self.refresh(force=True)

    def clear_license_key(self, actor: str) -> LicenseState:
        with self._session() as session:
            self._set_setting(session, LICENSE_KEY_SETTING, "", actor)
            self._clear_license_cache(session)
            session.commit()

        return self.refresh(force=True)

    def package_download_context(self) -> tuple[LicenseState, str, str]:
        state = self.refresh_if_due()
        if state.plan == "community" or not state.valid:
            raise LicenseAccessError(
                "Package updates require an active paid GAARD license."
            )

        with self._session() as session:
            license_key = self._effective_license_key(session)
            instance_id = self._get_or_create_instance_id(session)
            session.commit()

        if not license_key:
            raise LicenseAccessError(
                "Package updates require a configured GAARD license key."
            )

        return state, license_key, instance_id

    def require_feature(self, feature: str, detail: str | None = None) -> None:
        state = self.refresh_if_due()
        if state.features.get(feature):
            return

        raise LicenseAccessError(
            detail
            or (
                f"This operation requires the '{feature}' license entitlement. "
                f"Current plan: {state.plan}."
            )
        )

    def identity_management_allowed(self) -> bool:
        return bool(self.refresh_if_due().features.get("identity_management"))

    def ensure_datasource_type_allowed(self, database_type: str) -> None:
        if is_sql_source_type(database_type):
            self.require_feature(
                "sql_sources",
                "SQL datasource support is not enabled by the current license.",
            )
            return

        self.require_feature(
            "non_sql_sources",
            (
                "This datasource type requires a license with non-SQL source support. "
                "Upgrade the license or keep this source inactive."
            ),
        )

    def ensure_active_source_limit(
        self,
        connectors: list[DatasourceConnector],
    ) -> None:
        state = self.refresh_if_due()
        limit = state.limits.get("sources")
        if limit is None:
            return

        active_non_system = [
            connector
            for connector in connectors
            if connector.active and connector.connector_key != "metadata-db"
        ]
        if len(active_non_system) <= limit:
            return

        raise LicenseAccessError(
            (
                f"The current {state.plan} license allows {limit} active datasource"
                f"{'' if limit == 1 else 's'}. Deactivate another datasource or "
                "update the license before activating this source."
            )
        )

    def ensure_datasource_contexts_allowed(
        self,
        contexts: list[tuple[DatasourceConnector, Any]],
    ) -> None:
        if len(contexts) > 1:
            self.require_feature(
                "multi_source",
                "Queries spanning multiple datasources require a license with multi-source access.",
            )

        for connector, _cache in contexts:
            self.ensure_datasource_type_allowed(connector.database_type)

    def ensure_models_allowed(self, model: str) -> None:
        model_names = normalize_model_names(model)
        if len(model_names) <= 1:
            return

        self.require_feature(
            "multiple_models",
            "Configuring more than one model requires a license with multiple-model support.",
        )

    def ensure_identity_management_allowed(self) -> None:
        if self.identity_management_allowed():
            return

        state = self.state
        raise LicenseAccessError(
            (
                "Identity management is available with the Enterprise plan. "
                f"Current plan: {state.plan}."
            )
        )

    def ensure_machine_consumer_limit(self, count: int) -> None:
        state = self.refresh_if_due()
        if state.features.get("unlimited_machine_consumers"):
            return
        limit = state.limits.get("machine_consumers")
        if limit is None or count <= limit:
            return
        raise LicenseAccessError(
            (
                f"The current {state.plan} license allows {limit} machine consumer"
                f"{'' if limit == 1 else 's'}."
            )
        )

    def _background_loop(self) -> None:
        while not self._stop_event.is_set():
            state = self.state
            now = utc_now()
            wait_seconds = 60
            if state.next_check_at is not None:
                wait_seconds = max(1, int((state.next_check_at - now).total_seconds()))
            else:
                wait_seconds = max(60, self._check_interval_seconds())

            if self._stop_event.wait(wait_seconds):
                return
            try:
                self.refresh_if_due()
            except Exception:
                logger.exception("Unexpected error while refreshing GAARD license status.")

    def _validate_online(
        self,
        *,
        license_key: str,
        instance_id: str,
        checked_at: datetime,
    ) -> LicenseValidationResult:
        payload = {
            "license_key": license_key,
            "product": PRODUCT_NAME,
            "gaard_version": gaard_api_version(),
            "instance_id": instance_id,
            "features_requested": list(FEATURE_KEYS),
        }

        try:
            response = self._http_post(
                settings.gaard_license_verify_url,
                json=payload,
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            return LicenseValidationResult(
                kind="transient",
                message=f"Online license validation failed: {http_error_summary(exc)}.",
            )

        status_code = int(response.status_code)
        if status_code == 200:
            try:
                response_payload = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                return LicenseValidationResult(
                    kind="transient",
                    status_code=status_code,
                    message=f"License server returned invalid JSON: {exc}.",
                )
            return self._state_from_online_payload(response_payload, checked_at)

        if status_code == 400:
            return LicenseValidationResult(
                kind="configuration_error",
                status_code=status_code,
                message="License validation request was rejected by the license server.",
            )

        if status_code == 429 or status_code >= 500:
            return LicenseValidationResult(
                kind="transient",
                status_code=status_code,
                message=(
                    "License server is temporarily unavailable "
                    f"(HTTP {status_code}); using cached status if available."
                ),
            )

        return LicenseValidationResult(
            kind="configuration_error",
            status_code=status_code,
            message=f"License validation failed with HTTP {status_code}.",
        )

    def _state_from_online_payload(
        self,
        payload: dict[str, Any],
        checked_at: datetime,
    ) -> LicenseValidationResult:
        status_value = str(payload.get("status") or "invalid")
        message = payload.get("message")
        valid = bool(payload.get("valid"))
        plan = normalize_plan(payload.get("plan"))
        current_period_end = parse_datetime(payload.get("current_period_end"))
        grace_until = parse_datetime(payload.get("grace_until"))
        next_check_at = self._next_regular_check(checked_at)

        if not valid:
            invalid_state = community_state(status=status_value, message=message)
            invalid_state = self._with_check_times(
                invalid_state,
                last_checked_at=checked_at,
                next_check_at=next_check_at,
            )
            return LicenseValidationResult(
                kind="invalid",
                state=invalid_state,
                response_payload=payload,
                message=message or f"License status is {status_value}.",
            )

        if plan is None or status_value not in {"active", "grace"}:
            invalid_state = community_state(
                status=status_value,
                message=message or "License server did not return an active paid plan.",
            )
            invalid_state = self._with_check_times(
                invalid_state,
                last_checked_at=checked_at,
                next_check_at=next_check_at,
            )
            return LicenseValidationResult(
                kind="invalid",
                state=invalid_state,
                response_payload=payload,
                message=invalid_state.message,
            )

        if status_value == "grace" and grace_until is not None and checked_at > grace_until:
            invalid_state = community_state(
                status="expired",
                message=message or "License grace period has expired.",
            )
            invalid_state = self._with_check_times(
                invalid_state,
                last_checked_at=checked_at,
                next_check_at=next_check_at,
            )
            return LicenseValidationResult(
                kind="invalid",
                state=invalid_state,
                response_payload=payload,
                message=invalid_state.message,
            )

        entitlements = build_entitlements(
            plan,
            payload.get("features") if isinstance(payload.get("features"), dict) else {},
            payload.get("limits") if isinstance(payload.get("limits"), dict) else {},
        )
        state = LicenseState(
            plan=plan,
            status=status_value,
            valid=True,
            features=entitlements.features,
            limits=entitlements.limits,
            current_period_end=current_period_end,
            grace_until=grace_until,
            last_checked_at=checked_at,
            next_check_at=next_check_at,
            message=message,
            source="online",
        )
        return LicenseValidationResult(
            kind="success",
            state=state,
            response_payload=payload,
            message=message,
        )

    def _load_cached_state(
        self,
        session: Session,
        license_key: str,
        now: datetime,
    ) -> LicenseState | None:
        setting = session.get(AdminSetting, LICENSE_CACHE_SETTING)
        if setting is None or not setting.value:
            return None

        try:
            cache = json.loads(setting.value)
        except json.JSONDecodeError:
            return None

        if cache.get("key_fingerprint") != fingerprint_license_key(license_key):
            return None

        payload = cache.get("payload")
        if not isinstance(payload, dict):
            return None

        checked_at = parse_datetime(cache.get("checked_at"))
        if checked_at is None:
            return None

        result = self._state_from_online_payload(payload, checked_at)
        if result.kind != "success" or result.state is None:
            return None

        allowed_until = checked_at + timedelta(days=self._offline_grace_days())
        current_period_end = result.state.current_period_end
        if current_period_end is not None:
            allowed_until = min(
                allowed_until,
                current_period_end + timedelta(days=self._offline_grace_days()),
            )

        grace_until = result.state.grace_until
        if result.state.status == "grace" and grace_until is not None:
            allowed_until = min(allowed_until, grace_until)

        if now > allowed_until:
            return None

        return LicenseState(
            **{
                **result.state.__dict__,
                "source": "cache",
                "next_check_at": self._next_backoff_check(now),
            }
        )

    def _save_license_cache(
        self,
        *,
        session: Session,
        license_key: str,
        payload: dict[str, Any],
        checked_at: datetime,
    ) -> None:
        cache = {
            "key_fingerprint": fingerprint_license_key(license_key),
            "payload": payload,
            "checked_at": serialize_datetime(checked_at),
        }
        self._set_setting(
            session,
            LICENSE_CACHE_SETTING,
            json.dumps(cache, ensure_ascii=False, sort_keys=True),
            "system",
        )

    def _clear_license_cache(self, session: Session) -> None:
        setting = session.get(AdminSetting, LICENSE_CACHE_SETTING)
        if setting is not None:
            setting.value = ""
            setting.updated_by = "system"

    def _set_state(self, session: Session, state: LicenseState) -> None:
        self._set_setting(session, LICENSE_EDITION_SETTING, state.plan, "system")
        with self._state_lock:
            self._state = state

    def _with_check_times(
        self,
        state: LicenseState,
        *,
        last_checked_at: datetime | None = None,
        next_check_at: datetime | None = None,
        message: str | None = None,
    ) -> LicenseState:
        return LicenseState(
            plan=state.plan,
            status=state.status,
            valid=state.valid,
            features=dict(state.features),
            limits=dict(state.limits),
            current_period_end=state.current_period_end,
            grace_until=state.grace_until,
            last_checked_at=last_checked_at
            if last_checked_at is not None
            else state.last_checked_at,
            next_check_at=next_check_at
            if next_check_at is not None
            else state.next_check_at,
            message=message if message is not None else state.message,
            source=state.source,
        )

    def _get_or_create_instance_id(self, session: Session) -> str:
        setting = session.get(AdminSetting, INSTANCE_ID_SETTING)
        if setting is not None and setting.value.strip():
            return setting.value.strip()

        instance_id = str(uuid4())
        self._set_setting(session, INSTANCE_ID_SETTING, instance_id, "system")
        session.flush()
        return instance_id

    def _effective_license_key(self, session: Session) -> str:
        env_key = settings.gaard_license_key.strip()
        if env_key:
            return env_key

        metadata_setting = session.get(AdminSetting, LICENSE_KEY_SETTING)
        metadata_key = metadata_setting.value.strip() if metadata_setting else ""
        if metadata_key:
            return metadata_key
        return ""

    def _set_setting(
        self,
        session: Session,
        key: str,
        value: str,
        actor: str,
    ) -> None:
        setting = session.get(AdminSetting, key)
        if setting is None:
            session.add(AdminSetting(key=key, value=value, updated_by=actor))
            return

        setting.value = value
        setting.updated_by = actor

    def _next_regular_check(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self._check_interval_seconds())

    def _next_backoff_check(self, now: datetime) -> datetime:
        backoff_seconds = min(
            self._check_interval_seconds(),
            max(60, 2 ** min(self._consecutive_transient_failures - 1, 8)),
        )
        return now + timedelta(seconds=backoff_seconds)

    def _check_interval_seconds(self) -> int:
        return max(1, int(settings.gaard_license_check_interval_seconds or 86_400))

    def _offline_grace_days(self) -> int:
        return max(0, int(settings.gaard_license_offline_grace_days or 7))

    def _session(self) -> Session:
        from gaard_api.admin.database import create_session

        return create_session()


def gaard_api_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("gaard-api")
    except PackageNotFoundError:
        return "0.0.0"


license_service = LicenseService()
