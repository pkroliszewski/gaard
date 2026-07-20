import json
import logging
import re
import queue
import threading
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from gaard_core.conversation_context.llm_classifier import LlmConversationContextClassifier
from gaard_core.conversation_context.mock_classifier import MockConversationContextClassifier
from gaard_core.errors import (
    ConfigurationError,
    LlmProviderError,
    QueryExecutionError,
    QueryPipelineStepError,
    SqlValidationError,
)
from gaard_core.llm_output import remove_thinking_blocks
from gaard_core.query_intent.llm_classifier import LlmQueryIntentClassifier
from gaard_core.query_intent.mock_classifier import MockQueryIntentClassifier
from gaard_core.query_pipeline.llm_sql_generator import LlmSqlGenerator
from gaard_core.query_pipeline.mock_sql_generator import MockSqlGenerator
from gaard_core.query_pipeline.models import (
    ContextMode,
    ConversationContextClassification,
    ConversationContextDecision,
    OutputClassification,
    QueryIntentClassification,
    QueryIntentDecision,
    QueryRequest,
    QueryResponse,
)
from gaard_core.query_pipeline.pipeline import QueryPipeline
from gaard_core.result_classifier.llm_classifier import LlmResultClassifier
from gaard_core.result_classifier.mock_classifier import MockResultClassifier
from gaard_core.result_interpreter.llm_interpreter import LlmResultInterpreter
from gaard_core.result_interpreter.mock_interpreter import MockResultInterpreter
from gaard_core.sql_validator.select_only import SelectOnlySqlValidator
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage

from gaard_api.admin.database import create_session
from gaard_api.admin.prompt_runtime import (
    get_answer_explanation_prompt_compiler,
    get_conversation_context_prompt_compiler,
    get_intent_classification_prompt_compiler,
    get_result_classification_prompt_compiler,
    get_result_interpretation_prompt_compiler,
    get_sql_generation_prompt_compiler,
)
from gaard_api.admin.services import (
    ACCESS_ERROR_INTENT_CLASSIFICATION,
    ACCESS_ERROR_SQL_VALIDATION,
    get_active_business_logic_prompt_safe,
    get_active_datasource_connectors,
    get_datasource_connector_by_key,
    get_llm_runtime_config_safe,
    get_query_runtime_config_safe,
    learn_business_logic_from_sql_error,
    LlmRuntimeConfig,
    QueryRuntimeConfig,
    record_data_query_access_error_audit,
    record_data_query_audit,
    record_data_query_pipeline_error_audit,
    record_data_query_sql_error_audit,
)
from gaard_api.auth_dependencies import AuthenticatedSession, get_current_enterprise_api_user
from gaard_api.conversations import (
    Conversation,
    ConversationPrincipal,
    ambiguous_context_response,
    build_compact_conversation_context,
    build_conversation_metadata,
    conversation_exists,
    ensure_conversation,
    load_conversation_for_owner,
    new_topic_classification,
    record_conversation_turn,
)
from gaard_api.extensions import get_query_hook_registry
from gaard_api.license import license_service
from gaard_api.query_hooks import (
    DatasourceContext,
    DatasourceContexts,
    QueryExecutor,
    SqlDialectPlan,
)

router = APIRouter()
logger = logging.getLogger(__name__)

READ_ONLY_REFUSAL_ANSWER = (
    "Nie mogę tego zrobić. GAARD obsługuje tylko odczyt danych i nie wykonuje "
    "operacji modyfikujących, usuwających ani tworzących dane."
)

READ_ONLY_SCOPE_REFUSAL_ANSWER = (
    "Nie mogę obsłużyć tego zapytania. GAARD odpowiada tylko na pytania, "
    "które można zrealizować jako bezpieczny odczyt danych SQL SELECT."
)

ALLOWLIST_REFUSAL_ANSWER = (
    "Nie mogę wykonać tego zapytania, ponieważ wymagane tabele, kolumny, "
    "relacje albo dowody są poza zakresem dozwolonym dla tego zadania."
)

SQL_SYNTAX_REFUSAL_ANSWER = (
    "Nie mogę wykonać tego zapytania, ponieważ wygenerowany SQL nie przeszedł "
    "walidacji składni lub zasad pojedynczego zapytania."
)

CLARIFICATION_REFUSAL_ANSWER = "Potrzebuję doprecyzowania, zanim bezpiecznie rozpocznę tę analizę."

NO_ACTIVE_DATASOURCES_ANSWER = (
    "No active data sources are selected. Please select at least one data source "
    "before asking a data question."
)

VALIDATION_SQL_PREFIXES = (
    "Only SELECT queries are allowed. ",
    "DDL and DML statements are not allowed. ",
    "Only single-statement SQL queries are allowed. SQL: ",
    "Invalid SQL syntax. ",
)

INFERENCE_METADATA_KEYS = {
    "active_datasource_ids",
    "assumptions",
    "confidence",
    "datasource_id",
    "datasource_ids",
    "intent_classification_mode",
    "intent_confidence",
    "intent_decision",
    "intent_model_response",
    "intent_reason",
    "llm_sql_language",
    "output_classification",
    "output_classification_error",
    "output_classification_mode",
    "raw_sql_output",
    "result_interpretation_mode",
    "sql_generation_mode",
}


class QueryAnswerExplanationRequest(BaseModel):
    question: str = Field(min_length=1)
    sql: str = ""
    answer: str = ""
    rows: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    inference_metadata: dict[str, Any] = Field(default_factory=dict)
    prompt_metadata: dict[str, Any] = Field(default_factory=dict)
    business_logic: str = ""
    datasource_id: str = ""
    datasource_ids: list[str] = Field(default_factory=list)


class QueryAnswerExplanationResponse(BaseModel):
    explanation: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def create_llm_client(
    llm_config: LlmRuntimeConfig | None = None,
) -> OpenAICompatibleClient:
    llm_config = llm_config or get_llm_runtime_config_safe()

    if llm_config.provider != "openai-compatible":
        raise ConfigurationError(f"Unsupported GAARD_LLM_PROVIDER: {llm_config.provider}")

    if llm_config.api_key == "change-me":
        raise ConfigurationError("GAARD_LLM_API_KEY must be configured when using LLM mode.")

    return OpenAICompatibleClient(
        base_url=llm_config.base_url,
        api_key=llm_config.api_key,
        timeout_seconds=llm_config.timeout_seconds,
    )


def explain_query_answer(
    request: QueryAnswerExplanationRequest,
    llm_config: LlmRuntimeConfig | None = None,
) -> QueryAnswerExplanationResponse:
    compiler = get_answer_explanation_prompt_compiler()
    if compiler is None:
        raise ConfigurationError("Active answer_explanation prompt is not configured.")

    llm_config = llm_config or get_llm_runtime_config_safe()
    payload = build_answer_explanation_payload(request)
    compiled_prompt = compiler.compile(payload)
    response = create_llm_client(llm_config).create_chat_completion(
        ChatCompletionRequest(
            model=llm_config.model,
            temperature=0.0,
            extra_body=llm_config.extra_body,
            messages=[
                ChatMessage(role="system", content=compiled_prompt.system_prompt),
                ChatMessage(role="user", content=compiled_prompt.user_prompt),
            ],
        )
    )

    return QueryAnswerExplanationResponse(
        explanation=remove_thinking_blocks(response.content).strip(),
        metadata={
            **compiled_prompt.metadata,
            "model": response.model or llm_config.model,
            "datasource_ids": payload["datasource_ids"],
            "business_logic_included": bool(payload["business_logic"]),
        },
    )


def build_answer_explanation_payload(
    request: QueryAnswerExplanationRequest,
) -> dict[str, Any]:
    metadata = request.metadata or {}
    inference_metadata = {
        **extract_inference_metadata(metadata),
        **request.inference_metadata,
    }
    prompt_metadata = {
        **extract_prompt_metadata(metadata),
        **request.prompt_metadata,
    }
    datasource_ids = datasource_ids_for_explanation(request)
    business_logic = request.business_logic.strip() or active_business_logic_for_explanation(
        datasource_ids,
    )

    return {
        "question": request.question,
        "sql": request.sql,
        "answer": request.answer,
        "result": {
            "columns": request.columns or columns_from_rows(request.rows),
            "rows": request.rows,
        },
        "metadata": metadata,
        "inference_metadata": inference_metadata,
        "prompt_metadata": prompt_metadata,
        "business_logic": business_logic,
        "datasource_id": request.datasource_id or str(metadata.get("datasource_id") or ""),
        "datasource_ids": datasource_ids,
    }


def extract_inference_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    extracted = {
        key: value
        for key, value in metadata.items()
        if key in INFERENCE_METADATA_KEYS
        or key.startswith("intent_")
        or key.startswith("context_")
    }
    conversation = metadata.get("conversation")
    if isinstance(conversation, dict):
        extracted["conversation"] = {
            key: conversation.get(key)
            for key in (
                "context_decision",
                "standalone_question",
                "confidence",
                "context_reason",
                "context_source",
                "context_model_response",
            )
            if key in conversation
        }
    return extracted


def extract_prompt_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    extracted = {
        key: value
        for key, value in metadata.items()
        if "prompt" in key and isinstance(value, (dict, list, str, int, float, bool))
    }
    conversation = metadata.get("conversation")
    if isinstance(conversation, dict):
        context_prompt = conversation.get("context_prompt")
        if isinstance(context_prompt, dict):
            extracted["conversation_context_prompt"] = context_prompt
    return extracted


def datasource_ids_for_explanation(request: QueryAnswerExplanationRequest) -> list[str]:
    ids: list[str] = []
    ids.extend(request.datasource_ids)
    ids.extend(split_datasource_ids(request.datasource_id))

    metadata = request.metadata or {}
    for key in ("datasource_ids", "active_datasource_ids"):
        value = metadata.get(key)
        if isinstance(value, list):
            ids.extend(str(item) for item in value)
        else:
            ids.extend(split_datasource_ids(str(value or "")))

    ids.extend(split_datasource_ids(str(metadata.get("datasource_id") or "")))

    seen: set[str] = set()
    normalized: list[str] = []
    for item in ids:
        datasource_id = item.strip()
        if not datasource_id or datasource_id in seen:
            continue
        seen.add(datasource_id)
        normalized.append(datasource_id)
    return normalized


def split_datasource_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def active_business_logic_for_explanation(datasource_ids: list[str]) -> str:
    connector_refs: list[tuple[int, str, str]] = []
    try:
        with create_session() as session:
            if datasource_ids:
                for datasource_id in datasource_ids:
                    connector = get_datasource_connector_by_key(session, datasource_id)
                    if connector is not None:
                        connector_refs.append(
                            (connector.id, connector.name, connector.connector_key)
                        )
            if not connector_refs:
                connector_refs.extend(
                    (connector.id, connector.name, connector.connector_key)
                    for connector in get_active_datasource_connectors(session)
                )
    except Exception:
        return ""

    blocks: list[str] = []
    for connector_id, name, connector_key in connector_refs:
        business_logic = get_active_business_logic_prompt_safe(connector_id)
        if business_logic:
            blocks.append(
                f"Data source {name} ({connector_key}):\n{business_logic}"
            )
    return "\n\n".join(blocks)


def columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns


def create_sql_generator(
    datasource_context: DatasourceContext | DatasourceContexts | None = None,
    llm_config: LlmRuntimeConfig | None = None,
    runtime_config: QueryRuntimeConfig | None = None,
    dialect_plan: SqlDialectPlan | None = None,
) -> MockSqlGenerator | LlmSqlGenerator:
    runtime_config = runtime_config or get_query_runtime_config_safe()

    if runtime_config.sql_generation_mode == "mock":
        return MockSqlGenerator()

    if runtime_config.sql_generation_mode == "llm":
        llm_config = llm_config or get_llm_runtime_config_safe()

        if datasource_context is None:
            datasource_context = (
                get_query_hook_registry()
                .resolve_effective_query_context(
                    QueryRequest(question="__sql_generation_context__")
                )
                .datasource_contexts
            )

        if datasource_context is not None:
            datasource_contexts = normalize_datasource_contexts(datasource_context)
            if datasource_contexts:
                formatted_schema = get_query_hook_registry().format_datasource_schemas(
                    datasource_contexts
                )
                dialect_plan = dialect_plan or resolve_sql_dialect_plan(datasource_contexts)
                logger.info(
                    "Creating LLM SQL generator: datasources=%r dialect=%r parser_dialect=%r "
                    "schema_context=%r",
                    [
                        {
                            "id": connector.id,
                            "key": connector.connector_key,
                            "database_type": connector.database_type,
                            "sql_dialect": connector.sql_dialect,
                        }
                        for connector, _cache in datasource_contexts
                    ],
                    dialect_plan.prompt_dialect,
                    dialect_plan.sqlglot_read_dialect,
                    formatted_schema,
                )
                return LlmSqlGenerator(
                    client=create_llm_client(llm_config),
                    model=llm_config.model,
                    formatted_schema=formatted_schema,
                    dialect=dialect_plan.prompt_dialect,
                    max_rows=runtime_config.query_max_rows,
                    extra_body=llm_config.extra_body,
                    prompt_compiler=get_sql_generation_prompt_compiler(),
                )

        raise ConfigurationError(
            "No active data sources are selected. SQL generation requires at least one "
            "active datasource."
        )

    raise ConfigurationError(
        f"Unsupported GAARD_SQL_GENERATION_MODE: {runtime_config.sql_generation_mode}"
    )


def resolve_intent_classification_mode() -> str:
    runtime_config = get_query_runtime_config_safe()

    if runtime_config.intent_classification_mode == "auto":
        return "llm" if runtime_config.sql_generation_mode == "llm" else "mock"

    return runtime_config.intent_classification_mode


def create_intent_classifier(
    llm_config: LlmRuntimeConfig | None = None,
) -> MockQueryIntentClassifier | LlmQueryIntentClassifier:
    intent_classification_mode = resolve_intent_classification_mode()

    if intent_classification_mode == "mock":
        return MockQueryIntentClassifier()

    if intent_classification_mode == "llm":
        llm_config = llm_config or get_llm_runtime_config_safe()

        return LlmQueryIntentClassifier(
            client=create_llm_client(llm_config),
            model=llm_config.model,
            extra_body=llm_config.extra_body,
            prompt_compiler=get_intent_classification_prompt_compiler(),
        )

    raise ConfigurationError(
        "Unsupported GAARD_INTENT_CLASSIFICATION_MODE: "
        f"{get_query_runtime_config_safe().intent_classification_mode}"
    )


def create_conversation_context_classifier(
    llm_config: LlmRuntimeConfig | None = None,
) -> MockConversationContextClassifier | LlmConversationContextClassifier:
    context_classification_mode = resolve_intent_classification_mode()

    if context_classification_mode == "mock":
        return MockConversationContextClassifier()

    if context_classification_mode == "llm":
        llm_config = llm_config or get_llm_runtime_config_safe()

        return LlmConversationContextClassifier(
            client=create_llm_client(llm_config),
            model=llm_config.model,
            extra_body=llm_config.extra_body,
            prompt_compiler=get_conversation_context_prompt_compiler(),
        )

    raise ConfigurationError(
        f"Unsupported GAARD conversation context classification mode: {context_classification_mode}"
    )


def create_result_interpreter(
    llm_config: LlmRuntimeConfig | None = None,
    runtime_config: QueryRuntimeConfig | None = None,
) -> MockResultInterpreter | LlmResultInterpreter:
    runtime_config = runtime_config or get_query_runtime_config_safe()

    if runtime_config.result_interpretation_mode == "mock":
        return MockResultInterpreter()

    if runtime_config.result_interpretation_mode == "llm":
        llm_config = llm_config or get_llm_runtime_config_safe()

        return LlmResultInterpreter(
            client=create_llm_client(llm_config),
            model=llm_config.model,
            extra_body=llm_config.extra_body,
            prompt_compiler=get_result_interpretation_prompt_compiler(),
        )

    raise ConfigurationError(
        f"Unsupported GAARD_RESULT_INTERPRETATION_MODE: {runtime_config.result_interpretation_mode}"
    )


def resolve_output_classification_mode(
    runtime_config: QueryRuntimeConfig | None = None,
) -> str:
    runtime_config = runtime_config or get_query_runtime_config_safe()
    if runtime_config.output_classification_mode == "auto":
        return "llm" if runtime_config.result_interpretation_mode == "llm" else "mock"

    return runtime_config.output_classification_mode


def create_result_classifier(
    llm_config: LlmRuntimeConfig | None = None,
    runtime_config: QueryRuntimeConfig | None = None,
) -> MockResultClassifier | LlmResultClassifier:
    runtime_config = runtime_config or get_query_runtime_config_safe()
    output_classification_mode = resolve_output_classification_mode(runtime_config)

    if output_classification_mode == "mock":
        return MockResultClassifier()

    if output_classification_mode == "llm":
        llm_config = llm_config or get_llm_runtime_config_safe()

        return LlmResultClassifier(
            client=create_llm_client(llm_config),
            model=llm_config.model,
            extra_body=llm_config.extra_body,
            prompt_compiler=get_result_classification_prompt_compiler(),
        )

    raise ConfigurationError(
        f"Unsupported GAARD_OUTPUT_CLASSIFICATION_MODE: {runtime_config.output_classification_mode}"
    )


def create_pipeline(
    datasource_context: DatasourceContext | DatasourceContexts | None = None,
    interpret: bool = True,
    enterprise_access: bool = True,
) -> QueryPipeline:
    if datasource_context is None:
        datasource_context = (
            get_query_hook_registry()
            .resolve_effective_query_context(QueryRequest(question="__pipeline_context__"))
            .datasource_contexts
        )

    runtime_config = get_query_runtime_config_safe()
    datasource_contexts = normalize_datasource_contexts(datasource_context)
    dialect_plan = resolve_sql_dialect_plan(datasource_contexts)
    executor = create_datasource_executor(
        datasource_contexts,
        runtime_config,
        dialect_plan,
        enterprise_access=enterprise_access,
    )
    output_classification_mode = resolve_output_classification_mode(runtime_config)
    llm_modes = {runtime_config.sql_generation_mode}
    if interpret:
        llm_modes.add(runtime_config.result_interpretation_mode)
        llm_modes.add(output_classification_mode)
    llm_config = get_llm_runtime_config_safe() if "llm" in llm_modes else None

    return QueryPipeline(
        sql_generator=create_sql_generator(
            datasource_context,
            llm_config,
            runtime_config,
            dialect_plan,
        ),
        sql_validator=SelectOnlySqlValidator(dialect=dialect_plan.sqlglot_read_dialect),
        executor=executor,
        interpreter=create_result_interpreter(llm_config, runtime_config)
        if interpret
        else MockResultInterpreter(),
        classifier=create_result_classifier(llm_config, runtime_config)
        if interpret
        else MockResultClassifier(),
        sql_generation_mode=runtime_config.sql_generation_mode,
        result_interpretation_mode=runtime_config.result_interpretation_mode
        if interpret
        else "none",
        output_classification_mode=output_classification_mode if interpret else "none",
    )


def normalize_datasource_contexts(
    datasource_context: DatasourceContext | DatasourceContexts | None,
) -> DatasourceContexts:
    from gaard_api.query_hooks import normalize_datasource_contexts as normalize

    return normalize(datasource_context)


def resolve_sql_dialect_plan(datasource_contexts: DatasourceContexts) -> SqlDialectPlan:
    return get_query_hook_registry().resolve_sql_dialect_plan(datasource_contexts)


def create_datasource_executor(
    datasource_contexts: DatasourceContexts,
    runtime_config: QueryRuntimeConfig,
    dialect_plan: SqlDialectPlan,
    *,
    enterprise_access: bool = True,
) -> QueryExecutor:
    license_service.ensure_datasource_contexts_allowed(
        datasource_contexts,
        enterprise_access=enterprise_access,
    )
    return get_query_hook_registry().create_datasource_executor(
        datasource_contexts,
        runtime_config.query_max_rows,
        dialect_plan,
    )


def detect_datasource_ids_from_sql(
    sql: str,
    datasource_contexts: DatasourceContexts,
    dialect_plan: SqlDialectPlan,
) -> list[str]:
    return get_query_hook_registry().detect_datasource_ids_from_sql(
        sql,
        datasource_contexts,
        dialect_plan,
    )


def is_tableless_sql(sql: str, dialect_plan: SqlDialectPlan) -> bool:
    return get_query_hook_registry().is_tableless_sql(sql, dialect_plan)


def request_for_detected_datasources(
    request: QueryRequest,
    detected_datasource_ids: list[str],
    *,
    tableless_sql: bool = False,
) -> QueryRequest:
    if tableless_sql:
        return request.model_copy(update={"datasource_id": "", "datasource_ids": []})

    if not detected_datasource_ids:
        return request

    return request.model_copy(
        update={
            "datasource_id": ",".join(detected_datasource_ids),
            "datasource_ids": detected_datasource_ids,
        }
    )


def metadata_for_detected_datasources(
    metadata: dict[str, Any],
    detected_datasource_ids: list[str],
    *,
    tableless_sql: bool = False,
) -> dict[str, Any]:
    if tableless_sql:
        return {**metadata, "datasource_id": "", "datasource_ids": []}

    if not detected_datasource_ids:
        return metadata

    return {
        **metadata,
        "datasource_id": ",".join(detected_datasource_ids),
        "datasource_ids": detected_datasource_ids,
    }


def extract_sql_from_validation_error(error_message: str) -> str:
    for prefix in VALIDATION_SQL_PREFIXES:
        if error_message.startswith(prefix):
            return error_message.removeprefix(prefix).strip()

    return ""


def validation_error_metadata(exc: SqlValidationError) -> dict[str, Any]:
    if exc.metadata.get("primary_error_category"):
        return exc.metadata

    category = (
        "sql.validation.write_operation"
        if any(
            text in exc.message
            for text in (
                "Only SELECT queries are allowed",
                "DDL and DML statements are not allowed",
            )
        )
        else (
            "sql.validation.syntax"
            if any(
                text in exc.message
                for text in (
                    "Only single-statement SQL queries are allowed",
                    "Invalid SQL syntax",
                )
            )
            else "sql.validation.disallowed_column"
        )
    )
    return {
        **exc.metadata,
        "primary_error_category": category,
        "error_categories": [category],
    }


def is_read_only_intent(intent: QueryIntentClassification) -> bool:
    return intent.decision == QueryIntentDecision.READ_ONLY_DATA_QUESTION


def intent_metadata(
    intent: QueryIntentClassification,
    intent_classification_mode: str,
) -> dict[str, Any]:
    model_response = intent.model_response or {
        "decision": intent.decision.value,
        "confidence": intent.confidence,
        "reason": intent.reason,
    }

    return {
        "intent_classification_mode": intent_classification_mode,
        "intent_decision": intent.decision.value,
        "intent_confidence": intent.confidence,
        "intent_reason": intent.reason,
        "intent_model_response": model_response,
    }


def build_access_refusal_response(
    request: QueryRequest,
    reason: str,
    sql: str = "",
    metadata: dict[str, Any] | None = None,
) -> QueryResponse:
    metadata = metadata or {}
    return QueryResponse(
        question=request.question,
        answer=access_refusal_answer(reason, metadata),
        sql=sql,
        rows=[],
        metadata={
            "duration_ms": 0,
            "datasource_id": request.datasource_id,
            "user_id": request.user_id,
            "output_classification": OutputClassification.UNKNOWN.value,
            "blocked": True,
            "blocked_reason": reason,
            **metadata,
        },
    )


def build_no_active_datasources_response(request: QueryRequest) -> QueryResponse:
    return QueryResponse(
        question=request.question,
        answer=NO_ACTIVE_DATASOURCES_ANSWER,
        sql="",
        rows=[],
        metadata={
            "duration_ms": 0,
            "datasource_id": request.datasource_id,
            "datasource_ids": request.datasource_ids,
            "user_id": request.user_id,
            "output_classification": OutputClassification.UNKNOWN.value,
            "blocked": True,
            "blocked_reason": "datasource.none_active",
        },
    )


def access_refusal_answer(reason: str, metadata: dict[str, Any]) -> str:
    if reason != ACCESS_ERROR_SQL_VALIDATION:
        return READ_ONLY_SCOPE_REFUSAL_ANSWER

    categories = set(metadata.get("error_categories") or [])
    primary = metadata.get("primary_error_category")
    if primary:
        categories.add(str(primary))

    if "sql.validation.write_operation" in categories:
        return READ_ONLY_REFUSAL_ANSWER
    if "intent.ambiguous_requires_clarification" in categories:
        return CLARIFICATION_REFUSAL_ANSWER
    if "sql.validation.syntax" in categories:
        return SQL_SYNTAX_REFUSAL_ANSWER
    if "sql.validation.bind_parameter" in categories:
        return SQL_SYNTAX_REFUSAL_ANSWER
    if categories & {
        "sql.validation.disallowed_column",
        "sql.validation.disallowed_table",
        "sql.validation.disallowed_relationship",
        "sql.validation.select_star",
        "task.inconsistent_allowlist",
        "task.insufficient_evidence",
    }:
        return ALLOWLIST_REFUSAL_ANSWER
    return READ_ONLY_SCOPE_REFUSAL_ANSWER


def run_sql_request(
    effective_request: QueryRequest,
    datasource_context: DatasourceContext | DatasourceContexts | None,
    extra_metadata: dict[str, Any] | None = None,
    on_stage: Any | None = None,
    enterprise_access: bool = True,
) -> QueryResponse:
    extra_metadata = extra_metadata or {}
    datasource_contexts = normalize_datasource_contexts(datasource_context)
    dialect_plan = resolve_sql_dialect_plan(datasource_contexts)
    logger.info(
        "Starting SQL request: question=%r datasource_id=%r datasource_ids=%r "
        "resolved_datasources=%r prompt_dialect=%r parser_dialect=%r",
        effective_request.question,
        effective_request.datasource_id,
        effective_request.datasource_ids,
        [
            {
                "id": connector.id,
                "key": connector.connector_key,
                "database_type": connector.database_type,
                "sql_dialect": connector.sql_dialect,
            }
            for connector, _cache in datasource_contexts
        ],
        dialect_plan.prompt_dialect,
        dialect_plan.sqlglot_read_dialect,
    )
    extra_metadata = {
        **extra_metadata,
        "llm_sql_language": dialect_plan.prompt_dialect,
    }
    learning_connector_id = datasource_contexts[0][0].id if len(datasource_contexts) == 1 else None
    intent_mode = resolve_intent_classification_mode()
    intent_llm_config = get_llm_runtime_config_safe() if intent_mode == "llm" else None
    try:
        intent = create_intent_classifier(intent_llm_config).classify(effective_request)
    except LlmProviderError as exc:
        audit_log = record_data_query_pipeline_error_audit(
            request=effective_request,
            sql="",
            error_code=exc.code,
            error_message=exc.message,
            error_detail=exc.message,
            pipeline_phase="intent_classification",
            metadata={**extra_metadata, "intent_classification_mode": intent_mode},
        )
        learn_business_logic_from_sql_error(
            connector_id=learning_connector_id,
            audit_id=audit_log.id if audit_log is not None else None,
        )
        raise

    current_intent_metadata = intent_metadata(intent, intent_mode)
    audit_metadata = {**current_intent_metadata, **extra_metadata}

    if not is_read_only_intent(intent):
        response = build_access_refusal_response(
            effective_request,
            ACCESS_ERROR_INTENT_CLASSIFICATION,
            metadata=audit_metadata,
        )
        audit_log = record_data_query_access_error_audit(
            request=effective_request,
            answer=response.answer,
            reason=ACCESS_ERROR_INTENT_CLASSIFICATION,
            metadata=audit_metadata,
        )
        if audit_log is not None:
            response.metadata["data_query_audit_id"] = audit_log.id
        return response
    pipeline = create_pipeline(
        datasource_context,
        interpret=effective_request.interpret,
        enterprise_access=enterprise_access,
    )
    try:
        # Keep the regular endpoint compatible with pipelines that do not
        # implement streaming progress callbacks (including integrations).
        if on_stage is None:
            response = pipeline.handle(effective_request)
        else:
            response = pipeline.handle(effective_request, on_stage=on_stage)
    except QueryExecutionError as exc:
        detected_datasource_ids = detect_datasource_ids_from_sql(
            exc.sql,
            datasource_contexts,
            dialect_plan,
        )
        tableless_sql = is_tableless_sql(exc.sql, dialect_plan)
        audit_request = request_for_detected_datasources(
            effective_request,
            detected_datasource_ids,
            tableless_sql=tableless_sql,
        )
        audit_log = record_data_query_sql_error_audit(
            request=audit_request,
            sql=exc.sql,
            error_code=exc.code,
            error_message=exc.message,
            error_detail=exc.error_detail,
            metadata=metadata_for_detected_datasources(
                extra_metadata,
                detected_datasource_ids,
                tableless_sql=tableless_sql,
            ),
        )
        learn_business_logic_from_sql_error(
            connector_id=learning_connector_id,
            audit_id=audit_log.id if audit_log is not None else None,
        )
        raise
    except QueryPipelineStepError as exc:
        detected_datasource_ids = detect_datasource_ids_from_sql(
            exc.sql,
            datasource_contexts,
            dialect_plan,
        )
        tableless_sql = is_tableless_sql(exc.sql, dialect_plan)
        audit_request = request_for_detected_datasources(
            effective_request,
            detected_datasource_ids,
            tableless_sql=tableless_sql,
        )
        audit_log = record_data_query_pipeline_error_audit(
            request=audit_request,
            sql=exc.sql,
            error_code=exc.code,
            error_message=exc.message,
            error_detail=exc.error_detail,
            pipeline_phase=exc.phase,
            metadata=metadata_for_detected_datasources(
                audit_metadata,
                detected_datasource_ids,
                tableless_sql=tableless_sql,
            ),
        )
        learn_business_logic_from_sql_error(
            connector_id=learning_connector_id,
            audit_id=audit_log.id if audit_log is not None else None,
        )
        raise
    except SqlValidationError as exc:
        validation_metadata = validation_error_metadata(exc)
        validation_sql = extract_sql_from_validation_error(exc.message)
        detected_datasource_ids = detect_datasource_ids_from_sql(
            validation_sql,
            datasource_contexts,
            dialect_plan,
        )
        tableless_sql = is_tableless_sql(validation_sql, dialect_plan)
        access_metadata = metadata_for_detected_datasources(
            {**audit_metadata, **validation_metadata},
            detected_datasource_ids,
            tableless_sql=tableless_sql,
        )
        audit_request = request_for_detected_datasources(
            effective_request,
            detected_datasource_ids,
            tableless_sql=tableless_sql,
        )
        response = build_access_refusal_response(
            effective_request,
            ACCESS_ERROR_SQL_VALIDATION,
            sql=validation_sql,
            metadata=access_metadata,
        )
        audit_log = record_data_query_access_error_audit(
            request=audit_request,
            answer=response.answer,
            reason=ACCESS_ERROR_SQL_VALIDATION,
            sql=response.sql,
            error_code=exc.code,
            error_detail=exc.message,
            metadata=access_metadata,
        )
        if audit_log is not None:
            response.metadata["data_query_audit_id"] = audit_log.id
        learn_business_logic_from_sql_error(
            connector_id=learning_connector_id,
            audit_id=audit_log.id if audit_log is not None else None,
        )
        return response

    detected_datasource_ids = detect_datasource_ids_from_sql(
        response.sql,
        datasource_contexts,
        dialect_plan,
    )
    tableless_sql = is_tableless_sql(response.sql, dialect_plan)
    response.metadata = metadata_for_detected_datasources(
        response.metadata,
        detected_datasource_ids,
        tableless_sql=tableless_sql,
    )
    response.metadata.update(current_intent_metadata)
    response.metadata.update(
        metadata_for_detected_datasources(
            extra_metadata,
            detected_datasource_ids,
            tableless_sql=tableless_sql,
        )
    )
    audit_request = request_for_detected_datasources(
        effective_request,
        detected_datasource_ids,
        tableless_sql=tableless_sql,
    )
    audit_log = record_data_query_audit(audit_request, response)
    if audit_log is not None:
        response.metadata["data_query_audit_id"] = audit_log.id

    return response


def effective_query_request(
    request: QueryRequest,
    principal: AuthenticatedSession | None = None,
) -> tuple[QueryRequest, DatasourceContexts]:
    effective_context = get_query_hook_registry().resolve_effective_query_context(request)
    contexts = get_query_hook_registry().filter_datasource_contexts(
        principal,
        effective_context.datasource_contexts,
    )
    logger.info(
        "Resolved query datasource context: requested_datasource_id=%r "
        "requested_datasource_ids=%r effective_datasource_id=%r "
        "effective_datasource_ids=%r resolved_datasources=%r",
        request.datasource_id,
        request.datasource_ids,
        effective_context.request.datasource_id,
        effective_context.request.datasource_ids,
        [
            {
                "id": connector.id,
                "key": connector.connector_key,
                "database_type": connector.database_type,
                "sql_dialect": connector.sql_dialect,
            }
            for connector, _cache in contexts
        ],
    )
    license_service.ensure_datasource_contexts_allowed(
        contexts,
        enterprise_access=(
            principal is None
            or principal.user.enterprise_access
            or principal.user.role == "admin"
        ),
    )
    return effective_context.request, contexts


def conversation_principal(user: AuthenticatedSession) -> ConversationPrincipal:
    return ConversationPrincipal(
        owner_user_id=str(user.user.id),
        owner_username=user.user.username,
    )


def resolve_request_conversation(
    request: QueryRequest,
    principal: ConversationPrincipal,
) -> tuple[Conversation | None, ConversationContextClassification, QueryRequest | None]:
    if request.context_mode == ContextMode.OFF:
        return None, new_topic_classification(request.question), request

    if request.context_mode != ContextMode.NEW and request.conversation_id:
        conversation = ensure_existing_conversation(request.conversation_id, principal)
    else:
        conversation = ensure_conversation(
            principal,
            request,
            force_new=request.context_mode == ContextMode.NEW,
        )

    if request.context_mode == ContextMode.NEW:
        classification = new_topic_classification(request.question)
        return (
            conversation,
            classification,
            request.model_copy(
                update={
                    "question": request.question,
                    "conversation_id": conversation.conversation_id,
                }
            ),
        )

    context = build_compact_conversation_context(conversation.conversation_id)
    if not context.get("turns"):
        classification = new_topic_classification(request.question)
        return (
            conversation,
            classification,
            request.model_copy(
                update={
                    "question": request.question,
                    "conversation_id": conversation.conversation_id,
                }
            ),
        )

    context_mode = resolve_intent_classification_mode()
    if context_mode != "llm":
        deterministic = deterministic_follow_up_classification(request, context)
        if deterministic is not None:
            return (
                conversation,
                deterministic,
                request.model_copy(
                    update={
                        "question": deterministic.standalone_question,
                        "conversation_id": conversation.conversation_id,
                    }
                ),
            )

    llm_config = get_llm_runtime_config_safe() if context_mode == "llm" else None
    classification = create_conversation_context_classifier(llm_config).classify(
        request,
        context,
    )
    if classification.decision == ConversationContextDecision.AMBIGUOUS:
        return conversation, classification, None

    standalone_question = (
        classification.standalone_question.strip()
        if classification.decision == ConversationContextDecision.FOLLOW_UP
        else request.question
    )
    if classification.decision == ConversationContextDecision.FOLLOW_UP:
        guard_rewrite = guard_follow_up_rewrite(
            request.question,
            standalone_question,
            context,
            classification,
            allow_deterministic_fallback=context_mode != "llm",
        )
        if guard_rewrite is None:
            classification = classification.model_copy(
                update={
                    "decision": ConversationContextDecision.AMBIGUOUS,
                    "standalone_question": "",
                    "reason": (
                        classification.reason
                        or "Follow-up was not rewritten with enough prior context."
                    ),
                }
            )
            return conversation, classification, None
        standalone_question = guard_rewrite

    if not standalone_question:
        classification = classification.model_copy(
            update={
                "decision": ConversationContextDecision.AMBIGUOUS,
                "reason": classification.reason or "Missing standalone follow-up question.",
            }
        )
        return conversation, classification, None

    classification = classification.model_copy(update={"standalone_question": standalone_question})
    return (
        conversation,
        classification,
        request.model_copy(
            update={
                "question": standalone_question,
                "conversation_id": conversation.conversation_id,
            }
        ),
    )


def deterministic_follow_up_classification(
    request: QueryRequest,
    context: dict[str, Any],
) -> ConversationContextClassification | None:
    question = normalize_question_text(request.question)
    previous = latest_completed_turn(context)
    if previous is None:
        return None

    previous_question = previous_scope_question(previous)
    if not previous_question:
        return None

    projected_result = rewrite_previous_result_projection(
        original_question=request.question,
        normalized_question=question,
        previous=previous,
        previous_question=previous_question,
    )
    if projected_result:
        return ConversationContextClassification(
            decision=ConversationContextDecision.FOLLOW_UP,
            confidence=0.94,
            standalone_question=projected_result["standalone_question"],
            reason=(
                "Deterministic rewrite: preserve the previous result set filters "
                "and change only the requested output fields."
            ),
            model_response=projected_result,
            source="deterministic",
        )

    if is_open_only_command(question):
        filter_rewrite = rewrite_filter_follow_up(
            previous,
            previous_question,
            filter_instruction="ogranicz do otwartych",
        )
        return ConversationContextClassification(
            decision=ConversationContextDecision.FOLLOW_UP,
            confidence=0.98,
            standalone_question=filter_rewrite["standalone_question"],
            reason="Deterministic rewrite: preserve previous topic and add open-status filter.",
            model_response=filter_rewrite,
            source="deterministic",
        )

    previous_period = rewrite_previous_period_follow_up(previous, previous_question, question)
    if previous_period:
        return ConversationContextClassification(
            decision=ConversationContextDecision.FOLLOW_UP,
            confidence=0.96,
            standalone_question=previous_period["standalone_question"],
            reason="Deterministic rewrite: replace current time period with previous period.",
            model_response=previous_period,
            source="deterministic",
        )

    return None


def guard_follow_up_rewrite(
    original_question: str,
    standalone_question: str,
    context: dict[str, Any],
    classification: ConversationContextClassification,
    *,
    allow_deterministic_fallback: bool,
) -> str | None:
    original = normalize_question_text(original_question)
    standalone = normalize_question_text(standalone_question)
    if not standalone:
        return None

    previous = latest_completed_turn(context)
    previous_question = (
        str(previous.get("standalone_question") or previous.get("question") or "")
        if previous
        else ""
    )

    if rewrite_is_too_close_to_original(original, standalone):
        if classification.model_response.get("current_question_is_standalone") is True:
            return original_question.strip()
        if not allow_deterministic_fallback:
            return None
        deterministic = deterministic_follow_up_classification(
            QueryRequest(question=original_question),
            context,
        )
        if deterministic is not None:
            return deterministic.standalone_question
        return None

    if is_open_only_command(original) and not carries_previous_context(
        standalone, previous_question
    ):
        if not allow_deterministic_fallback:
            return None
        deterministic = deterministic_follow_up_classification(
            QueryRequest(question=original_question),
            context,
        )
        if deterministic is not None:
            return deterministic.standalone_question
        return None

    return standalone_question.strip()


def rewrite_previous_result_projection(
    *,
    original_question: str,
    normalized_question: str,
    previous: dict[str, Any],
    previous_question: str,
) -> dict[str, Any]:
    if not asks_for_previous_result_details(normalized_question):
        return {}

    if has_singular_result_reference(normalized_question) and not previous_result_is_single(
        previous
    ):
        return {}

    projection_instruction = normalize_projection_instruction(original_question)
    if not projection_instruction:
        return {}

    previous_scope = previous_question.strip().rstrip(".?")
    if not previous_scope:
        return {}

    return projection_rewrite_payload(
        rule="previous_result_projection",
        base_question=previous_scope,
        projection_instruction=projection_instruction,
        reference_sql=str(previous.get("sql") or ""),
    )


def rewrite_filter_follow_up(
    previous: dict[str, Any],
    previous_question: str,
    *,
    filter_instruction: str,
) -> dict[str, Any]:
    projection_instruction = previous_projection_instruction(previous)
    filtered_question = append_filter_once(previous_question, filter_instruction).rstrip("?")
    if projection_instruction:
        return projection_rewrite_payload(
            rule="open_only_filter",
            base_question=filtered_question,
            projection_instruction=projection_instruction,
            reference_sql=str(previous.get("sql") or ""),
        )

    return {
        "rule": "open_only_filter",
        "base_question": filtered_question,
        "standalone_question": f"{filtered_question}?",
    }


def rewrite_previous_period_follow_up(
    previous: dict[str, Any],
    previous_question: str,
    normalized_question: str,
) -> dict[str, Any]:
    shifted_question = resolve_previous_period(normalized_question, previous_question)
    if not shifted_question:
        return {}

    projection_instruction = previous_projection_instruction(previous)
    if projection_instruction:
        return projection_rewrite_payload(
            rule="previous_period",
            base_question=shifted_question,
            projection_instruction=projection_instruction,
            reference_sql=str(previous.get("sql") or ""),
        )

    return {
        "rule": "previous_period",
        "base_question": shifted_question,
        "standalone_question": shifted_question,
    }


def projection_rewrite_payload(
    *,
    rule: str,
    base_question: str,
    projection_instruction: str,
    reference_sql: str,
) -> dict[str, Any]:
    base = base_question.strip().rstrip(".?")
    projection = projection_instruction.strip().rstrip(".?")
    standalone_parts = [
        (f"Zachowaj ten sam zestaw rekordów opisany przez pytanie bazowe: {base}."),
    ]
    if reference_sql.strip():
        standalone_parts.append(
            "Użyj poprzedniego SQL jako referencji dla JOIN i WHERE, bez kopiowania "
            f"niepotrzebnych kolumn SELECT:\n{reference_sql.strip()}"
        )
    standalone_parts.append(f"Zmień tylko zwracane kolumny/SELECT tak, aby zwrócić: {projection}.")
    standalone_parts.append(
        "Nie używaj bind-parametrów ani placeholderów typu :name, ?, $1 lub @name; "
        "SQL musi być wykonywalny bez podstawiania parametrów."
    )

    return {
        "rule": rule,
        "base_question": base,
        "projection_instruction": projection,
        "uses_reference_sql": bool(reference_sql.strip()),
        "standalone_question": "\n".join(standalone_parts),
    }


def previous_scope_question(previous: dict[str, Any]) -> str:
    model_response = previous_context_model_response(previous)
    base_question = str(model_response.get("base_question") or "").strip()
    if base_question:
        return base_question

    standalone = str(previous.get("standalone_question") or previous.get("question") or "")
    parsed = parse_projection_rewrite(standalone)
    if parsed:
        return parsed["base_question"]

    return standalone.strip()


def previous_projection_instruction(previous: dict[str, Any]) -> str:
    model_response = previous_context_model_response(previous)
    projection_instruction = str(model_response.get("projection_instruction") or "").strip()
    if projection_instruction:
        return projection_instruction

    standalone = str(previous.get("standalone_question") or "")
    parsed = parse_projection_rewrite(standalone)
    if parsed:
        return parsed["projection_instruction"]

    return ""


def previous_context_model_response(previous: dict[str, Any]) -> dict[str, Any]:
    model_response = previous.get("context_model_response")
    return model_response if isinstance(model_response, dict) else {}


def parse_projection_rewrite(value: str, depth: int = 0) -> dict[str, str]:
    if depth > 3:
        return {}

    compact = value.strip()
    if not compact:
        return {}

    new_format = re.search(
        r"Zachowaj ten sam zestaw rekordów opisany przez pytanie bazowe:\s*"
        r"(?P<base>.*?)\.\s*"
        r"(?:Użyj poprzedniego SQL.*?\s*)?"
        r"Zmień tylko zwracane kolumny/SELECT tak, aby zwrócić:\s*"
        r"(?P<projection>.*?)\.\s*(?:Nie używaj|$)",
        compact,
        flags=re.DOTALL,
    )
    if new_format:
        base = " ".join(new_format.group("base").split()).strip()
        projection = " ".join(new_format.group("projection").split()).strip()
        nested = parse_projection_rewrite(base, depth + 1)
        if nested:
            base = nested["base_question"]
            base = move_projection_filters_to_base(
                base,
                nested["projection_instruction"],
            )
        base = move_projection_filters_to_base(base, projection)
        return {
            "base_question": base,
            "projection_instruction": projection,
        }

    old_format = re.search(
        r'Użyj tych samych filtrów, zakresu czasu i źródła danych co w pytaniu:\s*"'
        r'(?P<base>.*)"\.\s*Zwróć dla tego samego zestawu rekordów:\s*'
        r"(?P<projection>.*?)\.?$",
        compact,
        flags=re.DOTALL,
    )
    if old_format:
        base = " ".join(old_format.group("base").split()).strip()
        projection = " ".join(old_format.group("projection").split()).strip()
        nested = parse_projection_rewrite(base, depth + 1)
        if nested:
            base = nested["base_question"]
            base = move_projection_filters_to_base(
                base,
                nested["projection_instruction"],
            )
        base = move_projection_filters_to_base(base, projection)
        return {
            "base_question": base,
            "projection_instruction": projection.rstrip(".?"),
        }

    return {}


def move_projection_filters_to_base(base_question: str, projection_instruction: str) -> str:
    normalized_projection = normalize_question_text(projection_instruction)
    if "otwart" in normalized_projection and "otwart" not in normalize_question_text(base_question):
        return append_filter_once(base_question, "ogranicz do otwartych").rstrip("?")

    return base_question


def asks_for_previous_result_details(normalized_question: str) -> bool:
    if not normalized_question:
        return False

    if has_result_reference(normalized_question) and has_projection_signal(normalized_question):
        return True

    return (
        has_projection_command(normalized_question)
        and has_detail_projection_signal(normalized_question)
        and not introduces_new_scope(normalized_question)
    )


def has_result_reference(normalized_question: str) -> bool:
    tokens = normalized_tokens(normalized_question)
    reference_tokens = {
        "ich",
        "nich",
        "te",
        "tej",
        "tego",
        "tym",
        "tych",
        "tamte",
        "tamtych",
        "same",
        "samego",
        "samych",
        "their",
        "them",
        "these",
        "those",
        "this",
        "that",
        "it",
    }
    if tokens & reference_tokens:
        return True

    reference_phrases = (
        "tych samych",
        "ten sam",
        "ta sama",
        "to samo",
        "same records",
        "same rows",
        "same result",
        "previous result",
        "poprzedni wynik",
        "poprzedniego wyniku",
    )
    return any(phrase in normalized_question for phrase in reference_phrases)


def has_singular_result_reference(normalized_question: str) -> bool:
    tokens = normalized_tokens(normalized_question)
    return bool(tokens & {"tej", "tego", "tym", "this", "that", "it"})


def previous_result_is_single(previous: dict[str, Any]) -> bool:
    result_summary = previous.get("result_summary")
    if not isinstance(result_summary, dict):
        return False

    scalar_count = result_summary.get("scalar_count")
    if isinstance(scalar_count, (int, float)) and not isinstance(scalar_count, bool):
        return scalar_count == 1

    row_count = result_summary.get("row_count")
    if isinstance(row_count, int):
        return row_count == 1

    return False


def has_projection_signal(normalized_question: str) -> bool:
    return has_projection_command(normalized_question) or has_detail_projection_signal(
        normalized_question
    )


def has_projection_command(normalized_question: str) -> bool:
    command_prefixes = (
        "daj ",
        "podaj ",
        "pokaż ",
        "pokaz ",
        "wypisz ",
        "wyświetl ",
        "wyswietl ",
        "zwróć ",
        "zwroc ",
        "lista ",
        "show ",
        "list ",
        "give ",
        "return ",
        "display ",
    )
    return normalized_question.startswith(command_prefixes)


def has_detail_projection_signal(normalized_question: str) -> bool:
    projection_terms = (
        "opis",
        "opisy",
        "szczegół",
        "szczegol",
        "szczegóły",
        "szczegoly",
        "nazwa",
        "nazwy",
        "status",
        "id",
        "identyfikator",
        "kolumn",
        "atrybut",
        "pole",
        "pola",
        "wartość",
        "wartosc",
        "wartości",
        "wartosci",
        "detail",
        "details",
        "description",
        "descriptions",
        "name",
        "names",
        "field",
        "fields",
        "column",
        "columns",
        "value",
        "values",
    )
    return any(term in normalized_question for term in projection_terms)


def introduces_new_scope(normalized_question: str) -> bool:
    scope_terms = (
        " w tym ",
        " w poprzednim ",
        " w przyszłym ",
        " w przyszlym ",
        " dzisiaj",
        " wczoraj",
        " jutro",
        " ostatni",
        " ostatnie",
        " ostatnich",
        " region",
        " kraju",
        " mieście",
        " miescie",
        " status ",
        " filtr",
        " gdzie ",
        " from ",
        " before ",
        " after ",
        " between ",
        " where ",
        " region",
        " city",
        " country",
        " status ",
        " filter",
    )
    padded = f" {normalized_question} "
    return any(term in padded for term in scope_terms)


def normalize_projection_instruction(question: str) -> str:
    compact = question.strip().rstrip(".?")
    compact = re.sub(
        r"\b(ich|nich|tych|te|tej|tego|tym|tamte|tamtych|their|them|these|those|this|that|it)\b",
        "",
        compact,
        flags=re.IGNORECASE,
    )
    compact = re.sub(
        r"\b(tych\s+samych|same\s+records|same\s+rows)\b", "", compact, flags=re.IGNORECASE
    )
    compact = " ".join(compact.split()).strip(" ,;:")
    return compact or question.strip().rstrip(".?")


def normalized_tokens(value: str) -> set[str]:
    return set(re.findall(r"[\wąćęłńóśźż]+", value.lower()))


def latest_completed_turn(context: dict[str, Any]) -> dict[str, Any] | None:
    for turn in reversed(context.get("turns") or []):
        if isinstance(turn, dict):
            return turn
    return None


def normalize_question_text(value: str) -> str:
    return " ".join(value.lower().strip().rstrip("?.!").split())


def is_open_only_command(normalized_question: str) -> bool:
    return normalized_question in {
        "pokaż tylko otwarte",
        "pokaz tylko otwarte",
        "tylko otwarte",
        "ogranicz do otwartych",
        "ogranicz status do otwartych",
        "ogranicz do spraw otwartych",
        "pokaż otwarte",
        "pokaz otwarte",
    }


def append_filter_once(previous_question: str, filter_text: str) -> str:
    compact = previous_question.strip().rstrip(".?")
    normalized = normalize_question_text(compact)
    if "otwart" in normalized:
        return f"{compact}?"
    return f"{compact}, {filter_text}?"


def resolve_previous_period(
    normalized_question: str,
    previous_question: str,
) -> str:
    if normalized_question not in {
        "a w poprzednim",
        "w poprzednim",
        "a poprzedni",
        "poprzedni",
        "a w poprzednim?",
    }:
        return ""

    replacements = [
        (r"\bw tym tygodniu\b", "w poprzednim tygodniu"),
        (r"\bbieżącym tygodniu\b", "poprzednim tygodniu"),
        (r"\btym miesiącu\b", "poprzednim miesiącu"),
        (r"\bbieżącym miesiącu\b", "poprzednim miesiącu"),
        (r"\bdzisiaj\b", "wczoraj"),
    ]
    for pattern, replacement in replacements:
        if re.search(pattern, previous_question, flags=re.IGNORECASE):
            return re.sub(pattern, replacement, previous_question, flags=re.IGNORECASE)

    return ""


def rewrite_is_too_close_to_original(original: str, standalone: str) -> bool:
    if standalone == original:
        return True
    if len(standalone.split()) <= max(3, len(original.split()) + 1):
        return True
    return False


def carries_previous_context(standalone: str, previous_question: str) -> bool:
    previous_tokens = {
        token
        for token in re.findall(r"[\wąćęłńóśźż]+", previous_question.lower())
        if len(token) >= 5
    }
    standalone_tokens = set(re.findall(r"[\wąćęłńóśźż]+", standalone.lower()))
    return bool(previous_tokens & standalone_tokens)


def ensure_existing_conversation(
    conversation_id: str,
    principal: ConversationPrincipal,
):
    loaded = load_conversation_for_owner(conversation_id, principal)
    if loaded is not None:
        return loaded
    if conversation_exists(conversation_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conversation belongs to another user.",
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Conversation was not found.",
    )


def add_conversation_to_response(
    response: QueryResponse,
    *,
    conversation,
    classification: ConversationContextClassification,
    original_request: QueryRequest,
    effective_request: QueryRequest,
    mode: str,
    status_value: str = "completed",
) -> QueryResponse:
    response.question = original_request.question
    audit_id = response.metadata.get("data_query_audit_id")
    turn = record_conversation_turn(
        conversation,
        mode=mode,
        original_question=original_request.question,
        standalone_question=effective_request.question,
        answer=response.answer,
        sql=response.sql,
        metadata=response.metadata,
        context_classification=classification,
        rows=response.rows,
        status=status_value,
        data_query_audit_id=audit_id if isinstance(audit_id, int) else None,
        analysis_session_id=str(response.metadata.get("analysis_session_id") or ""),
    )
    response.metadata["conversation"] = build_conversation_metadata(
        conversation,
        turn,
        classification,
    )
    return response


def ndjson_line(payload: dict[str, Any]) -> str:
    return f"{json.dumps(payload, ensure_ascii=False)}\n"


@router.post("/query", response_model=QueryResponse)
def query(
    request: QueryRequest,
    _user: AuthenticatedSession = Depends(get_current_enterprise_api_user),
) -> QueryResponse:
    effective_request, datasource_context = effective_query_request(request, _user)
    conversation, context_classification, query_request = resolve_request_conversation(
        effective_request,
        conversation_principal(_user),
    )
    if conversation is not None and query_request is None:
        return ambiguous_context_response(
            effective_request,
            conversation,
            context_classification,
        )
    query_request = query_request or effective_request
    active_datasource_ids = [connector.connector_key for connector, _cache in datasource_context]
    if not active_datasource_ids:
        response = build_no_active_datasources_response(query_request)
        if conversation is not None:
            return add_conversation_to_response(
                response,
                conversation=conversation,
                classification=context_classification,
                original_request=effective_request,
                effective_request=query_request,
                mode="sql",
                status_value="blocked",
            )
        return response

    response = run_sql_request(
        query_request,
        datasource_context,
        {"active_datasource_ids": active_datasource_ids} if active_datasource_ids else None,
        enterprise_access=_user.user.enterprise_access or _user.user.role == "admin",
    )
    if conversation is not None:
        return add_conversation_to_response(
            response,
            conversation=conversation,
            classification=context_classification,
            original_request=effective_request,
            effective_request=query_request,
            mode="sql",
        )
    return response


@router.post("/query/explain", response_model=QueryAnswerExplanationResponse)
def query_explain(
    request: QueryAnswerExplanationRequest,
    _user: AuthenticatedSession = Depends(get_current_enterprise_api_user),
) -> QueryAnswerExplanationResponse:
    return explain_query_answer(request)


@router.post("/query/stream")
def query_stream(
    request: QueryRequest,
    _user: AuthenticatedSession = Depends(get_current_enterprise_api_user),
) -> StreamingResponse:
    def single_response() -> Iterator[str]:
        # Every stream event has a ``final`` key so consumers can safely
        # distinguish progress events from the terminal response.
        yield ndjson_line({"stage": "processing_query", "final": None})
        events: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def run_query() -> None:
            try:
                effective_request, datasource_context = effective_query_request(request, _user)
                conversation, context_classification, query_request = resolve_request_conversation(
                    effective_request,
                    conversation_principal(_user),
                )
                active_datasource_ids = [
                    connector.connector_key for connector, _cache in datasource_context
                ]
                extra_metadata = (
                    {"active_datasource_ids": active_datasource_ids}
                    if active_datasource_ids
                    else None
                )

                if conversation is not None and query_request is None:
                    events.put({
                        "final": ambiguous_context_response(
                            effective_request,
                            conversation,
                            context_classification,
                        ).model_dump(mode="json")
                    })
                    return

                resolved_request = query_request or effective_request
                if not active_datasource_ids:
                    response = build_no_active_datasources_response(resolved_request)
                    if conversation is not None:
                        response = add_conversation_to_response(
                            response,
                            conversation=conversation,
                            classification=context_classification,
                            original_request=effective_request,
                            effective_request=resolved_request,
                            mode="sql",
                            status_value="blocked",
                        )
                    events.put({"final": response.model_dump(mode="json")})
                    return

                response = run_sql_request(
                    resolved_request,
                    datasource_context,
                    extra_metadata,
                    on_stage=lambda stage: events.put({"stage": stage, "final": None}),
                    enterprise_access=_user.user.enterprise_access or _user.user.role == "admin",
                )
                if conversation is not None:
                    response = add_conversation_to_response(
                        response,
                        conversation=conversation,
                        classification=context_classification,
                        original_request=effective_request,
                        effective_request=resolved_request,
                        mode="sql",
                    )
                events.put({"final": response.model_dump(mode="json")})
            except Exception as exc:
                events.put({"error": {"code": "QUERY_FAILED", "message": str(exc)}})
            finally:
                events.put(None)

        threading.Thread(target=run_query, daemon=True).start()
        while True:
            event = events.get()
            if event is None:
                return
            yield ndjson_line(event)

    return StreamingResponse(
        single_response(),
        media_type="application/x-ndjson",
    )
