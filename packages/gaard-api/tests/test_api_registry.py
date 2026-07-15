from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import pytest

from gaard_api.api_registry import ApiRegistry
from gaard_api.core.error_handlers import register_error_handlers
from gaard_api.extension_services import DatasourceHostService
from gaard_api.extensions import (
    EXTRACT_JOBS_LICENSE_MESSAGE,
    _create_api_extension_services,
    enforce_extension_license_entitlements,
    is_extract_job_mutation,
)
from gaard_api.license import LicenseAccessError


def test_api_registry_mounts_extension_router_and_admin_page(tmp_path: Path) -> None:
    router = APIRouter()

    @router.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    html_path = tmp_path / "index.html"
    html_path.write_text("<h1>Private tools</h1>", encoding="utf-8")

    registry = ApiRegistry()
    registry.register_router(extension_id="acme-tools", router=router, prefix="/tools")
    registry.register_admin_page(
        extension_id="acme-tools",
        section_key="tools",
        label="ACME Tools",
        description="Private ACME admin tools.",
        html_path=html_path,
    )

    app = FastAPI()
    registry.apply_to(app)

    with TestClient(app) as client:
        api_response = client.get("/api/v1/extensions/acme-tools/tools/ping")
        page_response = client.get("/admin/extensions/acme-tools/tools")

    assert api_response.status_code == 200
    assert api_response.json() == {"status": "ok"}
    assert page_response.status_code == 200
    assert "Private tools" in page_response.text
    assert [section.serialize() for section in registry.list_admin_sections()] == [
        {
            "section_id": "extension:acme-tools:tools",
            "extension_id": "acme-tools",
            "section_key": "tools",
            "label": "ACME Tools",
            "description": "Private ACME admin tools.",
            "path": "/admin/extensions/acme-tools/tools",
            "order": 1000,
        }
    ]


def test_api_registry_rejects_admin_paths_outside_extension_namespace() -> None:
    registry = ApiRegistry()

    with pytest.raises(ValueError, match="/admin/extensions/acme-tools"):
        registry.register_admin_section(
            extension_id="acme-tools",
            section_key="tools",
            label="ACME Tools",
            path="/admin/prompts",
        )


def test_api_registry_accepts_extension_frontend_modules_only_from_its_assets() -> None:
    registry = ApiRegistry()

    registry.register_admin_frontend_module(
        extension_id="acme-tools",
        module_path="/admin/extensions/acme-tools/assets/admin.js",
    )

    assert registry.list_admin_frontend_modules()[0].module_path.endswith("/admin.js")
    with pytest.raises(ValueError, match="frontend modules"):
        registry.register_admin_frontend_module(
            extension_id="acme-tools", module_path="/admin/assets/admin.js"
        )


def test_api_registry_applies_inherited_dependencies_to_extension_routes() -> None:
    def require_token(authorization: str | None = Header(default=None)) -> None:
        if authorization != "Bearer test-token":
            raise HTTPException(status_code=401, detail="Missing token.")

    router = APIRouter()

    @router.get("/secure")
    def secure() -> dict[str, str]:
        return {"status": "ok"}

    registry = ApiRegistry(dependencies=[Depends(require_token)])
    registry.register_router(extension_id="acme-tools", router=router)

    app = FastAPI()
    registry.apply_to(app)

    with TestClient(app) as client:
        unauthorized_response = client.get("/api/v1/extensions/acme-tools/secure")
        authorized_response = client.get(
            "/api/v1/extensions/acme-tools/secure",
            headers={"Authorization": "Bearer test-token"},
        )

    assert unauthorized_response.status_code == 401
    assert authorized_response.status_code == 200


def test_extract_job_mutations_are_guarded_by_host_license(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DenyingLicenseService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None]] = []

        def require_feature(self, feature: str, detail: str | None = None) -> None:
            self.calls.append((feature, detail))
            raise LicenseAccessError(detail)

    license_service = DenyingLicenseService()
    monkeypatch.setattr("gaard_api.license.license_service", license_service)

    router = APIRouter()

    @router.get("/jobs")
    def list_jobs() -> dict[str, str]:
        return {"status": "listed"}

    @router.post("/jobs")
    def create_job() -> dict[str, str]:
        return {"status": "queued"}

    @router.post("/jobs/{job_id}/refresh")
    def refresh_job(job_id: str) -> dict[str, str]:
        return {"status": f"refreshed:{job_id}"}

    registry = ApiRegistry(dependencies=[Depends(enforce_extension_license_entitlements)])
    registry.register_router(extension_id="gaard-extract", router=router)

    app = FastAPI()
    register_error_handlers(app)
    registry.apply_to(app)

    with TestClient(app) as client:
        list_response = client.get("/api/v1/extensions/gaard-extract/jobs")
        create_response = client.post("/api/v1/extensions/gaard-extract/jobs")
        refresh_response = client.post("/api/v1/extensions/gaard-extract/jobs/job-1/refresh")

    assert list_response.status_code == 200
    assert create_response.status_code == 403
    assert create_response.json()["error"]["message"] == EXTRACT_JOBS_LICENSE_MESSAGE
    assert refresh_response.status_code == 403
    assert refresh_response.json()["error"]["message"] == EXTRACT_JOBS_LICENSE_MESSAGE
    assert license_service.calls == [
        ("extract_jobs", EXTRACT_JOBS_LICENSE_MESSAGE),
        ("extract_jobs", EXTRACT_JOBS_LICENSE_MESSAGE),
    ]


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/v1/extensions/gaard-extract/jobs", True),
        ("POST", "/api/v1/extensions/gaard-extract/jobs/", True),
        ("POST", "/api/v1/extensions/gaard-extract/jobs/job-1/refresh", True),
        ("GET", "/api/v1/extensions/gaard-extract/jobs", False),
        ("POST", "/api/v1/extensions/gaard-extract/llm-extracting-config", False),
        ("POST", "/api/v1/extensions/other/jobs", False),
    ],
)
def test_extract_job_mutation_matcher(method: str, path: str, expected: bool) -> None:
    assert is_extract_job_mutation(method, path) is expected


def test_api_extension_services_include_read_only_datasource_service() -> None:
    services = _create_api_extension_services()

    assert isinstance(services["datasources"], DatasourceHostService)
    assert hasattr(services["datasources"], "list_datasources")
    assert hasattr(services["datasources"], "get_schema")
