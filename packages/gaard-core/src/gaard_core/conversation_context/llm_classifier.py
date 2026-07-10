import json
from typing import Any, Protocol, cast

from gaard_core.llm_output import remove_thinking_blocks
from gaard_core.prompt_compiler.conversation_context_prompt import (
    ConversationContextPromptCompiler,
)
from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_core.query_pipeline.models import (
    ConversationContextClassification,
    ConversationContextDecision,
    QueryRequest,
)
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage


class ConversationContextPromptCompilerProtocol(Protocol):
    def compile(
        self,
        request: QueryRequest,
        conversation_context: dict[str, Any],
    ) -> CompiledPrompt:
        pass


class LlmConversationContextClassifier:
    def __init__(
        self,
        client: OpenAICompatibleClient,
        model: str,
        extra_body: dict[str, Any] | None = None,
        prompt_compiler: ConversationContextPromptCompilerProtocol | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.extra_body = extra_body or {}
        self.prompt_compiler = prompt_compiler or ConversationContextPromptCompiler()

    def classify(
        self,
        request: QueryRequest,
        conversation_context: dict[str, Any],
    ) -> ConversationContextClassification:
        compiled_prompt = self.prompt_compiler.compile(
            request=request,
            conversation_context=conversation_context,
        )
        response = self.client.create_chat_completion(
            ChatCompletionRequest(
                model=self.model,
                temperature=0.0,
                extra_body=self.extra_body,
                messages=[
                    ChatMessage(role="system", content=compiled_prompt.system_prompt),
                    ChatMessage(role="user", content=compiled_prompt.user_prompt),
                ],
            )
        )

        return parse_conversation_context_classification(response.content)


def parse_conversation_context_classification(
    value: str,
) -> ConversationContextClassification:
    cleaned = remove_thinking_blocks(value).strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()
    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        payload = {"decision": cleaned}

    if not isinstance(payload, dict):
        return ConversationContextClassification()

    return ConversationContextClassification(
        decision=parse_conversation_context_decision(payload.get("decision")),
        confidence=parse_confidence(payload.get("confidence")),
        standalone_question=str(payload.get("standalone_question") or "").strip(),
        reason=str(payload.get("reason") or ""),
        model_response=payload,
    )


def parse_conversation_context_decision(value: object) -> ConversationContextDecision:
    if not isinstance(value, str):
        return ConversationContextDecision.AMBIGUOUS

    normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "new": ConversationContextDecision.NEW_TOPIC,
        "new_question": ConversationContextDecision.NEW_TOPIC,
        "newtopic": ConversationContextDecision.NEW_TOPIC,
        "continue": ConversationContextDecision.FOLLOW_UP,
        "continuation": ConversationContextDecision.FOLLOW_UP,
        "followup": ConversationContextDecision.FOLLOW_UP,
        "follow_up_question": ConversationContextDecision.FOLLOW_UP,
        "unclear": ConversationContextDecision.AMBIGUOUS,
        "needs_clarification": ConversationContextDecision.AMBIGUOUS,
    }
    if normalized in aliases:
        return aliases[normalized]

    for item in ConversationContextDecision:
        if normalized == item.value:
            return item

    return ConversationContextDecision.AMBIGUOUS


def parse_confidence(value: object) -> float:
    try:
        confidence = float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0

    return max(0.0, min(1.0, confidence))
