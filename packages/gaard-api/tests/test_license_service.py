from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import logging
import threading

import httpx2 as httpx
import pytest

from gaard_api.admin.database import reset_metadata_store_for_tests
from gaard_api.core.settings import settings
from gaard_api.license import (
    LicenseService,
    license_service,
)


LICENSE_KEY = "gaard_live_abc123456789"
ENV_LICENSE_KEY = "gaard_live_env_enterprise"


@pytest.fixture()
def isolated_license_service(tmp_path, monkeypatch) -> Iterator[LicenseService]:
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


def valid_payload(plan: str) -> dict:
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


def invalid_payload(status: str) -> dict:
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


def response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def test_no_license_key_uses_community(isolated_license_service: LicenseService) -> None:
    state = isolated_license_service.refresh(force=True)

    assert state.plan == "community"
    assert state.status == "missing"
    assert state.features["sql_sources"] is True
    assert state.features["non_sql_sources"] is False
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
    requests: list[dict] = []
    monkeypatch.setattr(settings, "gaard_license_key", LICENSE_KEY)

    def fake_post(url, json, timeout):
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


def test_env_license_key_takes_precedence_over_admin_ui_key(
    isolated_license_service: LicenseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    def fake_post(url, json, timeout):
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

    def failing_post(url, json, timeout):
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

    def failing_post(url, json, timeout):
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

    def fake_post(url, json, timeout):
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

    def fake_post(url, json, timeout):
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
