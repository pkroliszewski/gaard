from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import pytest

from gaard_api.api_registry import ApiRegistry
from gaard_api.extension_services import DatasourceHostService
from gaard_api.extensions import _create_api_extension_services


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


def test_api_extension_services_include_read_only_datasource_service() -> None:
    services = _create_api_extension_services()

    assert isinstance(services["datasources"], DatasourceHostService)
    assert hasattr(services["datasources"], "list_datasources")
    assert hasattr(services["datasources"], "get_schema")
