import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from gaard_core.conversation_context.llm_classifier import LlmConversationContextClassifier
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatCompletionResponse
from sqlalchemy import func, select

from gaard_api.admin.database import create_session, reset_metadata_store_for_tests
from gaard_api.admin.models import Conversation, ConversationTurn
from gaard_api.api.v1 import analysis as analysis_module
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


class FakeContextLlm:
    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)
        self.requests: list[ChatCompletionRequest] = []

    def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        self.requests.append(request)
        return ChatCompletionResponse(content=next(self.responses))


def context_llm(monkeypatch: pytest.MonkeyPatch, *responses: str) -> FakeContextLlm:
    client = FakeContextLlm(*responses)
    classifier = LlmConversationContextClassifier(cast(OpenAICompatibleClient, client), "test")
    monkeypatch.setattr(query_module, "create_conversation_context_classifier", lambda: classifier)
    return client


def ask(
    client: TestClient,
    headers: dict[str, str],
    question: str,
    conversation_id: str = "",
    endpoint: str = "/api/v1/query",
) -> dict[str, Any]:
    response = client.post(
        endpoint,
        headers=headers,
        json={
            "question": question,
            "conversation_id": conversation_id or None,
        },
    )
    assert response.status_code == 200, response.text
    if endpoint.endswith("/stream"):
        events = [json.loads(line) for line in response.text.splitlines()]
        return next(event["final"] for event in reversed(events) if event.get("final"))
    return response.json()


@pytest.mark.parametrize(
    "endpoint", ["/api/v1/query", "/api/v1/query/stream", "/api/v1/analysis/stream"]
)
def test_follow_up_snapshot_is_current_summary_used_for_execution(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    headers = auth_headers(conversation_client)
    llm = context_llm(monkeypatch, '{"decision":"follow_up"}', "How many active patients in May?")
    executed_questions: list[str] = []
    run_sql = query_module.run_sql_request

    def capture_request(request: Any, *args: Any, **kwargs: Any) -> Any:
        executed_questions.append(request.question)
        return run_sql(request, *args, **kwargs)

    monkeypatch.setattr(query_module, "run_sql_request", capture_request)
    monkeypatch.setattr(analysis_module, "run_sql_request", capture_request)
    first = ask(
        conversation_client, headers, "How many active patients are there?", endpoint=endpoint
    )
    cid = first["metadata"]["conversation"]["id"]
    second = ask(conversation_client, headers, "And in May?", cid, endpoint=endpoint)
    meta = second["metadata"]["conversation"]
    assert meta["context_decision"] == "follow_up"
    assert meta["standalone_question"] == "How many active patients in May?"
    assert executed_questions[-1] == meta["standalone_question"]
    assert second["question"] == "And in May?"
    assert len(llm.requests) == 2
    detail = conversation_client.get(f"/api/v1/conversations/{cid}", headers=headers).json()
    assert len(detail["turns"]) == 2
    assert detail["item"]["turn_count"] == 2
    turn = detail["turns"][-1]
    assert turn["standalone_question"] == meta["standalone_question"]
    assert turn["status"] == "completed"
    if endpoint != "/api/v1/analysis/stream":
        assert turn["sql"]
    context_url = f"/api/v1/conversations/{cid}/turns/{meta['turn_id']}/context"
    snapshot = conversation_client.get(context_url, headers=headers)
    assert snapshot.status_code == 200
    assert snapshot.json()["context"] == "How many active patients in May?"
    assert len(llm.requests) == 2  # Viewing never regenerates the context.


def test_all_turns_since_new_topic_are_used_and_old_snapshot_never_changes(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(conversation_client)
    responses = [
        item
        for i in range(7)
        for item in ('{"decision":"follow_up"}', f"How many active patients for period {i}?")
    ] + ['{"decision":"new_topic"}', '{"decision":"follow_up"}', "How many appointments tomorrow?"]
    llm = context_llm(monkeypatch, *responses)
    first = ask(conversation_client, headers, "How many active patients are there?")
    cid = first["metadata"]["conversation"]["id"]
    first_snapshot = None
    for i in range(7):
        result = ask(conversation_client, headers, f"And period {i}?", cid)
        first_snapshot = first_snapshot or result["metadata"]["conversation"]
    before_boundary = llm.requests[-1].messages[1].content
    assert "How many active patients are there?" in before_boundary
    for i in range(7):
        assert f"And period {i}?" in before_boundary
    ask(conversation_client, headers, "How many appointments today?", cid)
    ask(conversation_client, headers, "And tomorrow?", cid)
    after_boundary = llm.requests[-1].messages[1].content
    assert "How many appointments today?" in after_boundary
    assert "And tomorrow?" in after_boundary
    assert "active patients" not in after_boundary
    assert "period" not in after_boundary
    assert first_snapshot is not None
    snapshot = conversation_client.get(
        f"/api/v1/conversations/{cid}/turns/{first_snapshot['turn_id']}/context", headers=headers
    ).json()
    assert snapshot["context"] == "How many active patients for period 0?"


def test_follow_up_summary_can_equal_current_question_without_extra_flag(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(conversation_client)
    context_llm(monkeypatch, '{"decision":"follow_up"}', "How many patients this week?")
    first = ask(conversation_client, headers, "How many patients last week?")
    second = ask(
        conversation_client,
        headers,
        "How many patients this week?",
        first["metadata"]["conversation"]["id"],
    )
    assert second["metadata"]["conversation"]["context_decision"] == "follow_up"
    assert second["sql"]


def test_new_topic_decision_and_follow_up_snapshot_survive_execution_error(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(conversation_client)
    context_llm(monkeypatch, '{"decision":"follow_up"}', "How many patients in May?")
    first = ask(conversation_client, headers, "How many patients are there?")
    cid = first["metadata"]["conversation"]["id"]

    def fail(*args: Any, **kwargs: Any) -> Any:
        with create_session() as session:
            turns = list(session.scalars(select(ConversationTurn).order_by(ConversationTurn.id)))
        assert turns[-1].context_decision == "follow_up"
        assert turns[-1].standalone_question == "How many patients in May?"
        assert turns[-1].status == "running"
        raise RuntimeError("Database unavailable")

    monkeypatch.setattr(query_module, "run_sql_request", fail)
    response = conversation_client.post(
        "/api/v1/query/stream",
        headers=headers,
        json={
            "question": "And in May?",
            "conversation_id": cid,
        },
    )
    assert "Database unavailable" in response.text
    with create_session() as session:
        turn = session.scalars(
            select(ConversationTurn).order_by(ConversationTurn.id.desc())
        ).first()
        assert turn is not None
        assert turn.status == "failed"
        assert turn.context_decision == "follow_up"
        assert turn.standalone_question == "How many patients in May?"


def test_summary_failure_keeps_intent_but_does_not_claim_old_context_was_used(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(conversation_client)
    context_llm(monkeypatch, '{"decision":"follow_up"}', "")
    first = ask(conversation_client, headers, "How many patients are there?")
    cid = first["metadata"]["conversation"]["id"]
    response = conversation_client.post(
        "/api/v1/query/stream",
        headers=headers,
        json={
            "question": "And in May?",
            "conversation_id": cid,
        },
    )
    assert "empty response" in response.text
    with create_session() as session:
        turn = session.scalars(
            select(ConversationTurn).order_by(ConversationTurn.id.desc())
        ).first()
        assert turn is not None
        assert turn.status == "failed"
        assert turn.context_decision == "follow_up"
        turn_id = turn.turn_id
    assert (
        conversation_client.get(
            f"/api/v1/conversations/{cid}/turns/{turn_id}/context", headers=headers
        ).status_code
        == 409
    )


def test_analysis_reply_can_start_new_context_without_old_analysis_state(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = auth_headers(conversation_client)
    llm = context_llm(monkeypatch, '{"decision":"new_topic"}')
    initial = conversation_client.post(
        "/api/v1/analysis/stream", headers=headers, json={"question": "dopytaj o zakres"}
    )
    events = [json.loads(line) for line in initial.text.splitlines()]
    session_id = events[0]["session_id"]
    cid = events[0]["session_started"]["conversation_id"]
    assert events[-1]["event"] == "user_question"
    response = conversation_client.post(
        f"/api/v1/analysis/{session_id}/messages/stream",
        headers=headers,
        json={"message": "How many active patients are there?"},
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[0]["event"] == "session_started"
    assert events[0]["session_id"] != session_id
    assert events[0]["session_started"]["conversation_id"] == cid
    final = events[-1]["final"]
    assert final["metadata"]["conversation"]["context_decision"] == "new_topic"
    assert (
        final["metadata"]["conversation"]["standalone_question"]
        == "How many active patients are there?"
    )
    assert len(llm.requests) == 1
    session = conversation_client.get(
        f"/api/v1/analysis/{events[0]['session_id']}", headers=headers
    ).json()["item"]
    assert len(session["context"]["messages"]) == 1


def test_context_endpoint_checks_owner_and_turn_membership(
    conversation_client: TestClient,
) -> None:
    headers = auth_headers(conversation_client)
    first = ask(conversation_client, headers, "How many patients?")
    meta = first["metadata"]["conversation"]
    with create_session() as session:
        session.add(Conversation(conversation_id="foreign", owner_user_id="999", title="Private"))
        session.add(
            ConversationTurn(
                conversation_id="foreign",
                turn_id="secret",
                original_question="Secret?",
                standalone_question="Secret context",
                context_decision="new_topic",
            )
        )
        session.commit()
    prefix = "/api/v1/conversations"
    assert (
        conversation_client.get(
            f"{prefix}/foreign/turns/secret/context", headers=headers
        ).status_code
        == 403
    )
    assert (
        conversation_client.get(
            f"{prefix}/{meta['id']}/turns/secret/context", headers=headers
        ).status_code
        == 404
    )
    assert (
        conversation_client.get(
            f"{prefix}/{meta['id']}/turns/{meta['turn_id']}/context"
        ).status_code
        == 401
    )
