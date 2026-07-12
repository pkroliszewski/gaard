from typing import Any

from gaard_core.errors import ConfigurationError
from gaard_core.json_utils import json_dumps
from gaard_core.prompt_compiler.models import CompiledPrompt, SqlGenerationPromptRequest
from gaard_core.prompt_compiler.schema_formatter import SchemaPromptFormatter
from gaard_core.query_pipeline.models import (
    ConversationContextDecision,
    QueryRequest,
    QueryResult,
)

from gaard_api.admin.models import PromptTemplate
from gaard_api.admin.services import get_active_prompt_template_safe


class MetadataSqlGenerationPromptCompiler:
    def __init__(
        self,
        prompt_template: PromptTemplate,
        schema_formatter: SchemaPromptFormatter | None = None,
    ) -> None:
        self.prompt_template = prompt_template
        self.schema_formatter = schema_formatter or SchemaPromptFormatter()

    def compile(self, request: SqlGenerationPromptRequest) -> CompiledPrompt:
        formatted_schema = self._resolve_formatted_schema(request)

        system_prompt = self.prompt_template.system_prompt.format(
            dialect=request.dialect,
            max_rows=request.max_rows,
        )
        user_prompt = self.prompt_template.user_prompt_template.format(
            schema=formatted_schema,
            question=request.question,
            dialect=request.dialect,
            max_rows=request.max_rows,
        )

        return CompiledPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={
                "dialect": request.dialect,
                "max_rows": request.max_rows,
                "prompt_key": self.prompt_template.prompt_key,
                "prompt_version": self.prompt_template.version,
                "schema_source": "formatted_schema"
                if request.formatted_schema is not None
                else "database_schema",
                "tables_count": len(request.database_schema.tables)
                if request.database_schema is not None
                else None,
            },
        )

    def _resolve_formatted_schema(self, request: SqlGenerationPromptRequest) -> str:
        if request.formatted_schema is not None:
            return request.formatted_schema

        if request.database_schema is None:
            raise ConfigurationError("Either database_schema or formatted_schema must be provided.")

        return self.schema_formatter.format(request.database_schema)


class MetadataIntentClassificationPromptCompiler:
    def __init__(self, prompt_template: PromptTemplate) -> None:
        self.prompt_template = prompt_template

    def compile(self, request: QueryRequest) -> CompiledPrompt:
        payload = {
            "question": request.question,
            "datasource_id": request.datasource_id,
            "user_id": request.user_id,
        }
        payload_json = json_dumps(payload, ensure_ascii=False, indent=2)

        return CompiledPrompt(
            system_prompt=self.prompt_template.system_prompt,
            user_prompt=self.prompt_template.user_prompt_template.format(
                payload=payload_json,
                question=request.question,
                datasource_id=request.datasource_id,
                user_id=request.user_id,
            ),
            metadata={
                "prompt_key": self.prompt_template.prompt_key,
                "prompt_version": self.prompt_template.version,
            },
        )


class MetadataConversationContextPromptCompiler:
    def __init__(self, prompt_template: PromptTemplate) -> None:
        self.prompt_template = prompt_template

    def compile(
        self,
        request: QueryRequest,
        conversation_context: dict[str, Any],
    ) -> CompiledPrompt:
        recent_turns = self._recent_turns(conversation_context)
        payload = {
            "turn_t_minus_2": recent_turns[0] if len(recent_turns) == 2 else {},
            "turn_t_minus_1": recent_turns[-1] if recent_turns else {},
            "turn_t": {
                "question": request.question,
                "datasource_id": request.datasource_id,
                "datasource_ids": request.datasource_ids,
            },
        }
        payload_json = json_dumps(payload, ensure_ascii=False, indent=2)

        return CompiledPrompt(
            system_prompt=self.prompt_template.system_prompt,
            user_prompt=self.prompt_template.user_prompt_template.format(
                payload=payload_json,
                question=request.question,
                datasource_id=request.datasource_id,
                datasource_ids=json_dumps(request.datasource_ids, ensure_ascii=False),
            ),
            metadata={
                "allowed_decisions": [item.value for item in ConversationContextDecision],
                "decision_task": "logical_continuation_yes_no",
                "prompt_key": self.prompt_template.prompt_key,
                "prompt_version": self.prompt_template.version,
            },
        )

    def _recent_turns(self, conversation_context: dict[str, Any]) -> list[dict[str, Any]]:
        turns = [
            turn for turn in conversation_context.get("turns", []) if isinstance(turn, dict)
        ][-2:]
        labels = ["t-2", "t-1"] if len(turns) == 2 else ["t-1"]
        return [
            {
                "label": label,
                "question": str(turn.get("question") or ""),
                "standalone_question": str(turn.get("standalone_question") or ""),
                "answer": str(turn.get("answer") or ""),
                "sql": str(turn.get("sql") or ""),
                "context_decision": str(turn.get("context_decision") or ""),
                "context_reason": str(turn.get("context_reason") or ""),
            }
            for label, turn in zip(labels, turns, strict=False)
        ]


class MetadataResultInterpretationPromptCompiler:
    def __init__(self, prompt_template: PromptTemplate) -> None:
        self.prompt_template = prompt_template

    def compile(
        self,
        request: QueryRequest,
        sql: str,
        result: QueryResult,
    ) -> CompiledPrompt:
        payload = {
            "question": request.question,
            "sql": sql,
            "columns": result.columns,
            "rows": result.rows,
        }
        payload_json = json_dumps(payload, ensure_ascii=False, indent=2)

        return CompiledPrompt(
            system_prompt=self.prompt_template.system_prompt,
            user_prompt=self.prompt_template.user_prompt_template.format(
                payload=payload_json,
                question=request.question,
                sql=sql,
                columns=json_dumps(result.columns, ensure_ascii=False),
                rows=json_dumps(result.rows, ensure_ascii=False),
            ),
            metadata={
                "rows_count": len(result.rows),
                "columns_count": len(result.columns),
                "prompt_key": self.prompt_template.prompt_key,
                "prompt_version": self.prompt_template.version,
            },
        )


class MetadataResultClassificationPromptCompiler:
    def __init__(self, prompt_template: PromptTemplate) -> None:
        self.prompt_template = prompt_template

    def compile(
        self,
        request: QueryRequest,
        answer: str,
    ) -> CompiledPrompt:
        payload = {
            "question": request.question,
            "answer": answer,
        }
        payload_json = json_dumps(payload, ensure_ascii=False, indent=2)

        return CompiledPrompt(
            system_prompt=self.prompt_template.system_prompt,
            user_prompt=self.prompt_template.user_prompt_template.format(
                payload=payload_json,
                question=request.question,
                answer=answer,
            ),
            metadata={
                "prompt_key": self.prompt_template.prompt_key,
                "prompt_version": self.prompt_template.version,
            },
        )


def get_sql_generation_prompt_compiler() -> MetadataSqlGenerationPromptCompiler | None:
    prompt_template = get_active_prompt_template_safe("sql_generation")

    if prompt_template is None:
        return None

    return MetadataSqlGenerationPromptCompiler(prompt_template=prompt_template)


def get_intent_classification_prompt_compiler() -> (
    MetadataIntentClassificationPromptCompiler | None
):
    prompt_template = get_active_prompt_template_safe("intent_classification")

    if prompt_template is None:
        return None

    return MetadataIntentClassificationPromptCompiler(prompt_template=prompt_template)


def get_conversation_context_prompt_compiler() -> (
    MetadataConversationContextPromptCompiler | None
):
    prompt_template = get_active_prompt_template_safe("conversation_context_classification")

    if prompt_template is None:
        return None

    return MetadataConversationContextPromptCompiler(prompt_template=prompt_template)


def get_result_interpretation_prompt_compiler() -> (
    MetadataResultInterpretationPromptCompiler | None
):
    prompt_template = get_active_prompt_template_safe("result_interpretation")

    if prompt_template is None:
        return None

    return MetadataResultInterpretationPromptCompiler(prompt_template=prompt_template)


def get_result_classification_prompt_compiler() -> (
    MetadataResultClassificationPromptCompiler | None
):
    prompt_template = get_active_prompt_template_safe("result_classification")

    if prompt_template is None:
        return None

    return MetadataResultClassificationPromptCompiler(prompt_template=prompt_template)
