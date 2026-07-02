from typing import Any

import httpx2 as httpx

from gaard_core.errors import LlmProviderError
from gaard_llm.providers.models import ChatCompletionRequest, ChatCompletionResponse


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: int = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def create_chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        url = f"{self.base_url}/chat/completions"

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in request.messages
            ],
            "temperature": request.temperature,
        }

        payload.update(request.extra_body)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = httpx.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            detail_suffix = f" {detail[:500]}" if detail else ""

            raise LlmProviderError(
                f"LLM provider returned HTTP {exc.response.status_code}.{detail_suffix}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LlmProviderError("LLM provider request failed.") from exc

        data: dict[str, Any] = response.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmProviderError("Invalid OpenAI-compatible response format.") from exc

        return ChatCompletionResponse(
            content=content.strip(),
            model=data.get("model"),
            raw=data,
        )
