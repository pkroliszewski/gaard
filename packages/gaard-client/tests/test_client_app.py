import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from gaard_client.main import app, proxy_json_request


def test_client_app_serves_index_and_config(monkeypatch: Any) -> None:
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
    assert "/api/dashboards" in response.text
    assert "data-save-widget" in response.text
    assert "data-toggle-dashboard-menu" in response.text
    assert "Add new dashboard" in response.text
    assert "data-delete-dashboard" in response.text
    assert "analysis-progress" in response.text
    assert "analysis-log" in response.text
    assert "analysis-reply-question" in response.text
    assert "data-analysis-progress" in response.text
    assert "data-new-chat" in response.text
    assert "syncConversationFromResponse" in response.text
    assert "renderDatasourcesView" in response.text
    assert "Excel workbooks" in response.text
    assert "Connected feature" not in response.text
    assert "data-add-source" in response.text
    assert "openSourcePicker" in response.text
    assert "mergeDatasourcesPreservingOrder" in response.text
    assert "preserveOrder: true" in response.text
    assert "api-error-banner" in response.text
    assert "reportApiError" in response.text
    assert "formatApiResponseError" in response.text
    assert "/api/widgets/title-suggestion" in response.text
    assert "/api/query/explain" in response.text
    assert "data-explain-answer" in response.text
    assert "renderAnswerExplanation" in response.text
    assert "function renderMarkdown" in response.text
    assert "markdown-content" in response.text
    assert "data-save-widget-form" in response.text
    assert "Save metric" in response.text
    assert "data-edit-metric" in response.text
    assert "Edit metric name" in response.text
    assert "data-delete-metric" in response.text
    assert "Deleting this metric will also remove it from all dashboards." in response.text
    assert "metrics-groups" in response.text
    assert "Data Source:" in response.text
    assert "metric-title" in response.text
    assert "formatDefaultMetricDatasourceName" in response.text
    assert "(default)" in response.text
    assert "hasLongSeries" in response.text
    assert "longSeriesWidgetOptions" in response.text
    assert 'types.push("stacked_bar", "multi_line")' in response.text
    assert "dashboard-spinner" in response.text
    assert "data-edit-active-dashboard" in response.text
    assert "Edit dashboard" in response.text
    assert "Save changes" in response.text
    assert "include_result" in response.text
    assert "includeResult: false" in response.text
    assert "dashboardEditMode" in response.text
    assert "dashboardLayoutSaving" in response.text
    assert "dashboardLayoutSavePromise" in response.text
    assert "dashboardLayoutSaveSequence" in response.text
    assert "data-toggle-dashboard-edit" in response.text
    assert "Edit layout" in response.text
    assert "Finish editing" in response.text
    assert "Saving dashboard layout" in response.text
    assert "dashboard-edit-saving-spinner" in response.text
    assert "toggleDashboardEditMode" in response.text
    assert "setDashboardLayoutSaving" in response.text
    assert "flushPendingDashboardLayoutSave" in response.text
    assert "await state.dashboardLayoutSavePromise" in response.text
    assert "saveSequence !== state.dashboardLayoutSaveSequence" in response.text
    assert "updatedWidget.layout" in response.text
    assert "return updated;" not in response.text
    assert "columnOpts" in response.text
    assert "breakpointForWindow: false" in response.text
    assert "breakpoints" in response.text
    assert 'layout: "list"' in response.text
    assert 'layout: "moveScale"' in response.text
    assert "canPersistDashboardLayout" in response.text
    assert "getColumn?.()" in response.text
    assert '"dragstop resizestop"' in response.text
    assert '"change dragstop resizestop"' not in response.text
    assert "grid.enable?.()" in response.text
    assert "grid.disable?.()" in response.text
    assert "resizestop" in response.text
    assert "alwaysShowResizeHandle" in response.text
    assert "/api/datasources/excel" in response.text
    assert 'message.mode === "analysis" && getRows(payload.final).length > 0' in (response.text)
    assert "user_question" in response.text
    assert "Investigation" not in response.text
    assert "investigation" not in response.text


def test_client_dashboard_edit_mode_styles() -> None:
    client = TestClient(app)
    response = client.get("/assets/styles.css")

    assert response.status_code == 200
    assert "dashboard-edit-mode-button" in response.text
    assert "dashboard-edit-saving-spinner" in response.text
    assert "dashboard-grid-readonly" in response.text
    assert "ui-resizable-handle" in response.text


def test_client_dashboard_mobile_styles() -> None:
    client = TestClient(app)
    response = client.get("/assets/styles.css")

    assert response.status_code == 200
    assert "@media (max-width: 700px)" in response.text
    assert ".dashboard-grid" in response.text
    assert ".dashboard-user-widget" in response.text
    assert ".dashboard-widget-chart" in response.text


def test_proxy_json_request_returns_json_and_forwards_arguments(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}
    files = {"file": ("data.xlsx", b"content", "application/octet-stream")}

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(
            self,
            method: str,
            url: str,
            **kwargs: Any,
        ) -> httpx.Response:
            captured.update({"method": method, "url": url, **kwargs})
            return httpx.Response(
                status_code=200,
                request=httpx.Request(method, url),
                json={"status": "ok"},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        proxy_json_request(
            "POST",
            "http://backend.example/api/v1/upload",
            timeout=120.0,
            request_kwargs={
                "json": {"name": "report"},
                "headers": {"Authorization": "Bearer token"},
                "params": {"active": True},
                "files": files,
            },
        )
    )

    assert result == {"status": "ok"}
    assert captured == {
        "timeout": 120.0,
        "method": "POST",
        "url": "http://backend.example/api/v1/upload",
        "json": {"name": "report"},
        "headers": {"Authorization": "Bearer token"},
        "params": {"active": True},
        "files": files,
    }


def test_proxy_json_request_preserves_json_backend_error(monkeypatch: Any) -> None:
    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(
            self,
            method: str,
            url: str,
            **kwargs: Any,
        ) -> httpx.Response:
            return httpx.Response(
                status_code=403,
                headers={"content-type": "application/json"},
                request=httpx.Request(method, url),
                json={"detail": "Forbidden"},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            proxy_json_request(
                "GET",
                "http://backend.example/api/v1/protected",
                timeout=30.0,
            )
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"detail": "Forbidden"}


def test_proxy_json_request_preserves_text_backend_error(monkeypatch: Any) -> None:
    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(
            self,
            method: str,
            url: str,
            **kwargs: Any,
        ) -> httpx.Response:
            return httpx.Response(
                status_code=500,
                headers={"content-type": "text/plain"},
                request=httpx.Request(method, url),
                text="Internal error",
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            proxy_json_request(
                "GET",
                "http://backend.example/api/v1/failure",
                timeout=30.0,
            )
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal error"


def test_proxy_json_request_maps_transport_error_to_bad_gateway(
    monkeypatch: Any,
) -> None:
    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(
            self,
            method: str,
            url: str,
            **kwargs: Any,
        ) -> httpx.Response:
            raise httpx.ConnectError(
                "connection lost",
                request=httpx.Request(method, url),
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            proxy_json_request(
                "GET",
                "http://backend.example/api/v1/unavailable",
                timeout=30.0,
            )
        )

    assert exc_info.value.status_code == 502
    assert str(exc_info.value.detail).startswith("Backend request failed:")


def test_client_app_preserves_login_and_datasource_requests(monkeypatch: Any) -> None:
    captured: list[dict[str, Any]] = []

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(
            self,
            method: str,
            url: str,
            **kwargs: Any,
        ) -> httpx.Response:
            captured.append(
                {
                    "method": method,
                    "url": url,
                    "timeout": self.timeout,
                    **kwargs,
                }
            )
            return httpx.Response(
                status_code=200,
                request=httpx.Request(method, url),
                json={"status": "ok"},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)
    authorization = {"Authorization": "Bearer token"}

    login_response = client.post(
        "/api/auth/login",
        json={
            "username": "ada",
            "password": "secret-password",
            "backend_url": "http://backend.example/",
        },
    )
    datasources_response = client.get(
        "/api/datasources?backend_url=http://backend.example/",
        headers=authorization,
    )
    selection_response = client.put(
        "/api/datasources/selection",
        headers=authorization,
        json={
            "datasource_ids": ["primary", "archive"],
            "backend_url": "http://backend.example/",
        },
    )
    upload_response = client.post(
        "/api/datasources/excel?backend_url=http://backend.example/&active=false",
        headers=authorization,
        files={
            "file": (
                "report.xlsx",
                b"workbook-content",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert login_response.status_code == 200
    assert datasources_response.status_code == 200
    assert selection_response.status_code == 200
    assert upload_response.status_code == 200
    assert captured[0] == {
        "method": "POST",
        "url": "http://backend.example/api/v1/admin/auth/login",
        "timeout": 30.0,
        "json": {"username": "ada", "password": "secret-password"},
    }
    assert captured[1] == {
        "method": "GET",
        "url": "http://backend.example/api/v1/admin/datasources?available_only=true",
        "timeout": 30.0,
        "headers": authorization,
    }
    assert captured[2] == {
        "method": "POST",
        "url": "http://backend.example/api/v1/admin/datasources/selection",
        "timeout": 30.0,
        "json": {"datasource_ids": ["primary", "archive"]},
        "headers": authorization,
    }
    assert captured[3]["method"] == "POST"
    assert captured[3]["url"] == "http://backend.example/api/v1/admin/datasources/excel-upload"
    assert captured[3]["timeout"] == 120.0
    assert captured[3]["headers"] == authorization
    assert captured[3]["params"] == {"active": False}
    assert captured[3]["files"] == {
        "file": (
            "report.xlsx",
            b"workbook-content",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }


def test_client_app_proxies_query(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            json = kwargs["json"]
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            request = httpx.Request(method, url)

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
    assert captured["method"] == "POST"
    assert captured["url"] == "http://backend.example/api/v1/query"
    assert captured["json"] == {
        "question": "How many active patients are there?",
        "user_id": "client",
        "mode": "sql",
    }


def test_client_app_proxies_password_change(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            json = kwargs["json"]
            headers = kwargs["headers"]
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            request = httpx.Request(method, url)
            return httpx.Response(
                status_code=200,
                request=request,
                json={"username": "ada", "must_change_password": False, "role": "user"},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.post(
        "/api/auth/change-password",
        headers={"Authorization": "Bearer token"},
        json={
            "current_password": "temporary-password",
            "new_password": "new-user-password",
            "backend_url": "http://backend.example/",
        },
    )

    assert response.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"] == "http://backend.example/api/v1/admin/auth/change-password"
    assert captured["headers"] == {"Authorization": "Bearer token"}
    assert captured["json"] == {
        "current_password": "temporary-password",
        "new_password": "new-user-password",
    }


def test_client_app_proxies_current_user(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            headers = kwargs["headers"]
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            request = httpx.Request(method, url)
            return httpx.Response(
                status_code=200,
                request=request,
                json={"username": "ada", "must_change_password": True, "role": "user"},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.get(
        "/api/auth/me?backend_url=http://backend.example/",
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 200
    assert response.json()["must_change_password"] is True
    assert captured["method"] == "GET"
    assert captured["url"] == "http://backend.example/api/v1/admin/me"
    assert captured["headers"] == {"Authorization": "Bearer token"}


def test_client_app_proxies_conversation_history(monkeypatch: Any) -> None:
    captured: list[dict[str, Any]] = []

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            captured.append({"method": method, "url": url, "headers": kwargs["headers"]})
            request = httpx.Request(method, url)
            if url.endswith("/api/v1/conversations?limit=50"):
                return httpx.Response(
                    status_code=200,
                    request=request,
                    json={"items": [{"id": "conversation-1", "title": "Active patients"}]},
                )
            return httpx.Response(
                status_code=200,
                request=request,
                json={
                    "item": {"id": "conversation-1", "title": "Active patients"},
                    "turns": [{"id": "turn-1", "question": "How many active patients?"}],
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    list_response = client.get(
        "/api/conversations?backend_url=http://backend.example/",
        headers={"Authorization": "Bearer token"},
    )
    detail_response = client.get(
        "/api/conversations/conversation-1?backend_url=http://backend.example/",
        headers={"Authorization": "Bearer token"},
    )

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert list_response.json()["items"][0]["id"] == "conversation-1"
    assert detail_response.json()["turns"][0]["id"] == "turn-1"
    assert captured == [
        {
            "method": "GET",
            "url": "http://backend.example/api/v1/conversations?limit=50",
            "headers": {"Authorization": "Bearer token"},
        },
        {
            "method": "GET",
            "url": "http://backend.example/api/v1/conversations/conversation-1?limit=100",
            "headers": {"Authorization": "Bearer token"},
        },
    ]


def test_client_app_proxies_query_conversation_id(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            json = kwargs["json"]
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            request = httpx.Request(method, url)

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
    assert captured["method"] == "POST"
    assert captured["url"] == "http://backend.example/api/v1/query"
    assert captured["json"] == {
        "question": "and in May?",
        "user_id": "client",
        "mode": "sql",
        "conversation_id": "conversation-1",
    }


def test_client_app_proxies_query_explanation(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            json = kwargs["json"]
            headers = kwargs.get("headers")
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            request = httpx.Request(method, url)

            return httpx.Response(
                status_code=200,
                request=request,
                json={
                    "explanation": "The SQL counts completed appointments.",
                    "metadata": {"prompt_key": "answer_explanation"},
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.post(
        "/api/query/explain",
        headers={"Authorization": "Bearer token"},
        json={
            "question": "How many patients were admitted?",
            "sql": "SELECT COUNT(*) AS patient_count FROM appointments",
            "answer": "12 patients were admitted.",
            "rows": [{"patient_count": 12}],
            "metadata": {
                "datasource_id": "default",
                "sql_generation_prompt_metadata": {"prompt_key": "sql_generation"},
            },
            "backend_url": "http://backend.example/",
        },
    )

    assert response.status_code == 200
    assert captured["method"] == "POST"
    assert response.json()["metadata"]["prompt_key"] == "answer_explanation"
    assert captured["url"] == "http://backend.example/api/v1/query/explain"
    assert captured["headers"] == {"Authorization": "Bearer token"}
    assert captured["json"] == {
        "question": "How many patients were admitted?",
        "sql": "SELECT COUNT(*) AS patient_count FROM appointments",
        "answer": "12 patients were admitted.",
        "rows": [{"patient_count": 12}],
        "columns": [],
        "metadata": {
            "datasource_id": "default",
            "sql_generation_prompt_metadata": {"prompt_key": "sql_generation"},
        },
        "inference_metadata": {},
        "prompt_metadata": {},
        "business_logic": "",
        "datasource_id": "",
        "datasource_ids": [],
    }


def test_client_app_proxies_analysis_stream(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self) -> "FakeStreamResponse":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def aiter_text(self) -> AsyncIterator[str]:
            yield (
                json.dumps(
                    {
                        "event": "analysis_step",
                        "session_id": "abc",
                        "analysis_step": {
                            "iteration": 1,
                            "visible_question": "Do I have everything?",
                            "visible_reasoning": "Checking available context.",
                        },
                    }
                )
                + "\n"
            )
            yield (
                json.dumps(
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
                )
                + "\n"
            )

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        def stream(self, method: str, url: str, json: dict[str, Any]) -> FakeStreamResponse:
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


def test_client_app_proxies_widget_save_from_query(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            json = kwargs["json"]
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            request = httpx.Request(method, url)

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
    assert captured["method"] == "POST"
    assert captured["url"] == ("http://backend.example/api/v1/admin/overview/widgets/from-query")
    assert captured["json"] == {
        "label": "Active patients",
        "widget_type": "scalar",
        "datasource_key": "default",
        "question": "How many active patients?",
        "sql": "SELECT COUNT(*) AS value FROM patients",
        "rows": [],
        "result_mode": "data",
    }


def test_client_app_proxies_widget_title_suggestion(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            json = kwargs["json"]
            headers = kwargs.get("headers")
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            request = httpx.Request(method, url)

            return httpx.Response(
                status_code=200,
                request=request,
                json={"title": "Doctors by Specialty"},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.post(
        "/api/widgets/title-suggestion",
        headers={"Authorization": "Bearer token"},
        json={
            "question": "How many doctors are there by specialty?",
            "sql": "SELECT specialization, COUNT(*) FROM doctors GROUP BY specialization",
            "backend_url": "http://backend.example/",
        },
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Doctors by Specialty"
    assert captured["method"] == "POST"
    assert captured["url"] == (
        "http://backend.example/api/v1/admin/overview/widgets/title-suggestion"
    )
    assert captured["headers"] == {"Authorization": "Bearer token"}
    assert captured["json"] == {
        "question": "How many doctors are there by specialty?",
        "sql": "SELECT specialization, COUNT(*) FROM doctors GROUP BY specialization",
    }


def test_client_app_proxies_saved_metric_update(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            json = kwargs["json"]
            headers = kwargs.get("headers")
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            request = httpx.Request(method, url)

            return httpx.Response(
                status_code=200,
                request=request,
                json={"item": {"widget_key": "client_metric", "label": json["label"]}},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.patch(
        "/api/dashboard-metrics/client_metric",
        headers={"Authorization": "Bearer token"},
        json={
            "label": "New metric name",
            "backend_url": "http://backend.example/",
        },
    )

    assert response.status_code == 200
    assert response.json()["item"]["label"] == "New metric name"
    assert captured["method"] == "PATCH"
    assert captured["url"] == ("http://backend.example/api/v1/dashboards/metrics/client_metric")
    assert captured["headers"] == {"Authorization": "Bearer token"}
    assert captured["json"] == {"label": "New metric name"}


def test_client_app_proxies_saved_metric_delete(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            headers = kwargs.get("headers")
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            request = httpx.Request(method, url)

            return httpx.Response(
                status_code=200,
                request=request,
                json={
                    "status": "deleted",
                    "widget_key": "client_metric",
                    "removed_dashboard_widgets": 2,
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.delete(
        "/api/dashboard-metrics/client_metric?backend_url=http://backend.example/",
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 200
    assert captured["method"] == "DELETE"
    assert response.json() == {
        "status": "deleted",
        "widget_key": "client_metric",
        "removed_dashboard_widgets": 2,
    }
    assert captured["url"] == ("http://backend.example/api/v1/dashboards/metrics/client_metric")
    assert captured["headers"] == {"Authorization": "Bearer token"}


def test_client_app_proxies_dashboard_crud(monkeypatch: Any) -> None:
    captured: list[dict[str, object]] = []

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def request(
            self,
            method: str,
            url: str,
            **kwargs: Any,
        ) -> httpx.Response:
            return await getattr(self, method.lower())(url, **kwargs)

        async def get(self, url: str, headers: Any = None) -> httpx.Response:
            captured.append({"method": "GET", "url": url, "headers": headers})
            request = httpx.Request("GET", url)
            return httpx.Response(
                status_code=200,
                request=request,
                json={"items": [{"id": "dash-1", "name": "Operations"}]},
            )

        async def post(self, url: str, json: dict[str, Any], headers: Any = None) -> httpx.Response:
            captured.append({"method": "POST", "url": url, "json": json, "headers": headers})
            request = httpx.Request("POST", url)
            if url.endswith("/widgets"):
                return httpx.Response(
                    status_code=200,
                    request=request,
                    json={"item": {"id": "widget-1", "title": json["title"]}},
                )
            return httpx.Response(
                status_code=200,
                request=request,
                json={"item": {"id": "dash-2", "name": json["name"]}},
            )

        async def put(self, url: str, json: dict[str, Any], headers: Any = None) -> httpx.Response:
            captured.append({"method": "PUT", "url": url, "json": json, "headers": headers})
            request = httpx.Request("PUT", url)
            if not url.endswith("/active"):
                return httpx.Response(
                    status_code=200,
                    request=request,
                    json={"item": {"id": url.rsplit("/", 1)[-1], "name": json["name"]}},
                )
            return httpx.Response(
                status_code=200,
                request=request,
                json={
                    "active_dashboard_id": json["dashboard_id"],
                    "active_dashboard": {"id": json["dashboard_id"]},
                },
            )

        async def patch(
            self, url: str, json: dict[str, Any], headers: Any = None
        ) -> httpx.Response:
            captured.append({"method": "PATCH", "url": url, "json": json, "headers": headers})
            request = httpx.Request("PATCH", url)
            return httpx.Response(
                status_code=200,
                request=request,
                json={"items": json["items"]},
            )

        async def delete(self, url: str, headers: Any = None) -> httpx.Response:
            captured.append({"method": "DELETE", "url": url, "headers": headers})
            request = httpx.Request("DELETE", url)
            return httpx.Response(
                status_code=200,
                request=request,
                json={"status": "deleted", "id": "dash-2"},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)
    headers = {"Authorization": "Bearer token"}

    list_response = client.get(
        "/api/dashboards?backend_url=http://backend.example/",
        headers=headers,
    )
    create_response = client.post(
        "/api/dashboards",
        headers=headers,
        json={
            "name": "Operations",
            "description": "Daily view",
            "backend_url": "http://backend.example/",
        },
    )
    update_response = client.put(
        "/api/dashboards/dash-1",
        headers=headers,
        json={
            "name": "Operations updated",
            "description": "Updated daily view",
            "backend_url": "http://backend.example/",
        },
    )
    delete_response = client.delete(
        "/api/dashboards/dash-2?backend_url=http://backend.example/",
        headers=headers,
    )
    active_response = client.put(
        "/api/dashboards/active",
        headers=headers,
        json={
            "dashboard_id": "dash-1",
            "backend_url": "http://backend.example/",
        },
    )
    metrics_response = client.get(
        "/api/dashboard-metrics?backend_url=http://backend.example/&include_result=false",
        headers=headers,
    )
    widget_list_response = client.get(
        "/api/dashboards/dash-1/widgets?backend_url=http://backend.example/",
        headers=headers,
    )
    widget_create_response = client.post(
        "/api/dashboards/dash-1/widgets",
        headers=headers,
        json={
            "metric_widget_key": "client_metric",
            "title": "Metric",
            "visualization_type": "bar",
            "backend_url": "http://backend.example/",
        },
    )
    widget_layout_response = client.patch(
        "/api/dashboards/dash-1/widgets/layout",
        headers=headers,
        json={
            "items": [{"widget_id": "widget-1", "x": 1, "y": 2, "w": 6, "h": 4}],
            "backend_url": "http://backend.example/",
        },
    )
    widget_delete_response = client.delete(
        "/api/dashboards/dash-1/widgets/widget-1?backend_url=http://backend.example/",
        headers=headers,
    )

    assert list_response.status_code == 200
    assert create_response.status_code == 200
    assert update_response.status_code == 200
    assert delete_response.status_code == 200
    assert active_response.status_code == 200
    assert metrics_response.status_code == 200
    assert widget_list_response.status_code == 200
    assert widget_create_response.status_code == 200
    assert widget_layout_response.status_code == 200
    assert widget_delete_response.status_code == 200
    assert captured[0]["url"] == "http://backend.example/api/v1/dashboards"
    assert captured[1]["url"] == "http://backend.example/api/v1/dashboards"
    assert captured[1]["json"] == {
        "name": "Operations",
        "description": "Daily view",
    }
    assert captured[2]["url"] == "http://backend.example/api/v1/dashboards/dash-1"
    assert captured[2]["json"] == {
        "name": "Operations updated",
        "description": "Updated daily view",
    }
    assert captured[3]["url"] == "http://backend.example/api/v1/dashboards/dash-2"
    assert captured[4]["url"] == "http://backend.example/api/v1/dashboards/active"
    assert captured[4]["json"] == {"dashboard_id": "dash-1"}
    assert captured[5]["url"] == (
        "http://backend.example/api/v1/dashboards/metrics?include_result=false"
    )
    assert captured[6]["url"] == "http://backend.example/api/v1/dashboards/dash-1/widgets"
    assert captured[7]["url"] == "http://backend.example/api/v1/dashboards/dash-1/widgets"
    assert captured[7]["json"] == {
        "metric_widget_key": "client_metric",
        "title": "Metric",
        "visualization_type": "bar",
    }
    assert captured[8]["url"] == ("http://backend.example/api/v1/dashboards/dash-1/widgets/layout")
    assert captured[8]["json"] == {
        "items": [{"widget_id": "widget-1", "x": 1, "y": 2, "w": 6, "h": 4}]
    }
    assert captured[9]["url"] == (
        "http://backend.example/api/v1/dashboards/dash-1/widgets/widget-1"
    )
    assert all(call["headers"] == headers for call in captured)


def test_client_app_streams_analysis_user_question(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self) -> "FakeStreamResponse":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def aiter_text(self) -> AsyncIterator[str]:
            yield (
                json.dumps(
                    {
                        "event": "session_started",
                        "session_id": "session-1",
                        "session_started": {
                            "session_id": "session-1",
                            "status": "running",
                        },
                    }
                )
                + "\n"
            )
            yield (
                json.dumps(
                    {
                        "event": "user_question",
                        "session_id": "session-1",
                        "user_question": {"question": "Jaki zakres?"},
                    }
                )
                + "\n"
            )

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        def stream(self, method: str, url: str, json: dict[str, Any]) -> FakeStreamResponse:
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


def test_client_app_proxies_analysis_session_reply_stream(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self) -> "FakeStreamResponse":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        async def aiter_text(self) -> AsyncIterator[str]:
            yield (
                json.dumps(
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
                )
                + "\n"
            )

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            pass

        def stream(self, method: str, url: str, json: dict[str, Any]) -> FakeStreamResponse:
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
    assert captured["url"] == ("http://backend.example/api/v1/analysis/session-1/messages/stream")
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
