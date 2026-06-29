import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from gaard_core.errors import (
    ConfigurationError,
    LlmProviderError,
    QueryExecutionError,
    QueryPipelineStepError,
    SqlValidationError,
)
from gaard_core.query_intent.llm_classifier import LlmQueryIntentClassifier
from gaard_core.query_intent.mock_classifier import MockQueryIntentClassifier
from gaard_core.query_pipeline.llm_sql_generator import LlmSqlGenerator
from gaard_core.query_pipeline.mock_sql_generator import MockSqlGenerator
from gaard_core.query_pipeline.models import (
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
from gaard_core.schema.context import SchemaContextService
from gaard_core.sql_validator.select_only import SelectOnlySqlValidator
from gaard_llm.openai_compatible.client import OpenAICompatibleClient

from gaard_api.admin.models import DatasourceConnector, DatasourceSchemaCache
from gaard_api.admin.prompt_runtime import (
    get_intent_classification_prompt_compiler,
    get_result_classification_prompt_compiler,
    get_result_interpretation_prompt_compiler,
    get_sql_generation_prompt_compiler,
)
from gaard_api.admin.services import (
    ACCESS_ERROR_INTENT_CLASSIFICATION,
    ACCESS_ERROR_SQL_VALIDATION,
    get_active_business_logic_prompt_safe,
    get_datasource_schema_context_safe,
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
from gaard_api.api.v1.schema import get_schema_cache_key
from gaard_api.core.schema_cache import schema_context_cache
from gaard_api.core.settings import settings
from gaard_api.extensions import get_connector_registry

router = APIRouter()

DatasourceContext = tuple[DatasourceConnector, DatasourceSchemaCache]

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

VALIDATION_SQL_PREFIXES = (
    "Only SELECT queries are allowed. ",
    "DDL and DML statements are not allowed. ",
    "Only single-statement SQL queries are allowed. SQL: ",
    "Invalid SQL syntax. ",
)


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


def create_sql_generator(
    datasource_context: DatasourceContext | None = None,
    llm_config: LlmRuntimeConfig | None = None,
    runtime_config: QueryRuntimeConfig | None = None,
) -> MockSqlGenerator | LlmSqlGenerator:
    runtime_config = runtime_config or get_query_runtime_config_safe()

    if runtime_config.sql_generation_mode == "mock":
        return MockSqlGenerator()

    if runtime_config.sql_generation_mode == "llm":
        llm_config = llm_config or get_llm_runtime_config_safe()

        if datasource_context is None:
            datasource_context = get_datasource_schema_context_safe()

        if datasource_context is not None:
            connector, schema_cache = datasource_context
            formatted_schema = append_business_logic_to_schema(
                schema_cache.formatted_schema,
                connector.id,
            )

            return LlmSqlGenerator(
                client=create_llm_client(llm_config),
                model=llm_config.model,
                formatted_schema=formatted_schema,
                dialect=connector.sql_dialect,
                max_rows=runtime_config.query_max_rows,
                extra_body=llm_config.extra_body,
                prompt_compiler=get_sql_generation_prompt_compiler(),
            )

        introspector = (
            get_connector_registry()
            .detect_from_database_url(settings.gaard_datasource_url)
            .introspector_factory(settings.gaard_datasource_url)
        )

        schema_context_service = SchemaContextService(
            introspector=introspector,
            cache=schema_context_cache,
        )

        schema_context = schema_context_service.get_schema_context(get_schema_cache_key())

        return LlmSqlGenerator(
            client=create_llm_client(llm_config),
            model=llm_config.model,
            formatted_schema=schema_context.formatted_schema,
            dialect=settings.gaard_sql_dialect,
            max_rows=runtime_config.query_max_rows,
            extra_body=llm_config.extra_body,
            prompt_compiler=get_sql_generation_prompt_compiler(),
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
    datasource_context: DatasourceContext | None = None,
    interpret: bool = True,
) -> QueryPipeline:
    if datasource_context is None:
        datasource_context = get_datasource_schema_context_safe()

    runtime_config = get_query_runtime_config_safe()
    database_url = (
        datasource_context[0].database_url
        if datasource_context is not None
        else settings.gaard_datasource_url
    )
    sql_dialect = (
        datasource_context[0].sql_dialect
        if datasource_context is not None
        else settings.gaard_sql_dialect
    )

    connector_definition = (
        get_connector_registry().get(datasource_context[0].database_type)
        if datasource_context is not None
        else get_connector_registry().detect_from_database_url(database_url)
    )
    executor = connector_definition.executor_factory(
        database_url,
        runtime_config.query_max_rows,
    )
    output_classification_mode = resolve_output_classification_mode(runtime_config)
    llm_modes = {runtime_config.sql_generation_mode}
    if interpret:
        llm_modes.add(runtime_config.result_interpretation_mode)
        llm_modes.add(output_classification_mode)
    llm_config = get_llm_runtime_config_safe() if "llm" in llm_modes else None

    return QueryPipeline(
        sql_generator=create_sql_generator(datasource_context, llm_config, runtime_config),
        sql_validator=SelectOnlySqlValidator(dialect=sql_dialect),
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


def append_business_logic_to_schema(formatted_schema: str, connector_id: int) -> str:
    business_logic = get_active_business_logic_prompt_safe(connector_id)

    if not business_logic:
        return formatted_schema

    return f"{formatted_schema}\n\n{business_logic}"


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
    datasource_context: DatasourceContext | None,
    extra_metadata: dict[str, Any] | None = None,
) -> QueryResponse:
    extra_metadata = extra_metadata or {}
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
            connector_id=datasource_context[0].id if datasource_context is not None else None,
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

    pipeline = create_pipeline(datasource_context, interpret=effective_request.interpret)
    try:
        response = pipeline.handle(effective_request)
    except QueryExecutionError as exc:
        audit_log = record_data_query_sql_error_audit(
            request=effective_request,
            sql=exc.sql,
            error_code=exc.code,
            error_message=exc.message,
            error_detail=exc.error_detail,
            metadata=extra_metadata,
        )
        learn_business_logic_from_sql_error(
            connector_id=datasource_context[0].id if datasource_context is not None else None,
            audit_id=audit_log.id if audit_log is not None else None,
        )
        raise
    except QueryPipelineStepError as exc:
        audit_log = record_data_query_pipeline_error_audit(
            request=effective_request,
            sql=exc.sql,
            error_code=exc.code,
            error_message=exc.message,
            error_detail=exc.error_detail,
            pipeline_phase=exc.phase,
            metadata=audit_metadata,
        )
        learn_business_logic_from_sql_error(
            connector_id=datasource_context[0].id if datasource_context is not None else None,
            audit_id=audit_log.id if audit_log is not None else None,
        )
        raise
    except SqlValidationError as exc:
        validation_metadata = validation_error_metadata(exc)
        response = build_access_refusal_response(
            effective_request,
            ACCESS_ERROR_SQL_VALIDATION,
            sql=extract_sql_from_validation_error(exc.message),
            metadata={**audit_metadata, **validation_metadata},
        )
        audit_log = record_data_query_access_error_audit(
            request=effective_request,
            answer=response.answer,
            reason=ACCESS_ERROR_SQL_VALIDATION,
            sql=response.sql,
            error_code=exc.code,
            error_detail=exc.message,
            metadata={**audit_metadata, **validation_metadata},
        )
        if audit_log is not None:
            response.metadata["data_query_audit_id"] = audit_log.id
        learn_business_logic_from_sql_error(
            connector_id=datasource_context[0].id if datasource_context is not None else None,
            audit_id=audit_log.id if audit_log is not None else None,
        )
        return response

    response.metadata.update(current_intent_metadata)
    response.metadata.update(extra_metadata)
    audit_log = record_data_query_audit(effective_request, response)
    if audit_log is not None:
        response.metadata["data_query_audit_id"] = audit_log.id

    return response


def effective_query_request(request: QueryRequest) -> tuple[QueryRequest, DatasourceContext | None]:
    datasource_context = get_datasource_schema_context_safe()
    effective_request = request

    if datasource_context is not None:
        effective_request = request.model_copy(
            update={"datasource_id": datasource_context[0].connector_key}
        )

    return effective_request, datasource_context


def ndjson_line(payload: dict[str, Any]) -> str:
    return f"{json.dumps(payload, ensure_ascii=False)}\n"


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    effective_request, datasource_context = effective_query_request(request)
    return run_sql_request(effective_request, datasource_context)


@router.post("/query/stream")
def query_stream(request: QueryRequest) -> StreamingResponse:
    effective_request, datasource_context = effective_query_request(request)

    def single_response() -> Iterator[str]:
        yield ndjson_line(
            {
                "final": run_sql_request(effective_request, datasource_context).model_dump(
                    mode="json"
                )
            }
        )

    return StreamingResponse(
        single_response(),
        media_type="application/x-ndjson",
    )
