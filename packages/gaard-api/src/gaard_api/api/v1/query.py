import json
import time
from collections.abc import Iterator
from queue import Queue
from threading import Thread
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from gaard_connectors.sqlalchemy.executor import SQLAlchemyQueryExecutor
from gaard_connectors.sqlalchemy.introspector import SQLAlchemySchemaIntrospector
from gaard_core.errors import (
    ConfigurationError,
    LlmProviderError,
    QueryExecutionError,
    QueryPipelineStepError,
    SqlValidationError,
)
from gaard_core.investigation import (
    InvestigationContext,
    InvestigationLoop,
    InvestigationLoopConfig,
    InvestigationLoopResult,
    InvestigationRoute,
    LlmInvestigationReadinessAgent,
    MockInvestigationReadinessAgent,
    RequiredAnalysisTask,
)
from gaard_core.query_intent.llm_classifier import LlmQueryIntentClassifier
from gaard_core.query_intent.mock_classifier import MockQueryIntentClassifier
from gaard_core.query_pipeline.llm_sql_generator import LlmSqlGenerator
from gaard_core.query_pipeline.mock_sql_generator import MockSqlGenerator
from gaard_core.query_pipeline.models import (
    OutputClassification,
    QueryIntentClassification,
    QueryIntentDecision,
    QueryMode,
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
    get_investigation_readiness_prompt_compiler,
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
    upsert_investigation_analysis_business_logic_suggestion,
)
from gaard_api.api.v1.schema import get_schema_cache_key
from gaard_api.core.schema_cache import schema_context_cache
from gaard_api.core.settings import settings

router = APIRouter()

DatasourceContext = tuple[DatasourceConnector, DatasourceSchemaCache]
ProgressCallback = Callable[[dict[str, Any]], None]

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

CLARIFICATION_REFUSAL_ANSWER = (
    "Potrzebuję doprecyzowania, zanim bezpiecznie rozpocznę tę analizę."
)

ANALYSIS_MODE_PENDING_ANSWER = (
    "Tryb Investigation wymaga dodatkowej analizy przed wygenerowaniem SQL. "
    "Ścieżka Analysis nie jest jeszcze zaimplementowana."
)

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

        introspector = SQLAlchemySchemaIntrospector(
            database_url=settings.gaard_datasource_url,
        )

        schema_context_service = SchemaContextService(
            introspector=introspector,
            cache=schema_context_cache,
        )

        schema_context = schema_context_service.get_schema_context(
            get_schema_cache_key()
        )

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
        "Unsupported GAARD_RESULT_INTERPRETATION_MODE: "
        f"{runtime_config.result_interpretation_mode}"
    )


def resolve_output_classification_mode() -> str:
    runtime_config = get_query_runtime_config_safe()

    if runtime_config.output_classification_mode == "auto":
        return "llm" if runtime_config.result_interpretation_mode == "llm" else "mock"

    return runtime_config.output_classification_mode


def create_result_classifier(
    llm_config: LlmRuntimeConfig | None = None,
    runtime_config: QueryRuntimeConfig | None = None,
) -> MockResultClassifier | LlmResultClassifier:
    runtime_config = runtime_config or get_query_runtime_config_safe()
    output_classification_mode = (
        "llm"
        if runtime_config.output_classification_mode == "auto"
        and runtime_config.result_interpretation_mode == "llm"
        else (
            "mock"
            if runtime_config.output_classification_mode == "auto"
            else runtime_config.output_classification_mode
        )
    )

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
        "Unsupported GAARD_OUTPUT_CLASSIFICATION_MODE: "
        f"{runtime_config.output_classification_mode}"
    )


def create_pipeline(datasource_context: DatasourceContext | None = None) -> QueryPipeline:
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

    executor = SQLAlchemyQueryExecutor(
        database_url=database_url,
        max_rows=runtime_config.query_max_rows,
    )
    llm_config = (
        get_llm_runtime_config_safe()
        if "llm"
        in {
            runtime_config.sql_generation_mode,
            runtime_config.result_interpretation_mode,
            (
                "llm"
                if runtime_config.output_classification_mode == "auto"
                and runtime_config.result_interpretation_mode == "llm"
                else runtime_config.output_classification_mode
            ),
        }
        else None
    )
    output_classification_mode = (
        "llm"
        if runtime_config.output_classification_mode == "auto"
        and runtime_config.result_interpretation_mode == "llm"
        else (
            "mock"
            if runtime_config.output_classification_mode == "auto"
            else runtime_config.output_classification_mode
        )
    )

    return QueryPipeline(
        sql_generator=create_sql_generator(datasource_context, llm_config, runtime_config),
        sql_validator=SelectOnlySqlValidator(dialect=sql_dialect),
        executor=executor,
        interpreter=create_result_interpreter(llm_config, runtime_config),
        classifier=create_result_classifier(llm_config, runtime_config),
        sql_generation_mode=runtime_config.sql_generation_mode,
        result_interpretation_mode=runtime_config.result_interpretation_mode,
        output_classification_mode=output_classification_mode,
    )


def append_business_logic_to_schema(formatted_schema: str, connector_id: int) -> str:
    business_logic = get_active_business_logic_prompt_safe(connector_id)

    if not business_logic:
        return formatted_schema

    return f"{formatted_schema}\n\n{business_logic}"


def schema_and_business_logic_for_investigation(
    datasource_context: DatasourceContext | None,
) -> tuple[str, str]:
    if datasource_context is not None:
        connector, schema_cache = datasource_context
        return (
            schema_cache.formatted_schema,
            get_active_business_logic_prompt_safe(connector.id),
        )

    introspector = SQLAlchemySchemaIntrospector(
        database_url=settings.gaard_datasource_url,
    )
    schema_context_service = SchemaContextService(
        introspector=introspector,
        cache=schema_context_cache,
    )
    schema_context = schema_context_service.get_schema_context(get_schema_cache_key())

    return schema_context.formatted_schema, ""


def create_investigation_context(
    request: QueryRequest,
    datasource_context: DatasourceContext | None,
) -> InvestigationContext:
    formatted_schema, business_logic = schema_and_business_logic_for_investigation(
        datasource_context
    )

    return InvestigationContext(
        question=request.question,
        datasource_id=request.datasource_id,
        user_id=request.user_id,
        formatted_schema=formatted_schema,
        business_logic=business_logic,
    )


def resolve_investigation_mode(runtime_config: QueryRuntimeConfig) -> str:
    if runtime_config.investigation_mode == "auto":
        return "llm" if runtime_config.sql_generation_mode == "llm" else "mock"

    return runtime_config.investigation_mode


def create_investigation_readiness_agent(
    runtime_config: QueryRuntimeConfig | None = None,
    llm_config: LlmRuntimeConfig | None = None,
) -> MockInvestigationReadinessAgent | LlmInvestigationReadinessAgent:
    runtime_config = runtime_config or get_query_runtime_config_safe()
    investigation_mode = resolve_investigation_mode(runtime_config)

    if investigation_mode == "mock":
        return MockInvestigationReadinessAgent()

    if investigation_mode == "llm":
        llm_config = llm_config or get_llm_runtime_config_safe()
        return LlmInvestigationReadinessAgent(
            client=create_llm_client(llm_config),
            model=llm_config.model,
            extra_body=llm_config.extra_body,
            prompt_compiler=get_investigation_readiness_prompt_compiler(),
        )

    raise ConfigurationError(
        f"Unsupported GAARD_INVESTIGATION_MODE: {runtime_config.investigation_mode}"
    )


def investigation_iteration_metadata(result: InvestigationLoopResult) -> list[dict[str, Any]]:
    return [
        {
            "iteration": item.iteration,
            "agent": item.agent,
            **item.decision.model_dump(mode="json"),
        }
        for item in result.iterations
    ]


def investigation_metadata(
    result: InvestigationLoopResult,
    investigation_mode: str,
) -> dict[str, Any]:
    steps = investigation_iteration_metadata(result)
    metadata = {
        "query_mode": QueryMode.INVESTIGATION.value,
        "investigation_backend_status": "readiness_gate_active",
        "investigation_mode": investigation_mode,
        "investigation_route": result.route.value,
        "investigation_loop": {
            "max_iterations": result.max_iterations,
            "iterations_run": len(result.iterations),
            "confidence_threshold": result.confidence_threshold,
        },
        "investigation_steps": steps,
        "investigation_audit_trail": steps,
    }

    if result.route == InvestigationRoute.ANALYSIS:
        metadata["analysis_mode_status"] = "not_implemented"

    return metadata


def required_analysis_tasks_from_result(
    result: InvestigationLoopResult,
) -> list[RequiredAnalysisTask]:
    decision = result.final_decision
    if decision is None:
        return []

    if decision.required_analysis_tasks:
        return [
            task
            for task in decision.required_analysis_tasks
            if task.required_analysis.strip()
        ]

    tasks: list[RequiredAnalysisTask] = []
    for index, required_analysis in enumerate(decision.required_analysis):
        tasks.append(
            RequiredAnalysisTask(
                missing_information=decision.missing_information[index]
                if index < len(decision.missing_information)
                else "",
                required_analysis=required_analysis,
            )
        )

    return tasks


def emit_investigation_progress(
    progress_callback: ProgressCallback | None,
    payload: dict[str, Any],
) -> None:
    if progress_callback is not None:
        progress_callback(payload)


def record_investigation_readiness_audit(
    effective_request: QueryRequest,
    result: InvestigationLoopResult,
    metadata: dict[str, Any],
) -> int | None:
    decision = result.final_decision
    response = QueryResponse(
        question=effective_request.question,
        answer=decision.reason if decision is not None else "No readiness decision.",
        sql="",
        rows=[],
        metadata={
            "duration_ms": 0,
            "datasource_id": effective_request.datasource_id,
            "user_id": effective_request.user_id,
            "output_classification": OutputClassification.UNKNOWN.value,
            **metadata,
            "investigation_step": "readiness",
        },
    )
    audit_log = record_data_query_audit(effective_request, response)
    return audit_log.id if audit_log is not None else None


def datasource_connector_id(datasource_context: DatasourceContext | None) -> int | None:
    return datasource_context[0].id if datasource_context is not None else None


def run_investigation_analysis_tasks(
    effective_request: QueryRequest,
    datasource_context: DatasourceContext | None,
    result: InvestigationLoopResult,
    progress_callback: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    analysis_results: list[dict[str, Any]] = []
    tasks = required_analysis_tasks_from_result(result)

    for index, task in enumerate(tasks):
        emit_investigation_progress(
            progress_callback,
            {
                "step": "analysis_sql",
                "analysis_task_index": index,
                "data_question": task.required_analysis,
                "decisions": [
                    f"Running analysis SQL task {index + 1} of {len(tasks)}."
                ],
            },
        )
        analysis_request = effective_request.model_copy(
            update={
                "question": task.required_analysis,
                "mode": QueryMode.SQL,
            }
        )
        analysis_metadata = {
            "query_mode": QueryMode.INVESTIGATION.value,
            "investigation_backend_status": "readiness_gate_active",
            "investigation_route": InvestigationRoute.ANALYSIS.value,
            "investigation_step": "analysis_sql",
            "analysis_task_index": index,
            "analysis_missing_information": task.missing_information,
            "analysis_required_analysis": task.required_analysis,
            "analysis_category": task.category,
            "analysis_expected_output": task.expected_output,
            "original_question": effective_request.question,
        }
        analysis_response = run_sql_request(
            analysis_request,
            datasource_context,
            analysis_metadata,
        )
        learning_result = record_analysis_business_logic_if_possible(
            effective_request=effective_request,
            datasource_context=datasource_context,
            task=task,
            task_index=index,
            analysis_response=analysis_response,
        )
        task_result = {
            "analysis_task_index": index,
            "missing_information": task.missing_information,
            "required_analysis": task.required_analysis,
            "category": task.category,
            "expected_output": task.expected_output,
            "sql": analysis_response.sql,
            "rows": analysis_response.rows,
            "answer": analysis_response.answer,
            "audit_log_id": analysis_response.metadata.get("data_query_audit_id"),
            "business_logic_learning": learning_result,
        }
        analysis_results.append(task_result)
        emit_investigation_progress(
            progress_callback,
            {
                "step": "analysis_sql_complete",
                "analysis_task_index": index,
                "data_question": task.required_analysis,
                "decisions": [
                    f"Analysis SQL task {index + 1} completed.",
                    business_logic_progress_message(learning_result),
                ],
            },
        )

    return analysis_results


def record_analysis_business_logic_if_possible(
    effective_request: QueryRequest,
    datasource_context: DatasourceContext | None,
    task: RequiredAnalysisTask,
    task_index: int,
    analysis_response: QueryResponse,
) -> dict[str, Any]:
    if analysis_response.metadata.get("blocked"):
        learning_result = {
            "status": "skipped",
            "reason": "Analysis SQL task was blocked and did not produce evidence.",
        }
    else:
        source_audit_id = analysis_response.metadata.get("data_query_audit_id")
        learning_result = upsert_investigation_analysis_business_logic_suggestion(
            connector_id=datasource_connector_id(datasource_context),
            source_audit_id=source_audit_id if isinstance(source_audit_id, int) else None,
            missing_information=task.missing_information,
            required_analysis=task.required_analysis,
            category=task.category,
            analysis_response=analysis_response,
        )

    record_investigation_business_logic_audit(
        effective_request=effective_request,
        task=task,
        task_index=task_index,
        analysis_response=analysis_response,
        learning_result=learning_result,
    )
    return learning_result


def record_investigation_business_logic_audit(
    effective_request: QueryRequest,
    task: RequiredAnalysisTask,
    task_index: int,
    analysis_response: QueryResponse,
    learning_result: dict[str, Any],
) -> None:
    response = QueryResponse(
        question=effective_request.question,
        answer=business_logic_progress_message(learning_result),
        sql=analysis_response.sql,
        rows=[],
        metadata={
            "duration_ms": 0,
            "datasource_id": effective_request.datasource_id,
            "user_id": effective_request.user_id,
            "output_classification": OutputClassification.UNKNOWN.value,
            "query_mode": QueryMode.INVESTIGATION.value,
            "investigation_backend_status": "readiness_gate_active",
            "investigation_route": InvestigationRoute.ANALYSIS.value,
            "investigation_step": "analysis_business_logic",
            "analysis_task_index": task_index,
            "analysis_missing_information": task.missing_information,
            "analysis_required_analysis": task.required_analysis,
            "analysis_category": task.category,
            "analysis_source_audit_log_id": analysis_response.metadata.get(
                "data_query_audit_id"
            ),
            "business_logic_learning": learning_result,
        },
    )
    record_data_query_audit(effective_request, response)


def business_logic_progress_message(learning_result: dict[str, Any]) -> str:
    status = str(learning_result.get("status") or "")
    if status == "created":
        return "Business logic suggestion was created and is pending approval."
    if status == "existing":
        return "Business logic suggestion already exists; no duplicate was created."
    if status == "skipped":
        return f"Business logic suggestion was skipped: {learning_result.get('reason')}"
    return "Business logic suggestion step completed."


def extract_sql_from_validation_error(error_message: str) -> str:
    for prefix in VALIDATION_SQL_PREFIXES:
        if error_message.startswith(prefix):
            return error_message.removeprefix(prefix).strip()

    return ""


def validation_error_metadata(exc: SqlValidationError) -> dict[str, Any]:
    if exc.metadata.get("primary_error_category"):
        return exc.metadata

    category = "sql.validation.write_operation" if any(
        text in exc.message
        for text in (
            "Only SELECT queries are allowed",
            "DDL and DML statements are not allowed",
        )
    ) else (
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
    intent_llm_config = (
        get_llm_runtime_config_safe() if intent_mode == "llm" else None
    )
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

    pipeline = create_pipeline(datasource_context)
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


def run_investigation_request(
    effective_request: QueryRequest,
    datasource_context: DatasourceContext | None,
    progress_callback: ProgressCallback | None = None,
) -> QueryResponse:
    started_at = time.perf_counter()
    runtime_config = get_query_runtime_config_safe()
    investigation_mode = resolve_investigation_mode(runtime_config)
    context = create_investigation_context(effective_request, datasource_context)
    loop_config = InvestigationLoopConfig(max_iterations=1)

    emit_investigation_progress(
        progress_callback,
        {
            "step": "readiness",
            "data_question": effective_request.question,
            "decisions": ["Running Investigation readiness check."],
        },
    )
    try:
        result = InvestigationLoop(
            readiness_agent=create_investigation_readiness_agent(runtime_config),
            config=loop_config,
        ).run(context)
    except LlmProviderError as exc:
        record_data_query_pipeline_error_audit(
            request=effective_request,
            sql="",
            error_code=exc.code,
            error_message=exc.message,
            error_detail=exc.message,
            pipeline_phase="investigation_readiness",
            metadata={
                "query_mode": QueryMode.INVESTIGATION.value,
                "investigation_mode": investigation_mode,
                "investigation_backend_status": "readiness_gate_active",
            },
        )
        raise

    metadata = investigation_metadata(result, investigation_mode)
    metadata["investigation_readiness_duration_ms"] = round(
        (time.perf_counter() - started_at) * 1000,
        2,
    )
    readiness_audit_log_id = record_investigation_readiness_audit(
        effective_request,
        result,
        metadata,
    )
    if readiness_audit_log_id is not None:
        metadata["readiness_audit_log_id"] = readiness_audit_log_id
    emit_investigation_progress(
        progress_callback,
        {
            "step": "readiness_complete",
            "data_question": effective_request.question,
            "decisions": [
                f"Investigation readiness route: {result.route.value}."
            ],
        },
    )

    if result.route == InvestigationRoute.SQL:
        emit_investigation_progress(
            progress_callback,
            {
                "step": "sql",
                "data_question": effective_request.question,
                "decisions": ["Readiness passed; running the normal SQL pipeline."],
            },
        )
        return run_sql_request(
            effective_request,
            datasource_context,
            {**metadata, "investigation_step": "sql"},
        )

    analysis_results = run_investigation_analysis_tasks(
        effective_request=effective_request,
        datasource_context=datasource_context,
        result=result,
        progress_callback=progress_callback,
    )
    metadata["analysis_results"] = analysis_results
    metadata["analysis_tasks_count"] = len(analysis_results)
    metadata["investigation_step"] = "final"

    response = QueryResponse(
        question=effective_request.question,
        answer=ANALYSIS_MODE_PENDING_ANSWER,
        sql="",
        rows=[],
        metadata={
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "datasource_id": effective_request.datasource_id,
            "user_id": effective_request.user_id,
            "output_classification": OutputClassification.UNKNOWN.value,
            **metadata,
        },
    )
    record_data_query_audit(effective_request, response)
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


def stream_investigation_response(
    effective_request: QueryRequest,
    datasource_context: DatasourceContext | None,
) -> Iterator[str]:
    queue: Queue[dict[str, Any] | None] = Queue()

    def progress_callback(payload: dict[str, Any]) -> None:
        queue.put({"progress": payload})

    def worker() -> None:
        try:
            response = run_investigation_request(
                effective_request,
                datasource_context,
                progress_callback=progress_callback,
            )
            queue.put({"final": response.model_dump(mode="json")})
        except Exception as exc:
            queue.put(
                {
                    "error": {
                        "message": str(exc),
                        "type": exc.__class__.__name__,
                    }
                }
            )
        finally:
            queue.put(None)

    thread = Thread(target=worker, daemon=True)
    thread.start()

    while True:
        item = queue.get()
        if item is None:
            break

        yield ndjson_line(item)

    thread.join()


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    effective_request, datasource_context = effective_query_request(request)

    if effective_request.mode == QueryMode.INVESTIGATION:
        return run_investigation_request(effective_request, datasource_context)

    return run_sql_request(effective_request, datasource_context)


@router.post("/query/stream")
def query_stream(request: QueryRequest) -> StreamingResponse:
    effective_request, datasource_context = effective_query_request(request)

    if effective_request.mode != QueryMode.INVESTIGATION:
        def single_response() -> Iterator[str]:
            yield ndjson_line({"final": query(effective_request).model_dump(mode="json")})

        return StreamingResponse(
            single_response(),
            media_type="application/x-ndjson",
        )

    return StreamingResponse(
        stream_investigation_response(effective_request, datasource_context),
        media_type="application/x-ndjson",
    )
