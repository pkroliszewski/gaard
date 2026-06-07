import json
from typing import Any, Protocol

from gaard_core.llm_output import remove_thinking_blocks
from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_core.prompt_compiler.result_classification_prompt import (
    ResultClassificationPromptCompiler,
)
from gaard_core.query_pipeline.models import OutputClassification, QueryRequest
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage


class ClassificationPromptCompiler(Protocol):
    def compile(
        self,
        request: QueryRequest,
        answer: str,
    ) -> CompiledPrompt:
        pass


class LlmResultClassifier:
    def __init__(
        self,
        client: OpenAICompatibleClient,
        model: str,
        extra_body: dict[str, Any] | None = None,
        prompt_compiler: ClassificationPromptCompiler | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.extra_body = extra_body or {}
        self.prompt_compiler = prompt_compiler or ResultClassificationPromptCompiler()

    def classify(
        self,
        request: QueryRequest,
        answer: str,
    ) -> OutputClassification:
        compiled_prompt = self.prompt_compiler.compile(
            request=request,
            answer=answer,
        )

        response = self.client.create_chat_completion(
            ChatCompletionRequest(
                model=self.model,
                temperature=0.0,
                extra_body=self.extra_body,
                messages=[
                    ChatMessage(
                        role="system",
                        content=compiled_prompt.system_prompt,
                    ),
                    ChatMessage(
                        role="user",
                        content=compiled_prompt.user_prompt,
                    ),
                ],
            )
        )

        return parse_output_classification(response.content)


def parse_output_classification(value: str) -> OutputClassification:
    cleaned = remove_thinking_blocks(value).strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        payload = cleaned

    if isinstance(payload, dict):
        payload = payload.get("output_classification") or payload.get("classification")

    if not isinstance(payload, str):
        return OutputClassification.UNKNOWN

    normalized = payload.strip().lower().replace(" ", "_").replace("-", "_")

    for item in OutputClassification:
        if normalized == item.value:
            return item

    return OutputClassification.UNKNOWN
