from __future__ import annotations

from collections.abc import Iterator
import hashlib
import io
from importlib.metadata import PackageNotFoundError
from pathlib import Path
import subprocess
import time
import zipfile

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from gaard_api.admin.database import reset_metadata_store_for_tests
from gaard_api.core.settings import settings
from gaard_api.license import license_service
from gaard_api.main import app
from gaard_api.package_updates import package_update_jobs, package_update_service


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
    package_update_service.reset_for_tests()
    package_update_jobs.reset_for_tests()

    with TestClient(app) as client:
        yield client

    package_update_jobs.reset_for_tests()
    package_update_service.reset_for_tests()
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
    return paid_payload("enterprise")


def paid_payload(plan: str) -> dict:
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


def package_bundle_zip(
    *,
    pack: str = "data-analyst",
    package_name: str = "gaard-duckdb-excel-connector",
    package_version: str = "0.3.0",
    package_dir: str = "gaard-duckdb-excel-connector",
) -> bytes:
    buffer = io.BytesIO()
    manifest = {
        "name": f"gaard-{pack}-pack",
        "version": package_version,
        "plan": pack,
        "file_name": f"gaard-{pack}-pack-{package_version}.zip",
        "gaard_version": ">=0.2.2",
        "description": "Test package bundle",
        "packages": [
            {
                "name": package_name,
                "version": package_version,
                "path": f"packages/{package_dir}",
                "type": "python-package",
                "description": "Test package",
            }
        ],
    }

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json_dumps(manifest))
        archive.writestr(
            f"packages/{package_dir}/pyproject.toml",
            "\n".join(
                [
                    "[build-system]",
                    'requires = ["setuptools>=69", "wheel"]',
                    'build-backend = "setuptools.build_meta"',
                    "",
                    "[project]",
                    f'name = "{package_name}"',
                    f'version = "{package_version}"',
                    'description = "Test package"',
                    'requires-python = ">=3.11"',
                    "",
                    "[tool.setuptools.packages.find]",
                    'where = ["src"]',
                    "",
                ]
            ),
        )
        archive.writestr(f"packages/{package_dir}/src/example_package/__init__.py", "")
    return buffer.getvalue()


def package_download_zip() -> bytes:
    inner_content = package_bundle_zip()
    inner_sha256 = hashlib.sha256(inner_content).hexdigest()
    inner_path = "packages/data-analyst/gaard-data-analyst-pack/gaard-data-analyst-pack-0.3.0.zip"
    buffer = io.BytesIO()
    manifest = {
        "generated_at": "2026-07-05T09:32:42.219Z",
        "plan": "data-analyst",
        "packages": [
            {
                "name": "gaard-data-analyst-pack",
                "version": "0.3.0",
                "plan": "data-analyst",
                "file_name": "gaard-data-analyst-pack-0.3.0.zip",
                "sha256": inner_sha256,
                "path": inner_path,
            }
        ],
    }

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json_dumps(manifest))
        archive.writestr(inner_path, inner_content)
    return buffer.getvalue()


def json_dumps(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def wait_for_package_job(client: TestClient, token: str, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/v1/admin/license/packages/update/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] != "running":
            return payload
        time.sleep(0.05)
    raise AssertionError("Package update job did not finish.")


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


def test_package_update_requires_paid_license(license_client: TestClient) -> None:
    token = login(license_client)

    response = license_client.post(
        "/api/v1/admin/license/packages/update",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "LICENSE_ENTITLEMENT_REQUIRED"


def test_paid_license_can_download_extract_and_install_package_updates(
    license_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = login(license_client)
    package_dir = tmp_path / "packages"
    pip_calls: list[list[str]] = []
    download_requests: list[dict] = []

    monkeypatch.setattr(settings, "gaard_package_directory", str(package_dir))
    monkeypatch.setattr(settings, "gaard_package_download_url", "https://packages.test/download")
    license_service.set_http_post_for_tests(
        lambda url, json, timeout: httpx.Response(200, json=paid_payload("data_analyst"))
    )
    package_update_service.set_package_version_for_tests(
        lambda package_name: "0.2.0"
        if package_name == "gaard-duckdb-excel-connector"
        else (_ for _ in ()).throw(PackageNotFoundError(package_name))
    )

    def fake_download_post(url, json, headers, timeout):
        download_requests.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
            }
        )
        return httpx.Response(
            200,
            content=package_download_zip(),
            headers={"content-type": "application/zip"},
        )

    def fake_pip_runner(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        pip_calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="installed", stderr="")

    package_update_service.set_http_post_for_tests(fake_download_post)
    package_update_service.set_pip_runner_for_tests(fake_pip_runner)

    save_response = license_client.put(
        "/api/v1/admin/license/key",
        headers={"Authorization": f"Bearer {token}"},
        json={"license_key": LICENSE_KEY},
    )
    assert save_response.status_code == 200

    update_response = license_client.post(
        "/api/v1/admin/license/packages/update",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert update_response.status_code == 202
    job_payload = update_response.json()
    assert job_payload["status"] == "running"
    assert job_payload["job_id"]

    payload = wait_for_package_job(license_client, token, job_payload["job_id"])
    assert payload["status"] == "succeeded"
    assert payload["stage"] == "complete"
    assert payload["percent"] == 100
    result = payload["result"]
    assert result["status"] == "updated"
    assert result["installed_count"] == 1
    assert result["restart_required"] is True
    assert (package_dir / "gaard-duckdb-excel-connector" / "pyproject.toml").is_file()
    assert (package_dir / ".downloads" / "gaard-data-analyst-pack-0.3.0.zip").is_file()
    assert pip_calls
    assert str(package_dir / "gaard-duckdb-excel-connector") in pip_calls[0]
    assert len(download_requests) == 1
    download_request = download_requests[0]
    assert download_request["url"] == "https://packages.test/download"
    assert download_request["json"]["license_key"] == LICENSE_KEY
    assert download_request["json"]["product"] == "gaard"
    assert download_request["json"]["gaard_version"]
    assert download_request["json"]["instance_id"]
    assert set(download_request["json"]) == {
        "license_key",
        "product",
        "gaard_version",
        "instance_id",
    }
    assert "Authorization" not in download_request["headers"]
    assert LICENSE_KEY not in update_response.text
    assert LICENSE_KEY not in json_dumps(payload)

    audit_response = license_client.get(
        "/api/v1/admin/audit/admin-events",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert audit_response.status_code == 200
    assert "license.packages.update" in audit_response.text
    assert LICENSE_KEY not in audit_response.text


def test_package_update_surfaces_download_request_errors(
    license_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = login(license_client)

    monkeypatch.setattr(settings, "gaard_package_directory", str(tmp_path / "packages"))
    monkeypatch.setattr(settings, "gaard_package_download_url", "https://packages.test/download")
    license_service.set_http_post_for_tests(
        lambda url, json, timeout: httpx.Response(200, json=paid_payload("data_analyst"))
    )
    package_update_service.set_http_post_for_tests(
        lambda url, json, headers, timeout: httpx.Response(
            400,
            json={"detail": "missing package plan"},
        )
    )

    save_response = license_client.put(
        "/api/v1/admin/license/key",
        headers={"Authorization": f"Bearer {token}"},
        json={"license_key": LICENSE_KEY},
    )
    assert save_response.status_code == 200

    update_response = license_client.post(
        "/api/v1/admin/license/packages/update",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert update_response.status_code == 202
    payload = wait_for_package_job(license_client, token, update_response.json()["job_id"])
    assert payload["status"] == "failed"
    assert (
        payload["error"]["message"]
        == "Package download failed with HTTP 400: missing package plan"
    )
    assert LICENSE_KEY not in json_dumps(payload)
