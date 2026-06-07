from gaard_core.json_utils import json_dumps
from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_core.query_pipeline.models import QueryIntentDecision, QueryRequest


class IntentClassificationPromptCompiler:
    def compile(self, request: QueryRequest) -> CompiledPrompt:
        payload = {
            "question": request.question,
            "datasource_id": request.datasource_id,
            "user_id": request.user_id,
        }

        return CompiledPrompt(
            system_prompt=self._build_system_prompt(),
            user_prompt=self._build_user_prompt(payload),
            metadata={
                "allowed_decisions": [item.value for item in QueryIntentDecision],
            },
        )

    def _build_system_prompt(self) -> str:
        return """You are GAARD Query Intent Classification.

Your task is to decide whether the user's request can be fulfilled only by a read-only SQL SELECT query.

Allowed decisions:
- read_only_data_question: the user asks a question about data that can be answered with a read-only SELECT or WITH query.
- write_or_mutation_request: the user asks to insert, update, delete, reset, clear, modify, create, alter, drop, or otherwise change data, schema, configuration, files, permissions, or system state.
- non_data_request: the request is not a question about database data.
- ambiguous: the intent is unclear or it is not safe to decide that it is read-only.

Decision rules:
1. Allow only requests whose intent is to read, count, list, aggregate, compare, summarize, inspect, or analyze existing data.
2. Reject requests that ask for a change, even if a SELECT query could be used to find the affected rows.
3. Reject destructive, administrative, or state-changing requests.
4. Choose ambiguous instead of guessing when the intent is unclear.

Output rules:
- Return only a JSON object.
- Do not include markdown.
- Do not include reasoning outside the JSON.
- Do not include <think> blocks.
- Use exactly this JSON shape:
  {"decision":"read_only_data_question","confidence":0.0,"reason":"short reason"}
"""

    def _build_user_prompt(self, payload: dict[str, str]) -> str:
        return f"""Classify this user request before SQL generation.

Input JSON:
{json_dumps(payload, ensure_ascii=False, indent=2)}

Return one JSON object with:
- decision: one of {", ".join(item.value for item in QueryIntentDecision)}
- confidence: number from 0 to 1
- reason: short explanation
"""
