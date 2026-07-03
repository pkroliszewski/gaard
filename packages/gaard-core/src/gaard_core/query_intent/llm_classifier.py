import json
from typing import Any, Protocol, cast

from gaard_core.llm_output import remove_thinking_blocks
from gaard_core.prompt_compiler.intent_classification_prompt import (
    IntentClassificationPromptCompiler,
)
from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_core.query_pipeline.models import (
    QueryIntentClassification,
    QueryIntentDecision,
    QueryRequest,
)
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage


class IntentPromptCompiler(Protocol):
    def compile(self, request: QueryRequest) -> CompiledPrompt:
        pass


class LlmQueryIntentClassifier:
    def __init__(
        self,
        client: OpenAICompatibleClient,
        model: str,
        extra_body: dict[str, Any] | None = None,
        prompt_compiler: IntentPromptCompiler | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.extra_body = extra_body or {}
        self.prompt_compiler = prompt_compiler or IntentClassificationPromptCompiler()

    def classify(self, request: QueryRequest) -> QueryIntentClassification:
        compiled_prompt = self.prompt_compiler.compile(request=request)

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

        return parse_query_intent_classification(response.content)


def parse_query_intent_classification(value: str) -> QueryIntentClassification:
    cleaned = remove_thinking_blocks(value).strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        payload = {"decision": cleaned}

    if not isinstance(payload, dict):
        return QueryIntentClassification()

    return QueryIntentClassification(
        decision=parse_query_intent_decision(payload.get("decision")),
        confidence=parse_confidence(payload.get("confidence")),
        reason=str(payload.get("reason") or ""),
        model_response=payload,
    )


def parse_query_intent_decision(value: object) -> QueryIntentDecision:
    if not isinstance(value, str):
        return QueryIntentDecision.AMBIGUOUS

    normalized = value.strip().lower().replace(" ", "_").replace("-", "_")

    aliases = {
        "read_only": QueryIntentDecision.READ_ONLY_DATA_QUESTION,
        "readonly": QueryIntentDecision.READ_ONLY_DATA_QUESTION,
        "select": QueryIntentDecision.READ_ONLY_DATA_QUESTION,
        "write": QueryIntentDecision.WRITE_OR_MUTATION_REQUEST,
        "mutation": QueryIntentDecision.WRITE_OR_MUTATION_REQUEST,
        "unsafe": QueryIntentDecision.WRITE_OR_MUTATION_REQUEST,
        "non_data": QueryIntentDecision.NON_DATA_REQUEST,
        "not_data": QueryIntentDecision.NON_DATA_REQUEST,
    }

    if normalized in aliases:
        return aliases[normalized]

    for item in QueryIntentDecision:
        if normalized == item.value:
            return item

    return QueryIntentDecision.AMBIGUOUS


def parse_confidence(value: object) -> float:
    try:
        confidence = float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0

    return max(0.0, min(1.0, confidence))
