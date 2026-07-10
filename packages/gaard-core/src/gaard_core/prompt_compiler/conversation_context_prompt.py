from typing import Any

from gaard_core.json_utils import json_dumps
from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_core.query_pipeline.models import ConversationContextDecision, QueryRequest


class ConversationContextPromptCompiler:
    def compile(
        self,
        request: QueryRequest,
        conversation_context: dict[str, Any],
    ) -> CompiledPrompt:
        payload = {
            "question": request.question,
            "datasource_id": request.datasource_id,
            "datasource_ids": request.datasource_ids,
            "conversation_context": conversation_context,
        }

        return CompiledPrompt(
            system_prompt=self._build_system_prompt(),
            user_prompt=self._build_user_prompt(payload),
            metadata={
                "allowed_decisions": [item.value for item in ConversationContextDecision],
            },
        )

    def _build_system_prompt(self) -> str:
        return """You are GAARD Conversation Context Classification.

Your task is to decide whether a user's new data question starts a new topic, continues the current conversation, or is too ambiguous to safely continue.

Allowed decisions:
- new_topic: the question stands on its own or changes topic.
- follow_up: the question clearly depends on previous turns and can be rewritten as a standalone data question.
- ambiguous: the question appears to depend on prior context but cannot be rewritten safely.

Decision rules:
1. Use follow_up only when the current question can be resolved from the supplied compact conversation context.
2. Use new_topic when the question is already self-contained.
3. Use ambiguous instead of guessing when key entities, metrics, filters, date ranges, or datasource scope cannot be inferred.
4. Treat detail/projection requests about the previous result as follow_up when the previous turn defines the result set. If the previous question counted, grouped, or filtered records and the user now asks for descriptions, names, statuses, fields, details, or values for those same records, rewrite by preserving the previous filters/date range/datasource and changing only the returned fields.
5. Do not mark a projection/detail follow-up ambiguous merely because the previous answer did not expose row ids. The previous standalone question and SQL are enough context for the next SQL generation step.
6. Use ambiguous when the user asks for one specific record but the context indicates multiple records and no selector is provided.
7. Never include rows or sensitive data in the standalone question.

Output rules:
- Return only a JSON object.
- Do not include markdown.
- Do not include reasoning outside the JSON.
- Do not include <think> blocks.
- Use exactly this JSON shape:
  {"decision":"new_topic","confidence":0.0,"standalone_question":"rewritten question or empty","reason":"short reason"}
"""

    def _build_user_prompt(self, payload: dict[str, Any]) -> str:
        return f"""Classify this new user question against the compact conversation context.

Input JSON:
{json_dumps(payload, ensure_ascii=False, indent=2)}

Return one JSON object with:
- decision: one of {", ".join(item.value for item in ConversationContextDecision)}
- confidence: number from 0 to 1
- standalone_question: required for follow_up, otherwise empty
- reason: short explanation
"""
