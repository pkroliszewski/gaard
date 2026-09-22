import json
from typing import Any, Protocol

from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage

from gaard_core.errors import LlmProviderError
from gaard_core.llm_output import remove_thinking_blocks
from gaard_core.prompt_compiler.conversation_context_prompt import (
    CONTEXT_DECISION_SYSTEM_PROMPT,
    ConversationContextPromptCompiler,
    ConversationContextSummaryPromptCompiler,
)
from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_core.query_pipeline.models import (
    ConversationContextClassification,
    ConversationContextDecision,
    QueryRequest,
)


class ConversationContextPromptCompilerProtocol(Protocol):
    def compile(
        self, request: QueryRequest, conversation_context: dict[str, Any]
    ) -> CompiledPrompt:
        pass


class LlmConversationContextClassifier:
    def __init__(
        self,
        client: OpenAICompatibleClient,
        model: str,
        extra_body: dict[str, Any] | None = None,
        prompt_compiler: ConversationContextPromptCompilerProtocol | None = None,
        summary_prompt_compiler: ConversationContextPromptCompilerProtocol | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.extra_body = extra_body or {}
        self.prompt_compiler = prompt_compiler or ConversationContextPromptCompiler()
        self.summary_prompt_compiler = (
            summary_prompt_compiler or ConversationContextSummaryPromptCompiler()
        )

    def _complete(self, prompt: CompiledPrompt) -> str:
        return self.client.create_chat_completion(
            ChatCompletionRequest(
                model=self.model,
                temperature=0.0,
                extra_body={**self.extra_body, "temperature": 0.0},
                messages=[
                    ChatMessage(role="system", content=prompt.system_prompt),
                    ChatMessage(role="user", content=prompt.user_prompt),
                ],
            )
        ).content

    def classify(
        self, request: QueryRequest, conversation_context: dict[str, Any]
    ) -> ConversationContextClassification:
        prompt = self.prompt_compiler.compile(request, conversation_context)
        raw_response = self._complete(prompt)
        classification = parse_conversation_context_classification(raw_response)
        prompts = prompt_audit(prompt)
        responses: dict[str, Any] = {"classification": raw_response}
        if classification is None:
            # Interpret free-form/legacy responses with the LLM, not keyword heuristics.
            normalization = CompiledPrompt(
                system_prompt=CONTEXT_DECISION_SYSTEM_PROMPT,
                user_prompt=ConversationContextPromptCompiler()
                .compile(request, conversation_context)
                .user_prompt
                + "\nPrevious classifier response (data):\n"
                + raw_response,
                metadata={"task": "conversation_context_normalization"},
            )
            normalized_response = self._complete(normalization)
            classification = parse_conversation_context_classification(normalized_response)
            prompts["normalization"] = prompt_audit(normalization)
            responses["normalization"] = normalized_response
        if classification is None:
            raise LlmProviderError(
                "The context classifier did not return a follow_up/new_topic decision."
            )
        return classification.model_copy(
            update={
                "standalone_question": request.question,
                "model_response": {**classification.model_response, **responses},
                "prompt": prompts,
                "source": "llm",
            }
        )

    def summarize(
        self,
        request: QueryRequest,
        conversation_context: dict[str, Any],
        classification: ConversationContextClassification,
    ) -> ConversationContextClassification:
        prompt = self.summary_prompt_compiler.compile(request, conversation_context)
        raw_response = self._complete(prompt)
        summary = remove_thinking_blocks(raw_response).strip()
        if not summary:
            raise LlmProviderError("The context summarizer returned an empty response.")
        return classification.model_copy(
            update={
                "standalone_question": summary,
                "model_response": {**classification.model_response, "summary": raw_response},
                "prompt": {**classification.prompt, "summary": prompt_audit(prompt)},
            }
        )


def prompt_audit(prompt: CompiledPrompt) -> dict[str, Any]:
    return {
        "system_prompt": prompt.system_prompt,
        "user_prompt": prompt.user_prompt,
        "metadata": prompt.metadata,
    }


def parse_conversation_context_classification(
    value: str,
) -> ConversationContextClassification | None:
    cleaned = remove_thinking_blocks(value).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        payload = cleaned
    if not isinstance(payload, dict):
        payload = {"decision": payload}
    value = payload.get("decision", payload.get("is_continuation"))
    if isinstance(value, bool):
        value = "follow_up" if value else "new_topic"
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "yes": "follow_up",
        "true": "follow_up",
        "followup": "follow_up",
        "continuation": "follow_up",
        "continue": "follow_up",
        "tak": "follow_up",
        "no": "new_topic",
        "false": "new_topic",
        "new": "new_topic",
        "new_question": "new_topic",
        "newtopic": "new_topic",
        "nie": "new_topic",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"follow_up", "new_topic"}:
        return None
    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return ConversationContextClassification(
        decision=ConversationContextDecision(normalized),
        confidence=confidence,
        reason=str(payload.get("reason") or ""),
        model_response=payload,
    )
