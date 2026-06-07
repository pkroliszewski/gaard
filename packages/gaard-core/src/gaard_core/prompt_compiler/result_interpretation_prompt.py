from typing import Any

from gaard_core.json_utils import json_dumps
from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_core.query_pipeline.models import QueryRequest, QueryResult


class ResultInterpretationPromptCompiler:
    def compile(
        self,
        request: QueryRequest,
        sql: str,
        result: QueryResult,
    ) -> CompiledPrompt:
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            question=request.question,
            sql=sql,
            rows=result.rows,
            columns=result.columns,
        )

        return CompiledPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={
                "rows_count": len(result.rows),
                "columns_count": len(result.columns),
            },
        )

    def _build_system_prompt(self) -> str:
        return """You are GAARD Data Result Interpreter.

Your task is to explain SQL query results to the user.

Rules:
- Answer in the same language as the user's question.
- Pay attention to correct user's language grammar and plural forms.
- Use only the data provided in the result.
- Do not invent facts.
- Be concise.
- Prefer one short paragraph.
- If the result is empty, say that the query returned no rows.
- If the result contains aggregated values, explain the value directly.
- Do not mention that you are an AI model.
- Do not include markdown tables unless explicitly needed.
- Do not include reasoning.
- Do not include <think> blocks.
- Return only the final answer.
"""

    def _build_user_prompt(
        self,
        question: str,
        sql: str,
        rows: list[dict[str, Any]],
        columns: list[str],
    ) -> str:
        payload = {
            "question": question,
            "sql": sql,
            "columns": columns,
            "rows": rows,
        }

        return f"""Interpret the following SQL result for the user.

Input JSON:
{json_dumps(payload, ensure_ascii=False, indent=2)}

Return only the final user-facing answer.
"""
