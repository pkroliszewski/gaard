from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from gaard_api.admin.database import reset_metadata_store_for_tests
from gaard_api.core.settings import settings
from gaard_api.license import license_service
from gaard_api.main import app


LICENSE_KEY = "gaard_live_abc123456789"


@pytest.fixture()
def license_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
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

    with TestClient(app) as client:
        yield client

    license_service.reset_for_tests()
    reset_metadata_store_for_tests()


def login(client: TestClient) -> str:
    login_response = client.post(
        "/api/v1/admin/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["token"]

    password_response = client.post(
        "/api/v1/admin/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "admin",
            "new_password": "new-admin-password",
        },
    )
    assert password_response.status_code == 200
    return token


def enterprise_payload() -> dict:
    return {
        "valid": True,
        "status": "active",
        "plan": "enterprise",
        "features": {},
        "limits": {},
        "current_period_end": "2035-01-01T00:00:00Z",
        "grace_until": None,
        "server_time": "2026-07-04T00:00:00Z",
        "message": None,
    }


def test_license_status_is_admin_only_and_does_not_return_key(
    license_client: TestClient,
) -> None:
    unauthorized_response = license_client.get("/license/status")
    assert unauthorized_response.status_code == 401

    token = login(license_client)
    response = license_client.get(
        "/api/v1/admin/license/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    root_response = license_client.get(
        "/license/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert root_response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "plan",
        "status",
        "valid",
        "current_period_end",
        "grace_until",
        "last_checked_at",
        "next_check_at",
        "message",
    }
    assert "license_key" not in payload
    assert payload["plan"] == "community"


def test_admin_can_set_license_key_without_key_leaking_to_response_or_audit(
    license_client: TestClient,
) -> None:
    token = login(license_client)
    license_service.set_http_post_for_tests(
        lambda url, json, timeout: httpx.Response(200, json=enterprise_payload())
    )

    response = license_client.put(
        "/api/v1/admin/license/key",
        headers={"Authorization": f"Bearer {token}"},
        json={"license_key": LICENSE_KEY},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["plan"] == "enterprise"
    assert payload["valid"] is True
    assert LICENSE_KEY not in response.text

    audit_response = license_client.get(
        "/api/v1/admin/audit/admin-events",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert audit_response.status_code == 200
    audit_text = audit_response.text
    assert LICENSE_KEY not in audit_text
    assert "gaard_live_abc..." in audit_text


def test_admin_can_force_license_recheck(license_client: TestClient) -> None:
    token = login(license_client)
    calls = 0

    def fake_post(url, json, timeout):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=enterprise_payload())

    license_service.set_http_post_for_tests(fake_post)
    save_response = license_client.put(
        "/api/v1/admin/license/key",
        headers={"Authorization": f"Bearer {token}"},
        json={"license_key": LICENSE_KEY},
    )
    assert save_response.status_code == 200

    check_response = license_client.post(
        "/api/v1/admin/license/check",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert check_response.status_code == 200
    assert check_response.json()["plan"] == "enterprise"
    assert calls == 2
