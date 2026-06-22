import json

import httpx
from fastapi.testclient import TestClient

from services.client.app.main import app


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
    assert "/api/query/stream" in response.text
    assert "/api/widgets/from-query" in response.text
    assert "data-save-widget" in response.text
    assert "data_question" in response.text


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


def test_client_app_proxies_query_mode(monkeypatch) -> None:
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            pass

        async def post(self, url, json):
            captured["json"] = json
            request = httpx.Request("POST", url)

            return httpx.Response(
                status_code=200,
                request=request,
                json={
                    "question": json["question"],
                    "answer": "Investigation answer.",
                    "sql": "",
                    "rows": [],
                    "metadata": {
                        "query_mode": "investigation",
                    },
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.post(
        "/api/query",
        json={
            "question": "gdzie uciekajo mi pinionżki",
            "backend_url": "http://backend.example/",
            "mode": "investigation",
        },
    )

    assert response.status_code == 200
    assert response.json()["metadata"]["query_mode"] == "investigation"
    assert captured["json"] == {
        "question": "gdzie uciekajo mi pinionżki",
        "user_id": "client",
        "mode": "investigation",
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


def test_client_app_streams_investigation_progress(monkeypatch) -> None:
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
                    "data_question": "What evidence is needed?",
                    "decisions": ["Planning governed evidence."],
                }
            ) + "\n"
            yield json.dumps(
                {
                    "final": {
                        "question": "q",
                        "answer": "done",
                        "sql": "",
                        "rows": [],
                        "metadata": {"query_mode": "investigation"},
                    }
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
        "/api/query/stream",
        json={
            "question": "q",
            "backend_url": "http://backend.example/",
            "mode": "investigation",
        },
    )

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[0] == {
        "data_question": "What evidence is needed?",
        "decisions": ["Planning governed evidence."],
    }
    assert set(lines[0]) == {"data_question", "decisions"}
    assert lines[1]["final"]["answer"] == "done"
    assert captured["method"] == "POST"
    assert captured["url"] == "http://backend.example/api/v1/query/stream"
    assert captured["json"] == {
        "question": "q",
        "user_id": "client",
        "mode": "investigation",
    }


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
