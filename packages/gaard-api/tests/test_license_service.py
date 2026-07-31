from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx2 as httpx
import pytest
from sqlalchemy import select

from gaard_api.admin.database import create_session, reset_metadata_store_for_tests
from gaard_api.admin.models import AdminUser, DatasourceConnector
from gaard_api.core.settings import settings
from gaard_api.license import (
    LicenseAccessError,
    LicenseService,
    license_service,
)

LICENSE_KEY = "gaard_live_abc123456789"
ENV_LICENSE_KEY = "gaard_live_env_enterprise"


@pytest.fixture()
def isolated_license_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[LicenseService]:
    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    monkeypatch.setattr(settings, "gaard_license_key", "")
    monkeypatch.setattr(settings, "gaard_license_verify_url", "https://license.test/validate")
    monkeypatch.setattr(settings, "gaard_license_check_interval_seconds", 3_600)
    monkeypatch.setattr(settings, "gaard_license_offline_grace_days", 7)
    reset_metadata_store_for_tests()
    license_service.reset_for_tests()

    yield license_service

    license_service.reset_for_tests()
    reset_metadata_store_for_tests()


def valid_payload(plan: str) -> dict[str, Any]:
    return {
        "valid": True,
        "status": "active",
        "plan": plan,
        "features": {},
        "limits": {},
        "current_period_end": "2035-01-01T00:00:00Z",
        "grace_until": None,
        "server_time": "2026-07-04T00:00:00Z",
        "message": None,
    }


def invalid_payload(status: str) -> dict[str, Any]:
    return {
        "valid": False,
        "status": status,
        "plan": None,
        "features": {},
        "limits": {
            "human_users": None,
            "machine_consumers": None,
            "dashboards": None,
            "sources": None,
        },
        "current_period_end": None,
        "grace_until": None,
        "server_time": "2026-07-04T00:00:00Z",
        "message": f"License is {status}.",
    }


def response(payload: dict[str, Any], status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def test_no_license_key_uses_community(isolated_license_service: LicenseService) -> None:
    state = isolated_license_service.refresh(force=True)

    assert state.plan == "community"
    assert state.status == "missing"
    assert state.features["sql_sources"] is True
    assert state.features["non_sql_sources"] is False
    assert state.features["extract_jobs"] is False
    assert state.limits == {
        "human_users": 1,
        "machine_consumers": 1,
        "dashboards": 1,
        "sources": 1,
    }


def test_active_data_analyst_license_enables_data_analyst_entitlements(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> httpx.Response:
        requests.append(json)
        return response(valid_payload("data_analyst"))

    isolated_license_service.set_http_post_for_tests(fake_post)
    state = isolated_license_service.refresh(force=True)
    isolated_license_service.refresh(force=True)

    assert state.plan == "data_analyst"
    assert state.valid is True
    assert state.features["sql_sources"] is True
    assert state.features["non_sql_sources"] is True
    assert state.features["multi_source"] is True
    assert state.features["multiple_models"] is True
    assert state.features["extract_jobs"] is False
    assert state.features["identity_management"] is False
    assert state.limits["sources"] is None
    assert state.limits["human_users"] == 1
    assert requests[0]["license_key"] == LICENSE_KEY
    assert requests[0]["product"] == "gaard"
    assert requests[0]["instance_id"]
    assert requests[1]["instance_id"] == requests[0]["instance_id"]


def test_active_enterprise_license_enables_enterprise_entitlements(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)
    isolated_license_service.set_http_post_for_tests(
        lambda url, json, timeout: response(valid_payload("enterprise"))
    )

    state = isolated_license_service.refresh(force=True)

    assert state.plan == "enterprise"
    assert all(state.features.values())
    assert all(value is None for value in state.limits.values())


def test_identity_management_requires_enterprise_entitlement(
    isolated_license_service: LicenseService,
) -> None:
    state = isolated_license_service.refresh(force=True)

    assert state.plan == "community"
    assert isolated_license_service.identity_management_allowed() is False
    with pytest.raises(LicenseAccessError, match="Enterprise plan"):
        isolated_license_service.ensure_identity_management_allowed()


def test_identity_management_allows_active_enterprise_license(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)
    isolated_license_service.set_http_post_for_tests(
        lambda url, json, timeout: response(valid_payload("enterprise"))
    )
    state = isolated_license_service.refresh(force=True)

    assert state.plan == "enterprise"
    assert isolated_license_service.identity_management_allowed() is True
    isolated_license_service.ensure_identity_management_allowed()



def test_license_refresh_revokes_newest_excess_enterprise_users_and_preserves_admin_seat(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)
    payload = valid_payload("enterprise")
    payload["limits"] = {"human_users": 2}
    isolated_license_service.set_http_post_for_tests(
        lambda url, json, timeout: response(payload)
    )

    with create_session() as session:
        default_admin = session.scalar(
            select(AdminUser).where(AdminUser.username == "admin")
        )
        assert default_admin is not None
        default_admin_id = default_admin.id
        older_user = AdminUser(
            username="licensed-user-1",
            password_hash="unused",
            role="user",
            enterprise_access=True,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        newer_user = AdminUser(
            username="licensed-user-2",
            password_hash="unused",
            role="user",
            enterprise_access=True,
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        newest_user = AdminUser(
            username="licensed-user-3",
            password_hash="unused",
            role="user",
            enterprise_access=True,
            created_at=datetime(2026, 1, 3, tzinfo=UTC),
        )
        session.add_all([older_user, newer_user, newest_user])
        session.commit()
        older_user_id = older_user.id
        newer_user_id = newer_user.id
        newest_user_id = newest_user.id

    isolated_license_service.refresh(force=True)

    with create_session() as session:
        loaded_default_admin = session.get(AdminUser, default_admin_id)
        loaded_older_user = session.get(AdminUser, older_user_id)
        loaded_newer_user = session.get(AdminUser, newer_user_id)
        loaded_newest_user = session.get(AdminUser, newest_user_id)
        assert loaded_default_admin is not None and loaded_default_admin.enterprise_access is True
        assert loaded_older_user is not None and loaded_older_user.enterprise_access is True
        assert loaded_newer_user is not None and loaded_newer_user.enterprise_access is False
        assert loaded_newest_user is not None and loaded_newest_user.enterprise_access is False


def test_unassigned_enterprise_user_is_limited_to_community_query_features(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)
    isolated_license_service.set_http_post_for_tests(
        lambda url, json, timeout: response(valid_payload("enterprise"))
    )
    isolated_license_service.refresh(force=True)

    isolated_license_service.ensure_datasource_contexts_allowed(
        [(cast(DatasourceConnector, SimpleNamespace(database_type="sqlite")), None)],
        enterprise_access=False,
    )
    with pytest.raises(LicenseAccessError, match="assigned Enterprise user license"):
        isolated_license_service.ensure_datasource_contexts_allowed(
            [(cast(DatasourceConnector, SimpleNamespace(database_type="duckdb-excel")), None)],
            enterprise_access=False,
        )


def test_env_license_key_takes_precedence_over_admin_ui_key(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> httpx.Response:
        requests.append(json["license_key"])
        if json["license_key"] == ENV_LICENSE_KEY:
            return response(valid_payload("enterprise"))
        return response(invalid_payload("deleted"))

    isolated_license_service.set_http_post_for_tests(fake_post)
    isolated_license_service.set_license_key(LICENSE_KEY, "admin")
    monkeypatch.setattr(settings, "gaard_license_key", ENV_LICENSE_KEY)

    state = isolated_license_service.refresh(force=True)

    assert state.plan == "enterprise"
    assert state.valid is True
    assert requests[-1] == ENV_LICENSE_KEY


def test_valid_false_immediately_downgrades_paid_access(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)
    isolated_license_service.set_http_post_for_tests(
        lambda url, json, timeout: response(valid_payload("enterprise"))
    )
    assert isolated_license_service.refresh(force=True).plan == "enterprise"

    isolated_license_service.set_http_post_for_tests(
        lambda url, json, timeout: response(invalid_payload("invalid"))
    )
    state = isolated_license_service.refresh(force=True)

    assert state.plan == "community"
    assert state.status == "invalid"
    assert state.valid is False
    assert state.features["identity_management"] is False
    assert state.features["non_sql_sources"] is False


@pytest.mark.parametrize("server_status", ["revoked", "expired", "deleted"])
def test_revoked_expired_deleted_have_no_paid_access(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
    server_status: str,
) -> None:
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)
    isolated_license_service.set_http_post_for_tests(
        lambda url, json, timeout: response(invalid_payload(server_status))
    )

    state = isolated_license_service.refresh(force=True)

    assert state.plan == "community"
    assert state.status == server_status
    assert state.valid is False
    assert state.features["multi_source"] is False


def test_network_error_uses_valid_cache_within_offline_grace(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)
    isolated_license_service.set_http_post_for_tests(
        lambda url, json, timeout: response(valid_payload("data_analyst"))
    )
    assert isolated_license_service.refresh(force=True).plan == "data_analyst"

    def failing_post(url: str, json: dict[str, Any], timeout: float) -> httpx.Response:
        raise httpx.ConnectError("offline")

    isolated_license_service.set_http_post_for_tests(failing_post)
    state = isolated_license_service.refresh(force=True)

    assert state.plan == "data_analyst"
    assert state.valid is True
    assert state.source == "cache"


def test_network_error_after_offline_grace_downgrades(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2026, 7, 4, tzinfo=UTC)
    monkeypatch.setattr("gaard_api.license.utc_now", lambda: start)
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)
    isolated_license_service.set_http_post_for_tests(
        lambda url, json, timeout: response(valid_payload("enterprise"))
    )
    assert isolated_license_service.refresh(force=True).plan == "enterprise"

    monkeypatch.setattr(
        "gaard_api.license.utc_now",
        lambda: start + timedelta(days=8, seconds=1),
    )

    def failing_post(url: str, json: dict[str, Any], timeout: float) -> httpx.Response:
        raise httpx.ConnectError("offline")

    isolated_license_service.set_http_post_for_tests(failing_post)
    state = isolated_license_service.refresh(force=True)

    assert state.plan == "community"
    assert state.valid is False
    assert state.features["identity_management"] is False


@pytest.mark.parametrize("status_code", [429, 500])
def test_transient_http_errors_back_off_without_spamming_license_server(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    calls = 0
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json={"error": "temporary"})

    isolated_license_service.set_http_post_for_tests(fake_post)
    first_state = isolated_license_service.refresh(force=True)
    second_state = isolated_license_service.refresh_if_due()

    assert calls == 1
    assert first_state.plan == "community"
    assert second_state.next_check_at == first_state.next_check_at


def test_full_license_key_is_never_logged(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)
    isolated_license_service.set_http_post_for_tests(
        lambda url, json, timeout: response(invalid_payload("revoked"))
    )

    caplog.set_level(logging.INFO, logger="gaard_api.license")
    isolated_license_service.refresh(force=True)

    assert LICENSE_KEY not in caplog.text
    assert "gaard_live_abc..." in caplog.text


def test_parallel_refreshes_do_not_start_multiple_online_validations(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> httpx.Response:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return response(valid_payload("enterprise"))

    isolated_license_service.set_http_post_for_tests(fake_post)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(isolated_license_service.refresh, force=False)
            for _ in range(8)
        ]
        assert entered.wait(timeout=2)
        release.set()
        states = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert any(state.plan == "enterprise" for state in states)
