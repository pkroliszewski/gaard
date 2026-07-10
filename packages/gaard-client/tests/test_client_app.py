import json

import httpx2 as httpx
from fastapi.testclient import TestClient

from gaard_client.main import app


def test_client_app_serves_index_and_config(monkeypatch) -> None:
    monkeypatch.setenv("GAARD_CLIENT_BACKEND_URL", "http://backend.example")
    client = TestClient(app)

    index_response = client.get("/")
    assert index_response.status_code == 200
    assert "GAARD Client" in index_response.text

    config_response = client.get("/config.js")
    assert config_response.status_code == 200
    assert "http://backend.example" in config_response.text


def test_client_app_warns_when_response_uses_mock_modes() -> None:
    client = TestClient(app)

    response = client.get("/assets/main.js")

    assert response.status_code == 200
    assert "This response used mock data processing" in response.text
    assert "sql_generation_mode" in response.text
    assert "result_interpretation_mode" in response.text
    assert "output_classification_mode" in response.text
    assert "/api/analysis/stream" in response.text
    assert "/api/widgets/from-query" in response.text
    assert "data-save-widget" in response.text
    assert "analysis-progress" in response.text
    assert "analysis-log" in response.text
    assert "analysis-reply-question" in response.text
    assert "data-analysis-progress" in response.text
    assert "data-new-chat" in response.text
    assert "syncConversationFromResponse" in response.text
    assert 'message.mode === "analysis" && getRows(payload.final).length > 0' in (
        response.text
    )
    assert "user_question" in response.text
    assert "Investigation" not in response.text
    assert "investigation" not in response.text


def test_client_app_proxies_query(monkeypatch) -> None:
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            pass

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            request = httpx.Request("POST", url)

            return httpx.Response(
                status_code=200,
                request=request,
                json={
                    "question": json["question"],
                    "answer": "There are 4 active patients.",
                    "sql": "SELECT COUNT(*) AS active_patients FROM patients",
                    "rows": [{"active_patients": 4}],
                    "metadata": {
                        "duration_ms": 12.5,
                        "datasource_id": "default",
                        "output_classification": "neutral_data",
                    },
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.post(
        "/api/query",
        json={
            "question": "How many active patients are there?",
            "backend_url": "http://backend.example/",
        },
    )

    assert response.status_code == 200
    assert response.json()["metadata"]["output_classification"] == "neutral_data"
    assert captured["url"] == "http://backend.example/api/v1/query"
    assert captured["json"] == {
        "question": "How many active patients are there?",
        "user_id": "client",
        "mode": "sql",
    }


def test_client_app_proxies_query_conversation_id(monkeypatch) -> None:
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            pass

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            request = httpx.Request("POST", url)

            return httpx.Response(
                status_code=200,
                request=request,
                json={
                    "question": json["question"],
                    "answer": "ok",
                    "sql": "SELECT 1",
                    "rows": [{"value": 1}],
                    "metadata": {
                        "conversation": {"id": json["conversation_id"]},
                        "output_classification": "neutral_data",
                    },
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.post(
        "/api/query",
        json={
            "question": "and in May?",
            "conversation_id": "conversation-1",
            "backend_url": "http://backend.example/",
        },
    )

    assert response.status_code == 200
    assert captured["url"] == "http://backend.example/api/v1/query"
    assert captured["json"] == {
        "question": "and in May?",
        "user_id": "client",
        "mode": "sql",
        "conversation_id": "conversation-1",
    }


def test_client_app_proxies_analysis_stream(monkeypatch) -> None:
    captured = {}

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            pass

        async def aiter_text(self):
            yield json.dumps(
                {
                    "event": "analysis_step",
                    "session_id": "abc",
                    "analysis_step": {
                        "iteration": 1,
                        "visible_question": "Do I have everything?",
                        "visible_reasoning": "Checking available context.",
                    },
                }
            ) + "\n"
            yield json.dumps(
                {
                    "event": "final",
                    "session_id": "abc",
                    "final": {
                        "question": "q",
                        "answer": "Analysis answer.",
                        "sql": "",
                        "rows": [],
                        "metadata": {
                            "analysis_mode": "analysis",
                            "analysis_session_id": "abc",
                        },
                    },
                }
            ) + "\n"

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            pass

        def stream(self, method, url, json):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return FakeStreamResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.post(
        "/api/analysis/stream",
        json={
            "question": "where is my spend going",
            "backend_url": "http://backend.example/",
        },
    )

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[0]["event"] == "analysis_step"
    assert lines[1]["final"]["metadata"]["analysis_mode"] == "analysis"
    assert captured["method"] == "POST"
    assert captured["url"] == "http://backend.example/api/v1/analysis/stream"
    assert captured["json"] == {
        "question": "where is my spend going",
        "user_id": "client",
    }


def test_client_app_proxies_widget_save_from_query(monkeypatch) -> None:
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            pass

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            request = httpx.Request("POST", url)

            return httpx.Response(
                status_code=200,
                request=request,
                json={
                    "item": {
                        "widget_key": "client_active_patients",
                        "label": json["label"],
                        "active": False,
                    },
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.post(
        "/api/widgets/from-query",
        json={
            "label": "Active patients",
            "widget_type": "scalar",
            "datasource_key": "default",
            "question": "How many active patients?",
            "sql": "SELECT COUNT(*) AS value FROM patients",
            "backend_url": "http://backend.example/",
        },
    )

    assert response.status_code == 200
    assert response.json()["item"]["active"] is False
    assert captured["url"] == (
        "http://backend.example/api/v1/admin/overview/widgets/from-query"
    )
    assert captured["json"] == {
        "label": "Active patients",
        "widget_type": "scalar",
        "datasource_key": "default",
        "question": "How many active patients?",
        "sql": "SELECT COUNT(*) AS value FROM patients",
        "result_mode": "data",
    }


def test_client_app_streams_analysis_user_question(monkeypatch) -> None:
    captured = {}

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            pass

        async def aiter_text(self):
            yield json.dumps(
                {
                    "event": "session_started",
                    "session_id": "session-1",
                    "session_started": {
                        "session_id": "session-1",
                        "status": "running",
                    },
                }
            ) + "\n"
            yield json.dumps(
                {
                    "event": "user_question",
                    "session_id": "session-1",
                    "user_question": {"question": "Jaki zakres?"},
                }
            ) + "\n"

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            pass

        def stream(self, method, url, json):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return FakeStreamResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.post(
        "/api/analysis/stream",
        json={
            "question": "q",
            "backend_url": "http://backend.example/",
        },
    )

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[0]["event"] == "session_started"
    assert lines[1]["event"] == "user_question"
    assert lines[1]["user_question"]["question"] == "Jaki zakres?"
    assert captured["method"] == "POST"
    assert captured["url"] == "http://backend.example/api/v1/analysis/stream"
    assert captured["json"] == {
        "question": "q",
        "user_id": "client",
    }


def test_client_app_proxies_analysis_session_reply_stream(monkeypatch) -> None:
    captured = {}

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            pass

        async def aiter_text(self):
            yield json.dumps(
                {
                    "event": "final",
                    "session_id": "session-1",
                    "final": {
                        "question": "q",
                        "answer": "done",
                        "sql": "SELECT 1",
                        "rows": [{"value": 1}],
                        "metadata": {"analysis_session_id": "session-1"},
                    },
                }
            ) + "\n"

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            pass

        def stream(self, method, url, json):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return FakeStreamResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.post(
        "/api/analysis/session-1/messages/stream",
        json={
            "message": "last month",
            "backend_url": "http://backend.example/",
        },
    )

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[0]["final"]["answer"] == "done"
    assert captured["method"] == "POST"
    assert captured["url"] == (
        "http://backend.example/api/v1/analysis/session-1/messages/stream"
    )
    assert captured["json"] == {"message": "last month"}


def test_client_app_rejects_invalid_backend_url() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/query",
        json={
            "question": "How many active patients are there?",
            "backend_url": "localhost:8000",
        },
    )

    assert response.status_code == 400
