from typing import Any

import httpx2 as httpx
import pytest
from gaard_core.errors import LlmProviderError

from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage


def test_openai_compatible_client_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, Any] = {}

    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        captured_kwargs.update(kwargs)
        request = httpx.Request(
            method="POST",
            url="https://example.com/v1/chat/completions",
        )

        return httpx.Response(
            status_code=200,
            request=request,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "content": "SELECT 1 AS value",
                        }
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAICompatibleClient(
        base_url="https://example.com/v1",
        api_key="test-key",
    )

    response = client.create_chat_completion(
        ChatCompletionRequest(
            model="test-model",
            messages=[
                ChatMessage(role="system", content="system"),
                ChatMessage(role="user", content="user"),
            ],
        )
    )

    assert response.content == "SELECT 1 AS value"
    assert response.model == "test-model"
    assert "chat_template_kwargs" not in captured_kwargs["json"]


def test_openai_compatible_client_merges_extra_body(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, Any] = {}

    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        captured_kwargs.update(kwargs)
        request = httpx.Request(
            method="POST",
            url="https://example.com/v1/chat/completions",
        )

        return httpx.Response(
            status_code=200,
            request=request,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "content": "SELECT 1 AS value",
                        }
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAICompatibleClient(
        base_url="https://example.com/v1",
        api_key="test-key",
    )

    client.create_chat_completion(
        ChatCompletionRequest(
            model="test-model",
            extra_body={
                "chat_template_kwargs": {
                    "enable_thinking": False,
                },
            },
            messages=[
                ChatMessage(role="system", content="system"),
                ChatMessage(role="user", content="user"),
            ],
        )
    )

    assert captured_kwargs["json"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_openai_compatible_client_wraps_invalid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        request = httpx.Request(
            method="POST",
            url="https://example.com/v1/chat/completions",
        )

        return httpx.Response(
            status_code=200,
            request=request,
            json={
                "unexpected": "format",
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAICompatibleClient(
        base_url="https://example.com/v1",
        api_key="test-key",
    )

    with pytest.raises(LlmProviderError):
        client.create_chat_completion(
            ChatCompletionRequest(
                model="test-model",
                messages=[
                    ChatMessage(role="system", content="system"),
                    ChatMessage(role="user", content="user"),
                ],
            )
        )


def test_openai_compatible_client_includes_provider_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        request = httpx.Request(
            method="POST",
            url="https://example.com/v1/chat/completions",
        )

        return httpx.Response(
            status_code=400,
            request=request,
            json={
                "error": {
                    "message": "Unrecognized request argument supplied.",
                    "type": "invalid_request_error",
                }
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAICompatibleClient(
        base_url="https://example.com/v1",
        api_key="test-key",
    )

    with pytest.raises(LlmProviderError) as exc_info:
        client.create_chat_completion(
            ChatCompletionRequest(
                model="test-model",
                messages=[
                    ChatMessage(role="system", content="system"),
                    ChatMessage(role="user", content="user"),
                ],
            )
        )

    assert "LLM provider returned HTTP 400." in str(exc_info.value)
    assert "Unrecognized request argument supplied." in str(exc_info.value)


def test_openai_compatible_client_lists_models(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(*args: Any, **kwargs: Any) -> httpx.Response:
        assert args[0] == "https://example.com/v1/models"
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"
        request = httpx.Request(method="GET", url=args[0])
        return httpx.Response(
            status_code=200,
            request=request,
            json={"data": [{"id": "model-b"}, {"id": "model-a"}, {"id": "model-a"}]},
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    assert OpenAICompatibleClient(
        base_url="https://example.com/v1", api_key="test-key"
    ).list_models() == ["model-a", "model-b"]
