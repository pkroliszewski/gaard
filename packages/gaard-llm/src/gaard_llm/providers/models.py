from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float = 0.0
    extra_body: dict[str, Any] = Field(default_factory=dict)


class ChatCompletionResponse(BaseModel):
    content: str
    model: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
