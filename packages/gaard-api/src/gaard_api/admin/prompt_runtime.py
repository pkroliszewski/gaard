from typing import Any

from gaard_core.errors import ConfigurationError
from gaard_core.json_utils import json_dumps
from gaard_core.prompt_compiler.conversation_context_prompt import (
    CONTEXT_DECISION_SYSTEM_PROMPT,
    CONTEXT_SUMMARY_SYSTEM_PROMPT,
    conversation_context_payload,
)
from gaard_core.prompt_compiler.models import CompiledPrompt, SqlGenerationPromptRequest
from gaard_core.prompt_compiler.schema_formatter import SchemaPromptFormatter
from gaard_core.prompt_compiler.sql_generation_prompt import sql_row_limit_instruction
from gaard_core.query_pipeline.models import (
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
            row_limit_instruction=sql_row_limit_instruction(request.dialect, request.max_rows),
        )
        user_prompt = self.prompt_template.user_prompt_template.format(
            schema=formatted_schema,
            question=request.question,
            dialect=request.dialect,
            max_rows=request.max_rows,
            row_limit_instruction=sql_row_limit_instruction(request.dialect, request.max_rows),
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
        payload_json = json_dumps(
            conversation_context_payload(request, conversation_context),
            ensure_ascii=False,
            indent=2,
        )
        is_summary = self.prompt_template.prompt_key == "conversation_context_summary"
        contract = CONTEXT_SUMMARY_SYSTEM_PROMPT if is_summary else CONTEXT_DECISION_SYSTEM_PROMPT
        user_prompt = self.prompt_template.user_prompt_template.format(
            payload=payload_json,
            question=request.question,
            datasource_id=request.datasource_id,
            datasource_ids=json_dumps(request.datasource_ids, ensure_ascii=False),
        )
        if payload_json not in user_prompt:
            user_prompt += "\n\nComplete current input:\n" + payload_json
        return CompiledPrompt(
            # The current contract also applies to customized prompts stored before this upgrade.
            system_prompt=(
                self.prompt_template.system_prompt
                if self.prompt_template.system_prompt == contract
                else self.prompt_template.system_prompt + "\n\nCurrent task contract:\n" + contract
            ),
            user_prompt=user_prompt,
            metadata={
                "task": "conversation_context_summary" if is_summary else "conversation_context_decision",
                "prompt_key": self.prompt_template.prompt_key,
                "prompt_version": self.prompt_template.version,
            },
        )


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


class MetadataAnswerExplanationPromptCompiler:
    def __init__(self, prompt_template: PromptTemplate) -> None:
        self.prompt_template = prompt_template

    def compile(self, payload: dict[str, Any]) -> CompiledPrompt:
        payload_json = json_dumps(payload, ensure_ascii=False, indent=2)
        metadata = payload.get("metadata")
        inference_metadata = payload.get("inference_metadata")
        prompt_metadata = payload.get("prompt_metadata")

        return CompiledPrompt(
            system_prompt=self.prompt_template.system_prompt,
            user_prompt=self.prompt_template.user_prompt_template.format(
                payload=payload_json,
                question=str(payload.get("question") or ""),
                sql=str(payload.get("sql") or ""),
                answer=str(payload.get("answer") or ""),
                metadata=json_dumps(metadata, ensure_ascii=False),
                inference_metadata=json_dumps(inference_metadata, ensure_ascii=False),
                prompt_metadata=json_dumps(prompt_metadata, ensure_ascii=False),
                business_logic=str(payload.get("business_logic") or ""),
            ),
            metadata={
                "prompt_key": self.prompt_template.prompt_key,
                "prompt_version": self.prompt_template.version,
                "has_sql": bool(str(payload.get("sql") or "").strip()),
                "has_result_rows": bool(
                    (payload.get("result") or {}).get("rows")
                    if isinstance(payload.get("result"), dict)
                    else False
                ),
                "has_business_logic": bool(
                    str(payload.get("business_logic") or "").strip()
                ),
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


def get_conversation_context_summary_prompt_compiler() -> (
    MetadataConversationContextPromptCompiler | None
):
    prompt_template = get_active_prompt_template_safe("conversation_context_summary")
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


def get_answer_explanation_prompt_compiler() -> (
    MetadataAnswerExplanationPromptCompiler | None
):
    prompt_template = get_active_prompt_template_safe("answer_explanation")

    if prompt_template is None:
        return None

    return MetadataAnswerExplanationPromptCompiler(prompt_template=prompt_template)
