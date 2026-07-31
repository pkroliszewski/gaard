import io
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from fastapi.testclient import TestClient
from gaard_connectors import create_builtin_connector_registry
from gaard_connectors.odbc.connection_string import parse_odbc_connection_string
from gaard_core.errors import LlmProviderError, QueryPipelineStepError
from gaard_core.query_pipeline.mock_sql_generator import MockSqlGenerator
from gaard_core.query_pipeline.models import (
    GeneratedSql,
    OutputClassification,
    QueryIntentClassification,
    QueryIntentDecision,
    QueryRequest,
)
from gaard_llm.providers.models import ChatCompletionRequest, ChatCompletionResponse
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from gaard_api.admin.database import (
    clear_expired_admin_sessions,
    create_session,
    reset_metadata_store_for_tests,
    seed_prompts,
)
from gaard_api.admin.models import (
    AdminSession,
    AdminSetting,
    AdminUser,
    BusinessKnowledgeClaim,
    BusinessLogicSuggestion,
    Dashboard,
    DashboardUserState,
    DashboardWidget,
    DataQueryAuditLog,
    DataQueryAuditType,
    DatasourceConnector,
    DatasourceSchemaCache,
    OverviewWidget,
    OverviewWidgetTag,
    PromptTemplate,
    UserDatasourceSelection,
    WidgetTag,
)
from gaard_api.admin.security import hash_password, hash_token
from gaard_api.admin.services import (
    get_active_business_logic_prompt_safe,
    get_governance_policy_for_schema,
    get_llm_runtime_config_safe,
    get_query_runtime_config,
    list_business_logic_suggestions,
    record_candidate_business_knowledge,
    set_setting,
)
from gaard_api.auth_dependencies import AuthenticatedSession, identity_id_for_principal
from gaard_api.core.settings import settings
from gaard_api.example_database import (
    MEDICAL_POC_DASHBOARD_ID,
    install_medical_poc_example_database,
)
from gaard_api.main import app
from gaard_api.query_hooks import QueryHookRegistry


@pytest.fixture()
def admin_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    demo_db = tmp_path / "demo.db"

    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    monkeypatch.setattr(settings, "gaard_datasource_url", f"sqlite:///{demo_db}")
    monkeypatch.setattr(settings, "gaard_sql_generation_mode", "mock")
    monkeypatch.setattr(settings, "gaard_result_interpretation_mode", "mock")
    monkeypatch.setattr(settings, "gaard_llm_api_key", "change-me")
    reset_metadata_store_for_tests()
    install_medical_poc_example_database(demo_db)

    with TestClient(app) as client:
        yield client

    reset_metadata_store_for_tests()


def login(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/admin/auth/login",
        json={
            "username": "admin",
            "password": "admin",
        },
    )

    assert response.status_code == 200
    return cast(dict[str, Any], response.json())


def change_password(client: TestClient, token: str) -> None:
    response = client.post(
        "/api/v1/admin/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "admin",
            "new_password": "new-admin-password",
        },
    )

    assert response.status_code == 200
    assert response.json()["must_change_password"] is False


def test_logout_removes_the_server_session(admin_client: TestClient) -> None:
    login_response = login(admin_client)
    token = login_response["token"]

    response = admin_client.post(
        "/api/v1/admin/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "logged_out"}
    assert admin_client.get(
        "/api/v1/admin/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 401


def test_authenticated_request_updates_last_seen_without_writing_every_request(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    with create_session() as session:
        admin_session = session.scalar(select(AdminSession).where(AdminSession.token_hash == hash_token(token)))
        assert admin_session is not None
        original_last_seen = datetime.now(UTC) - timedelta(minutes=6)
        admin_session.last_seen = original_last_seen
        session.commit()

    assert admin_client.get(
        "/api/v1/admin/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200
    with create_session() as session:
        first_seen = session.scalar(select(AdminSession.last_seen).where(AdminSession.token_hash == hash_token(token)))
        assert first_seen is not None and first_seen != original_last_seen

    assert admin_client.get(
        "/api/v1/admin/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200
    with create_session() as session:
        second_seen = session.scalar(select(AdminSession.last_seen).where(AdminSession.token_hash == hash_token(token)))
        assert second_seen == first_seen


def test_startup_cleanup_removes_stale_sessions(
    admin_client: TestClient,
) -> None:
    with create_session() as session:
        user = session.scalar(select(AdminUser).where(AdminUser.username == "admin"))
        assert user is not None
        session.add(AdminSession(
            token_hash="stale", user_id=user.id, last_seen=datetime.now(UTC) - timedelta(days=31)
        ))
        session.commit()
        clear_expired_admin_sessions(session)
        session.commit()
        assert session.scalar(select(AdminSession).where(AdminSession.token_hash == "stale")) is None


def test_identity_sessions_can_be_cleared_without_revoking_the_requesting_session(
    admin_client: TestClient,
) -> None:
    first_token = login(admin_client)["token"]
    change_password(admin_client, first_token)
    second_login = admin_client.post(
        "/api/v1/admin/auth/login",
        json={"username": "admin", "password": "new-admin-password"},
    )
    assert second_login.status_code == 200
    second_token = second_login.json()["token"]
    headers = {"Authorization": f"Bearer {second_token}"}

    identities = admin_client.get("/api/v1/admin/identities", headers=headers)
    assert identities.status_code == 200
    admin_identity = next(item for item in identities.json()["items"] if item["username"] == "admin")
    assert admin_identity["sessions_count"] == 2

    response = admin_client.delete(
        f"/api/v1/admin/identities/{admin_identity['id']}/sessions", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["cleared_sessions"] == 1

    assert admin_client.get("/api/v1/admin/me", headers=headers).status_code == 200
    assert admin_client.get(
        "/api/v1/admin/me", headers={"Authorization": f"Bearer {first_token}"}
    ).status_code == 401


def test_login_does_not_query_extension_auth_without_identity_license(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    class ExplodingAuthRegistry:
        def authenticate(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("extension auth provider should not be queried")

    monkeypatch.setattr(
        admin_api.license_service,
        "identity_management_allowed",
        lambda: False,
    )
    monkeypatch.setattr(
        admin_api,
        "get_auth_provider_registry",
        lambda: ExplodingAuthRegistry(),
    )

    response = admin_client.post(
        "/api/v1/admin/auth/login",
        json={"username": "external-user", "password": "external-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."


def test_external_users_store_the_provider_separately(admin_client: TestClient) -> None:
    from gaard_api.api.v1.admin import get_or_create_external_auth_user

    with create_session() as session:
        user = get_or_create_external_auth_user(session, "ldap", "ada")
        session.add(
            AdminUser(
                username="ada",
                password_hash=hash_password("local-password"),
                must_change_password=False,
            )
        )
        session.commit()

        assert user.username == "ada"
        assert user.auth_provider == "ldap"
        assert session.scalar(
            select(AdminUser).where(
                AdminUser.username == "ada", AdminUser.auth_provider == "ldap"
            )
        ) is not None


def test_admin_assigns_enterprise_access_and_unlicensed_users_are_blocked(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    assigned_counts: list[int] = []
    monkeypatch.setattr(admin_api.license_service, "identity_management_allowed", lambda: True)
    monkeypatch.setattr(admin_api.license_service, "ensure_identity_management_allowed", lambda: None)
    monkeypatch.setattr(
        admin_api.license_service,
        "ensure_human_user_seat_available",
        lambda count: assigned_counts.append(count),
    )
    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(features={"identity_management": True}),
    )

    with create_session() as session:
        analyst = AdminUser(
            username="analyst",
            password_hash=hash_password("analyst-password"),
            must_change_password=False,
            role="user",
            enterprise_access=False,
        )
        session.add(analyst)
        session.commit()
        analyst_id = analyst.id

    admin_headers = auth_headers(admin_client)
    identities = admin_client.get("/api/v1/admin/identities", headers=admin_headers)
    assert identities.json()["can_manage_identities"] is True
    item = next(identity for identity in identities.json()["items"] if identity["id"] == str(analyst_id))
    assert item["enterprise_access"] is False
    assert item["enterprise_access_editable"] is True

    # An unassigned user stays active while the global Enterprise license is valid.
    assert login_as(admin_client, "analyst", "analyst-password")

    response = admin_client.patch(
        f"/api/v1/admin/identities/{analyst_id}/enterprise-access",
        headers=admin_headers,
        json={"enterprise_access": True},
    )
    assert response.status_code == 200
    assert response.json() == {"enterprise_access": True}
    assert assigned_counts == [2]

    refreshed_identities = admin_client.get("/api/v1/admin/identities", headers=admin_headers)
    refreshed_item = next(
        identity
        for identity in refreshed_identities.json()["items"]
        if identity["id"] == str(analyst_id)
    )
    assert refreshed_item["enterprise_access"] is True

    assert login_as(admin_client, "analyst", "analyst-password")

    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(features={"identity_management": False}),
    )
    blocked = admin_client.post(
        "/api/v1/admin/auth/login",
        json={"username": "analyst", "password": "analyst-password"},
    )
    assert blocked.status_code == 403


def test_admin_enterprise_access_is_not_editable(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(admin_api.license_service, "ensure_identity_management_allowed", lambda: None)
    admin_headers = auth_headers(admin_client)

    response = admin_client.patch(
        "/api/v1/admin/identities/1/enterprise-access",
        headers=admin_headers,
        json={"enterprise_access": False},
    )

    assert response.status_code == 400


def test_additional_administrator_enterprise_access_is_assigned_like_a_user(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(admin_api.license_service, "ensure_identity_management_allowed", lambda: None)
    monkeypatch.setattr(
        admin_api.license_service,
        "ensure_human_user_seat_available",
        lambda assigned_users: None,
    )
    admin_headers = auth_headers(admin_client)
    with create_session() as session:
        additional_admin = AdminUser(
            username="licensed-admin",
            password_hash=hash_password("licensed-admin-password"),
            must_change_password=False,
            role="admin",
            enterprise_access=False,
        )
        session.add(additional_admin)
        session.commit()
        additional_admin_id = additional_admin.id

    response = admin_client.patch(
        f"/api/v1/admin/identities/{additional_admin_id}/enterprise-access",
        headers=admin_headers,
        json={"enterprise_access": True},
    )

    assert response.status_code == 200
    assert response.json() == {"enterprise_access": True}


def test_additional_administrator_cannot_clear_system_administrator_sessions(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(features={"identity_management": True}),
    )
    with create_session() as session:
        system_admin = session.scalar(select(AdminUser).where(AdminUser.is_system_admin.is_(True)))
        assert system_admin is not None
        session.add(
            AdminUser(
                username="second-admin",
                password_hash=hash_password("second-admin-password"),
                must_change_password=False,
                role="admin",
                enterprise_access=True,
            )
        )
        session.commit()

    second_admin_headers = login_as(admin_client, "second-admin", "second-admin-password")
    response = admin_client.delete(
        f"/api/v1/admin/identities/{system_admin.id}/sessions",
        headers=second_admin_headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "The system administrator cannot be managed by another administrator."
    )


def test_core_does_not_expose_built_in_identity_management(
    admin_client: TestClient,
) -> None:
    admin_login = login(admin_client)
    change_password(admin_client, admin_login["token"])
    admin_headers = {"Authorization": f"Bearer {admin_login['token']}"}

    response = admin_client.post(
        "/api/v1/admin/identities",
        headers=admin_headers,
        json={
            "display_name": "Ada Lovelace",
            "username": "ada",
        },
    )

    assert response.status_code == 405


def auth_headers(client: TestClient) -> dict[str, str]:
    token = login(client)["token"]
    change_password(client, token)
    return {"Authorization": f"Bearer {token}"}


def add_local_user(username: str, password: str) -> None:
    with create_session() as session:
        session.add(
            AdminUser(
                username=username,
                password_hash=hash_password(password),
                must_change_password=False,
            )
        )
        session.commit()


def login_as(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/admin/auth/login",
        json={
            "username": username,
            "password": password,
        },
    )

    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def user_headers(
    username: str = "client-user",
    *,
    enterprise_access: bool = False,
    role: str = "user",
) -> dict[str, str]:
    token = f"{username}-token"
    with create_session() as session:
        user = AdminUser(
            username=username,
            password_hash=hash_password("not-used"),
            must_change_password=False,
            role=role,
            enterprise_access=enterprise_access,
        )
        session.add(user)
        session.flush()
        session.add(
            AdminSession(
                token_hash=hash_token(token),
                user_id=user.id,
                username=username,
                role=role,
                auth_provider="local",
            )
        )
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def test_unlicensed_admin_has_read_only_identities_and_restricted_excel_datasources(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(features={"identity_management": True}),
    )
    headers = user_headers("unlicensed-admin", role="admin")
    with create_session() as session:
        session.add(
            DatasourceConnector(
                connector_key="excel-source",
                name="Excel source",
                database_type="duckdb-excel",
                database_url="duckdb-excel:///restricted.xlsx",
                sql_dialect="duckdb",
                active=True,
                updated_by="admin",
            )
        )
        session.add(
            DatasourceConnector(
                connector_key="restricted-sql-source",
                name="Restricted SQL source",
                database_type="sqlite",
                database_url="sqlite:///:memory:",
                sql_dialect="sqlite",
                active=True,
                updated_by="admin",
            )
        )
        session.commit()

    identities = admin_client.patch(
        "/api/v1/admin/identities/1/enterprise-access",
        headers=headers,
        json={"enterprise_access": False},
    )
    extensions = admin_client.get("/api/v1/admin/extensions", headers=headers)
    identity_list = admin_client.get("/api/v1/admin/identities", headers=headers)
    datasources = admin_client.get("/api/v1/admin/datasources", headers=headers)
    excel = next(item for item in datasources.json()["items"] if item["connector_key"] == "excel-source")
    sql_source = next(
        item
        for item in datasources.json()["items"]
        if item["connector_key"] == "restricted-sql-source"
    )
    excel_mutation = admin_client.post(
        f"/api/v1/admin/datasources/{excel['id']}/state",
        headers=headers,
        json={"active": True},
    )
    excel_deactivation = admin_client.post(
        f"/api/v1/admin/datasources/{excel['id']}/state",
        headers=headers,
        json={"active": False},
    )
    sql_deactivation = admin_client.post(
        f"/api/v1/admin/datasources/{sql_source['id']}/state",
        headers=headers,
        json={"active": False},
    )

    assert identities.status_code == 403
    assert identity_list.json()["can_manage_identities"] is False
    assert extensions.json()["admin_sections"] == []
    assert extensions.json()["admin_frontend_modules"] == []
    assert excel["enterprise_access_required"] is True
    assert excel_mutation.status_code == 403
    assert excel_deactivation.status_code == 200
    assert excel_deactivation.json()["item"]["active"] is False
    assert sql_deactivation.status_code == 200
    assert sql_deactivation.json()["item"]["active"] is False


def test_unlicensed_admin_cannot_generate_widget_sql_for_excel_datasource(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(features={"identity_management": True}),
    )
    headers = user_headers("widget-admin", role="admin")
    with create_session() as session:
        session.add(
            DatasourceConnector(
                connector_key="widget-excel-source",
                name="Excel source",
                database_type="duckdb-excel",
                database_url="duckdb-excel:///restricted.xlsx",
                sql_dialect="duckdb",
                active=False,
                updated_by="admin",
            )
        )
        session.commit()

    response = admin_client.post(
        "/api/v1/admin/overview/widgets/generate-sql",
        headers=headers,
        json={
            "widget_key": "restricted_widget",
            "datasource_key": "widget-excel-source",
            "question": "Show the rows.",
        },
    )

    assert response.status_code == 403
    assert "assigned Enterprise user license" in response.json()["detail"]


def test_admin_lists_datasource_types_from_connector_registry(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    response = admin_client.get(
        "/api/v1/admin/datasource-types",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    definitions = {item["type_key"]: item for item in response.json()["items"]}
    assert definitions["sqlite"]["default_sql_dialect"] == "sqlite"
    assert definitions["postgresql"]["default_sql_dialect"] == "postgres"
    assert definitions["oracle"]["default_sql_dialect"] == "oracle"
    assert definitions["mssql"]["default_sql_dialect"] == "tsql"
    assert definitions["ibm_db2"]["default_sql_dialect"] == "db2"
    assert definitions["teradata"]["default_sql_dialect"] == "teradata"
    assert definitions["sqlite"]["config_schema"]["required"] == ["database_path"]
    assert definitions["postgresql"]["config_schema"]["required"] == [
        "host",
        "port",
        "database",
        "username",
    ]
    assert definitions["mysql"]["config_schema"]["required"] == [
        "host",
        "port",
        "database",
        "username",
    ]
    assert definitions["oracle"]["config_schema"]["required"] == [
        "host",
        "port",
        "service_name",
        "username",
    ]
    assert definitions["mssql"]["config_schema"]["required"] == [
        "host",
        "port",
        "database",
        "username",
    ]
    assert definitions["ibm_db2"]["config_schema"]["required"] == [
        "host",
        "port",
        "database",
        "username",
    ]
    assert definitions["teradata"]["config_schema"]["required"] == [
        "host",
        "dbs_port",
        "username",
    ]
    assert "database_path" in definitions["sqlite"]["config_schema"]["properties"]


def test_admin_lists_extensions_inventory(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    response = admin_client.get(
        "/api/v1/admin/extensions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload
    assert "admin_sections" in payload
    assert "admin_frontend_modules" in payload
    assert payload["viewer"] == "admin"


def test_admin_web_loads_connector_types_from_the_registry_api(admin_client: TestClient) -> None:
    response = admin_client.get("/admin/assets/main.js")

    assert response.status_code == 200
    assert 'api("/api/v1/admin/datasource-types")' in response.text
    assert "loadAdminFrontendModules" in response.text
    assert "registerDatasourceExtension" in response.text
    assert "await loadDatasourceExtensions();" in response.text
    assert "await commitDatasourceExtensions(result.item);" in response.text
    assert 'api("/api/v1/admin/extensions")' in response.text
    assert "data-menu-group" in response.text
    assert "Dashboards" in response.text
    assert "Governance" in response.text
    assert "Configuration" in response.text
    assert "Extensions" in response.text
    assert "Data sources" in response.text
    assert 'src="/admin/assets/getgaard.svg"' in response.text
    assert "Update packages" in response.text
    assert "formatLicenseEditionLabel(state.license)" in response.text
    assert "<span>Community edition</span>" not in response.text
    assert 'api("/api/v1/admin/license/status")' in response.text
    assert 'api("/api/v1/admin/license/packages/update"' in response.text
    assert (
        "api(`/api/v1/admin/license/packages/update/${encodeURIComponent(jobId)}`)" in response.text
    )
    assert "package-update-progress" in response.text
    assert 'license.plan && license.plan !== "community"' in response.text
    license_menu_item = '{ key: "license", label: builtInSectionLabels.license }'
    assert response.text.count(license_menu_item) == 1
    governance_index = response.text.index('key: "governance"')
    configuration_index = response.text.index('key: "configuration"')
    extensions_index = response.text.index('key: "extensions"')
    license_index = response.text.index(license_menu_item)
    assert governance_index < configuration_index < license_index < extensions_index
    assert "extension-frame" in response.text
    assert "plugin unavailable" in response.text
    assert "renderDatabaseTypeOptions" not in response.text
    assert "Loading dashboard overview" in response.text
    assert "overview-page-loading" in response.text
    assert "data-toggle-overview-edit" in response.text
    assert "Edit layout" in response.text
    assert "overviewEditMode: false" in response.text
    assert "data-remove-overview-widget" in response.text
    assert "overview-widget-actions" in response.text

    styles_response = admin_client.get("/admin/assets/styles.css")
    assert styles_response.status_code == 200
    assert ".brand-logo" in styles_response.text
    assert ".nav button" in styles_response.text
    assert "font-weight: 800" not in styles_response.text
    assert "font-weight: 650" not in styles_response.text
    assert "calc(100vh - 260px)" in styles_response.text
    assert "overview-edit-mode-button" in styles_response.text
    assert "overview-grid-readonly" in styles_response.text
    assert ".overview-widget-actions" in styles_response.text

    logo_response = admin_client.get("/admin/assets/getgaard.svg")
    assert logo_response.status_code == 200
    assert "<svg" in logo_response.text


def test_query_endpoint_requires_authentication(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )

    assert response.status_code == 401


def test_query_endpoint_accepts_authenticated_user(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    response = admin_client.post(
        "/api/v1/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )

    assert response.status_code == 200
    assert response.json()["answer"]


def test_dashboards_are_scoped_to_authenticated_user(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(features={"identity_management": True}),
    )
    admin_headers = auth_headers(admin_client)
    add_local_user("analyst", "analyst-password")
    analyst_headers = login_as(admin_client, "analyst", "analyst-password")

    admin_response = admin_client.post(
        "/api/v1/dashboards",
        headers=admin_headers,
        json={
            "name": "Operations",
            "description": "Daily operational dashboard.",
        },
    )
    admin_second_response = admin_client.post(
        "/api/v1/dashboards",
        headers=admin_headers,
        json={
            "name": "Operations detail",
            "description": "Detailed operational dashboard.",
        },
    )
    analyst_response = admin_client.post(
        "/api/v1/dashboards",
        headers=analyst_headers,
        json={
            "name": "Finance",
            "description": "Revenue tracking.",
        },
    )

    assert admin_response.status_code == 200
    assert admin_second_response.status_code == 200
    assert analyst_response.status_code == 200
    admin_dashboard = admin_response.json()["item"]
    admin_second_dashboard = admin_second_response.json()["item"]
    analyst_dashboard = analyst_response.json()["item"]
    assert admin_dashboard["owner_username"] == "admin"
    assert analyst_dashboard["owner_username"] == "analyst"
    assert admin_second_response.json()["active_dashboard_id"] == admin_second_dashboard["id"]

    active_response = admin_client.put(
        "/api/v1/dashboards/active",
        headers=admin_headers,
        json={"dashboard_id": admin_dashboard["id"]},
    )
    assert active_response.status_code == 200
    assert active_response.json()["active_dashboard_id"] == admin_dashboard["id"]

    cross_active_response = admin_client.put(
        "/api/v1/dashboards/active",
        headers=admin_headers,
        json={"dashboard_id": analyst_dashboard["id"]},
    )
    assert cross_active_response.status_code == 404

    with create_session() as session:
        session.add(
            OverviewWidget(
                widget_key="admin_saved_metric",
                label="Prompt count",
                widget_type="scalar",
                datasource_key="metadata-db",
                question="How many prompt templates are configured?",
                sql="SELECT COUNT(*) AS value FROM prompt_templates",
                active=False,
                updated_by="admin",
            )
        )
        session.flush()
        for tag_name in ("public", "admin"):
            if session.get(WidgetTag, tag_name) is None:
                session.add(WidgetTag(name=tag_name))
        session.flush()
        metric = session.scalar(
            select(OverviewWidget).where(OverviewWidget.widget_key == "admin_saved_metric")
        )
        assert metric is not None
        for tag_name in ("public", "admin"):
            session.add(OverviewWidgetTag(widget_id=metric.id, tag_name=tag_name))
        session.commit()

    metrics_response = admin_client.get(
        "/api/v1/dashboards/metrics",
        headers=admin_headers,
    )
    assert metrics_response.status_code == 200
    assert metrics_response.json()["items"][0]["widget_key"] == "admin_saved_metric"
    assert metrics_response.json()["items"][0]["result"]["status"] == "ok"

    lightweight_metrics_response = admin_client.get(
        "/api/v1/dashboards/metrics?include_result=false",
        headers=admin_headers,
    )
    assert lightweight_metrics_response.status_code == 200
    assert lightweight_metrics_response.json()["items"][0]["widget_key"] == "admin_saved_metric"
    assert "result" not in lightweight_metrics_response.json()["items"][0]

    rename_metric_response = admin_client.patch(
        "/api/v1/dashboards/metrics/admin_saved_metric",
        headers=admin_headers,
        json={"label": "Updated prompt count"},
    )
    assert rename_metric_response.status_code == 200
    assert rename_metric_response.json()["item"]["label"] == "Updated prompt count"
    assert "result" not in rename_metric_response.json()["item"]

    cross_rename_metric_response = admin_client.patch(
        "/api/v1/dashboards/metrics/admin_saved_metric",
        headers=analyst_headers,
        json={"label": "Cross user rename"},
    )
    assert cross_rename_metric_response.status_code == 404

    dashboard_widget_response = admin_client.post(
        f"/api/v1/dashboards/{admin_dashboard['id']}/widgets",
        headers=admin_headers,
        json={
            "metric_widget_key": "admin_saved_metric",
            "title": "Prompt count",
            "visualization_type": "number",
        },
    )
    assert dashboard_widget_response.status_code == 200
    dashboard_widget = dashboard_widget_response.json()["item"]
    assert dashboard_widget["title"] == "Prompt count"
    assert dashboard_widget["visualization_type"] == "number"
    assert dashboard_widget["result"]["status"] == "ok"

    cross_widget_response = admin_client.post(
        f"/api/v1/dashboards/{admin_dashboard['id']}/widgets",
        headers=analyst_headers,
        json={
            "metric_widget_key": "admin_saved_metric",
            "title": "Cross user",
            "visualization_type": "number",
        },
    )
    assert cross_widget_response.status_code == 404

    layout_response = admin_client.patch(
        f"/api/v1/dashboards/{admin_dashboard['id']}/widgets/layout",
        headers=admin_headers,
        json={
            "items": [
                {
                    "widget_id": dashboard_widget["id"],
                    "x": 6,
                    "y": 2,
                    "w": 4,
                    "h": 3,
                }
            ]
        },
    )
    assert layout_response.status_code == 200
    assert layout_response.json()["items"][0]["layout"] == {
        "x": 6,
        "y": 2,
        "w": 4,
        "h": 3,
    }

    widgets_response = admin_client.get(
        f"/api/v1/dashboards/{admin_dashboard['id']}/widgets",
        headers=admin_headers,
    )
    assert widgets_response.status_code == 200
    assert widgets_response.json()["items"][0]["id"] == dashboard_widget["id"]
    assert widgets_response.json()["items"][0]["layout"] == {
        "x": 6,
        "y": 2,
        "w": 4,
        "h": 3,
    }

    cross_delete_metric_response = admin_client.delete(
        "/api/v1/dashboards/metrics/admin_saved_metric",
        headers=analyst_headers,
    )
    assert cross_delete_metric_response.status_code == 404

    delete_metric_response = admin_client.delete(
        "/api/v1/dashboards/metrics/admin_saved_metric",
        headers=admin_headers,
    )
    assert delete_metric_response.status_code == 200
    assert delete_metric_response.json() == {
        "status": "deleted",
        "widget_key": "admin_saved_metric",
        "removed_dashboard_widgets": 1,
    }

    deleted_metric_list = admin_client.get(
        "/api/v1/dashboards/metrics",
        headers=admin_headers,
    )
    assert deleted_metric_list.status_code == 200
    assert all(
        item["widget_key"] != "admin_saved_metric" for item in deleted_metric_list.json()["items"]
    )

    widgets_after_metric_delete = admin_client.get(
        f"/api/v1/dashboards/{admin_dashboard['id']}/widgets",
        headers=admin_headers,
    )
    assert widgets_after_metric_delete.status_code == 200
    assert widgets_after_metric_delete.json()["items"] == []

    with create_session() as session:
        assert (
            session.scalar(
                select(DashboardWidget).where(
                    DashboardWidget.owner_user_id == "1",
                    DashboardWidget.metric_widget_key == "admin_saved_metric",
                )
            )
            is None
        )
        assert (
            session.scalar(
                select(OverviewWidget).where(
                    OverviewWidget.widget_key == "admin_saved_metric",
                )
            )
            is None
        )

    admin_list = admin_client.get("/api/v1/dashboards", headers=admin_headers)
    analyst_list = admin_client.get("/api/v1/dashboards", headers=analyst_headers)

    assert [item["id"] for item in admin_list.json()["items"]] == [
        admin_second_dashboard["id"],
        admin_dashboard["id"],
        MEDICAL_POC_DASHBOARD_ID,
    ]
    assert admin_list.json()["active_dashboard_id"] == admin_dashboard["id"]
    assert admin_list.json()["active_dashboard"]["id"] == admin_dashboard["id"]
    assert [item["id"] for item in analyst_list.json()["items"]] == [analyst_dashboard["id"]]
    assert analyst_list.json()["active_dashboard_id"] == analyst_dashboard["id"]

    cross_delete = admin_client.delete(
        f"/api/v1/dashboards/{analyst_dashboard['id']}",
        headers=admin_headers,
    )
    assert cross_delete.status_code == 404

    update_dashboard_response = admin_client.put(
        f"/api/v1/dashboards/{admin_dashboard['id']}",
        headers=admin_headers,
        json={
            "name": "Operations renamed",
            "description": "Updated daily operational dashboard.",
        },
    )
    assert update_dashboard_response.status_code == 200
    assert update_dashboard_response.json()["item"]["name"] == "Operations renamed"
    assert update_dashboard_response.json()["item"]["description"] == (
        "Updated daily operational dashboard."
    )

    cross_update_dashboard_response = admin_client.put(
        f"/api/v1/dashboards/{analyst_dashboard['id']}",
        headers=admin_headers,
        json={
            "name": "Cross user rename",
            "description": "Should not be allowed.",
        },
    )
    assert cross_update_dashboard_response.status_code == 404

    admin_dashboards = admin_client.get(
        "/api/v1/admin/dashboards",
        headers=admin_headers,
    )
    assert admin_dashboards.status_code == 200
    assert {item["id"] for item in admin_dashboards.json()["items"]} == {
        admin_dashboard["id"],
        admin_second_dashboard["id"],
        analyst_dashboard["id"],
        MEDICAL_POC_DASHBOARD_ID,
    }

    identities = admin_client.get("/api/v1/admin/identities", headers=admin_headers)
    assert identities.status_code == 200
    admin_identity = next(
        item for item in identities.json()["items"] if item["username"] == "admin"
    )
    assert {dashboard["id"] for dashboard in admin_identity["dashboards"]} == {
        admin_dashboard["id"],
        admin_second_dashboard["id"],
        MEDICAL_POC_DASHBOARD_ID,
    }

    delete_response = admin_client.delete(
        f"/api/v1/dashboards/{admin_dashboard['id']}",
        headers=admin_headers,
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"
    assert delete_response.json()["active_dashboard_id"] == admin_second_dashboard["id"]

    with create_session() as session:
        dashboards = session.scalars(select(Dashboard)).all()
        assert {dashboard.dashboard_id for dashboard in dashboards} == {
            admin_second_dashboard["id"],
            analyst_dashboard["id"],
            MEDICAL_POC_DASHBOARD_ID,
        }
        widgets = session.scalars(select(DashboardWidget)).all()
        assert {widget.dashboard_id for widget in widgets} == {MEDICAL_POC_DASHBOARD_ID}
        admin_state = session.get(DashboardUserState, "1")
        assert admin_state is not None
        assert admin_state.active_dashboard_id == admin_second_dashboard["id"]


def test_system_seeded_mock_runtime_modes_are_migrated_to_current_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    monkeypatch.setattr(settings, "gaard_sql_generation_mode", "mock")
    monkeypatch.setattr(settings, "gaard_result_interpretation_mode", "mock")
    reset_metadata_store_for_tests()

    with create_session() as session:
        query_config = get_query_runtime_config(session)
        assert query_config.sql_generation_mode == "mock"
        assert query_config.result_interpretation_mode == "mock"

    monkeypatch.setattr(settings, "gaard_sql_generation_mode", "llm")
    monkeypatch.setattr(settings, "gaard_result_interpretation_mode", "llm")
    reset_metadata_store_for_tests()

    with create_session() as session:
        query_config = get_query_runtime_config(session)
        assert query_config.sql_generation_mode == "llm"
        assert query_config.result_interpretation_mode == "llm"
        sql_generation_mode = session.get(AdminSetting, "gaard_sql_generation_mode")
        assert sql_generation_mode is not None
        assert sql_generation_mode.updated_by == "system"

    reset_metadata_store_for_tests()


def test_investigation_prompts_are_not_seeded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    reset_metadata_store_for_tests()

    with create_session() as session:
        investigation_prompts = session.scalars(
            select(PromptTemplate).where(PromptTemplate.prompt_key.like("investigation_%"))
        ).all()
        assert investigation_prompts == []

        session.add(
            PromptTemplate(
                prompt_key="investigation_readiness",
                name="Investigation: readiness",
                description="Legacy prompt",
                system_prompt="system",
                user_prompt_template="user",
            )
        )
        session.commit()

        seed_prompts(session)
        session.commit()

        investigation_prompts = session.scalars(
            select(PromptTemplate).where(PromptTemplate.prompt_key.like("investigation_%"))
        ).all()
        assert investigation_prompts == []

    reset_metadata_store_for_tests()


def test_empty_metadata_seed_does_not_create_medical_poc_datasource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    reset_metadata_store_for_tests()

    with create_session() as session:
        connectors = session.scalars(select(DatasourceConnector)).all()

    assert [connector.connector_key for connector in connectors] == ["metadata-db"]
    assert all(connector.active is False for connector in connectors)

    reset_metadata_store_for_tests()


def test_candidate_business_knowledge_can_be_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    reset_metadata_store_for_tests()
    install_medical_poc_example_database(tmp_path / "demo.db")

    with create_session() as session:
        connector = session.scalar(
            select(DatasourceConnector).where(DatasourceConnector.connector_key == "default")
        )
        assert connector is not None
        connector_id = connector.id

    claim_ids = record_candidate_business_knowledge(
        connector_id,
        [
            {
                "knowledge_type": "term_mapping",
                "claim": "kardiolog maps to doctors.specialization=cardiology",
                "datasource_id": "medical_poc",
                "tables": ["doctors"],
                "columns": ["doctors.specialization"],
                "values": ["cardiology"],
                "evidence": [{"sql": "SELECT specialization FROM doctors"}],
                "confidence": 0.92,
                "request_id": "req_test",
            }
        ],
    )

    with create_session() as session:
        claim = session.get(BusinessKnowledgeClaim, claim_ids[0])
        assert claim is not None
        assert claim.status == "candidate"
        assert claim.knowledge_type == "term_mapping"
        assert claim.confidence == 0.92
        assert "cardiology" in claim.subject_json

    reset_metadata_store_for_tests()


def stub_business_logic_learning_llm(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> Any:
    monkeypatch.setattr(settings, "gaard_llm_api_key", "test-key")
    monkeypatch.setattr(settings, "gaard_llm_model", "lesson-model")
    with create_session() as session:
        set_setting(session, "gaard_llm_api_key", "test-key", "test")
        set_setting(session, "gaard_llm_model", "lesson-model", "test")
        session.commit()

    class FakeOpenAICompatibleClient:
        requests: ClassVar[list[ChatCompletionRequest]] = []

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
            self.__class__.requests.append(request)
            return ChatCompletionResponse(content=json.dumps(payload))

    monkeypatch.setattr(
        "gaard_api.admin.services.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    return FakeOpenAICompatibleClient


def test_admin_first_login_requires_password_change(admin_client: TestClient) -> None:
    login_response = login(admin_client)
    token = login_response["token"]

    assert login_response["must_change_password"] is True

    blocked_response = admin_client.get(
        "/api/v1/admin/prompts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert blocked_response.status_code == 403

    change_password(admin_client, token)

    prompts_response = admin_client.get(
        "/api/v1/admin/prompts",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert prompts_response.status_code == 200
    prompt_keys = {item["prompt_key"] for item in prompts_response.json()["items"]}
    assert len(prompt_keys) >= 2
    assert "conversation_context_classification" in prompt_keys
    assert "answer_explanation" in prompt_keys


def test_prompt_update_creates_admin_audit_event(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    prompts_response = admin_client.get(
        "/api/v1/admin/prompts",
        headers={"Authorization": f"Bearer {token}"},
    )
    prompt = prompts_response.json()["items"][0]

    update_response = admin_client.put(
        f"/api/v1/admin/prompts/{prompt['prompt_key']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": prompt["name"],
            "description": "Updated from test.",
            "system_prompt": prompt["system_prompt"],
            "user_prompt_template": prompt["user_prompt_template"],
            "active": True,
        },
    )

    assert update_response.status_code == 200
    assert update_response.json()["item"]["version"] == prompt["version"] + 1

    audit_response = admin_client.get(
        "/api/v1/admin/audit/admin-events",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    assert any(item["action"] == "prompt.update" for item in audit_response.json()["items"])


def test_llm_config_defaults_to_metadata_and_can_be_overridden(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    get_response = admin_client.get(
        "/api/v1/admin/llm-config",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert get_response.status_code == 200
    item = get_response.json()["item"]
    assert item["provider"] == "openai-compatible"
    assert item["base_url"] == "https://api.openai.com/v1"
    assert "api_key" not in item
    assert item["api_key_configured"] is False
    assert item["api_key_preview"] is None
    assert item["model"] == "gpt-4.1-mini"
    assert item["timeout_seconds"] == 60
    assert item["extra_body"] == {}
    assert item["sql_generation_mode"] == "mock"
    assert item["result_interpretation_mode"] == "mock"
    assert item["intent_classification_mode"] == "auto"
    assert item["output_classification_mode"] == "auto"
    assert item["query_max_rows"] == 100
    assert set(item["sources"].values()) == {"metadata"}

    update_response = admin_client.put(
        "/api/v1/admin/llm-config",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "http://metadata-llm.test/v1",
            "api_key": "metadata-secret",
            "model": "metadata-model",
            "timeout_seconds": 45,
            "intent_classification_mode": "llm",
            "sql_generation_mode": "llm",
            "result_interpretation_mode": "llm",
            "output_classification_mode": "llm",
            "query_max_rows": 250,
            "query_timeout_seconds": 20,
            "extra_body": {"temperature": 0, "chat_template_kwargs": {"enable_thinking": True}},
        },
    )

    assert update_response.status_code == 200
    updated_item = update_response.json()["item"]
    assert updated_item["base_url"] == "http://metadata-llm.test/v1"
    assert "api_key" not in updated_item
    assert updated_item["api_key_configured"] is True
    assert updated_item["api_key_preview"] == "****cret"
    assert updated_item["model"] == "metadata-model"
    assert updated_item["timeout_seconds"] == 45
    assert updated_item["intent_classification_mode"] == "llm"
    assert updated_item["sql_generation_mode"] == "llm"
    assert updated_item["result_interpretation_mode"] == "llm"
    assert updated_item["output_classification_mode"] == "llm"
    assert updated_item["query_max_rows"] == 250
    assert updated_item["query_timeout_seconds"] == 20
    assert updated_item["extra_body"] == {
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    assert set(updated_item["sources"].values()) == {"metadata"}

    runtime_config = get_llm_runtime_config_safe()
    assert runtime_config.base_url == "http://metadata-llm.test/v1"
    assert runtime_config.api_key == "metadata-secret"
    assert runtime_config.model == "metadata-model"
    assert runtime_config.timeout_seconds == 45
    assert runtime_config.extra_body == {
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": True},
    }

    audit_response = admin_client.get(
        "/api/v1/admin/audit/admin-events",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    audit_item = next(
        item for item in audit_response.json()["items"] if item["action"] == "llm_config.update"
    )
    assert audit_item["details"]["model"] == "metadata-model"
    assert "api_key" not in audit_item["details"]

    preserve_response = admin_client.put(
        "/api/v1/admin/llm-config",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "http://metadata-llm-2.test/v1",
            "api_key": "",
            "model": "metadata-model-2",
            "timeout_seconds": 30,
            "extra_body": {},
        },
    )

    assert preserve_response.status_code == 200
    preserved_item = preserve_response.json()["item"]
    assert "api_key" not in preserved_item
    assert preserved_item["api_key_configured"] is True
    assert preserved_item["api_key_preview"] == "****cret"

    runtime_config = get_llm_runtime_config_safe()
    assert runtime_config.base_url == "http://metadata-llm-2.test/v1"
    assert runtime_config.api_key == "metadata-secret"
    assert runtime_config.model == "metadata-model-2"

    clear_response = admin_client.put(
        "/api/v1/admin/llm-config",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "http://metadata-llm-3.test/v1",
            "clear_api_key": True,
            "model": "metadata-model-3",
            "timeout_seconds": 30,
            "extra_body": {},
        },
    )

    assert clear_response.status_code == 200
    cleared_item = clear_response.json()["item"]
    assert "api_key" not in cleared_item
    assert cleared_item["api_key_configured"] is False
    assert cleared_item["api_key_preview"] is None

    runtime_config = get_llm_runtime_config_safe()
    assert runtime_config.base_url == "http://metadata-llm-3.test/v1"
    assert runtime_config.api_key == "change-me"
    assert runtime_config.model == "metadata-model-3"


def test_llm_config_can_be_tested_without_saving(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    class FakeOpenAICompatibleClient:
        init_kwargs: dict[str, Any] | None = None
        requests: ClassVar[list[ChatCompletionRequest]] = []

        def __init__(self, **kwargs: Any) -> None:
            self.__class__.init_kwargs = kwargs

        def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
            self.__class__.requests.append(request)
            return ChatCompletionResponse(content="OK", model=request.model)

    monkeypatch.setattr(
        "gaard_api.admin.services.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    response = admin_client.post(
        "/api/v1/admin/llm-config/test",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "https://llm.example/v1",
            "api_key": "test-secret",
            "model": "chat-test",
            "timeout_seconds": 45,
            "extra_body": {"temperature": 0},
        },
    )

    assert response.status_code == 200
    assert response.json()["item"] == {"ok": True, "model": "chat-test"}
    assert FakeOpenAICompatibleClient.init_kwargs == {
        "base_url": "https://llm.example/v1",
        "api_key": "test-secret",
        "timeout_seconds": 45,
    }
    request = FakeOpenAICompatibleClient.requests[0]
    assert request.model == "chat-test"
    assert request.messages[0].role == "user"
    assert "connection test" in request.messages[0].content
    assert request.extra_body == {"temperature": 0}

    runtime_config = get_llm_runtime_config_safe()
    assert runtime_config.base_url == "https://api.openai.com/v1"
    assert runtime_config.api_key == "change-me"
    assert runtime_config.model == "gpt-4.1-mini"


def test_llm_models_are_listed_without_saving(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    class FakeOpenAICompatibleClient:
        init_kwargs: dict[str, Any] | None = None

        def __init__(self, **kwargs: Any) -> None:
            self.__class__.init_kwargs = kwargs

        def list_models(self) -> list[str]:
            return ["model-a", "model-b"]

    monkeypatch.setattr(
        "gaard_api.api.v1.admin.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    response = admin_client.post(
        "/api/v1/admin/llm-config/models",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "https://llm.example/v1",
            "api_key": "test-secret",
            "timeout_seconds": 45,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"items": ["model-a", "model-b"], "error": None}
    assert FakeOpenAICompatibleClient.init_kwargs == {
        "base_url": "https://llm.example/v1",
        "api_key": "test-secret",
        "timeout_seconds": 45,
    }


def test_llm_models_can_fail_without_blocking_manual_model_entry(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    class FailingOpenAICompatibleClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def list_models(self) -> list[str]:
            raise LlmProviderError("Connection refused")

    monkeypatch.setattr(
        "gaard_api.api.v1.admin.OpenAICompatibleClient",
        FailingOpenAICompatibleClient,
    )

    response = admin_client.post(
        "/api/v1/admin/llm-config/models",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "https://llm.example/v1",
            "api_key": "test-secret",
        },
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert "Could not load models" in response.json()["error"]


def test_llm_config_test_can_reuse_saved_api_key(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    update_response = admin_client.put(
        "/api/v1/admin/llm-config",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "https://saved-llm.example/v1",
            "api_key": "saved-secret",
            "model": "saved-model",
            "timeout_seconds": 30,
            "extra_body": {},
        },
    )
    assert update_response.status_code == 200

    class FakeOpenAICompatibleClient:
        init_kwargs: dict[str, Any] | None = None

        def __init__(self, **kwargs: Any) -> None:
            self.__class__.init_kwargs = kwargs

        def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
            return ChatCompletionResponse(content="OK", model=request.model)

    monkeypatch.setattr(
        "gaard_api.admin.services.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    response = admin_client.post(
        "/api/v1/admin/llm-config/test",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "https://llm.example/v1",
            "api_key": "",
            "model": "chat-test",
            "timeout_seconds": 45,
            "extra_body": {},
        },
    )

    assert response.status_code == 200
    assert FakeOpenAICompatibleClient.init_kwargs == {
        "base_url": "https://llm.example/v1",
        "api_key": "saved-secret",
        "timeout_seconds": 45,
    }


def test_llm_config_test_requires_api_key(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    response = admin_client.post(
        "/api/v1/admin/llm-config/test",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "https://llm.example/v1",
            "api_key": "",
            "model": "chat-test",
            "timeout_seconds": 45,
            "extra_body": {},
        },
    )

    assert response.status_code == 400
    assert "LLM API key is required" in response.json()["detail"]


def test_reasoning_config_defaults_to_metadata_and_can_be_overridden(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    get_response = admin_client.get(
        "/api/v1/admin/reasoning-config",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert get_response.status_code == 200
    item = get_response.json()["item"]
    assert item["intent_classification_mode"] == "auto"
    assert item["sql_generation_mode"] == "mock"
    assert item["result_interpretation_mode"] == "mock"
    assert item["output_classification_mode"] == "auto"
    assert item["query_max_rows"] == 100
    assert item["query_timeout_seconds"] == 30
    assert item["analysis_loop_count"] == 5
    assert item["analysis_auto_enable_business_logic"] is False
    assert set(item["sources"].values()) == {"metadata"}

    update_response = admin_client.put(
        "/api/v1/admin/reasoning-config",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "intent_classification_mode": "llm",
            "sql_generation_mode": "llm",
            "result_interpretation_mode": "llm",
            "output_classification_mode": "llm",
            "query_max_rows": 250,
            "query_timeout_seconds": 20,
            "analysis_loop_count": 7,
            "analysis_auto_enable_business_logic": True,
        },
    )

    assert update_response.status_code == 200
    updated_item = update_response.json()["item"]
    assert updated_item["intent_classification_mode"] == "llm"
    assert updated_item["sql_generation_mode"] == "llm"
    assert updated_item["result_interpretation_mode"] == "llm"
    assert updated_item["output_classification_mode"] == "llm"
    assert updated_item["query_max_rows"] == 250
    assert updated_item["query_timeout_seconds"] == 20
    assert updated_item["analysis_loop_count"] == 7
    assert updated_item["analysis_auto_enable_business_logic"] is True

    with create_session() as session:
        query_config = get_query_runtime_config(session)
        assert query_config.intent_classification_mode == "llm"
        assert query_config.query_max_rows == 250
        assert query_config.query_timeout_seconds == 20
        assert query_config.analysis_loop_count == 7
        assert query_config.analysis_auto_enable_business_logic is True

    audit_response = admin_client.get(
        "/api/v1/admin/audit/admin-events",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    audit_item = next(
        item
        for item in audit_response.json()["items"]
        if item["action"] == "reasoning_config.update"
    )
    assert audit_item["details"]["query_max_rows"] == 250


def test_governance_policy_defaults_to_metadata_and_can_be_overridden(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    get_response = admin_client.get(
        "/api/v1/admin/governance-policy",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert get_response.status_code == 200
    item = get_response.json()["item"]
    assert item["final_answer"] == {
        "record_level_pii_allowed": False,
        "prefer_aggregates_for_sensitive_domains": True,
    }
    assert item["sql"] == {
        "read_only": True,
        "select_star_allowed": False,
        "tenant_filter_required": False,
        "tenant_column": None,
    }
    assert item["privacy"] == {
        "forbidden_columns": {},
        "record_level_forbidden": False,
    }
    assert item["pii_column_names"]["identity"] == [
        "first_name",
        "last_name",
        "full_name",
    ]
    assert item["sources"]["governance_policy"] == "metadata"

    update_response = admin_client.put(
        "/api/v1/admin/governance-policy",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "final_answer": {
                "record_level_pii_allowed": True,
                "prefer_aggregates_for_sensitive_domains": False,
            },
            "sql": {
                "read_only": True,
                "select_star_allowed": True,
                "tenant_filter_required": True,
                "tenant_column": "tenant_id",
            },
            "privacy": {
                "forbidden_columns": {"employees": ["salary"]},
                "record_level_forbidden": True,
            },
            "pii_column_names": {"custom": ["employee_code"]},
        },
    )

    assert update_response.status_code == 200
    updated_item = update_response.json()["item"]
    assert updated_item["final_answer"]["record_level_pii_allowed"] is True
    assert updated_item["final_answer"]["prefer_aggregates_for_sensitive_domains"] is False
    assert updated_item["sql"]["select_star_allowed"] is True
    assert updated_item["sql"]["tenant_filter_required"] is True
    assert updated_item["sql"]["tenant_column"] == "tenant_id"
    assert updated_item["privacy"]["record_level_forbidden"] is True
    assert updated_item["privacy"]["forbidden_columns"] == {"employees": ["salary"]}
    assert updated_item["pii_column_names"] == {"custom": ["employee_code"]}

    audit_response = admin_client.get(
        "/api/v1/admin/audit/admin-events",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    audit_item = next(
        item
        for item in audit_response.json()["items"]
        if item["action"] == "governance_policy.update"
    )
    assert audit_item["details"]["pii_column_names"] == {"custom": ["employee_code"]}


def test_governance_policy_infers_pii_columns_from_metadata_dictionary(
    admin_client: TestClient,
) -> None:
    login_response = login(admin_client)
    change_password(admin_client, login_response["token"])
    schema_summary = {
        "tables": {
            "employees": {
                "columns": {
                    "full_name": {"type": "TEXT"},
                    "employee_code": {"type": "TEXT"},
                    "department": {"type": "TEXT"},
                }
            }
        }
    }

    with create_session() as session:
        default_policy = get_governance_policy_for_schema(session, schema_summary)
        assert default_policy["privacy"]["forbidden_columns"] == {"employees": ["full_name"]}

    update_response = admin_client.put(
        "/api/v1/admin/governance-policy",
        headers={"Authorization": f"Bearer {login_response['token']}"},
        json={
            "final_answer": {
                "record_level_pii_allowed": False,
                "prefer_aggregates_for_sensitive_domains": True,
            },
            "sql": {
                "read_only": True,
                "select_star_allowed": False,
                "tenant_filter_required": False,
                "tenant_column": None,
            },
            "privacy": {
                "forbidden_columns": {"employees": ["department"]},
                "record_level_forbidden": False,
            },
            "pii_column_names": {"internal": ["employee_code"]},
        },
    )
    assert update_response.status_code == 200

    with create_session() as session:
        updated_policy = get_governance_policy_for_schema(session, schema_summary)

    assert updated_policy["privacy"]["forbidden_columns"] == {
        "employees": ["department", "employee_code"]
    }


def test_overview_returns_metadata_backed_widgets(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    response = admin_client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()

    assert any(item["connector_key"] == "metadata-db" for item in body["datasources"])
    assert [item["widget_key"] for item in body["info_widgets"]] == [
        "prompts_count",
        "audit_retention",
        "schema_cache_ttl",
        "license_edition",
    ]
    assert body["runtime_widget"] is None
    assert [item["widget_key"] for item in body["table_widgets"]] == [
        "prompt_templates_table",
    ]
    assert body["info_widgets"][0]["grid_width"] == 1
    assert body["table_widgets"][0]["grid_width"] == 12
    assert body["table_widgets"][0]["result"]["status"] == "ok"
    assert body["table_widgets"][0]["result"]["columns"] == [
        "prompt_key",
        "name",
        "version",
        "active",
    ]
    assert len(body["table_widgets"][0]["result"]["rows"]) >= 1
    assert body["info_widgets"][0]["result"]["status"] == "ok"
    assert body["info_widgets"][0]["sql"] == "SELECT COUNT(*) AS value FROM prompt_templates"

    widgets_response = admin_client.get(
        "/api/v1/admin/overview/widgets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert widgets_response.status_code == 200
    widgets = widgets_response.json()["items"]
    runtime_widget = next(item for item in widgets if item["widget_key"] == "runtime_daily_queries")
    assert runtime_widget["active"] is False
    assert runtime_widget["grid_width"] == 12

    second_response = admin_client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second_response.status_code == 200

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert audit_response.status_code == 200
    assert audit_response.json()["items"] == []


def test_overview_widget_can_be_updated(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    update_response = admin_client.put(
        "/api/v1/admin/overview/widgets/prompts_count",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "label": "Prompt templates",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "Return one value for the prompt template count.",
            "tags": ["public", "user:admin", "user:not-a-user"],
            "assigned_usernames": [],
        },
    )

    assert update_response.status_code == 200
    assert update_response.json()["item"]["label"] == "Prompt templates"
    assert update_response.json()["item"]["sql"] == "SELECT 1 AS value"
    assert update_response.json()["item"]["assigned_usernames"] == ["admin"]
    assert "admin" in update_response.json()["item"]["tags"]
    assert "user:not-a-user" in update_response.json()["item"]["tags"]

    overview_response = admin_client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert overview_response.status_code == 200
    overview = overview_response.json()
    widget = next(
        item for item in overview["info_widgets"] if item["widget_key"] == "prompts_count"
    )
    assert widget["label"] == "Prompt templates"
    assert widget["question"] == "Return one value for the prompt template count."

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    audit_items = audit_response.json()["items"]
    assert len(audit_items) == 1
    assert audit_items[0]["audit_type"] == "info"
    assert audit_items[0]["output_classification"] == "unknown"
    assert audit_items[0]["user_id"] == "overview-widget-config:prompts_count"
    assert audit_items[0]["datasource_id"] == "metadata-db"
    assert audit_items[0]["question"] == "Return one value for the prompt template count."
    assert audit_items[0]["sql"] == "SELECT 1 AS value"
    assert audit_items[0]["metadata"]["operation"] == "overview_widget.update"
    assert audit_items[0]["metadata"]["widget_key"] == "prompts_count"


def test_admin_created_overview_widget_is_not_implicitly_assigned_to_creator(
    admin_client: TestClient,
) -> None:
    headers = auth_headers(admin_client)

    response = admin_client.post(
        "/api/v1/admin/overview/widgets",
        headers=headers,
        json={
            "widget_key": "admin_created_widget",
            "label": "Admin-created widget",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "Return one value for the prompt template count.",
        },
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["assigned_usernames"] == []
    assert item["tags"] == ["public"]


def test_overview_widget_save_only_generates_sql_when_question_changes(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    headers = auth_headers(admin_client)
    create_response = admin_client.post(
        "/api/v1/admin/overview/widgets",
        headers=headers,
        json={
            "widget_key": "save_sql_widget",
            "label": "Save SQL widget",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "Return one value for the prompt template count.",
        },
    )
    assert create_response.status_code == 200

    generated_questions: list[str] = []

    def generate_sql(*args: Any, **kwargs: Any) -> str:
        generated_questions.append(kwargs["query_request"].question)
        return "SELECT 1 AS value"

    monkeypatch.setattr(admin_api, "generate_overview_widget_sql", generate_sql)

    unchanged_response = admin_client.put(
        "/api/v1/admin/overview/widgets/save_sql_widget",
        headers=headers,
        json={
            "label": "Renamed save SQL widget",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "Return one value for the prompt template count.",
        },
    )
    assert unchanged_response.status_code == 200
    assert generated_questions == []

    changed_response = admin_client.put(
        "/api/v1/admin/overview/widgets/save_sql_widget",
        headers=headers,
        json={
            "label": "Renamed save SQL widget",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "Return one new value.",
        },
    )
    assert changed_response.status_code == 200
    assert generated_questions == ["Return one new value."]


def test_overview_widget_sql_can_be_generated_without_saving(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    headers = auth_headers(admin_client)
    monkeypatch.setattr(
        admin_api,
        "generate_overview_widget_sql",
        lambda **_kwargs: "SELECT 1 AS value",
    )

    response = admin_client.post(
        "/api/v1/admin/overview/widgets/generate-sql",
        headers=headers,
        json={
            "widget_key": "preview_sql_widget",
            "datasource_key": "metadata-db",
            "question": "Return one value.",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"sql": "SELECT 1 AS value"}
    with create_session() as session:
        assert session.scalar(
            select(OverviewWidget).where(OverviewWidget.widget_key == "preview_sql_widget")
        ) is None


def test_overview_widget_can_use_table_type(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    update_response = admin_client.put(
        "/api/v1/admin/overview/widgets/prompt_templates_table",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "label": "Prompt template rows",
            "widget_type": "table",
            "datasource_key": "metadata-db",
            "question": "Return rows for prompt templates.",
            "grid_width": 3,
        },
    )

    assert update_response.status_code == 200
    item = update_response.json()["item"]
    assert item["widget_type"] == "table"
    assert item["grid_width"] == 3
    assert item["result"]["status"] == "ok"
    assert item["result"]["columns"] == ["value"]
    assert item["result"]["rows"] == [{"value": 1}]


def test_overview_widget_grid_height_is_persisted(admin_client: TestClient) -> None:
    headers = auth_headers(admin_client)

    response = admin_client.patch(
        "/api/v1/admin/overview/widgets/prompts_count/state",
        headers=headers,
        json={
            "active": True,
            "position": 70,
            "grid_width": 6,
            "grid_height": 7,
        },
    )

    assert response.status_code == 200
    assert response.json()["item"]["grid_height"] == 7

    overview_response = admin_client.get("/api/v1/admin/overview", headers=headers)
    assert overview_response.status_code == 200
    widget = next(
        item
        for item in overview_response.json()["widgets"]
        if item["widget_key"] == "prompts_count"
    )
    assert widget["position"] == 70
    assert widget["grid_width"] == 6
    assert widget["grid_height"] == 7


def test_overview_widget_can_be_saved_from_query_and_deleted(
    admin_client: TestClient,
) -> None:
    headers = auth_headers(admin_client)
    create_response = admin_client.post(
        "/api/v1/admin/overview/widgets/from-query",
        headers=headers,
        json={
            "label": "Prompt count from client",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "How many prompts are configured?",
            "sql": "SELECT COUNT(*) AS value FROM prompt_templates",
            "rows": [{"value": 1}],
        },
    )

    assert create_response.status_code == 200
    created_item = create_response.json()["item"]
    widget_key = created_item["widget_key"]
    assert widget_key.startswith("client_prompt_count_from_client")
    assert created_item["active"] is False
    assert created_item["result_mode"] == "data"
    assert created_item["result"]["status"] == "ok"
    assert created_item["assigned_usernames"] == ["admin"]
    with create_session() as session:
        created_widget = session.scalar(
            select(OverviewWidget).where(OverviewWidget.widget_key == widget_key)
        )
        assert created_widget is not None
        assert set(
            session.scalars(
                select(OverviewWidgetTag.tag_name).where(
                    OverviewWidgetTag.widget_id == created_widget.id
                )
            )
        ) == {"public", "admin"}

    overview_response = admin_client.get(
        "/api/v1/admin/overview",
        headers=headers,
    )
    assert overview_response.status_code == 200
    assert all(item["widget_key"] != widget_key for item in overview_response.json()["widgets"])

    widgets_response = admin_client.get(
        "/api/v1/admin/overview/widgets",
        headers=headers,
    )
    assert widgets_response.status_code == 200
    assert any(item["widget_key"] == widget_key for item in widgets_response.json()["items"])

    delete_response = admin_client.delete(
        f"/api/v1/admin/overview/widgets/{widget_key}",
        headers=headers,
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"

    widgets_after_delete = admin_client.get(
        "/api/v1/admin/overview/widgets",
        headers=headers,
    )
    assert widgets_after_delete.status_code == 200
    assert all(item["widget_key"] != widget_key for item in widgets_after_delete.json()["items"])


def test_overview_widget_title_suggestion_uses_llm(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(admin_client)

    with create_session() as session:
        set_setting(session, "gaard_llm_api_key", "test-key", "test")
        set_setting(session, "gaard_llm_model", "title-model", "test")
        session.commit()

    class FakeOpenAICompatibleClient:
        init_kwargs: dict[str, Any] | None = None
        requests: ClassVar[list[ChatCompletionRequest]] = []

        def __init__(self, **kwargs: Any) -> None:
            self.__class__.init_kwargs = kwargs

        def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
            self.__class__.requests.append(request)
            return ChatCompletionResponse(
                content="```text\nDoctors by Specialty.\n```",
                model=request.model,
            )

    monkeypatch.setattr(
        "gaard_api.api.v1.admin.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    response = admin_client.post(
        "/api/v1/admin/overview/widgets/title-suggestion",
        headers=headers,
        json={
            "question": "How many doctors are there by specialty?",
            "sql": "SELECT specialization, COUNT(*) FROM doctors GROUP BY specialization",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"title": "Doctors by Specialty"}
    assert FakeOpenAICompatibleClient.init_kwargs is not None
    assert FakeOpenAICompatibleClient.init_kwargs["api_key"] == "test-key"
    request = FakeOpenAICompatibleClient.requests[0]
    assert request.model == "title-model"
    assert request.temperature == 0.0
    assert request.messages[0].role == "system"
    assert "same language as the user question" in request.messages[0].content
    assert "How many doctors" in request.messages[1].content


def test_overview_widget_from_query_strips_datasource_qualifier(
    admin_client: TestClient,
) -> None:
    headers = auth_headers(admin_client)
    create_response = admin_client.post(
        "/api/v1/admin/overview/widgets/from-query",
        headers=headers,
        json={
            "label": "Qualified prompt count",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "How many prompts are configured?",
            "sql": 'SELECT COUNT(*) AS value FROM "metadata-db".prompt_templates',
            "rows": [{"value": 1}],
        },
    )

    assert create_response.status_code == 200
    created_item = create_response.json()["item"]
    assert created_item["sql"] == ('SELECT COUNT(*) AS value FROM "metadata-db".prompt_templates')

    state_response = admin_client.patch(
        f"/api/v1/admin/overview/widgets/{created_item['widget_key']}/state",
        headers=headers,
        json={"active": True},
    )
    assert state_response.status_code == 200

    overview_response = admin_client.get(
        "/api/v1/admin/overview",
        headers=headers,
    )

    assert overview_response.status_code == 200
    widget = next(
        item
        for item in overview_response.json()["widgets"]
        if item["widget_key"] == created_item["widget_key"]
    )
    assert widget["result"]["status"] == "ok"
    assert widget["result"]["columns"] == ["value"]
    assert widget["result"]["rows"][0]["value"] >= 1


def test_overview_widget_generation_strips_unquoted_datasource_qualifier(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    def generate_qualified_sql(self: MockSqlGenerator, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(
            sql=(
                "SELECT CAST(value AS INTEGER) AS retention_days "
                "FROM metadata-db.admin_settings "
                "WHERE key = 'data_query_audit_retention_days'"
            ),
            confidence=0.8,
            assumptions=[],
        )

    monkeypatch.setattr(MockSqlGenerator, "generate", generate_qualified_sql)

    update_response = admin_client.put(
        "/api/v1/admin/overview/widgets/audit_retention",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "label": "Audit retention",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "Return audit retention days.",
        },
    )

    assert update_response.status_code == 200
    item = update_response.json()["item"]
    assert item["sql"] == (
        "SELECT CAST(value AS INTEGER) AS retention_days "
        "FROM metadata-db.admin_settings "
        "WHERE key = 'data_query_audit_retention_days'"
    )
    assert item["result"]["status"] == "ok"
    assert item["result"]["columns"] == ["retention_days"]


def test_overview_widget_can_return_interpreted_result(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    update_response = admin_client.put(
        "/api/v1/admin/overview/widgets/prompts_count",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "label": "Prompt templates",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "Return one value for the prompt template count.",
            "result_mode": "interpretation",
        },
    )

    assert update_response.status_code == 200
    item = update_response.json()["item"]
    assert item["result_mode"] == "interpretation"
    assert item["result"]["result_mode"] == "interpretation"
    assert item["result"]["answer"].startswith("Zapytanie zwróciło wynik:")
    assert item["result"]["value"] == item["result"]["answer"]


def test_overview_widget_sql_error_creates_metadata_business_logic_suggestion(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)
    stub_business_logic_learning_llm(
        monkeypatch,
        {
            "create_suggestion": True,
            "error_category": "schema.missing_table",
            "title": "Use metadata prompt table",
            "rule_text": (
                "When counting configured prompts in the metadata datasource, use "
                "`prompt_templates` rather than nonexistent table `prompts`."
            ),
            "failed_identifier": "prompts",
            "repaired_identifier": "prompt_templates",
            "confidence": 0.88,
            "terms": ["prompts", "prompt_templates"],
            "join_hints": [],
            "skip_reason": "",
        },
    )

    def generate_bad_sql(self: MockSqlGenerator, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(
            sql="SELECT COUNT(*) AS value FROM prompts",
            confidence=0.6,
            assumptions=["Intentional missing metadata table for overview learning test."],
        )

    monkeypatch.setattr(MockSqlGenerator, "generate", generate_bad_sql)

    update_response = admin_client.put(
        "/api/v1/admin/overview/widgets/prompts_count",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "label": "Prompt templates",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "How many prompts are configured?",
        },
    )

    assert update_response.status_code == 400

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    audit_item = audit_response.json()["items"][0]
    assert audit_item["output_classification"] == "unknown"
    assert audit_item["user_id"] == "overview-widget-config:prompts_count"
    assert audit_item["datasource_id"] == "metadata-db"
    assert audit_item["metadata"]["error_category"] == "schema.missing_table"
    assert audit_item["metadata"]["failed_identifier"] == "prompts"
    assert audit_item["metadata"]["business_logic_learning"]["suggestion_id"]

    visible_suggestions_response = admin_client.get(
        "/api/v1/admin/business-logic-suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert visible_suggestions_response.status_code == 200
    assert visible_suggestions_response.json()["datasource"]["connector_key"] == "default"
    assert visible_suggestions_response.json()["items"] == []

    with create_session() as session:
        metadata_connector = session.scalar(
            select(DatasourceConnector).where(DatasourceConnector.connector_key == "metadata-db")
        )
        assert metadata_connector is not None
        suggestions = list_business_logic_suggestions(session, metadata_connector.id)

    assert len(suggestions) == 1
    assert suggestions[0].failed_identifier == "prompts"
    assert suggestions[0].repaired_identifier == "prompt_templates"


def test_default_datasource_can_be_tested_and_introspected(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    datasources_response = admin_client.get(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert datasources_response.status_code == 200
    items = datasources_response.json()["items"]
    datasource = items[0]
    metadata_datasource = next(item for item in items if item["connector_key"] == "metadata-db")
    assert datasource["connector_key"] == "default"
    assert datasource["database_type"] == "sqlite"
    assert metadata_datasource["system_managed"] is True
    assert metadata_datasource["active"] is False

    test_response = admin_client.post(
        f"/api/v1/admin/datasources/{datasource['id']}/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert test_response.status_code == 200
    assert test_response.json()["status"] == "ok"

    introspect_response = admin_client.post(
        f"/api/v1/admin/datasources/{datasource['id']}/introspect",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert introspect_response.status_code == 200
    schema = introspect_response.json()["item"]["raw_schema"]
    assert {table["name"] for table in schema["tables"]} >= {
        "patients",
        "appointments",
        "doctors",
    }


def test_datasource_schema_returns_introspection_error(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    with create_session() as session:
        connector = DatasourceConnector(
            connector_key="broken-schema",
            name="Broken schema",
            database_type="sqlite",
            database_url="sqlite:///:memory:",
            sql_dialect="sqlite",
            active=False,
            updated_by="admin",
        )
        session.add(connector)
        session.commit()
        connector_id = connector.id

    def fail_introspection(
        _session: object,
        _connector: DatasourceConnector,
        _actor: str,
    ) -> DatasourceSchemaCache:
        raise ValueError("SQLAlchemy could not infer a common column type.")

    monkeypatch.setattr(admin_api, "introspect_datasource_connector", fail_introspection)

    response = admin_client.get(
        f"/api/v1/admin/datasources/{connector_id}/schema",
        headers=auth_headers(admin_client),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Schema introspection failed: SQLAlchemy could not infer a common column type."
    )


def test_unsaved_datasource_test_returns_connector_error(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    def fail_connection_test(_: DatasourceConnector) -> None:
        raise ValueError("SQLAlchemy could not infer a common column type.")

    monkeypatch.setattr(admin_api, "test_datasource_connection", fail_connection_test)

    response = admin_client.post(
        "/api/v1/admin/datasources/test",
        headers=auth_headers(admin_client),
        json={
            "database_type": "sqlite",
            "database_url": "sqlite:///:memory:",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Connection test failed: SQLAlchemy could not infer a common column type."
    )


def test_user_can_list_datasources_but_cannot_create_them(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(
            features={"identity_management": True, "sql_sources": True},
            limits={"sources": None},
        ),
    )
    monkeypatch.setattr(admin_api, "identity_privileges_are_active", lambda: False)
    headers = user_headers(enterprise_access=True)

    list_response = admin_client.get("/api/v1/admin/datasources", headers=headers)

    assert list_response.status_code == 200
    assert list_response.json()["viewer"] == "client-user"
    assert list_response.json()["items"]

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers=headers,
        json={
            "connector_key": "not-allowed",
            "name": "Not allowed",
            "database_type": "sqlite",
            "database_url": "sqlite:///not-allowed.db",
            "active": False,
        },
    )

    assert create_response.status_code == 403
    assert create_response.json()["detail"] == "Admin role is required."


def test_client_datasource_selection_is_per_user_and_requires_available_sources(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(features={"identity_management": True}),
    )
    monkeypatch.setattr(admin_api, "identity_privileges_are_active", lambda: False)
    first_user_headers = user_headers("first-user", enterprise_access=True)
    second_user_headers = user_headers("second-user", enterprise_access=True)

    first_response = admin_client.put(
        "/api/v1/admin/datasources/selection",
        headers=first_user_headers,
        json={"datasource_ids": ["default"]},
    )
    assert first_response.status_code == 200
    assert first_response.json()["selected_datasource_ids"] == ["default"]

    first_list_response = admin_client.get(
        "/api/v1/admin/datasources?available_only=true", headers=first_user_headers
    )
    second_list_response = admin_client.get(
        "/api/v1/admin/datasources?available_only=true", headers=second_user_headers
    )
    assert first_list_response.json()["selected_datasource_ids"] == ["default"]
    assert second_list_response.json()["selected_datasource_ids"] == []

    unavailable_response = admin_client.put(
        "/api/v1/admin/datasources/selection",
        headers=first_user_headers,
        json={"datasource_ids": ["metadata-db"]},
    )
    assert unavailable_response.status_code == 403


def test_identity_privileges_match_local_identity_ids_from_admin_permissions(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(admin_api, "identity_privileges_are_active", lambda: True)
    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(features={"identity_management": True}),
    )
    headers = user_headers("client-user", enterprise_access=True)

    with create_session() as session:
        user = session.scalar(select(AdminUser).where(AdminUser.username == "client-user"))
        connector = session.scalar(
            select(DatasourceConnector).where(DatasourceConnector.connector_key == "default")
        )
        assert user is not None
        assert connector is not None
        user_id = user.id
        admin_session = session.scalar(
            select(AdminSession).where(AdminSession.user_id == user_id)
        )
        assert admin_session is not None
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS identity_privilege_datasource_permissions (
                id INTEGER PRIMARY KEY,
                connector_id INTEGER,
                identity_id VARCHAR(512),
                allowed BOOLEAN
            )
        """))
        session.execute(
            text("""
                INSERT INTO identity_privilege_datasource_permissions
                    (connector_id, identity_id, allowed)
                VALUES (:connector_id, :identity_id, 1)
            """),
            {
                "connector_id": connector.id,
                "identity_id": str(user_id),
            },
        )
        session.commit()

    response = admin_client.get(
        "/api/v1/admin/datasources?available_only=true",
        headers=headers,
    )

    assert response.status_code == 200
    assert [item["connector_key"] for item in response.json()["items"]] == ["default"]
    principal = AuthenticatedSession(
        session=admin_session,
        user=user,
    )
    assert identity_id_for_principal(principal) == str(user_id)

    received_identity_ids: list[str | None] = []

    class CapturingHook:
        def filter_datasource_keys(
            self, identity_id: str | None, datasource_keys: list[str]
        ) -> list[str]:
            received_identity_ids.append(identity_id)
            return []

    registry = QueryHookRegistry()
    registry.register(CapturingHook())
    assert registry.filter_datasource_contexts(
        principal,
        [(connector, cast(DatasourceSchemaCache, object()))],
    ) == []
    assert received_identity_ids == [str(user_id)]


def test_deleting_datasource_removes_identity_privilege_permissions(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(admin_api, "identity_privileges_are_active", lambda: True)
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    with create_session() as session:
        connector = DatasourceConnector(
            connector_key="removable_source",
            name="Removable source",
            database_type="sqlite",
            database_url="sqlite:///./removable.db",
            sql_dialect="sqlite",
            active=False,
            updated_by="admin",
        )
        session.add(connector)
        session.flush()
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS identity_privilege_datasource_permissions (
                connector_id INTEGER NOT NULL,
                identity_id VARCHAR(512) NOT NULL,
                allowed BOOLEAN NOT NULL,
                UNIQUE (connector_id, identity_id)
            )
        """))
        session.execute(
            text("""
                INSERT INTO identity_privilege_datasource_permissions
                    (connector_id, identity_id, allowed)
                VALUES (:connector_id, '1', 1)
            """),
            {"connector_id": connector.id},
        )
        connector_id = connector.id
        session.commit()

    response = admin_client.delete(
        f"/api/v1/admin/datasources/{connector_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    with create_session() as session:
        remaining_permissions = session.scalar(
            text("""
                SELECT COUNT(*)
                FROM identity_privilege_datasource_permissions
                WHERE connector_id = :connector_id
            """),
            {"connector_id": connector_id},
        )
    assert remaining_permissions == 0


def test_deactivating_datasource_removes_it_from_client_selections(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(
            features={"identity_management": True, "sql_sources": True},
            limits={"sources": None},
        ),
    )
    monkeypatch.setattr(admin_api, "identity_privileges_are_active", lambda: False)
    headers = user_headers("first-user", enterprise_access=True)
    selection_response = admin_client.put(
        "/api/v1/admin/datasources/selection",
        headers=headers,
        json={"datasource_ids": ["default"]},
    )
    assert selection_response.status_code == 200

    token = login(admin_client)["token"]
    change_password(admin_client, token)
    datasource_response = admin_client.get(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
    )
    datasource_id = next(
        item["id"]
        for item in datasource_response.json()["items"]
        if item["connector_key"] == "default"
    )
    deactivate_response = admin_client.post(
        f"/api/v1/admin/datasources/{datasource_id}/state",
        headers={"Authorization": f"Bearer {token}"},
        json={"active": False},
    )
    assert deactivate_response.status_code == 200

    list_response = admin_client.get(
        "/api/v1/admin/datasources?available_only=true", headers=headers
    )
    assert list_response.json()["selected_datasource_ids"] == []
    with create_session() as session:
        user_id = session.scalar(select(AdminUser.id).where(AdminUser.username == "first-user"))
        selection = session.get(UserDatasourceSelection, str(user_id))
        assert selection is not None
        assert json.loads(selection.datasource_ids_json) == []


def test_datasource_connector_accepts_postgres_sql_dialect(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "pg_db",
            "name": "Postgres DB",
            "database_type": "postgresql",
            "database_url": "postgresql://user:pass@example.test/app",
            "sql_dialect": "postgres",
            "active": False,
        },
    )

    assert create_response.status_code == 200
    item = create_response.json()["item"]
    assert item["database_type"] == "postgresql"
    assert item["sql_dialect"] == "postgres"


def test_datasource_connector_builds_sqlite_url_from_database_path(
    admin_client: TestClient,
    tmp_path: Path,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)
    db_path = tmp_path / "path-source.db"
    sqlite3.connect(db_path).close()

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "path_db",
            "name": "Path DB",
            "database_type": "sqlite",
            "database_path": str(db_path),
            "active": False,
        },
    )

    assert create_response.status_code == 200
    item = create_response.json()["item"]
    assert item["database_type"] == "sqlite"
    assert item["database_url"] == f"sqlite:///{db_path}"
    assert item["sql_dialect"] == "sqlite"


def test_excel_upload_without_duckdb_excel_connector_returns_400(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    token = login(admin_client)["token"]
    change_password(admin_client, token)
    upload_dir = tmp_path / "excel-uploads"
    monkeypatch.setattr(settings, "gaard_excel_upload_directory", str(upload_dir))
    monkeypatch.setattr(admin_api, "get_connector_registry", create_builtin_connector_registry)

    response = admin_client.post(
        "/api/v1/admin/datasources/excel-upload",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "file": (
                "cases.xlsx",
                io.BytesIO(b"not a real workbook"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 400
    assert "duckdb-excel datasource connector" in response.json()["detail"]
    assert not upload_dir.exists()


def test_unassigned_user_cannot_use_or_upload_excel_datasources(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(admin_api, "get_connector_registry", create_builtin_connector_registry)
    monkeypatch.setattr(admin_api, "identity_privileges_are_active", lambda: False)
    headers = user_headers()
    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(features={"identity_management": True}),
    )

    with create_session() as session:
        user = session.scalar(select(AdminUser).where(AdminUser.username == "client-user"))
        assert user is not None
        user.role = "user"
        user.enterprise_access = False
        session.add(
            DatasourceConnector(
                connector_key="excel_source",
                name="Excel source",
                database_type="duckdb-excel",
                database_url="duckdb-excel:///test.xlsx",
                sql_dialect="duckdb",
                active=True,
                updated_by="admin",
            )
        )
        session.commit()

    list_response = admin_client.get(
        "/api/v1/admin/datasources?available_only=true",
        headers=headers,
    )
    assert list_response.status_code == 403
    assert list_response.json()["detail"] == (
        "This account has dashboard-only access because no Enterprise user license is assigned."
    )

    select_response = admin_client.put(
        "/api/v1/admin/datasources/selection",
        headers=headers,
        json={"datasource_ids": ["excel_source"]},
    )
    assert select_response.status_code == 403

    upload_response = admin_client.post(
        "/api/v1/admin/datasources/excel-upload",
        headers=headers,
        files={
            "file": (
                "cases.xlsx",
                io.BytesIO(b"not a real workbook"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert upload_response.status_code == 403
    assert upload_response.json()["detail"] == (
        "This account has dashboard-only access because no Enterprise user license is assigned."
    )


def test_unassigned_user_has_dashboard_only_access(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api import auth_dependencies
    from gaard_api.api.v1 import admin as admin_api

    monkeypatch.setattr(
        auth_dependencies.license_service,
        "identity_management_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(features={"identity_management": True}),
    )
    headers = user_headers("dashboard-only-user")

    dashboards = admin_client.get("/api/v1/dashboards", headers=headers)
    assert dashboards.status_code == 200

    query = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={"question": "How many records are there?"},
    )
    assert query.status_code == 403
    assert query.json()["detail"] == (
        "This account has dashboard-only access because no Enterprise user license is assigned."
    )

    datasources = admin_client.get("/api/v1/admin/datasources", headers=headers)
    assert datasources.status_code == 403
    assert datasources.json()["detail"] == query.json()["detail"]


def test_inactive_global_enterprise_allows_only_admin_login(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaard_api.api.v1 import admin as admin_api

    with create_session() as session:
        session.add(
            AdminUser(
                username="inactive-license-user",
                password_hash=hash_password("user-password"),
                must_change_password=False,
                role="user",
            )
        )
        session.commit()

    monkeypatch.setattr(
        admin_api.license_service,
        "refresh_if_due",
        lambda: SimpleNamespace(features={"identity_management": False}),
    )

    user_login = admin_client.post(
        "/api/v1/admin/auth/login",
        json={"username": "inactive-license-user", "password": "user-password"},
    )
    assert user_login.status_code == 403
    assert admin_client.post(
        "/api/v1/admin/auth/login",
        json={"username": "admin", "password": "admin"},
    ).status_code == 200


def test_public_datasource_activation_keeps_single_active_datasource(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import gaard_api.extensions

    monkeypatch.setattr(gaard_api.extensions, "get_query_hook_registry", QueryHookRegistry)

    token = login(admin_client)["token"]
    change_password(admin_client, token)
    first_db = tmp_path / "first-active.db"
    second_db = tmp_path / "second-active.db"
    sqlite3.connect(first_db).close()
    sqlite3.connect(second_db).close()

    for connector_key, database_url in (
        ("first_active", f"sqlite:///{first_db}"),
        ("second_active", f"sqlite:///{second_db}"),
    ):
        response = admin_client.post(
            "/api/v1/admin/datasources",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "connector_key": connector_key,
                "name": connector_key,
                "database_type": "sqlite",
                "database_url": database_url,
                "sql_dialect": "sqlite",
                "active": True,
            },
        )
        assert response.status_code == 200

    response = admin_client.get(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    active_keys = [item["connector_key"] for item in response.json()["items"] if item["active"]]
    assert active_keys == ["second_active"]


def test_datasource_connector_builds_postgres_url_from_connection_fields(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "pg_fields",
            "name": "Postgres fields",
            "database_type": "postgresql",
            "connection_config": {
                "host": "db.example.test",
                "port": 5433,
                "database": "analytics",
                "username": "reporter",
                "password": "pa:ss@word",
                "sslmode": "require",
            },
            "active": False,
        },
    )

    assert create_response.status_code == 200
    item = create_response.json()["item"]
    assert item["database_type"] == "postgresql"
    assert item["database_url"] == (
        "postgresql+psycopg://reporter:pa%3Ass%40word@"
        "db.example.test:5433/analytics?sslmode=require"
    )
    assert item["sql_dialect"] == "postgres"


def test_datasource_connector_builds_mysql_url_from_connection_fields(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "mysql_fields",
            "name": "MySQL fields",
            "database_type": "mysql",
            "connection_config": {
                "host": "mysql.example.test",
                "port": 3307,
                "database": "sales",
                "username": "reader",
                "password": "secret",
                "charset": "utf8mb4",
            },
            "active": False,
        },
    )

    assert create_response.status_code == 200
    item = create_response.json()["item"]
    assert item["database_type"] == "mysql"
    assert item["database_url"] == (
        "mysql+pymysql://reader:secret@mysql.example.test:3307/sales?charset=utf8mb4"
    )
    assert item["sql_dialect"] == "mysql"


def test_datasource_connector_builds_oracle_url_from_connection_fields(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "oracle_fields",
            "name": "Oracle fields",
            "database_type": "oracle",
            "connection_config": {
                "host": "oracle.example.test",
                "port": 1522,
                "service_name": "FREEPDB1",
                "username": "reader",
                "password": "secret",
            },
            "active": False,
        },
    )

    assert create_response.status_code == 200
    item = create_response.json()["item"]
    assert item["database_type"] == "oracle"
    assert item["database_url"] == (
        "oracle+oracledb://reader:secret@oracle.example.test:1522?service_name=FREEPDB1"
    )
    assert item["sql_dialect"] == "oracle"


def test_datasource_connector_builds_mssql_url_from_connection_fields(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "mssql_fields",
            "name": "SQL Server fields",
            "database_type": "mssql",
            "connection_config": {
                "host": "sqlserver.example.test",
                "port": 1434,
                "database": "warehouse",
                "username": "reader",
                "password": "secret",
                "driver": "ODBC Driver 18 for SQL Server",
                "Encrypt": "yes",
                "TrustServerCertificate": "no",
            },
            "active": False,
        },
    )

    assert create_response.status_code == 200
    item = create_response.json()["item"]
    assert item["database_type"] == "mssql"
    assert item["database_url"] == (
        "mssql+pyodbc://reader:secret@sqlserver.example.test:1434/warehouse"
        "?Encrypt=yes&TrustServerCertificate=no&driver=ODBC+Driver+18+for+SQL+Server"
    )
    assert item["sql_dialect"] == "tsql"


def test_datasource_connector_builds_odbc_dsn_url_without_returning_password(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)
    password = "P@ss:w/ord;ąćę% + {}"

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "odbc_dsn",
            "name": "ODBC DSN",
            "database_type": "odbc",
            "connection_config": {
                "connection_mode": "dsn",
                "sqlalchemy_drivername": "mssql+pyodbc",
                "dsn": "hospital_reporting",
                "username": "gaard_reader",
                "password": password,
            },
            "active": False,
        },
    )

    assert create_response.status_code == 200
    item = create_response.json()["item"]
    assert item["database_type"] == "odbc"
    assert item["sql_dialect"] == "tsql"
    assert password not in item["database_url"]
    assert "%2A%2A%2A" in item["database_url"]

    with create_session() as session:
        connector = session.scalar(
            select(DatasourceConnector).where(DatasourceConnector.connector_key == "odbc_dsn")
        )
        assert connector is not None
        parsed = make_url(connector.database_url)

    assert parsed.drivername == "mssql+pyodbc"
    odbc_options = dict(parse_odbc_connection_string(str(parsed.query["odbc_connect"])))
    assert odbc_options["DSN"] == "hospital_reporting"
    assert odbc_options["UID"] == "gaard_reader"
    assert odbc_options["PWD"] == password


def test_datasource_connector_builds_odbc_dsnless_url_from_connection_fields(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "odbc_dsnless",
            "name": "ODBC DSN-less",
            "database_type": "odbc",
            "connection_config": {
                "connection_mode": "dsnless",
                "sqlalchemy_drivername": "mssql+pyodbc",
                "odbc_driver": "ODBC Driver 18 for SQL Server",
                "host": "unixodbc-bridge.internal",
                "port": 1433,
                "database": "ERP",
                "username": "gaard_reader",
                "password": "secret",
                "extra_odbc_options": {
                    "TrustServerCertificate": "yes",
                    "Encrypt": "yes",
                },
            },
            "active": False,
        },
    )

    assert create_response.status_code == 200
    with create_session() as session:
        connector = session.scalar(
            select(DatasourceConnector).where(
                DatasourceConnector.connector_key == "odbc_dsnless"
            )
        )
        assert connector is not None
        parsed = make_url(connector.database_url)

    assert parsed.query["odbc_connect"] == (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        "SERVER=unixodbc-bridge.internal,1433;"
        "DATABASE=ERP;"
        "UID=gaard_reader;"
        "PWD=secret;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )


def test_datasource_connector_update_preserves_odbc_password_when_left_blank(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "odbc_keep_secret",
            "name": "ODBC keep secret",
            "database_type": "odbc",
            "connection_config": {
                "connection_mode": "dsn",
                "sqlalchemy_drivername": "mssql+pyodbc",
                "dsn": "hospital_reporting",
                "username": "gaard_reader",
                "password": "secret",
            },
            "active": False,
        },
    )
    assert create_response.status_code == 200
    datasource_id = create_response.json()["item"]["id"]

    update_response = admin_client.put(
        f"/api/v1/admin/datasources/{datasource_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "ODBC renamed",
            "database_type": "odbc",
            "connection_config": {
                "connection_mode": "dsn",
                "sqlalchemy_drivername": "mssql+pyodbc",
                "dsn": "hospital_reporting",
                "username": "gaard_reader",
                "password": "",
            },
            "active": False,
        },
    )

    assert update_response.status_code == 200
    assert "secret" not in update_response.json()["item"]["database_url"]

    with create_session() as session:
        connector = session.get(DatasourceConnector, datasource_id)
        assert connector is not None
        parsed = make_url(connector.database_url)

    assert parsed.query["odbc_connect"] == "DSN=hospital_reporting;UID=gaard_reader;PWD=secret;"


def test_datasource_connector_builds_ibm_db2_url_from_connection_fields(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "db2_fields",
            "name": "Db2 fields",
            "database_type": "ibm_db2",
            "connection_config": {
                "host": "db2.example.test",
                "port": 50001,
                "database": "analytics",
                "username": "reader",
                "password": "secret",
            },
            "active": False,
        },
    )

    assert create_response.status_code == 200
    item = create_response.json()["item"]
    assert item["database_type"] == "ibm_db2"
    assert item["database_url"] == "db2+ibm_db://reader:secret@db2.example.test:50001/analytics"
    assert item["sql_dialect"] == "db2"


def test_datasource_connector_builds_teradata_url_from_connection_fields(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "teradata_fields",
            "name": "Teradata fields",
            "database_type": "teradata",
            "connection_config": {
                "host": "td.example.test",
                "dbs_port": 1026,
                "database": "analytics",
                "username": "reader",
                "password": "secret",
                "tmode": "ANSI",
            },
            "active": False,
        },
    )

    assert create_response.status_code == 200
    item = create_response.json()["item"]
    assert item["database_type"] == "teradata"
    assert item["database_url"] == (
        "teradatasql://reader:secret@td.example.test?database=analytics&dbs_port=1026&tmode=ANSI"
    )
    assert item["sql_dialect"] == "teradata"


def test_sql_generation_prompt_uses_active_datasource_dialect(
    admin_client: TestClient,
) -> None:
    headers = auth_headers(admin_client)

    with create_session() as session:
        for connector in session.scalars(select(DatasourceConnector)):
            connector.active = False

        connector = DatasourceConnector(
            connector_key="mysql_leads",
            name="MySQL leads",
            database_type="mysql",
            database_url="mysql://user:pass@example.test/leads",
            sql_dialect="mysql",
            active=True,
        )
        session.add(connector)
        session.flush()
        session.add(
            DatasourceSchemaCache(
                connector_id=connector.id,
                schema_json='{"tables":[]}',
                table_settings_json="{}",
                formatted_schema="Table: lead\nColumns:\n- id: INTEGER",
            )
        )
        session.commit()

    response = admin_client.post(
        "/api/v1/prompts/sql-generation",
        headers=headers,
        json={"question": "ile jest wpisów w tabeli lead"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["dialect"] == "mysql"
    assert "Table: lead" in body["user_prompt"]


def test_business_logic_suggestions_include_all_active_datasources(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    with create_session() as session:
        for connector in session.scalars(select(DatasourceConnector)):
            connector.active = False

        con_a = DatasourceConnector(
            connector_key="con_a",
            name="Connector A",
            database_type="sqlite",
            database_url="sqlite:///a.db",
            sql_dialect="sqlite",
            active=True,
        )
        con_b = DatasourceConnector(
            connector_key="con_b",
            name="Connector B",
            database_type="sqlite",
            database_url="sqlite:///b.db",
            sql_dialect="sqlite",
            active=True,
        )
        con_disabled = DatasourceConnector(
            connector_key="con_disabled",
            name="Connector disabled",
            database_type="sqlite",
            database_url="sqlite:///disabled.db",
            sql_dialect="sqlite",
            active=False,
        )
        session.add_all([con_a, con_b, con_disabled])
        session.flush()
        session.add_all(
            [
                BusinessLogicSuggestion(
                    connector_id=con_a.id,
                    error_category="schema.missing_table",
                    title="Rule A",
                    rule_text="Use active datasource A.",
                ),
                BusinessLogicSuggestion(
                    connector_id=con_b.id,
                    error_category="schema.missing_column",
                    title="Rule B",
                    rule_text="Use active datasource B.",
                ),
                BusinessLogicSuggestion(
                    connector_id=con_disabled.id,
                    error_category="schema.missing_table",
                    title="Disabled rule",
                    rule_text="Do not show inactive datasource suggestions.",
                ),
            ]
        )
        session.commit()

    response = admin_client.get(
        "/api/v1/admin/business-logic-suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["connector_key"] for item in body["datasources"]] == ["con_a", "con_b"]
    assert body["datasource"]["connector_key"] == "con_a"
    assert {item["title"] for item in body["items"]} == {"Rule A", "Rule B"}


def test_business_logic_suggestions_returns_empty_state_without_active_datasources(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    with create_session() as session:
        for connector in session.scalars(select(DatasourceConnector)):
            connector.active = False
        session.commit()

    response = admin_client.get(
        "/api/v1/admin/business-logic-suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["datasource"] is None
    assert response.json()["datasources"] == []
    assert response.json()["items"] == []


def test_datasource_schema_table_settings_are_saved(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    datasource = admin_client.get(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["items"][0]

    admin_client.post(
        f"/api/v1/admin/datasources/{datasource['id']}/introspect",
        headers={"Authorization": f"Bearer {token}"},
    )

    update_response = admin_client.put(
        f"/api/v1/admin/datasources/{datasource['id']}/schema/tables",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "tables": {
                "patients": {
                    "selected": True,
                    "description": "People receiving care.",
                    "primary_key_prompt": "Use patients.id as the stable row identifier.",
                    "foreign_key_prompt": "",
                    "join_logic": "Join appointments.patient_id to patients.id.",
                },
                "appointments": {
                    "selected": False,
                    "description": "",
                    "primary_key_prompt": "",
                    "foreign_key_prompt": "",
                    "join_logic": "",
                },
                "doctors": {
                    "selected": True,
                    "description": "",
                    "primary_key_prompt": "",
                    "foreign_key_prompt": "",
                    "join_logic": "",
                },
            }
        },
    )

    assert update_response.status_code == 200
    item = update_response.json()["item"]
    assert "People receiving care." in item["formatted_schema"]
    assert "Join appointments.patient_id to patients.id." in item["formatted_schema"]
    assert "Table: appointments" not in item["formatted_schema"]


def test_query_endpoint_writes_data_query_audit(admin_client: TestClient) -> None:
    headers = auth_headers(admin_client)

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 200

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries",
        headers=headers,
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert items[0]["audit_type"] == "info"
    assert items[0]["output_classification"] == "neutral_data"
    assert "audit_type" not in items[0]["metadata"]
    assert "output_classification" not in items[0]["metadata"]
    assert items[0]["user_id"] == "alice"
    assert items[0]["question"] == "How many active patients are there?"

    classification_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?output_classification=neutral_data",
        headers=headers,
    )
    assert classification_response.status_code == 200
    assert len(classification_response.json()["items"]) == 1

    sql_match_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?sql_contains=COUNT",
        headers=headers,
    )
    assert sql_match_response.status_code == 200
    assert len(sql_match_response.json()["items"]) == 1

    sql_miss_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?sql_contains=missing_fragment",
        headers=headers,
    )
    assert sql_miss_response.status_code == 200
    assert sql_miss_response.json()["items"] == []

    invalid_classification_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?output_classification=surprising",
        headers=headers,
    )
    assert invalid_classification_response.status_code == 400

    with create_session() as session:
        audit_log = session.scalar(select(DataQueryAuditLog))

    assert audit_log is not None
    assert audit_log.type == DataQueryAuditType.INFO
    assert audit_log.output_classification == OutputClassification.NEUTRAL_DATA


def test_query_explain_endpoint_uses_answer_explanation_prompt(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(admin_client)
    monkeypatch.setattr(settings, "gaard_llm_api_key", "test-key")
    monkeypatch.setattr(settings, "gaard_llm_model", "explain-model")
    with create_session() as session:
        set_setting(session, "gaard_llm_api_key", "test-key", "test")
        set_setting(session, "gaard_llm_model", "explain-model", "test")
        connector = session.scalar(
            select(DatasourceConnector).where(DatasourceConnector.active.is_(True))
        )
        assert connector is not None
        connector_key = connector.connector_key
        session.add(
            BusinessLogicSuggestion(
                connector_id=connector.id,
                source_audit_id=None,
                status="approved",
                safety="safe",
                enabled=True,
                error_category="analysis",
                title="Count completed appointments",
                rule_text="When counting admitted patients, count completed appointments.",
                terms_json=json.dumps(["patients", "appointments"]),
                join_hints_json="[]",
                confidence=0.9,
                updated_by="test",
            )
        )
        session.commit()

    class FakeOpenAICompatibleClient:
        requests: ClassVar[list[ChatCompletionRequest]] = []

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
            self.__class__.requests.append(request)
            return ChatCompletionResponse(
                content="SQL liczy zakończone wizyty, bo o to pyta użytkownik.",
                model="explain-model",
            )

    monkeypatch.setattr(
        "gaard_api.api.v1.query.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    response = admin_client.post(
        "/api/v1/query/explain",
        headers=headers,
        json={
            "question": "Ilu pacjentów przyjęto w tym tygodniu?",
            "sql": "SELECT COUNT(*) AS patient_count FROM appointments WHERE status = 'completed'",
            "answer": "Przyjęto 12 pacjentów.",
            "rows": [{"patient_count": 12}],
            "metadata": {
                "datasource_id": connector_key,
                "datasource_ids": [connector_key],
                "intent_decision": "read_only_data_question",
                "sql_generation_mode": "llm",
                "sql_generation_prompt_metadata": {
                    "prompt_key": "sql_generation",
                    "prompt_version": 3,
                    "dialect": "sqlite",
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["explanation"] == "SQL liczy zakończone wizyty, bo o to pyta użytkownik."
    assert body["metadata"]["prompt_key"] == "answer_explanation"
    assert body["metadata"]["business_logic_included"] is True
    assert body["metadata"]["datasource_ids"] == [connector_key]

    llm_request = FakeOpenAICompatibleClient.requests[0]
    assert llm_request.model == "explain-model"
    prompt_text = "\n".join(message.content for message in llm_request.messages)
    assert "Ilu pacjentów przyjęto w tym tygodniu" in prompt_text
    assert "SELECT COUNT(*) AS patient_count" in prompt_text
    assert '"intent_decision": "read_only_data_question"' in prompt_text
    assert '"prompt_key": "sql_generation"' in prompt_text
    assert "When counting admitted patients" in prompt_text


def test_query_endpoint_can_return_raw_sql_output_without_interpretation(
    admin_client: TestClient,
) -> None:
    headers = auth_headers(admin_client)

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "pokaż wartość kontrolną",
            "user_id": "alice",
            "interpret": False,
        },
    )

    assert query_response.status_code == 200
    body = query_response.json()
    assert body["answer"] == ""
    assert body["sql"] == "SELECT 1 AS value"
    assert body["rows"] == [{"value": 1}]
    assert body["metadata"]["raw_sql_output"] is True
    assert body["metadata"]["result_interpretation_mode"] == "none"
    assert body["metadata"]["output_classification_mode"] == "none"
    assert body["metadata"]["output_classification"] == "unknown"

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries",
        headers=headers,
    )

    assert audit_response.status_code == 200
    item = audit_response.json()["items"][0]
    assert item["answer"] == ""
    assert item["sql"] == "SELECT 1 AS value"
    assert item["output_classification"] == "unknown"
    assert item["metadata"]["raw_sql_output"] is True


def test_query_endpoint_ignores_legacy_mode_field(
    admin_client: TestClient,
) -> None:
    headers = auth_headers(admin_client)

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "pokaż wartość kontrolną",
            "user_id": "alice",
            "mode": "investigation",
        },
    )

    assert query_response.status_code == 200
    body = query_response.json()
    assert body["answer"] == "Zapytanie zwróciło wynik: {'value': 1}."
    assert body["sql"] == "SELECT 1 AS value"
    assert body["rows"] == [{"value": 1}]
    assert "query_mode" not in body["metadata"]

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries",
        headers=headers,
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert items[0]["question"] == "pokaż wartość kontrolną"
    assert "query_mode" not in items[0]["metadata"]


def test_query_stream_ignores_legacy_mode_field(
    admin_client: TestClient,
) -> None:
    headers = auth_headers(admin_client)

    response = admin_client.post(
        "/api/v1/query/stream",
        headers=headers,
        json={
            "question": "pokaż wartość kontrolną",
            "user_id": "alice",
            "mode": "investigation",
        },
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.strip().splitlines()]
    # 3 events: processing query,waiting on data server and final: {question:'',anwser:''}
    assert len(events) == 3
    event = next((x for x in events if x["final"] is not None), None)
    assert event is not None
    assert event["final"]["sql"] == "SELECT 1 AS value"
    assert "query_mode" not in event["final"]["metadata"]


def test_query_blocks_write_intent_before_llm_and_writes_access_audit(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(admin_client)
    with create_session() as session:
        set_setting(session, "gaard_sql_generation_mode", "llm", "test")
        session.commit()

    class DenyIntentClassifier:
        def classify(self, request: QueryRequest) -> QueryIntentClassification:
            return QueryIntentClassification(
                decision=QueryIntentDecision.WRITE_OR_MUTATION_REQUEST,
                confidence=0.98,
                reason="The request asks to mutate data.",
            )

    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_intent_classifier",
        lambda llm_config=None: DenyIntentClassifier(),
    )

    questions = [
        "skasuj wszystkie zlecenia",
        "zmodufikuj zlecenia klienta Emix, tak, żeby były warte 100",
        "wyzeruj bazę danych",
    ]

    for question in questions:
        query_response = admin_client.post(
            "/api/v1/query",
            headers=headers,
            json={
                "question": question,
                "user_id": "alice",
            },
        )

        assert query_response.status_code == 200
        body = query_response.json()
        assert body["answer"].startswith("Nie mogę obsłużyć")
        assert body["sql"] == ""
        assert body["rows"] == []
        assert body["metadata"]["blocked"] is True
        assert body["metadata"]["blocked_reason"] == "access.intent_classification"
        assert body["metadata"]["intent_decision"] == "write_or_mutation_request"
        assert body["metadata"]["intent_confidence"] == 0.98
        assert body["metadata"]["intent_reason"] == "The request asks to mutate data."
        assert body["metadata"]["intent_model_response"] == {
            "decision": "write_or_mutation_request",
            "confidence": 0.98,
            "reason": "The request asks to mutate data.",
        }
        assert body["metadata"]["output_classification"] == "unknown"

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=access_error",
        headers=headers,
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert len(items) == 3
    assert {item["question"] for item in items} == set(questions)
    for item in items:
        assert item["audit_type"] == "access_error"
        assert item["output_classification"] == "unknown"
        assert item["user_id"] == "alice"
        assert item["answer"].startswith("Nie mogę obsłużyć")
        assert item["metadata"]["error_category"] == "access.intent_classification"
        assert item["metadata"]["intent_decision"] == "write_or_mutation_request"
        assert item["metadata"]["intent_confidence"] == 0.98
        assert item["metadata"]["intent_reason"] == "The request asks to mutate data."
        assert item["metadata"]["intent_model_response"] == {
            "decision": "write_or_mutation_request",
            "confidence": 0.98,
            "reason": "The request asks to mutate data.",
        }


def test_query_writes_access_audit_for_generated_non_select_sql(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(admin_client)

    def generate_update_sql(self: MockSqlGenerator, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(
            sql="UPDATE patients SET status = 'inactive'",
            confidence=0.6,
            assumptions=["Intentional non-read-only SQL for validation audit test."],
        )

    monkeypatch.setattr(MockSqlGenerator, "generate", generate_update_sql)

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "How many patients are there?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 200
    body = query_response.json()
    assert body["answer"].startswith("Nie mogę tego zrobić.")
    assert body["sql"] == "UPDATE patients SET status = 'inactive'"
    assert body["rows"] == []
    assert body["metadata"]["blocked"] is True
    assert body["metadata"]["blocked_reason"] == "access.sql_validation"
    assert body["metadata"]["intent_model_response"]["decision"] == "read_only_data_question"

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=access_error",
        headers=headers,
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert len(items) == 1
    assert items[0]["audit_type"] == "access_error"
    assert items[0]["sql"] == "UPDATE patients SET status = 'inactive'"
    assert items[0]["metadata"]["error_category"] == "access.sql_validation"
    assert items[0]["metadata"]["error_code"] == "SQL_VALIDATION_ERROR"
    assert items[0]["metadata"]["intent_model_response"]["decision"] == "read_only_data_question"


def test_query_writes_audit_for_llm_provider_error_during_sql_generation(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(admin_client)

    class AllowIntentClassifier:
        def classify(self, request: QueryRequest) -> QueryIntentClassification:
            return QueryIntentClassification(
                decision=QueryIntentDecision.READ_ONLY_DATA_QUESTION,
                confidence=0.99,
                reason="Read-only question.",
            )

    class FailingPipeline:
        def handle(self, request: QueryRequest) -> None:
            raise QueryPipelineStepError(
                message="LLM provider returned HTTP 400. context length exceeded",
                phase="sql_generation",
                sql="",
                error_code="LLM_PROVIDER_ERROR",
                error_detail="context length exceeded",
            )

    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_intent_classifier",
        lambda llm_config=None: AllowIntentClassifier(),
    )
    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_pipeline",
        lambda datasource_context=None, interpret=True, enterprise_access=True: FailingPipeline(),
    )

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "jakie zlecenia były ostatnio modyfikowane?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 502
    assert query_response.json()["error"]["code"] == "LLM_PROVIDER_ERROR"

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers=headers,
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert len(items) == 1
    assert items[0]["audit_type"] == "sql_error"
    assert items[0]["sql"] == ""
    assert items[0]["answer"] == "LLM provider returned HTTP 400. context length exceeded"
    assert items[0]["metadata"]["error_category"] == "llm.provider_error"
    assert items[0]["metadata"]["error_code"] == "LLM_PROVIDER_ERROR"
    assert items[0]["metadata"]["pipeline_phase"] == "sql_generation"
    assert items[0]["metadata"]["intent_decision"] == "read_only_data_question"


def test_query_writes_generated_sql_for_llm_provider_error_after_sql_generation(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(admin_client)

    class AllowIntentClassifier:
        def classify(self, request: QueryRequest) -> QueryIntentClassification:
            return QueryIntentClassification(
                decision=QueryIntentDecision.READ_ONLY_DATA_QUESTION,
                confidence=0.99,
                reason="Read-only question.",
            )

    class FailingPipeline:
        def handle(self, request: QueryRequest) -> None:
            raise QueryPipelineStepError(
                message="LLM provider request failed.",
                phase="result_interpretation",
                sql="SELECT id, temat FROM design_zlecenie ORDER BY updated_at DESC",
                error_code="LLM_PROVIDER_ERROR",
                error_detail="provider timeout",
            )

    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_intent_classifier",
        lambda llm_config=None: AllowIntentClassifier(),
    )
    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_pipeline",
        lambda datasource_context=None, interpret=True, enterprise_access=True: FailingPipeline(),
    )

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "jakie zlecenia były ostatnio modyfikowane?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 502

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers=headers,
    )

    assert audit_response.status_code == 200
    item = audit_response.json()["items"][0]
    assert item["metadata"]["error_category"] == "llm.provider_error"
    assert item["metadata"]["pipeline_phase"] == "result_interpretation"
    assert item["sql"] == "SELECT id, temat FROM design_zlecenie ORDER BY updated_at DESC"


def test_query_audit_uses_active_datasource_connector_key(admin_client: TestClient) -> None:
    headers = auth_headers(admin_client)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers=headers,
        json={
            "connector_key": "con_db",
            "name": "Connected DB",
            "database_type": "sqlite",
            "database_url": settings.gaard_datasource_url,
            "sql_dialect": "sqlite",
            "active": True,
        },
    )

    assert create_response.status_code == 200

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 200
    assert query_response.json()["metadata"]["datasource_id"] == "con_db"

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries",
        headers=headers,
    )

    assert audit_response.status_code == 200
    assert audit_response.json()["items"][0]["datasource_id"] == "con_db"


def test_query_without_active_datasources_returns_before_ai(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(admin_client)

    with create_session() as session:
        for connector in session.scalars(select(DatasourceConnector)):
            connector.active = False
        session.commit()

    def fail_if_ai_is_touched(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("AI and query pipeline should not run without active datasources.")

    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_intent_classifier",
        fail_if_ai_is_touched,
    )
    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_pipeline",
        fail_if_ai_is_touched,
    )

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 200
    payload = query_response.json()
    assert payload["answer"] == (
        "No active data sources are selected. Please select at least one data source "
        "before asking a data question."
    )
    assert payload["sql"] == ""
    assert payload["rows"] == []
    assert payload["metadata"]["blocked"] is True
    assert payload["metadata"]["blocked_reason"] == "datasource.none_active"


def test_query_endpoint_writes_sql_error_data_query_audit(
    admin_client: TestClient,
    tmp_path: Path,
) -> None:
    headers = auth_headers(admin_client)
    empty_db = tmp_path / "empty.db"

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers=headers,
        json={
            "connector_key": "broken_db",
            "name": "Broken DB",
            "database_type": "sqlite",
            "database_url": f"sqlite:///{empty_db}",
            "sql_dialect": "sqlite",
            "active": True,
        },
    )

    assert create_response.status_code == 200

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 400
    assert query_response.json()["error"]["code"] == "QUERY_EXECUTION_ERROR"

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers=headers,
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert len(items) == 1
    assert items[0]["audit_type"] == "sql_error"
    assert items[0]["output_classification"] == "unknown"
    assert items[0]["datasource_id"] == "broken_db"
    assert items[0]["user_id"] == "alice"
    assert "audit_type" not in items[0]["metadata"]
    assert items[0]["metadata"]["error_code"] == "QUERY_EXECUTION_ERROR"
    assert items[0]["metadata"]["business_logic_learning"]["status"] == "skipped"
    assert (
        items[0]["metadata"]["business_logic_learning"]["reason"]
        == "LLM API key is not configured for business logic learning."
    )
    assert "SELECT COUNT(*)" in items[0]["sql"]

    with create_session() as session:
        audit_log = session.scalar(select(DataQueryAuditLog))

    assert audit_log is not None
    assert audit_log.type == DataQueryAuditType.SQL_ERROR

    info_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=info",
        headers=headers,
    )

    assert info_response.status_code == 200
    assert info_response.json()["items"] == []


def test_sql_error_creates_datasource_scoped_business_logic_suggestion(
    admin_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(admin_client)
    learning_llm = stub_business_logic_learning_llm(
        monkeypatch,
        {
            "create_suggestion": True,
            "error_category": "schema.missing_table",
            "title": "Map project orders table",
            "rule_text": (
                "When the user asks about zlecenia or projects, use table "
                "`zlecenia_zlecenie` rather than nonexistent table `zlecenia`."
            ),
            "failed_identifier": "zlecenia",
            "repaired_identifier": "zlecenia_zlecenie",
            "confidence": 0.84,
            "terms": ["zlecenia", "projekty", "zlecenia_zlecenie"],
            "join_hints": [],
            "skip_reason": "",
        },
    )
    db_path = tmp_path / "projects.db"
    connection = sqlite3.connect(db_path)

    try:
        connection.execute("CREATE TABLE zlecenia_zlecenie (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE zlecenia_osoby (id INTEGER PRIMARY KEY)")
        connection.execute(
            """
            CREATE TABLE zlecenia_projektanci (
                zlecenie_id INTEGER NOT NULL,
                osoby_id INTEGER NOT NULL,
                FOREIGN KEY(zlecenie_id) REFERENCES zlecenia_zlecenie(id),
                FOREIGN KEY(osoby_id) REFERENCES zlecenia_osoby(id)
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    def generate_bad_sql(self: MockSqlGenerator, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(
            sql="SELECT COUNT(*) AS liczba_projektow FROM zlecenia",
            confidence=0.6,
            assumptions=["Intentional missing table for repair learning test."],
        )

    monkeypatch.setattr(MockSqlGenerator, "generate", generate_bad_sql)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers=headers,
        json={
            "connector_key": "con_db",
            "name": "Connected DB",
            "database_type": "sqlite",
            "database_url": f"sqlite:///{db_path}",
            "sql_dialect": "sqlite",
            "active": True,
        },
    )

    assert create_response.status_code == 200
    connector_id = create_response.json()["item"]["id"]

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "Który pracownik zrealizował najwięcej projektów",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 400

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers=headers,
    )
    audit_item = audit_response.json()["items"][0]

    assert audit_item["metadata"]["error_category"] == "schema.missing_table"
    assert audit_item["metadata"]["failed_identifier"] == "zlecenia"
    assert audit_item["metadata"]["business_logic_learning"]["suggestion_id"]
    assert audit_item["metadata"]["business_logic_learning"]["admin_section"] == "business-logic"
    assert len(learning_llm.requests) == 1
    learning_prompt = "\n".join(message.content for message in learning_llm.requests[0].messages)
    assert "Który pracownik zrealizował najwięcej projektów" in learning_prompt
    assert "SELECT COUNT(*) AS liczba_projektow FROM zlecenia" in learning_prompt
    assert "no such table: zlecenia" in learning_prompt

    suggestions_response = admin_client.get(
        "/api/v1/admin/business-logic-suggestions",
        headers=headers,
    )

    assert suggestions_response.status_code == 200
    assert suggestions_response.json()["datasource"]["connector_key"] == "con_db"
    suggestions = suggestions_response.json()["items"]
    assert len(suggestions) == 1
    assert suggestions[0]["status"] == "pending"
    assert suggestions[0]["enabled"] is False
    assert suggestions[0]["failed_identifier"] == "zlecenia"
    assert suggestions[0]["repaired_identifier"] == "zlecenia_zlecenie"
    assert "zlecenia_zlecenie" in suggestions[0]["rule_text"]

    enable_response = admin_client.put(
        f"/api/v1/admin/business-logic-suggestions/{suggestions[0]['id']}",
        headers=headers,
        json={"enabled": True},
    )

    assert enable_response.status_code == 200
    assert enable_response.json()["item"]["status"] == "active"
    assert enable_response.json()["item"]["enabled"] is True
    assert "zlecenia_zlecenie" in get_active_business_logic_prompt_safe(connector_id)

    edit_response = admin_client.put(
        f"/api/v1/admin/business-logic-suggestions/{suggestions[0]['id']}",
        headers=headers,
        json={
            "title": "Updated business logic",
            "rule_text": "Use `zlecenia_zlecenie` for customer project orders.",
        },
    )

    assert edit_response.status_code == 200
    edited = edit_response.json()["item"]
    assert edited["title"] == "Updated business logic"
    assert edited["rule_text"] == "Use `zlecenia_zlecenie` for customer project orders."
    assert edited["status"] == "active"
    assert edited["enabled"] is True
    assert "customer project orders" in get_active_business_logic_prompt_safe(connector_id)

    empty_edit_response = admin_client.put(
        f"/api/v1/admin/business-logic-suggestions/{suggestions[0]['id']}",
        headers=headers,
        json={"rule_text": "   "},
    )

    assert empty_edit_response.status_code == 400

    delete_response = admin_client.delete(
        f"/api/v1/admin/business-logic-suggestions/{suggestions[0]['id']}",
        headers=headers,
    )

    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"


def test_missing_column_sql_error_creates_business_logic_suggestion(
    admin_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(admin_client)
    stub_business_logic_learning_llm(
        monkeypatch,
        {
            "create_suggestion": True,
            "error_category": "schema.missing_column",
            "title": "Avoid missing address column",
            "rule_text": (
                "Do not select `adres` from `zlecenia_osoby`; this table does not "
                "store a full address column. Use only listed columns."
            ),
            "failed_identifier": "zlecenia_osoby.adres",
            "repaired_identifier": "",
            "confidence": 0.72,
            "terms": ["adres", "zlecenia_osoby"],
            "join_hints": [],
            "skip_reason": "",
        },
    )
    db_path = tmp_path / "people.db"
    connection = sqlite3.connect(db_path)

    try:
        connection.execute(
            """
            CREATE TABLE zlecenia_osoby (
                id INTEGER PRIMARY KEY,
                imie TEXT,
                nazwisko TEXT,
                email TEXT,
                telefon TEXT,
                kod TEXT,
                miasto TEXT,
                dzial_id INTEGER,
                koncern_id INTEGER
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    def generate_bad_sql(self: MockSqlGenerator, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(
            sql=(
                "SELECT imie, nazwisko, email, telefon, adres, kod, miasto, "
                "dzial_id, koncern_id FROM zlecenia_osoby WHERE id = 42"
            ),
            confidence=0.6,
            assumptions=["Intentional missing column for repair learning test."],
        )

    monkeypatch.setattr(MockSqlGenerator, "generate", generate_bad_sql)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers=headers,
        json={
            "connector_key": "con_db",
            "name": "Connected DB",
            "database_type": "sqlite",
            "database_url": f"sqlite:///{db_path}",
            "sql_dialect": "sqlite",
            "active": True,
        },
    )

    assert create_response.status_code == 200
    connector_id = create_response.json()["item"]["id"]

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "Podaj dane osobowe pracownika nr 42",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 400

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers=headers,
    )
    audit_item = audit_response.json()["items"][0]

    assert audit_item["metadata"]["error_category"] == "schema.missing_column"
    assert audit_item["metadata"]["failed_identifier"] == "zlecenia_osoby.adres"
    assert audit_item["metadata"]["business_logic_learning"]["suggestion_id"]

    suggestions_response = admin_client.get(
        "/api/v1/admin/business-logic-suggestions",
        headers=headers,
    )

    assert suggestions_response.status_code == 200
    suggestions = suggestions_response.json()["items"]
    assert len(suggestions) == 1
    assert suggestions[0]["status"] == "pending"
    assert suggestions[0]["enabled"] is False
    assert suggestions[0]["error_category"] == "schema.missing_column"
    assert suggestions[0]["failed_identifier"] == "zlecenia_osoby.adres"
    assert suggestions[0]["repaired_identifier"] == ""
    assert "Do not select `adres` from `zlecenia_osoby`" in suggestions[0]["rule_text"]

    enable_response = admin_client.put(
        f"/api/v1/admin/business-logic-suggestions/{suggestions[0]['id']}",
        headers=headers,
        json={"enabled": True},
    )

    assert enable_response.status_code == 200
    assert "Do not select `adres`" in get_active_business_logic_prompt_safe(connector_id)


def test_join_missing_column_sql_error_is_learned_by_llm(
    admin_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(admin_client)
    learning_llm = stub_business_logic_learning_llm(
        monkeypatch,
        {
            "create_suggestion": True,
            "error_category": "schema.missing_column",
            "title": "Use client name column with aliases",
            "rule_text": (
                "When grouping orders by client name, join `zlecenia_klienci` with an alias "
                "such as `zk` and select `zk.nazwa AS klient_nazwa`; do not reference "
                "unqualified column `klient_nazwa` as if it existed in the schema."
            ),
            "failed_identifier": "klient_nazwa",
            "repaired_identifier": "zlecenia_klienci.nazwa",
            "confidence": 0.9,
            "terms": ["klient", "klient_nazwa", "zlecenia_klienci", "nazwa"],
            "join_hints": ["zlecenia_osoby.klient -> zlecenia_klienci.id"],
            "skip_reason": "",
        },
    )
    db_path = tmp_path / "clients.db"
    connection = sqlite3.connect(db_path)

    try:
        connection.execute(
            """
            CREATE TABLE zlecenia_osoby (
                id INTEGER PRIMARY KEY,
                klient INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE zlecenia_klienci (
                id INTEGER PRIMARY KEY,
                nazwa TEXT NOT NULL
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    bad_sql = (
        "SELECT klient_nazwa, COUNT(*) AS ilosc FROM zlecenia_osoby "
        "JOIN zlecenia_klienci ON zlecenia_osoby.klient = zlecenia_klienci.id "
        "GROUP BY klient_nazwa ORDER BY ilosc DESC LIMIT 10"
    )

    def generate_bad_sql(self: MockSqlGenerator, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(
            sql=bad_sql,
            confidence=0.6,
            assumptions=["Intentional missing unqualified join column."],
        )

    monkeypatch.setattr(MockSqlGenerator, "generate", generate_bad_sql)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers=headers,
        json={
            "connector_key": "con_db",
            "name": "Connected DB",
            "database_type": "sqlite",
            "database_url": f"sqlite:///{db_path}",
            "sql_dialect": "sqlite",
            "active": True,
        },
    )

    assert create_response.status_code == 200

    query_response = admin_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "Pokaż klientów z największą liczbą zleceń",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 400

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers=headers,
    )
    audit_item = audit_response.json()["items"][0]

    assert audit_item["metadata"]["business_logic_learning"]["status"] == "pending_approval"
    assert audit_item["metadata"]["business_logic_learning"]["suggestion_id"]
    assert len(learning_llm.requests) == 1
    learning_prompt = "\n".join(message.content for message in learning_llm.requests[0].messages)
    assert "Pokaż klientów z największą liczbą zleceń" in learning_prompt
    assert bad_sql in learning_prompt
    assert "klient_nazwa" in learning_prompt

    suggestions_response = admin_client.get(
        "/api/v1/admin/business-logic-suggestions",
        headers=headers,
    )

    suggestions = suggestions_response.json()["items"]
    assert len(suggestions) == 1
    assert suggestions[0]["error_category"] == "schema.missing_column"
    assert suggestions[0]["failed_identifier"] == "klient_nazwa"
    assert suggestions[0]["repaired_identifier"] == "zlecenia_klienci.nazwa"
    assert "zk.nazwa AS klient_nazwa" in suggestions[0]["rule_text"]


def test_admin_app_is_served(admin_client: TestClient) -> None:
    response = admin_client.get("/admin")

    assert response.status_code == 200
    assert "GAARD Admin Console" in response.text
