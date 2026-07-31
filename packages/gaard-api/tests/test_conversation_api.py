from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from gaard_core.query_pipeline.models import (
    ConversationContextClassification,
    ConversationContextDecision,
)
from sqlalchemy import func, select

from gaard_api.admin.database import create_session, reset_metadata_store_for_tests
from gaard_api.admin.models import Conversation, ConversationTurn
from gaard_api.api.v1 import query as query_module
from gaard_api.core.settings import settings
from gaard_api.example_database import install_medical_poc_example_database
from gaard_api.main import app


@pytest.fixture()
def conversation_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
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


def auth_headers(client: TestClient) -> dict[str, str]:
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
    return {"Authorization": f"Bearer {token}"}


def test_query_creates_conversation_and_rewrites_follow_up(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)

    first = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={"question": "How many active patients are there?", "user_id": "alice"},
    )

    assert first.status_code == 200
    conversation_id = first.json()["metadata"]["conversation"]["id"]
    assert conversation_id

    second = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "a w maju?",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )

    assert second.status_code == 200
    second_payload = second.json()
    conversation = second_payload["metadata"]["conversation"]
    assert conversation["id"] == conversation_id
    assert conversation["context_decision"] == "follow_up"
    assert "active patients" in conversation["standalone_question"]
    assert second_payload["question"] == "a w maju?"

    with create_session() as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 2


def test_conversation_history_endpoints_return_user_threads(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)

    first = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={"question": "How many active patients are there?", "user_id": "alice"},
    )
    assert first.status_code == 200
    conversation_id = first.json()["metadata"]["conversation"]["id"]

    second = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "and how many appointments?",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200

    listed = conversation_client.get("/api/v1/conversations", headers=headers)

    assert listed.status_code == 200
    items = listed.json()["items"]
    assert items[0]["id"] == conversation_id
    assert items[0]["turn_count"] == 2
    assert items[0]["latest_question"] == "and how many appointments?"

    detail = conversation_client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["item"]["id"] == conversation_id
    assert [turn["question"] for turn in payload["turns"]] == [
        "How many active patients are there?",
        "and how many appointments?",
    ]
    assert payload["turns"][0]["answer"]
    assert payload["turns"][0]["metadata"]["output_classification"] == "neutral_data"


def test_query_context_mode_new_starts_new_conversation(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)
    first = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={"question": "How many patients are there?", "user_id": "alice"},
    )
    assert first.status_code == 200
    first_conversation_id = first.json()["metadata"]["conversation"]["id"]

    second = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "How many appointments are there?",
            "user_id": "alice",
            "conversation_id": first_conversation_id,
            "context_mode": "new",
        },
    )

    assert second.status_code == 200
    assert second.json()["metadata"]["conversation"]["id"] != first_conversation_id


def test_query_context_mode_off_keeps_stateless_behavior(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)

    response = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "How many patients are there?",
            "user_id": "alice",
            "context_mode": "off",
        },
    )

    assert response.status_code == 200
    assert "conversation" not in response.json()["metadata"]
    with create_session() as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 0


def test_query_ambiguous_context_returns_clarification_without_sql(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)
    first = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={"question": "How many patients are there?", "user_id": "alice"},
    )
    assert first.status_code == 200
    conversation_id = first.json()["metadata"]["conversation"]["id"]

    ambiguous = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "to",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )

    assert ambiguous.status_code == 200
    payload = ambiguous.json()
    assert payload["sql"] == ""
    assert payload["metadata"]["blocked_reason"] == "conversation.ambiguous_context"
    assert payload["metadata"]["conversation"]["context_decision"] == "ambiguous"


def test_query_rejects_foreign_conversation_id(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)
    with create_session() as session:
        session.add(
            Conversation(
                conversation_id="foreign-conversation",
                owner_user_id="999",
                owner_username="other",
                title="Foreign",
            )
        )
        session.commit()

    response = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "How many patients are there?",
            "user_id": "alice",
            "conversation_id": "foreign-conversation",
        },
    )

    assert response.status_code == 403


def test_query_open_only_follow_up_preserves_previous_topic(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)
    first = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "ile spraw było przetwarzanych w tym tygodniu",
            "user_id": "alice",
        },
    )
    assert first.status_code == 200
    conversation_id = first.json()["metadata"]["conversation"]["id"]

    second = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "pokaż tylko otwarte",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )

    assert second.status_code == 200
    conversation = second.json()["metadata"]["conversation"]
    assert conversation["context_decision"] == "follow_up"
    assert conversation["standalone_question"] == (
        "ile spraw było przetwarzanych w tym tygodniu, ogranicz do otwartych?"
    )
    assert conversation["context_reason"].startswith("Deterministic rewrite")


def test_query_previous_period_follow_up_uses_previous_time_scope(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)
    first = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "ile spraw było przetwarzanych w tym tygodniu",
            "user_id": "alice",
        },
    )
    assert first.status_code == 200
    conversation_id = first.json()["metadata"]["conversation"]["id"]

    second = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "a w poprzednim?",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )

    assert second.status_code == 200
    conversation = second.json()["metadata"]["conversation"]
    assert conversation["context_decision"] == "follow_up"
    assert conversation["standalone_question"] == (
        "ile spraw było przetwarzanych w poprzednim tygodniu"
    )


def test_query_projection_follow_up_preserves_previous_result_set(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)
    first = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )
    assert first.status_code == 200
    conversation_id = first.json()["metadata"]["conversation"]["id"]

    second = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "show their names",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )

    assert second.status_code == 200
    conversation = second.json()["metadata"]["conversation"]
    assert conversation["context_decision"] == "follow_up"
    assert "Zachowaj ten sam zestaw rekordów" in conversation["standalone_question"]
    assert "How many active patients are there" in conversation["standalone_question"]
    assert "show names" in conversation["standalone_question"]
    assert "Nie używaj bind-parametrów" in conversation["standalone_question"]
    assert conversation["context_reason"].startswith("Deterministic rewrite")


def test_query_singular_projection_follow_up_works_after_single_result(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)
    first = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "ile spraw było przetwarzanych w tym tygodniu, podaj tylko otwarte",
            "user_id": "alice",
        },
    )
    assert first.status_code == 200
    conversation_id = first.json()["metadata"]["conversation"]["id"]

    second = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "a w poprzednim?",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200

    third = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "podaj krótki opis tej sprawy",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )

    assert third.status_code == 200
    conversation = third.json()["metadata"]["conversation"]
    assert conversation["context_decision"] == "follow_up"
    assert "Zachowaj ten sam zestaw rekordów" in conversation["standalone_question"]
    assert "poprzednim tygodniu" in conversation["standalone_question"]
    assert "podaj krótki opis sprawy" in conversation["standalone_question"]


def test_query_previous_period_after_projection_uses_base_question(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)
    first = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "ile spraw było przetwarzanych w tym tygodniu",
            "user_id": "alice",
        },
    )
    assert first.status_code == 200
    conversation_id = first.json()["metadata"]["conversation"]["id"]

    second = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "podaj ich krótkie opisy i statusy",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200

    third = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "a w poprzednim?",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )

    assert third.status_code == 200
    conversation = third.json()["metadata"]["conversation"]
    standalone = conversation["standalone_question"]
    assert conversation["context_decision"] == "follow_up"
    assert "ile spraw było przetwarzanych w poprzednim tygodniu" in standalone
    assert "podaj krótkie opisy i statusy" in standalone
    assert "pytanie bazowe: Zachowaj" not in standalone


def test_query_follow_up_guard_rejects_unrewritten_classifier_output(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(conversation_client)
    first = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )
    assert first.status_code == 200
    conversation_id = first.json()["metadata"]["conversation"]["id"]

    class BadClassifier:
        def classify(
            self, request: Any, context: dict[str, Any]
        ) -> ConversationContextClassification:
            return ConversationContextClassification(
                decision=ConversationContextDecision.FOLLOW_UP,
                confidence=0.9,
                standalone_question=request.question,
                reason="Bad unexpanded follow-up.",
                model_response={"decision": "follow_up"},
            )

    monkeypatch.setattr(
        query_module,
        "create_conversation_context_classifier",
        lambda _llm_config=None: BadClassifier(),
    )

    response = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "zastosuj filtr",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sql"] == ""
    assert payload["metadata"]["conversation"]["context_decision"] == "ambiguous"


def test_query_accepts_llm_follow_up_when_current_question_is_standalone(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(conversation_client)
    first = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "ilu pacjentów było przyjętych tydzień temu",
            "user_id": "alice",
        },
    )
    assert first.status_code == 200
    conversation_id = first.json()["metadata"]["conversation"]["id"]

    second = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "a dwa tygodnie temu?",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200

    class StandaloneContinuationClassifier:
        def classify(
            self, request: Any, context: dict[str, Any]
        ) -> ConversationContextClassification:
            return ConversationContextClassification(
                decision=ConversationContextDecision.FOLLOW_UP,
                confidence=0.92,
                standalone_question=request.question,
                reason="LLM classified this as a logical continuation with a standalone question.",
                model_response={
                    "is_continuation": True,
                    "current_question_is_standalone": True,
                },
                prompt={
                    "system_prompt": "Decide whether turn t is a logical continuation.",
                    "user_prompt": "turn_t_minus_1 + turn_t",
                    "metadata": {"decision_task": "logical_continuation_yes_no"},
                },
                source="llm",
            )

    monkeypatch.setattr(
        query_module,
        "create_conversation_context_classifier",
        lambda _llm_config=None: StandaloneContinuationClassifier(),
    )

    third = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "ilu pacjentów przyjęto w tym tygodniu",
            "user_id": "alice",
            "conversation_id": conversation_id,
        },
    )

    assert third.status_code == 200
    payload = third.json()
    conversation = payload["metadata"]["conversation"]
    assert payload["sql"]
    assert payload["metadata"].get("blocked") is not True
    assert "Potrzebuję doprecyzowania" not in payload["answer"]
    assert conversation["context_decision"] == "follow_up"
    assert conversation["standalone_question"] == "ilu pacjentów przyjęto w tym tygodniu"
    assert conversation["context_source"] == "llm"
    assert conversation["context_model_response"]["is_continuation"] is True
    assert conversation["context_prompt"]["metadata"]["decision_task"] == (
        "logical_continuation_yes_no"
    )


def test_query_turn_metadata_keeps_context_reason_and_working_context(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)
    response = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "ile spraw było przetwarzanych w tym tygodniu",
            "user_id": "alice",
        },
    )
    assert response.status_code == 200

    with create_session() as session:
        turn = session.scalar(select(ConversationTurn))

    assert turn is not None
    metadata = query_module.json.loads(turn.metadata_json)
    assert metadata["context_reason"]
    assert metadata["context_model_response"] == {}
    assert metadata["working_context"]["time_scope"] == "current_week"


def test_query_turn_metadata_keeps_result_summary(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)
    response = conversation_client.post(
        "/api/v1/query",
        headers=headers,
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )
    assert response.status_code == 200

    with create_session() as session:
        turn = session.scalar(select(ConversationTurn))

    assert turn is not None
    metadata = query_module.json.loads(turn.metadata_json)
    assert metadata["result_summary"]["row_count"] == 1
    assert metadata["result_summary"]["columns"] == ["active_patients_count"]
    assert isinstance(metadata["result_summary"]["scalar_count"], int)
