from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gaard_api.admin.database import create_session, reset_metadata_store_for_tests
from gaard_api.admin.models import DatasourceConnector
from gaard_api.admin.services import set_setting
from gaard_api.core.settings import settings
from gaard_api.example_database import install_medical_poc_example_database
from gaard_api.main import app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    demo_db = tmp_path / "demo.db"

    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    monkeypatch.setattr(settings, "gaard_datasource_url", f"sqlite:///{demo_db}")
    reset_metadata_store_for_tests()
    install_medical_poc_example_database(demo_db)

    with create_session() as session:
        connector = session.scalar(
            select(DatasourceConnector).where(DatasourceConnector.connector_key == "default")
        )
        if connector is None:
            connector = DatasourceConnector(
                connector_key="default",
                name="Default DB",
                database_type="sqlite",
                database_url=settings.gaard_datasource_url,
                sql_dialect="sqlite",
                active=True,
            )
            session.add(connector)
        connector.database_url = settings.gaard_datasource_url
        connector.active = True
        session.commit()

    with TestClient(app) as test_client:
        yield test_client

    reset_metadata_store_for_tests()


def login(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/admin/auth/login",
        json={
            "username": "admin",
            "password": "admin",
        },
    )

    assert response.status_code == 200
    return response.json()


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


def auth_headers(client: TestClient) -> dict[str, str]:
    token = login(client)["token"]
    change_password(client, token)
    return {"Authorization": f"Bearer {token}"}


def test_query_returns_configuration_error_for_missing_llm_key(client: TestClient) -> None:
    headers = auth_headers(client)

    with create_session() as session:
        set_setting(session, "gaard_sql_generation_mode", "llm", "test")
        set_setting(session, "gaard_llm_api_key", "change-me", "test")
        session.commit()

    response = client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "Ilu jest pacjentów?",
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "CONFIGURATION_ERROR",
            "message": "GAARD_LLM_API_KEY must be configured when using LLM mode.",
        }
    }


def test_query_returns_configuration_error_for_missing_llm_key_in_interpreter(
    client: TestClient,
) -> None:
    headers = auth_headers(client)

    with create_session() as session:
        set_setting(session, "gaard_sql_generation_mode", "mock", "test")
        set_setting(session, "gaard_result_interpretation_mode", "llm", "test")
        set_setting(session, "gaard_llm_api_key", "change-me", "test")
        session.commit()

    response = client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "Ilu jest pacjentów?",
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "CONFIGURATION_ERROR",
            "message": "GAARD_LLM_API_KEY must be configured when using LLM mode.",
        }
    }
