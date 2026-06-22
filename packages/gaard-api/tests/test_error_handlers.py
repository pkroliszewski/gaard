from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gaard_api.admin.database import create_session, reset_metadata_store_for_tests
from gaard_api.admin.services import set_setting
from gaard_api.core.settings import settings
from gaard_api.main import app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    reset_metadata_store_for_tests()

    with TestClient(app) as test_client:
        yield test_client

    reset_metadata_store_for_tests()


def test_query_returns_configuration_error_for_missing_llm_key(client: TestClient) -> None:
    with create_session() as session:
        set_setting(session, "gaard_sql_generation_mode", "llm", "test")
        set_setting(session, "gaard_llm_api_key", "change-me", "test")
        session.commit()

    response = client.post(
        "/api/v1/query",
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
    with create_session() as session:
        set_setting(session, "gaard_sql_generation_mode", "mock", "test")
        set_setting(session, "gaard_result_interpretation_mode", "llm", "test")
        set_setting(session, "gaard_llm_api_key", "change-me", "test")
        session.commit()

    response = client.post(
        "/api/v1/query",
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
