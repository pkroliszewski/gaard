import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from gaard_core.errors import (
    ConfigurationError,
    LlmProviderError,
    QueryExecutionError,
    SqlValidationError,
)
from gaard_core.llm_output import remove_thinking_blocks
from gaard_core.query_pipeline.models import (
    OutputClassification,
    QueryRequest,
    QueryResponse,
    QueryResult,
)
from gaard_core.sql_validator.select_only import SelectOnlySqlValidator
from gaard_connectors import ConnectorRegistryError
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage
from gaard_plugin_api import ExtensionRecord
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
import sqlglot
from sqlglot import expressions as exp

from gaard_api.admin.database import create_session, get_session
from gaard_api.admin.models import (
    AdminSession,
    AdminUser,
    BusinessLogicSuggestion,
    Dashboard,
    DatasourceConnector,
    DatasourceSchemaCache,
    OverviewWidget,
    PromptTemplate,
    UserSavedMetric,
)
from gaard_api.admin.security import (
    create_session_token,
    hash_password,
    hash_token,
    verify_password,
)
from gaard_api.admin.services import (
    BUSINESS_LOGIC_STATUS_ACTIVE,
    BUSINESS_LOGIC_STATUS_PENDING,
    OVERVIEW_WIDGET_RESULT_DATA,
    OVERVIEW_WIDGET_RESULT_INTERPRETATION,
    OVERVIEW_WIDGET_SCALAR,
    OVERVIEW_WIDGET_TABLE,
    OVERVIEW_WIDGET_TIMESERIES,
    apply_data_query_audit_retention,
    coerce_data_query_audit_type,
    delete_business_logic_suggestion,
    get_active_datasource_connector,
    get_active_datasource_connectors,
    get_business_logic_suggestion,
    get_data_query_audit_retention_days,
    get_data_query_audit_type,
    get_datasource_connector,
    get_datasource_connector_by_key,
    get_or_create_datasource_schema_cache,
    get_datasource_schema_cache,
    get_governance_policy_config,
    get_governance_policy_sources,
    get_llm_config_sources,
    get_llm_runtime_config,
    get_query_runtime_config,
    get_overview_widget,
    get_prompt_template,
    get_setting,
    introspect_datasource_connector,
    is_system_datasource_connector,
    json_loads,
    list_admin_audit_logs,
    list_all_overview_widgets,
    list_business_logic_suggestions_for_connectors,
    list_data_query_audit_logs,
    list_datasource_connectors,
    list_overview_widgets,
    list_prompt_templates,
    learn_business_logic_from_sql_error,
    mask_database_url,
    normalize_datasource_configuration,
    record_admin_audit,
    record_data_query_audit,
    record_data_query_sql_error_audit,
    selected_schema_from_cache,
    set_business_logic_suggestion_enabled,
    set_governance_policy_config,
    set_llm_runtime_config,
    set_query_runtime_config,
    set_setting,
    set_active_datasource_connector,
    test_datasource_connection,
    test_llm_runtime_config,
    update_business_logic_suggestion_content,
    update_schema_table_settings,
)
from gaard_api.api.v1.schema import get_schema_cache_key
from gaard_api.core.schema_cache import schema_context_cache
from gaard_api.core.settings import settings
from gaard_api.extensions import (
    get_api_registry,
    get_auth_provider_registry,
    get_connector_registry,
    get_extension_manager,
)
from gaard_api.license import license_service, redact_license_key
from gaard_api.package_updates import package_update_jobs, package_update_service
from gaard_api.query_hooks import sqlglot_read_dialect

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    token: str
    username: str
    must_change_password: bool
    role: str = "admin"


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class IdentityUpdateRequest(BaseModel):
    display_name: str | None = None
    username: str | None = Field(default=None, min_length=1, max_length=255)
    new_password: str | None = Field(default=None, min_length=8)


class MeResponse(BaseModel):
    username: str
    must_change_password: bool
    role: str = "admin"


@dataclass(frozen=True)
class AuthenticatedSession:
    session: AdminSession
    user: AdminUser


class PromptUpdateRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    system_prompt: str = Field(min_length=1)
    user_prompt_template: str = Field(min_length=1)
    active: bool = True


class AuditSettingsRequest(BaseModel):
    data_query_retention_days: int = Field(ge=1, le=3650)


class SchemaCacheSettingsRequest(BaseModel):
    ttl_seconds: int = Field(ge=1, le=86_400)


class LlmConfigRequest(BaseModel):
    provider: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    clear_api_key: bool = False
    model: str = Field(min_length=1)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    intent_classification_mode: str | None = Field(
        default=None,
        pattern=r"^(auto|llm)$",
    )
    sql_generation_mode: str | None = Field(
        default=None,
        pattern=r"^llm$",
    )
    result_interpretation_mode: str | None = Field(
        default=None,
        pattern=r"^llm$",
    )
    output_classification_mode: str | None = Field(
        default=None,
        pattern=r"^(auto|llm)$",
    )
    query_max_rows: int | None = Field(default=None, ge=1, le=100_000)
    query_timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=3_600,
    )


class ReasoningConfigRequest(BaseModel):
    intent_classification_mode: str = Field(pattern=r"^(auto|llm)$")
    sql_generation_mode: str = Field(pattern=r"^llm$")
    result_interpretation_mode: str = Field(pattern=r"^llm$")
    output_classification_mode: str = Field(pattern=r"^(auto|llm)$")
    query_max_rows: int = Field(ge=1, le=100_000)
    query_timeout_seconds: int = Field(ge=1, le=3_600)
    analysis_loop_count: int | None = Field(default=None, ge=1, le=25)
    analysis_auto_enable_business_logic: bool | None = None


class GovernancePolicyRequest(BaseModel):
    final_answer: dict[str, Any] = Field(default_factory=dict)
    sql: dict[str, Any] = Field(default_factory=dict)
    privacy: dict[str, Any] = Field(default_factory=dict)
    pii_column_names: dict[str, list[str]] | list[str] = Field(default_factory=dict)


class OverviewWidgetUpdateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    widget_type: str = Field(pattern=r"^(scalar|timeseries|table)$")
    datasource_key: str = Field(min_length=1, max_length=255)
    question: str = Field(min_length=1)
    result_mode: str = Field(
        default=OVERVIEW_WIDGET_RESULT_DATA, pattern=r"^(data|interpretation)$"
    )
    position: int | None = Field(default=None, ge=10)
    grid_width: int | None = Field(default=None, ge=1, le=12)
    active: bool | None = None


class OverviewWidgetCreateRequest(OverviewWidgetUpdateRequest):
    widget_key: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_-]+$")
    position: int = Field(default=100, ge=10)
    grid_width: int | None = Field(default=None, ge=1, le=12)
    active: bool = True


class OverviewWidgetStateRequest(BaseModel):
    active: bool
    position: int | None = Field(default=None, ge=10)
    grid_width: int | None = Field(default=None, ge=1, le=12)


class OverviewWidgetFromQueryRequest(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    widget_type: str = Field(default=OVERVIEW_WIDGET_TABLE, pattern=r"^(scalar|timeseries|table)$")
    datasource_key: str = Field(default="default", min_length=1, max_length=255)
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    result_mode: str = Field(
        default=OVERVIEW_WIDGET_RESULT_DATA, pattern=r"^(data|interpretation)$"
    )


class OverviewWidgetTitleSuggestionRequest(BaseModel):
    question: str = Field(min_length=1)
    sql: str | None = None


class DatasourceConnectorRequest(BaseModel):
    connector_key: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1)
    database_type: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    connection_config: dict[str, Any] = Field(default_factory=dict)
    database_path: str | None = Field(default=None, min_length=1)
    database_url: str | None = Field(default=None, min_length=1)
    sql_dialect: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    active: bool = False


class DatasourceConnectorUpdateRequest(BaseModel):
    name: str = Field(min_length=1)
    database_type: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    connection_config: dict[str, Any] = Field(default_factory=dict)
    database_path: str | None = Field(default=None, min_length=1)
    database_url: str | None = Field(default=None, min_length=1)
    sql_dialect: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    active: bool = False


class DatasourceConnectionTestRequest(BaseModel):
    database_type: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    connection_config: dict[str, Any] = Field(default_factory=dict)
    database_path: str | None = Field(default=None, min_length=1)
    database_url: str | None = Field(default=None, min_length=1)


class DatasourceStateRequest(BaseModel):
    active: bool


class DatasourceSchemaTableSettingsRequest(BaseModel):
    tables: dict[str, dict[str, Any]]


class BusinessLogicSuggestionUpdateRequest(BaseModel):
    enabled: bool | None = None
    title: str | None = Field(default=None, min_length=1)
    rule_text: str | None = Field(default=None, min_length=1)


class LicenseKeyRequest(BaseModel):
    license_key: str | None = Field(default=None, min_length=1)
    clear_license_key: bool = False


def serialize_datetime(value: datetime) -> str:
    return value.isoformat()


def get_runtime_schema_cache_key(session: Session) -> str:
    connector = get_active_datasource_connector(session)

    if connector is None:
        return get_schema_cache_key()

    return get_schema_cache_key(connector.database_url, connector.sql_dialect)


def serialize_prompt(prompt: PromptTemplate) -> dict[str, Any]:
    return {
        "prompt_key": prompt.prompt_key,
        "name": prompt.name,
        "description": prompt.description,
        "system_prompt": prompt.system_prompt,
        "user_prompt_template": prompt.user_prompt_template,
        "version": prompt.version,
        "active": prompt.active,
        "updated_by": prompt.updated_by,
        "updated_at": serialize_datetime(prompt.updated_at),
    }


def serialize_datasource(connector: DatasourceConnector) -> dict[str, Any]:
    return {
        "id": connector.id,
        "connector_key": connector.connector_key,
        "name": connector.name,
        "database_type": connector.database_type,
        "database_url": connector.database_url,
        "masked_database_url": mask_database_url(connector.database_url),
        "sql_dialect": connector.sql_dialect,
        "active": connector.active,
        "system_managed": is_system_datasource_connector(connector),
        "updated_by": connector.updated_by,
        "updated_at": serialize_datetime(connector.updated_at),
    }


def serialize_datasource_schema(cache: DatasourceSchemaCache) -> dict[str, Any]:
    return {
        "schema": selected_schema_from_cache(cache).model_dump(),
        "raw_schema": json_loads(cache.schema_json),
        "table_settings": json_loads(cache.table_settings_json),
        "formatted_schema": cache.formatted_schema,
        "introspected_at": serialize_datetime(cache.introspected_at),
        "updated_by": cache.updated_by,
    }


def serialize_admin_dashboard(dashboard: Dashboard) -> dict[str, Any]:
    return {
        "id": dashboard.dashboard_id,
        "name": dashboard.name,
        "description": dashboard.description,
        "owner_user_id": dashboard.owner_user_id,
        "owner_username": dashboard.owner_username,
        "created_at": serialize_datetime(dashboard.created_at),
        "updated_at": serialize_datetime(dashboard.updated_at),
    }


def serialize_extension_record(record: ExtensionRecord) -> dict[str, Any]:
    manifest = record.manifest
    return {
        "entry_point_name": record.entry_point_name,
        "id": manifest.id if manifest else None,
        "version": manifest.version if manifest else None,
        "extension_api_version": manifest.extension_api_version if manifest else None,
        "status": record.status.value,
        "error": record.error,
        "requires": dict(manifest.requires) if manifest else {},
        "contributions": sorted(manifest.contributions) if manifest else [],
        "active_capabilities": sorted(record.active_capabilities),
    }


def serialize_llm_config(session: Session) -> dict[str, Any]:
    llm_config = get_llm_runtime_config(session)
    query_config = get_query_runtime_config(session)
    api_key_configured = bool(llm_config.api_key and llm_config.api_key != "change-me")

    return {
        "provider": llm_config.provider,
        "base_url": llm_config.base_url,
        "api_key_configured": api_key_configured,
        "api_key_preview": mask_secret(llm_config.api_key) if api_key_configured else None,
        "model": llm_config.model,
        "timeout_seconds": llm_config.timeout_seconds,
        "extra_body": llm_config.extra_body,
        "extra_body_json": json_dumps_pretty(llm_config.extra_body),
        "intent_classification_mode": query_config.intent_classification_mode,
        "sql_generation_mode": query_config.sql_generation_mode,
        "result_interpretation_mode": query_config.result_interpretation_mode,
        "output_classification_mode": query_config.output_classification_mode,
        "query_max_rows": query_config.query_max_rows,
        "query_timeout_seconds": query_config.query_timeout_seconds,
        "sources": get_llm_config_sources(session),
    }


def serialize_reasoning_config(session: Session) -> dict[str, Any]:
    query_config = get_query_runtime_config(session)
    sources = get_llm_config_sources(session)

    return {
        "intent_classification_mode": query_config.intent_classification_mode,
        "sql_generation_mode": query_config.sql_generation_mode,
        "result_interpretation_mode": query_config.result_interpretation_mode,
        "output_classification_mode": query_config.output_classification_mode,
        "query_max_rows": query_config.query_max_rows,
        "query_timeout_seconds": query_config.query_timeout_seconds,
        "analysis_loop_count": query_config.analysis_loop_count,
        "analysis_auto_enable_business_logic": (
            query_config.analysis_auto_enable_business_logic
        ),
        "sources": {
            field: sources[field]
            for field in (
                "intent_classification_mode",
                "sql_generation_mode",
                "result_interpretation_mode",
                "output_classification_mode",
                "query_max_rows",
                "query_timeout_seconds",
                "analysis_loop_count",
                "analysis_auto_enable_business_logic",
            )
        },
    }


def mask_secret(value: str) -> str:
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"


def serialize_governance_policy(session: Session) -> dict[str, Any]:
    config = get_governance_policy_config(session)

    return {
        **config,
        "governance_policy_json": json_dumps_pretty(config),
        "sources": get_governance_policy_sources(session),
    }


def serialize_overview_widget_config(widget: OverviewWidget) -> dict[str, Any]:
    return {
        "widget_key": widget.widget_key,
        "label": widget.label,
        "widget_type": widget.widget_type,
        "datasource_key": widget.datasource_key,
        "question": widget.question,
        "sql": widget.sql,
        "result_mode": normalize_overview_widget_result_mode(widget.result_mode),
        "position": widget.position,
        "grid_width": normalize_overview_widget_grid_width(
            widget.widget_type,
            widget.grid_width,
        ),
        "active": widget.active,
        "updated_by": widget.updated_by,
        "updated_at": serialize_datetime(widget.updated_at),
    }


def normalize_overview_widget_grid_width(
    widget_type: str,
    grid_width: int | None,
) -> int:
    if grid_width is None:
        return 12 if widget_type in {OVERVIEW_WIDGET_TABLE, OVERVIEW_WIDGET_TIMESERIES} else 1

    return max(1, min(12, int(grid_width)))


def normalize_overview_widget_result_mode(value: str | None) -> str:
    if value == OVERVIEW_WIDGET_RESULT_INTERPRETATION:
        return OVERVIEW_WIDGET_RESULT_INTERPRETATION

    return OVERVIEW_WIDGET_RESULT_DATA


def build_client_widget_key(session: Session, label: str, question: str) -> str:
    seed = label.strip() or question.strip() or "query_widget"
    normalized = re.sub(r"[^a-z0-9_-]+", "_", seed.lower()).strip("_-")
    normalized = normalized[:48].strip("_-") or "query_widget"
    base_key = f"client_{normalized}"
    widget_key = base_key
    suffix = 2

    while get_overview_widget(session, widget_key) is not None:
        suffix_text = f"_{suffix}"
        widget_key = f"{base_key[: 255 - len(suffix_text)]}{suffix_text}"
        suffix += 1

    return widget_key


def normalize_metric_title(value: str, fallback: str) -> str:
    cleaned = remove_thinking_blocks(value).strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()
        if cleaned.startswith("text"):
            cleaned = cleaned.removeprefix("text").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned.removesuffix("```").strip()

    cleaned = cleaned.strip().strip('"').strip("'")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(".:;-")
    if not cleaned:
        cleaned = fallback.strip()

    return cleaned[:80].strip() or "Saved Metric"


def fallback_metric_title(question: str) -> str:
    normalized = re.sub(r"\s+", " ", question.strip())
    normalized = normalized.rstrip("?!.:;-")
    if not normalized:
        return "Saved Metric"

    return normalized[0].upper() + normalized[1:80]


def suggest_metric_title_with_llm(session: Session, request: OverviewWidgetTitleSuggestionRequest) -> str:
    llm_config = get_llm_runtime_config(session)
    if llm_config.provider != "openai-compatible":
        raise ConfigurationError(f"Unsupported GAARD_LLM_PROVIDER: {llm_config.provider}")
    if not llm_config.api_key or llm_config.api_key == "change-me":
        raise ConfigurationError("GAARD_LLM_API_KEY must be configured to suggest metric titles.")

    client = OpenAICompatibleClient(
        base_url=llm_config.base_url,
        api_key=llm_config.api_key,
        timeout_seconds=llm_config.timeout_seconds,
    )
    fallback = fallback_metric_title(request.question)
    response = client.create_chat_completion(
        ChatCompletionRequest(
            model=llm_config.model,
            temperature=0.0,
            extra_body=llm_config.extra_body,
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "You write short human-friendly metric names for dashboard widgets. "
                        "Return only the metric name, without quotes, markdown, explanations, "
                        "or trailing punctuation. Use the same language as the user question. "
                        "Prefer 3 to 8 words and keep it under 80 characters."
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        "User question:\n"
                        f"{request.question.strip()}\n\n"
                        "Generated SQL, if useful:\n"
                        f"{(request.sql or '').strip()}\n\n"
                        "Return the metric name only."
                    ),
                ),
            ],
        )
    )

    return normalize_metric_title(response.content, fallback)


def build_client_excel_datasource_key(session: Session, filename: str) -> str:
    stem = Path(filename).stem or "excel"
    normalized = re.sub(r"[^a-z0-9_-]+", "_", stem.lower()).strip("_-") or "excel"
    base_key = f"client_excel_{normalized[:48].strip('_-') or 'source'}"
    connector_key = base_key
    suffix = 2

    while get_datasource_connector_by_key(session, connector_key) is not None:
        suffix_text = f"_{suffix}"
        connector_key = f"{base_key[: 255 - len(suffix_text)]}{suffix_text}"
        suffix += 1

    return connector_key


def safe_excel_upload_path(directory: Path, filename: str) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", Path(filename).name).strip("._")
    if not safe_name:
        safe_name = "source.xlsx"
    if Path(safe_name).suffix.lower() != ".xlsx":
        safe_name = f"{Path(safe_name).stem or 'source'}.xlsx"

    target = directory / safe_name
    suffix = 2
    while target.exists():
        target = directory / f"{Path(safe_name).stem}_{suffix}.xlsx"
        suffix += 1

    return target


def normalize_datasource_configuration_or_400(
    *,
    database_type: str,
    connection_config: dict[str, Any] | None = None,
    database_path: str | None = None,
    database_url: str | None = None,
    sql_dialect: str | None = None,
):
    try:
        return normalize_datasource_configuration(
            database_type=database_type,
            connection_config=connection_config,
            database_path=database_path,
            database_url=database_url,
            sql_dialect=sql_dialect,
        )
    except (ConnectorRegistryError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def ensure_excel_datasource_type_available() -> None:
    try:
        get_connector_registry().get("duckdb-excel")
    except ConnectorRegistryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Excel workbook uploads require the duckdb-excel datasource connector, "
                "which is not available in this GAARD API process."
            ),
        ) from exc


def serialize_overview_widget(
    session: Session,
    widget: OverviewWidget,
) -> dict[str, Any]:
    return {
        **serialize_overview_widget_config(widget),
        "result": execute_overview_widget(session, widget),
    }


def execute_overview_widget(
    session: Session,
    widget: OverviewWidget,
) -> dict[str, Any]:
    connector = get_datasource_connector_by_key(session, widget.datasource_key)

    if connector is None:
        return widget_error(f"Datasource '{widget.datasource_key}' does not exist.")

    if not widget.sql.strip():
        return widget_error("Widget SQL has not been generated yet.")

    try:
        result = execute_overview_sql(session, connector, widget.sql)
        payload = validate_overview_widget_result(widget, result)
        payload["result_mode"] = normalize_overview_widget_result_mode(widget.result_mode)

        if payload["result_mode"] == OVERVIEW_WIDGET_RESULT_INTERPRETATION:
            interpretation = interpret_overview_widget_result(connector, widget, result)
            payload["answer"] = interpretation
            payload["interpretation"] = interpretation
            payload["value"] = interpretation

        return payload
    except QueryExecutionError as exc:
        return widget_error(exc.message, sql=exc.sql)
    except (
        ConfigurationError,
        LlmProviderError,
        SQLAlchemyError,
        SqlValidationError,
        ValueError,
        TypeError,
    ) as exc:
        return widget_error(str(exc), sql=widget.sql)


def interpret_overview_widget_result(
    connector: DatasourceConnector,
    widget: OverviewWidget,
    result: QueryResult,
) -> str:
    from gaard_api.api.v1.query import create_result_interpreter

    query_request = build_overview_widget_query_request(
        connector=connector,
        widget_key=widget.widget_key,
        question=widget.question,
    )

    return create_result_interpreter().interpret(
        request=query_request,
        result=result,
        sql=widget.sql,
    )


def execute_overview_sql(
    session: Session,
    connector: DatasourceConnector,
    sql: str,
) -> QueryResult:
    executable_sql = prepare_overview_sql_for_connector(connector, sql)
    SelectOnlySqlValidator(dialect=sqlglot_read_dialect(connector.sql_dialect)).validate(
        executable_sql
    )

    return (
        get_connector_registry()
        .get(connector.database_type)
        .executor_factory(
            connector.database_url,
            get_query_runtime_config(session).query_max_rows,
        )
        .execute(executable_sql)
    )


def prepare_overview_sql_for_connector(
    connector: DatasourceConnector,
    sql: str,
) -> str:
    sql = strip_overview_datasource_qualifier(connector, sql)
    read_dialect = sqlglot_read_dialect(connector.sql_dialect)

    try:
        expression = sqlglot.parse_one(sql, read=read_dialect)
    except Exception:
        return sql

    for table in expression.find_all(exp.Table):
        if table.args.get("db") and table.args["db"].name == connector.connector_key:
            table.set("db", None)
        if table.args.get("catalog") and table.args["catalog"].name == connector.connector_key:
            table.set("catalog", None)

    if read_dialect:
        return expression.sql(dialect=read_dialect)
    return expression.sql()


def strip_overview_datasource_qualifier(
    connector: DatasourceConnector,
    sql: str,
) -> str:
    connector_key = re.escape(connector.connector_key)
    cleaned = re.sub(rf'"{connector_key}"\s*\.', "", sql)
    return re.sub(rf"(?<![\w\"`]){connector_key}\s*\.", "", cleaned)


def generate_overview_widget_sql(
    session: Session,
    connector: DatasourceConnector,
    query_request: QueryRequest,
    actor: str,
) -> str:
    schema_cache = get_or_create_datasource_schema_cache(session, connector, actor)
    session.commit()

    from gaard_api.api.v1.query import create_sql_generator, resolve_sql_dialect_plan

    datasource_context = (connector, schema_cache)
    generated_sql = create_sql_generator(
        datasource_context,
        dialect_plan=resolve_sql_dialect_plan([datasource_context]),
    ).generate(query_request)
    executable_sql = prepare_overview_sql_for_connector(connector, generated_sql.sql)
    SelectOnlySqlValidator(dialect=sqlglot_read_dialect(connector.sql_dialect)).validate(
        executable_sql
    )

    return generated_sql.sql


def build_overview_widget_query_request(
    connector: DatasourceConnector,
    widget_key: str,
    question: str,
) -> QueryRequest:
    return QueryRequest(
        question=question,
        datasource_id=connector.connector_key,
        user_id=f"overview-widget-config:{widget_key}",
    )


def record_overview_widget_query_audit(
    query_request: QueryRequest,
    widget: OverviewWidget,
    generated_sql: str,
    actor: str,
) -> None:
    record_data_query_audit(
        query_request,
        QueryResponse(
            question=query_request.question,
            answer="Generated SQL for overview widget source.",
            sql=generated_sql,
            rows=[],
            metadata={
                "actor": actor,
                "operation": "overview_widget.update",
                "widget_key": widget.widget_key,
                "widget_type": widget.widget_type,
                "result_mode": normalize_overview_widget_result_mode(widget.result_mode),
            },
        ),
    )


def record_overview_widget_sql_error_audit(
    query_request: QueryRequest,
    connector: DatasourceConnector,
    sql: str,
    error_code: str,
    error_message: str,
    error_detail: str,
    actor: str,
) -> None:
    audit_log = record_data_query_sql_error_audit(
        request=query_request,
        sql=sql,
        error_code=error_code,
        error_message=error_message,
        error_detail=error_detail,
    )
    learn_business_logic_from_sql_error(
        connector_id=connector.id,
        audit_id=audit_log.id if audit_log is not None else None,
        actor=actor,
    )


def validate_overview_widget_result(
    widget: OverviewWidget,
    result: QueryResult,
) -> dict[str, Any]:
    rows = result.rows
    columns = result.columns

    if widget.widget_type == OVERVIEW_WIDGET_SCALAR:
        if len(rows) != 1 or len(columns) != 1:
            raise ValueError("Scalar widgets must return exactly one row and one column.")

        value = rows[0][columns[0]]
        return {
            "status": "ok",
            "value": value,
            "columns": columns,
            "rows": rows,
            "answer": json.dumps(rows[0], ensure_ascii=False, default=str),
            "sql": widget.sql,
        }

    if widget.widget_type == OVERVIEW_WIDGET_TIMESERIES:
        if rows:
            validate_timeseries_rows(columns, rows)

        return {
            "status": "ok",
            "columns": columns,
            "rows": rows,
            "answer": json.dumps(rows, ensure_ascii=False, default=str),
            "sql": widget.sql,
        }

    if widget.widget_type == OVERVIEW_WIDGET_TABLE:
        if not columns and rows:
            raise ValueError("Table widgets must return named columns.")

        return {
            "status": "ok",
            "columns": columns,
            "rows": rows,
            "answer": json.dumps(rows, ensure_ascii=False, default=str),
            "sql": widget.sql,
        }

    raise ValueError(f"Unsupported widget type: {widget.widget_type}")


def validate_timeseries_rows(
    columns: list[str],
    rows: list[dict[str, Any]],
) -> None:
    if len(columns) < 2:
        raise ValueError("Time-series widgets must return at least two columns.")

    date_column = columns[0]

    for row in rows:
        if not is_date_like(row.get(date_column)):
            raise ValueError("The first time-series column must contain date values.")

    if len(columns) == 3 and all(is_number_like(row.get(columns[2])) for row in rows):
        return

    for row in rows:
        values = [row.get(column) for column in columns[1:]]

        if not values or not all(is_number_like(value) for value in values):
            raise ValueError(
                "Time-series widgets must return numeric value columns, or "
                "date/category/value columns."
            )


def is_date_like(value: Any) -> bool:
    if isinstance(value, datetime):
        return True

    if not isinstance(value, str) or not value.strip():
        return False

    normalized = value.strip().replace("Z", "+00:00")

    try:
        datetime.fromisoformat(normalized)
        return True
    except ValueError:
        return False


def is_number_like(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False

    if isinstance(value, int | float):
        return True

    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False

    return False


def widget_error(message: str, sql: str = "") -> dict[str, Any]:
    return {
        "status": "error",
        "error": message,
        "sql": sql,
        "columns": [],
        "rows": [],
    }


def json_dumps_pretty(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def serialize_business_logic_suggestion(
    suggestion: BusinessLogicSuggestion,
) -> dict[str, Any]:
    return {
        "id": suggestion.id,
        "connector_id": suggestion.connector_id,
        "source_audit_id": suggestion.source_audit_id,
        "status": suggestion.status,
        "safety": suggestion.safety,
        "enabled": suggestion.enabled,
        "error_category": suggestion.error_category,
        "title": suggestion.title,
        "rule_text": suggestion.rule_text,
        "terms": json_loads(suggestion.terms_json),
        "join_hints": json_loads(suggestion.join_hints_json),
        "failed_identifier": suggestion.failed_identifier,
        "repaired_identifier": suggestion.repaired_identifier,
        "confidence": suggestion.confidence,
        "created_at": serialize_datetime(suggestion.created_at),
        "updated_at": serialize_datetime(suggestion.updated_at),
        "updated_by": suggestion.updated_by,
    }


def get_authorization_token(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
        )

    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header.",
        )

    return token


def get_current_authenticated_session(
    authorization: Annotated[str | None, Header()] = None,
    session: Session = Depends(get_session),
) -> AuthenticatedSession:
    token = get_authorization_token(authorization)
    token_hash = hash_token(token)

    admin_session = session.scalar(
        select(AdminSession).where(AdminSession.token_hash == token_hash)
    )

    if admin_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin session.",
        )

    user = session.get(AdminUser, admin_session.user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin session.",
        )

    return AuthenticatedSession(session=admin_session, user=user)


def get_current_api_user(
    principal: AuthenticatedSession = Depends(get_current_authenticated_session),
) -> AuthenticatedSession:
    if principal.session.role not in {"user", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated user does not have API access.",
        )
    return principal


def get_current_admin_allow_password_change(
    principal: AuthenticatedSession = Depends(get_current_authenticated_session),
) -> AdminUser:
    if principal.session.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role is required.",
        )
    return principal.user


def get_current_admin(
    user: AdminUser = Depends(get_current_admin_allow_password_change),
) -> AdminUser:
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change is required before using the admin portal.",
        )

    return user


@router.post("/auth/login", response_model=LoginResponse)
def login(request: LoginRequest, session: Session = Depends(get_session)) -> LoginResponse:
    user = session.scalar(select(AdminUser).where(AdminUser.username == request.username))

    if user is not None and verify_password(request.password, user.password_hash):
        token = create_session_token()
        session.add(
            AdminSession(
                token_hash=hash_token(token),
                user_id=user.id,
                username=user.username,
                role="admin",
                auth_provider="local",
            )
        )
        record_admin_audit(
            session=session,
            actor=user.username,
            action="auth.login",
            resource_type="admin_user",
            resource_id=user.username,
        )
        session.commit()

        return LoginResponse(
            token=token,
            username=user.username,
            must_change_password=user.must_change_password,
            role="admin",
        )

    identity = None
    if license_service.identity_management_allowed():
        identity = get_auth_provider_registry().authenticate(
            session,
            request.username,
            request.password,
        )

    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    user = get_or_create_external_auth_user(session, identity.provider_id, identity.username)
    token = create_session_token()
    session.add(
        AdminSession(
            token_hash=hash_token(token),
            user_id=user.id,
            username=identity.username,
            role=identity.role,
            auth_provider=identity.provider_id,
        )
    )
    record_admin_audit(
        session=session,
        actor=identity.username,
        action="auth.login",
        resource_type="external_user",
        resource_id=identity.username,
        details={
            "provider_id": identity.provider_id,
            "provider_name": identity.provider_name,
            "role": identity.role,
        },
    )
    session.commit()

    return LoginResponse(
        token=token,
        username=identity.username,
        must_change_password=False,
        role=identity.role,
    )


def get_or_create_external_auth_user(
    session: Session,
    provider_id: str,
    username: str,
) -> AdminUser:
    local_username = f"{provider_id}:{username}"
    user = session.scalar(
        select(AdminUser).where(
            AdminUser.username == local_username,
            AdminUser.auth_provider == provider_id,
        )
    )
    if user is not None:
        user.must_change_password = False
        return user

    user = AdminUser(
        username=local_username,
        password_hash="external$disabled",
        must_change_password=False,
        auth_provider=provider_id,
    )
    session.add(user)
    session.flush()
    return user


@router.get("/me", response_model=MeResponse)
def get_me(
    principal: AuthenticatedSession = Depends(get_current_authenticated_session),
) -> MeResponse:
    return MeResponse(
        username=principal.session.username or principal.user.username,
        must_change_password=principal.user.must_change_password,
        role=principal.session.role,
    )


@router.post("/auth/change-password", response_model=MeResponse)
def change_password(
    request: ChangePasswordRequest,
    user: AdminUser = Depends(get_current_admin_allow_password_change),
    session: Session = Depends(get_session),
) -> MeResponse:
    user = session.merge(user)

    if not verify_password(request.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is invalid.",
        )

    user.password_hash = hash_password(request.new_password)
    user.must_change_password = False
    record_admin_audit(
        session=session,
        actor=user.username,
        action="auth.change_password",
        resource_type="admin_user",
        resource_id=user.username,
    )
    session.commit()

    return MeResponse(username=user.username, must_change_password=False)


@router.get("/identities")
def list_identities(
    refresh: bool = False,
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    with create_session() as session:
        items = [{
            "id": f"local:{item.id}", "username": item.username,
            "name": item.display_name or item.username, "role": "admin",
            "provider": "Built-in", "provider_id": "local",
            "editable_name": True, "editable_password": True, "attributes": {},
        } for item in session.scalars(
            select(AdminUser).where(AdminUser.auth_provider == "local").order_by(AdminUser.username)
        ).all()]
        if license_service.identity_management_allowed():
            for provider in get_auth_provider_registry().identity_providers():
                items.extend(provider.list_users(session, refresh=refresh))
        mark_overshadowed_identities(items)
        return {"items": items}


def mark_overshadowed_identities(items: list[dict[str, Any]]) -> None:
    """Mirror authentication-provider order when exposing duplicate identities."""
    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        username = str(item.get("username") or "").strip()
        if not username or item.get("overshadowed"):
            continue
        key = username.casefold()
        winner = seen.get(key)
        if winner is not None:
            item["overshadowed"] = True
            item["overshadowed_by"] = {
                "username": winner.get("username", ""),
                "provider": winner.get("provider", ""),
                "provider_id": winner.get("provider_id", ""),
            }
            continue
        seen[key] = item


@router.patch("/identities/{identity_id}")
def update_identity(
    identity_id: str,
    request: IdentityUpdateRequest,
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    if not identity_id.startswith("local:"):
        raise HTTPException(status_code=400, detail="External identities are managed by their provider.")
    with create_session() as session:
        target = session.get(AdminUser, int(identity_id.removeprefix("local:")))
        if target is None:
            raise HTTPException(status_code=404, detail="Identity not found.")
        if request.display_name is not None:
            target.display_name = request.display_name.strip()
        if request.username is not None:
            username = request.username.strip()
            if not username:
                raise HTTPException(status_code=400, detail="Username must not be empty.")
            existing = session.scalar(select(AdminUser).where(AdminUser.username == username))
            if existing is not None and existing.id != target.id:
                raise HTTPException(status_code=400, detail="Username is already in use.")
            target.username = username
        if request.new_password:
            target.password_hash = hash_password(request.new_password)
            target.must_change_password = False
        if request.username is not None or request.new_password:
            session.execute(delete(AdminSession).where(AdminSession.user_id == target.id))
        session.commit()
        return {"status": "updated"}


@router.get("/overview")
def get_overview(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    prompts = list_prompt_templates(session)
    retention_days = get_data_query_audit_retention_days(session)
    widgets = [
        serialize_overview_widget(session, widget) for widget in list_overview_widgets(session)
    ]

    return {
        "admin": {
            "username": user.username,
        },
        "license": license_service.status(),
        "prompts_count": len(prompts),
        "data_query_audit_retention_days": retention_days,
        "schema_cache_ttl_seconds": schema_context_cache.ttl_seconds,
        "schema_cache_key": get_runtime_schema_cache_key(session),
        "datasources": [
            {
                "connector_key": connector.connector_key,
                "name": connector.name,
                "database_type": connector.database_type,
                "masked_database_url": mask_database_url(connector.database_url),
                "active": connector.active,
            }
            for connector in list_datasource_connectors(session)
        ],
        "widgets": widgets,
        "info_widgets": [
            widget for widget in widgets if widget["widget_type"] == OVERVIEW_WIDGET_SCALAR
        ][:4],
        "runtime_widget": next(
            (widget for widget in widgets if widget["widget_type"] == OVERVIEW_WIDGET_TIMESERIES),
            None,
        ),
        "table_widgets": [
            widget for widget in widgets if widget["widget_type"] == OVERVIEW_WIDGET_TABLE
        ],
    }


@router.get("/dashboards")
def get_admin_dashboards(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    dashboards = session.scalars(
        select(Dashboard).order_by(Dashboard.updated_at.desc(), Dashboard.id.desc())
    )

    return {
        "items": [serialize_admin_dashboard(dashboard) for dashboard in dashboards],
        "viewer": user.username,
    }


@router.get("/overview/widgets")
def get_overview_widget_configs(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {
        "items": [
            serialize_overview_widget_config(widget)
            for widget in list_all_overview_widgets(session)
        ],
        "datasources": [
            {
                "connector_key": connector.connector_key,
                "name": connector.name,
                "database_type": connector.database_type,
                "masked_database_url": mask_database_url(connector.database_url),
                "active": connector.active,
            }
            for connector in list_datasource_connectors(session)
        ],
    }


@router.post("/overview/widgets")
def create_overview_widget(
    request: OverviewWidgetCreateRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if get_overview_widget(session, request.widget_key) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Overview widget already exists.",
        )

    datasource = get_datasource_connector_by_key(session, request.datasource_key)

    if datasource is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Datasource does not exist.",
        )

    query_request = build_overview_widget_query_request(
        connector=datasource,
        widget_key=request.widget_key,
        question=request.question,
    )
    generated_sql = ""
    next_grid_width = normalize_overview_widget_grid_width(
        request.widget_type,
        request.grid_width,
    )
    next_result_mode = normalize_overview_widget_result_mode(request.result_mode)

    try:
        generated_sql = generate_overview_widget_sql(
            session=session,
            connector=datasource,
            query_request=query_request,
            actor=user.username,
        )
        widget = OverviewWidget(
            widget_key=request.widget_key,
            label=request.label,
            widget_type=request.widget_type,
            datasource_key=request.datasource_key,
            question=request.question,
            sql=generated_sql,
            result_mode=next_result_mode,
            position=request.position,
            grid_width=next_grid_width,
            active=request.active,
            updated_by=user.username,
        )
        validate_overview_widget_result(
            widget,
            execute_overview_sql(session, datasource, generated_sql),
        )
    except QueryExecutionError as exc:
        record_overview_widget_sql_error_audit(
            query_request=query_request,
            connector=datasource,
            sql=exc.sql or generated_sql,
            error_code=exc.code,
            error_message=exc.message,
            error_detail=exc.error_detail,
            actor=user.username,
        )
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.message,
        ) from exc
    except (
        ConfigurationError,
        SQLAlchemyError,
        SqlValidationError,
        ValueError,
        TypeError,
    ) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    session.add(widget)
    record_admin_audit(
        session=session,
        actor=user.username,
        action="overview_widget.create",
        resource_type="overview_widget",
        resource_id=widget.widget_key,
        details={
            "label": widget.label,
            "widget_type": widget.widget_type,
            "datasource_key": widget.datasource_key,
            "result_mode": widget.result_mode,
            "position": widget.position,
            "grid_width": widget.grid_width,
            "active": widget.active,
        },
    )
    session.commit()
    record_overview_widget_query_audit(
        query_request=query_request,
        widget=widget,
        generated_sql=generated_sql,
        actor=user.username,
    )

    return {
        "item": serialize_overview_widget(session, widget),
    }


@router.post("/overview/widgets/from-query")
def create_overview_widget_from_query(
    request: OverviewWidgetFromQueryRequest,
    principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    datasource = get_datasource_connector_by_key(session, request.datasource_key)

    if datasource is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Datasource does not exist.",
        )

    widget_key = build_client_widget_key(session, request.label, request.question)
    grid_width = normalize_overview_widget_grid_width(
        request.widget_type,
        None,
    )
    result_mode = normalize_overview_widget_result_mode(request.result_mode)
    widget = OverviewWidget(
        widget_key=widget_key,
        label=request.label,
        widget_type=request.widget_type,
        datasource_key=request.datasource_key,
        question=request.question,
        sql=request.sql,
        result_mode=result_mode,
        position=100,
        grid_width=grid_width,
        active=False,
        updated_by="client",
    )

    try:
        query_result = QueryResult(
            columns=list(dict.fromkeys(column for row in request.rows for column in row)),
            rows=request.rows,
        )
        result_payload = validate_overview_widget_result(widget, query_result)
        result_payload["result_mode"] = result_mode
        if result_mode == OVERVIEW_WIDGET_RESULT_INTERPRETATION:
            interpretation = interpret_overview_widget_result(datasource, widget, query_result)
            result_payload["answer"] = interpretation
            result_payload["interpretation"] = interpretation
            result_payload["value"] = interpretation
    except QueryExecutionError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.message,
        ) from exc
    except (
        ConfigurationError,
        SQLAlchemyError,
        SqlValidationError,
        ValueError,
        TypeError,
    ) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    session.add(widget)
    session.add(
        UserSavedMetric(
            owner_user_id=str(principal.user.id),
            owner_username=principal.session.username or principal.user.username,
            widget_key=widget.widget_key,
        )
    )
    record_admin_audit(
        session=session,
        actor=principal.session.username or principal.user.username,
        action="overview_widget.create_from_query",
        resource_type="overview_widget",
        resource_id=widget.widget_key,
        details={
            "label": widget.label,
            "widget_type": widget.widget_type,
            "datasource_key": widget.datasource_key,
            "result_mode": widget.result_mode,
            "active": widget.active,
        },
    )
    session.commit()

    return {
        "item": {
            **serialize_overview_widget_config(widget),
            "result": result_payload,
        },
    }


@router.post("/overview/widgets/title-suggestion")
def suggest_overview_widget_title(
    request: OverviewWidgetTitleSuggestionRequest,
    _principal: AuthenticatedSession = Depends(get_current_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        title = suggest_metric_title_with_llm(session, request)
    except (ConfigurationError, LlmProviderError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {"title": title}


@router.put("/overview/widgets/{widget_key}")
def update_overview_widget(
    widget_key: str,
    request: OverviewWidgetUpdateRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    widget = get_overview_widget(session, widget_key)

    if widget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Overview widget not found.",
        )

    datasource = get_datasource_connector_by_key(session, request.datasource_key)

    if datasource is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Datasource does not exist.",
        )

    query_request = build_overview_widget_query_request(
        connector=datasource,
        widget_key=widget.widget_key,
        question=request.question,
    )
    generated_sql = ""
    next_position = request.position if request.position is not None else widget.position
    next_grid_width = normalize_overview_widget_grid_width(
        request.widget_type,
        request.grid_width if request.grid_width is not None else widget.grid_width,
    )
    next_result_mode = normalize_overview_widget_result_mode(request.result_mode)
    next_active = request.active if request.active is not None else widget.active

    try:
        generated_sql = generate_overview_widget_sql(
            session=session,
            connector=datasource,
            query_request=query_request,
            actor=user.username,
        )
        probe_widget = OverviewWidget(
            widget_key=widget.widget_key,
            label=request.label,
            widget_type=request.widget_type,
            datasource_key=request.datasource_key,
            question=request.question,
            sql=generated_sql,
            result_mode=next_result_mode,
            position=next_position,
            grid_width=next_grid_width,
            active=next_active,
        )
        validate_overview_widget_result(
            probe_widget,
            execute_overview_sql(session, datasource, generated_sql),
        )
    except QueryExecutionError as exc:
        record_overview_widget_sql_error_audit(
            query_request=query_request,
            connector=datasource,
            sql=exc.sql or generated_sql,
            error_code=exc.code,
            error_message=exc.message,
            error_detail=exc.error_detail,
            actor=user.username,
        )
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.message,
        ) from exc
    except (
        ConfigurationError,
        SQLAlchemyError,
        SqlValidationError,
        ValueError,
        TypeError,
    ) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    widget.label = request.label
    widget.widget_type = request.widget_type
    widget.datasource_key = request.datasource_key
    widget.question = request.question
    widget.sql = generated_sql
    widget.result_mode = next_result_mode
    widget.position = next_position
    widget.grid_width = next_grid_width
    widget.active = next_active
    widget.updated_by = user.username

    record_admin_audit(
        session=session,
        actor=user.username,
        action="overview_widget.update",
        resource_type="overview_widget",
        resource_id=widget.widget_key,
        details={
            "label": widget.label,
            "widget_type": widget.widget_type,
            "datasource_key": widget.datasource_key,
            "result_mode": widget.result_mode,
            "position": widget.position,
            "grid_width": widget.grid_width,
            "active": widget.active,
        },
    )
    session.commit()
    record_overview_widget_query_audit(
        query_request=query_request,
        widget=widget,
        generated_sql=generated_sql,
        actor=user.username,
    )

    return {
        "item": serialize_overview_widget(session, widget),
    }


@router.patch("/overview/widgets/{widget_key}/state")
def update_overview_widget_state(
    widget_key: str,
    request: OverviewWidgetStateRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    widget = get_overview_widget(session, widget_key)

    if widget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Overview widget not found.",
        )

    widget.active = request.active

    if request.position is not None:
        widget.position = request.position

    if request.grid_width is not None:
        widget.grid_width = normalize_overview_widget_grid_width(
            widget.widget_type,
            request.grid_width,
        )

    widget.updated_by = user.username
    record_admin_audit(
        session=session,
        actor=user.username,
        action="overview_widget.state",
        resource_type="overview_widget",
        resource_id=widget.widget_key,
        details={
            "position": widget.position,
            "grid_width": widget.grid_width,
            "active": widget.active,
        },
    )
    session.commit()

    return {
        "item": serialize_overview_widget_config(widget),
    }


@router.delete("/overview/widgets/{widget_key}")
def delete_overview_widget(
    widget_key: str,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    widget = get_overview_widget(session, widget_key)

    if widget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Overview widget not found.",
        )

    record_admin_audit(
        session=session,
        actor=user.username,
        action="overview_widget.delete",
        resource_type="overview_widget",
        resource_id=widget.widget_key,
        details={
            "label": widget.label,
            "widget_type": widget.widget_type,
            "datasource_key": widget.datasource_key,
        },
    )
    session.delete(widget)
    session.commit()

    return {
        "status": "deleted",
        "widget_key": widget_key,
    }


@router.get("/audit/data-queries")
def get_data_query_audit(
    limit: int = 100,
    audit_type: str | None = None,
    output_classification: str | None = None,
    sql_contains: str | None = None,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        parsed_audit_type = coerce_data_query_audit_type(audit_type) if audit_type else None
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported data query audit type.",
        ) from exc

    try:
        parsed_output_classification = (
            OutputClassification(output_classification) if output_classification else None
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported output classification.",
        ) from exc

    logs = list_data_query_audit_logs(
        session,
        limit=min(limit, 500),
        audit_type=parsed_audit_type,
        output_classification=parsed_output_classification,
        sql_contains=sql_contains,
    )
    session.commit()

    return {
        "items": [
            {
                "id": item.id,
                "audit_type": get_data_query_audit_type(item),
                "occurred_at": serialize_datetime(item.occurred_at),
                "user_id": item.user_id,
                "datasource_id": item.datasource_id,
                "question": item.question,
                "answer": item.answer,
                "sql": item.sql,
                "llm_sql_language": item.llm_sql_language,
                "output_classification": item.output_classification,
                "metadata": json_loads(item.metadata_json),
            }
            for item in logs
        ],
        "viewer": user.username,
    }


@router.get("/audit/admin-events")
def get_admin_audit(
    limit: int = 100,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    logs = list_admin_audit_logs(session, limit=min(limit, 500))

    return {
        "items": [
            {
                "id": item.id,
                "occurred_at": serialize_datetime(item.occurred_at),
                "actor": item.actor,
                "action": item.action,
                "resource_type": item.resource_type,
                "resource_id": item.resource_id,
                "details": json_loads(item.details_json),
            }
            for item in logs
        ],
        "viewer": user.username,
    }


@router.get("/audit/settings")
def get_audit_settings(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {
        "data_query_retention_days": get_data_query_audit_retention_days(session),
        "viewer": user.username,
    }


@router.put("/audit/settings")
def update_audit_settings(
    request: AuditSettingsRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    set_setting(
        session,
        "data_query_audit_retention_days",
        str(request.data_query_retention_days),
        user.username,
    )
    apply_data_query_audit_retention(session)
    record_admin_audit(
        session=session,
        actor=user.username,
        action="audit.retention.update",
        resource_type="admin_setting",
        resource_id="data_query_audit_retention_days",
        details={"data_query_retention_days": request.data_query_retention_days},
    )
    session.commit()

    return {
        "data_query_retention_days": request.data_query_retention_days,
    }


@router.get("/prompts")
def get_prompts(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {
        "items": [serialize_prompt(prompt) for prompt in list_prompt_templates(session)],
        "viewer": user.username,
    }


@router.get("/prompts/{prompt_key}")
def get_prompt(
    prompt_key: str,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    prompt = get_prompt_template(session, prompt_key)

    if prompt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found.")

    return {
        "item": serialize_prompt(prompt),
        "viewer": user.username,
    }


@router.put("/prompts/{prompt_key}")
def update_prompt(
    prompt_key: str,
    request: PromptUpdateRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    prompt = get_prompt_template(session, prompt_key)

    if prompt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found.")

    prompt.name = request.name
    prompt.description = request.description
    prompt.system_prompt = request.system_prompt
    prompt.user_prompt_template = request.user_prompt_template
    prompt.active = request.active
    prompt.version += 1
    prompt.updated_by = user.username
    record_admin_audit(
        session=session,
        actor=user.username,
        action="prompt.update",
        resource_type="prompt_template",
        resource_id=prompt.prompt_key,
        details={"version": prompt.version},
    )
    session.commit()

    return {
        "item": serialize_prompt(prompt),
    }


@router.get("/datasources")
def get_datasources(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {
        "items": [
            serialize_datasource(connector) for connector in list_datasource_connectors(session)
        ],
        "viewer": user.username,
    }


@router.get("/datasource-types")
def get_datasource_types(
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    return {
        "items": [definition.serialize() for definition in get_connector_registry().list()],
        "viewer": user.username,
    }


@router.get("/extensions")
def get_extensions(
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    return {
        "items": [serialize_extension_record(record) for record in get_extension_manager().records],
        "admin_sections": [
            section.serialize() for section in get_api_registry().list_admin_sections()
        ],
        "viewer": user.username,
    }


@router.post("/datasources")
def create_datasource(
    request: DatasourceConnectorRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if request.connector_key == "metadata-db":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The metadata datasource is managed by GAARD.",
        )

    normalized_config = normalize_datasource_configuration_or_400(
        database_type=request.database_type,
        connection_config=request.connection_config,
        database_path=request.database_path,
        database_url=request.database_url,
        sql_dialect=request.sql_dialect,
    )

    license_service.ensure_datasource_type_allowed(normalized_config.database_type)

    existing = session.scalar(
        select(DatasourceConnector).where(
            DatasourceConnector.connector_key == request.connector_key
        )
    )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Datasource connector key already exists.",
        )

    connector = DatasourceConnector(
        connector_key=request.connector_key,
        name=request.name,
        database_type=normalized_config.database_type,
        database_url=normalized_config.database_url,
        sql_dialect=normalized_config.sql_dialect,
        active=request.active,
        updated_by=user.username,
    )
    session.add(connector)
    session.flush()

    if request.active:
        set_active_datasource_connector(session, connector, user.username)

    license_service.ensure_active_source_limit(list_datasource_connectors(session))

    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.create",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
        details={
            "database_type": connector.database_type,
            "active": connector.active,
        },
    )
    session.commit()

    return {"item": serialize_datasource(connector)}


@router.post("/datasources/excel-upload")
async def upload_excel_datasource(
    file: UploadFile = File(...),
    active: bool = False,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    filename = file.filename or ""
    if Path(filename).suffix.lower() != ".xlsx":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .xlsx files can be added as Excel datasources.",
        )

    ensure_excel_datasource_type_available()
    license_service.ensure_datasource_type_allowed("duckdb-excel")

    upload_dir = Path(settings.gaard_excel_upload_directory).expanduser().resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    target_path = safe_excel_upload_path(upload_dir, filename)

    try:
        with target_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                destination.write(chunk)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Excel file could not be saved: {exc}",
        ) from exc

    request = DatasourceConnectorRequest(
        connector_key=build_client_excel_datasource_key(session, filename),
        name=Path(filename).stem or target_path.stem,
        database_type="duckdb-excel",
        database_url=f"duckdb-excel:///{target_path.as_posix()}",
        sql_dialect="duckdb",
        active=active,
    )

    try:
        normalized_config = normalize_datasource_configuration_or_400(
            database_type=request.database_type,
            connection_config=request.connection_config,
            database_path=request.database_path,
            database_url=request.database_url,
            sql_dialect=request.sql_dialect,
        )
    except HTTPException:
        target_path.unlink(missing_ok=True)
        raise

    connector = DatasourceConnector(
        connector_key=request.connector_key,
        name=request.name,
        database_type=normalized_config.database_type,
        database_url=normalized_config.database_url,
        sql_dialect=normalized_config.sql_dialect,
        active=active,
        updated_by=user.username,
    )
    session.add(connector)
    session.flush()

    if active:
        set_active_datasource_connector(session, connector, user.username)

    license_service.ensure_active_source_limit(list_datasource_connectors(session))

    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.excel_upload",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
        details={
            "database_type": connector.database_type,
            "filename": Path(filename).name,
            "active": connector.active,
        },
    )
    session.commit()

    return {"item": serialize_datasource(connector)}


@router.post("/datasources/{connector_id}/state")
def update_datasource_state(
    connector_id: int,
    request: DatasourceStateRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    if is_system_datasource_connector(connector):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The metadata datasource is managed by GAARD.",
        )

    license_service.ensure_datasource_type_allowed(connector.database_type)

    if request.active:
        set_active_datasource_connector(session, connector, user.username)
    else:
        connector.active = False
        connector.updated_by = user.username

    license_service.ensure_active_source_limit(list_datasource_connectors(session))
    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.state.update",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
        details={
            "database_type": connector.database_type,
            "active": connector.active,
        },
    )
    session.commit()

    return {"item": serialize_datasource(connector)}


@router.put("/datasources/{connector_id}")
def update_datasource(
    connector_id: int,
    request: DatasourceConnectorUpdateRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    if is_system_datasource_connector(connector):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The metadata datasource is managed by GAARD.",
        )

    normalized_config = normalize_datasource_configuration_or_400(
        database_type=request.database_type,
        connection_config=request.connection_config,
        database_path=request.database_path,
        database_url=request.database_url,
        sql_dialect=request.sql_dialect,
    )

    license_service.ensure_datasource_type_allowed(normalized_config.database_type)

    connector.name = request.name
    connector.database_type = normalized_config.database_type
    connector.database_url = normalized_config.database_url
    connector.sql_dialect = normalized_config.sql_dialect
    connector.active = request.active
    connector.updated_by = user.username

    if request.active:
        set_active_datasource_connector(session, connector, user.username)

    license_service.ensure_active_source_limit(list_datasource_connectors(session))

    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.update",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
        details={
            "database_type": connector.database_type,
            "active": connector.active,
        },
    )
    session.commit()

    return {"item": serialize_datasource(connector)}


@router.delete("/datasources/{connector_id}")
def delete_datasource(
    connector_id: int,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    if is_system_datasource_connector(connector):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The metadata datasource is managed by GAARD.",
        )

    connector_key = connector.connector_key
    database_type = connector.database_type
    was_active = connector.active
    schema_cache = get_datasource_schema_cache(session, connector.id)
    if schema_cache is not None:
        session.delete(schema_cache)
    session.delete(connector)

    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.delete",
        resource_type="datasource_connector",
        resource_id=connector_key,
        details={
            "database_type": database_type,
            "active": was_active,
        },
    )
    session.commit()

    return {"status": "deleted"}


@router.post("/datasources/{connector_id}/activate")
def activate_datasource(
    connector_id: int,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    if is_system_datasource_connector(connector):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The metadata datasource cannot be activated as the query datasource.",
        )

    set_active_datasource_connector(session, connector, user.username)
    license_service.ensure_active_source_limit(list_datasource_connectors(session))
    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.activate",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
    )
    session.commit()

    return {"item": serialize_datasource(connector)}


@router.post("/datasources/{connector_id}/test")
def test_datasource(
    connector_id: int,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    license_service.ensure_datasource_type_allowed(connector.database_type)

    try:
        test_datasource_connection(connector)
    except Exception as exc:
        record_admin_audit(
            session=session,
            actor=user.username,
            action="datasource.test_failed",
            resource_type="datasource_connector",
            resource_id=connector.connector_key,
            details={"error": str(exc)},
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Connection test failed: {exc}",
        ) from exc

    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.test",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
    )
    session.commit()

    return {"status": "ok"}


@router.post("/datasources/test")
def test_datasource_from_request(
    request: DatasourceConnectionTestRequest,
) -> dict[str, Any]:
    normalized_config = normalize_datasource_configuration_or_400(
        database_type=request.database_type,
        connection_config=request.connection_config,
        database_path=request.database_path,
        database_url=request.database_url,
    )

    connector = DatasourceConnector(
        connector_key="__preview__",
        name="__preview__",
        database_type=normalized_config.database_type,
        database_url=normalized_config.database_url,
        sql_dialect=normalized_config.sql_dialect,
        active=False,
    )
    license_service.ensure_datasource_type_allowed(connector.database_type)
    test_datasource_connection(connector)
    return {"status": "ok"}


@router.post("/datasources/{connector_id}/introspect")
def introspect_datasource(
    connector_id: int,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    license_service.ensure_datasource_type_allowed(connector.database_type)

    try:
        cache = introspect_datasource_connector(session, connector, user.username)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Schema introspection failed: {exc}",
        ) from exc

    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.introspect",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
    )
    session.commit()

    return {"item": serialize_datasource_schema(cache)}


@router.get("/datasources/{connector_id}/schema")
def get_datasource_schema(
    connector_id: int,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    cache = get_datasource_schema_cache(session, connector.id)

    if cache is None:
        license_service.ensure_datasource_type_allowed(connector.database_type)
        cache = introspect_datasource_connector(session, connector, user.username)
        session.commit()

    return {
        "item": serialize_datasource_schema(cache),
        "viewer": user.username,
    }


@router.put("/datasources/{connector_id}/schema/tables")
def update_datasource_schema_tables(
    connector_id: int,
    request: DatasourceSchemaTableSettingsRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    cache = get_datasource_schema_cache(session, connector.id)

    if cache is None:
        license_service.ensure_datasource_type_allowed(connector.database_type)
        cache = introspect_datasource_connector(session, connector, user.username)

    cache = update_schema_table_settings(
        session=session,
        cache=cache,
        table_settings={"tables": request.tables},
        actor=user.username,
    )
    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.schema.update",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
        details={"tables": list(request.tables.keys())},
    )
    session.commit()

    return {"item": serialize_datasource_schema(cache)}


@router.get("/business-logic-suggestions")
def get_business_logic_suggestions(
    connector_id: int | None = None,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connectors = []
    if connector_id is not None:
        connector = get_datasource_connector(session, connector_id)
        if connector is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Datasource connector not found.",
            )
        connectors = [connector]
    else:
        connectors = get_active_datasource_connectors(session)

    if not connectors:
        return {
            "datasource": None,
            "datasources": [],
            "items": [],
            "statuses": [BUSINESS_LOGIC_STATUS_PENDING, BUSINESS_LOGIC_STATUS_ACTIVE],
            "viewer": user.username,
        }

    suggestions = list_business_logic_suggestions_for_connectors(
        session,
        [connector.id for connector in connectors],
    )

    return {
        "datasource": serialize_datasource(connectors[0]),
        "datasources": [serialize_datasource(connector) for connector in connectors],
        "items": [
            serialize_business_logic_suggestion(suggestion)
            for suggestion in suggestions
        ],
        "statuses": [BUSINESS_LOGIC_STATUS_PENDING, BUSINESS_LOGIC_STATUS_ACTIVE],
        "viewer": user.username,
    }


@router.put("/business-logic-suggestions/{suggestion_id}")
def update_business_logic_suggestion(
    suggestion_id: int,
    request: BusinessLogicSuggestionUpdateRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    suggestion = get_business_logic_suggestion(session, suggestion_id)

    if suggestion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business logic suggestion not found.",
        )

    if request.title is not None and not request.title.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Business logic suggestion title cannot be empty.",
        )

    if request.rule_text is not None and not request.rule_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Business logic suggestion rule text cannot be empty.",
        )

    if request.enabled is not None:
        suggestion = set_business_logic_suggestion_enabled(
            session=session,
            suggestion=suggestion,
            enabled=request.enabled,
            actor=user.username,
        )

    if request.title is not None or request.rule_text is not None:
        suggestion = update_business_logic_suggestion_content(
            suggestion=suggestion,
            title=request.title,
            rule_text=request.rule_text,
            actor=user.username,
        )

    record_admin_audit(
        session=session,
        actor=user.username,
        action="business_logic_suggestion.update",
        resource_type="business_logic_suggestion",
        resource_id=str(suggestion.id),
        details={
            "enabled": suggestion.enabled,
            "status": suggestion.status,
            "content_updated": request.title is not None or request.rule_text is not None,
            "connector_id": suggestion.connector_id,
        },
    )
    session.commit()

    return {"item": serialize_business_logic_suggestion(suggestion)}


@router.delete("/business-logic-suggestions/{suggestion_id}")
def remove_business_logic_suggestion(
    suggestion_id: int,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    suggestion = get_business_logic_suggestion(session, suggestion_id)

    if suggestion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business logic suggestion not found.",
        )

    connector_id = suggestion.connector_id
    delete_business_logic_suggestion(session, suggestion)
    record_admin_audit(
        session=session,
        actor=user.username,
        action="business_logic_suggestion.delete",
        resource_type="business_logic_suggestion",
        resource_id=str(suggestion_id),
        details={"connector_id": connector_id},
    )
    session.commit()

    return {"status": "deleted"}


@router.get("/llm-config")
def get_llm_config(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {
        "item": serialize_llm_config(session),
        "viewer": user.username,
    }


@router.put("/llm-config")
def update_llm_config(
    request: LlmConfigRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if request.provider != "openai-compatible":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only openai-compatible LLM provider is supported.",
        )

    license_service.ensure_models_allowed(request.model)

    current_llm_config = get_llm_runtime_config(session)
    current_query_config = get_query_runtime_config(session)
    timeout_seconds = request.timeout_seconds or current_llm_config.timeout_seconds
    intent_classification_mode = (
        request.intent_classification_mode or current_query_config.intent_classification_mode
    )
    sql_generation_mode = request.sql_generation_mode or current_query_config.sql_generation_mode
    result_interpretation_mode = (
        request.result_interpretation_mode or current_query_config.result_interpretation_mode
    )
    output_classification_mode = (
        request.output_classification_mode or current_query_config.output_classification_mode
    )
    query_max_rows = request.query_max_rows or current_query_config.query_max_rows
    query_timeout_seconds = (
        request.query_timeout_seconds or current_query_config.query_timeout_seconds
    )
    api_key = None
    if request.clear_api_key:
        api_key = "change-me"
    elif request.api_key is not None and request.api_key.strip():
        api_key = request.api_key.strip()

    set_llm_runtime_config(
        session=session,
        provider=request.provider,
        base_url=request.base_url,
        api_key=api_key,
        model=request.model,
        timeout_seconds=timeout_seconds,
        extra_body=request.extra_body,
        actor=user.username,
    )
    set_query_runtime_config(
        session=session,
        intent_classification_mode=intent_classification_mode,
        sql_generation_mode=sql_generation_mode,
        result_interpretation_mode=result_interpretation_mode,
        output_classification_mode=output_classification_mode,
        query_max_rows=query_max_rows,
        query_timeout_seconds=query_timeout_seconds,
        actor=user.username,
    )
    record_admin_audit(
        session=session,
        actor=user.username,
        action="llm_config.update",
        resource_type="admin_setting",
        resource_id="llm_config",
        details={
            "provider": request.provider,
            "base_url": request.base_url,
            "model": request.model,
            "timeout_seconds": timeout_seconds,
            "extra_body": request.extra_body,
            "intent_classification_mode": intent_classification_mode,
            "sql_generation_mode": sql_generation_mode,
            "result_interpretation_mode": result_interpretation_mode,
            "output_classification_mode": output_classification_mode,
            "query_max_rows": query_max_rows,
            "query_timeout_seconds": query_timeout_seconds,
        },
    )
    session.commit()

    return {"item": serialize_llm_config(session)}


@router.post("/llm-config/test")
def test_llm_config(
    request: LlmConfigRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    license_service.ensure_models_allowed(request.model)

    try:
        return {
            "item": test_llm_runtime_config(
                session,
                provider=request.provider,
                base_url=request.base_url,
                api_key=request.api_key,
                clear_api_key=request.clear_api_key,
                model=request.model,
                timeout_seconds=request.timeout_seconds,
                extra_body=request.extra_body,
            ),
            "viewer": user.username,
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/reasoning-config")
def get_reasoning_config(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {
        "item": serialize_reasoning_config(session),
        "viewer": user.username,
    }


@router.put("/reasoning-config")
def update_reasoning_config(
    request: ReasoningConfigRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    set_query_runtime_config(
        session=session,
        intent_classification_mode=request.intent_classification_mode,
        sql_generation_mode=request.sql_generation_mode,
        result_interpretation_mode=request.result_interpretation_mode,
        output_classification_mode=request.output_classification_mode,
        query_max_rows=request.query_max_rows,
        query_timeout_seconds=request.query_timeout_seconds,
        actor=user.username,
        analysis_loop_count=request.analysis_loop_count,
        analysis_auto_enable_business_logic=request.analysis_auto_enable_business_logic,
    )
    record_admin_audit(
        session=session,
        actor=user.username,
        action="reasoning_config.update",
        resource_type="admin_setting",
        resource_id="reasoning_config",
        details=request.model_dump(mode="json"),
    )
    session.commit()

    return {"item": serialize_reasoning_config(session)}


@router.get("/governance-policy")
def get_governance_policy(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {
        "item": serialize_governance_policy(session),
        "viewer": user.username,
    }


@router.put("/governance-policy")
def update_governance_policy(
    request: GovernancePolicyRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        config = set_governance_policy_config(
            session=session,
            config=request.model_dump(mode="json"),
            actor=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    record_admin_audit(
        session=session,
        actor=user.username,
        action="governance_policy.update",
        resource_type="admin_setting",
        resource_id="governance_policy",
        details=config,
    )
    session.commit()

    return {"item": serialize_governance_policy(session)}


@router.get("/schema-cache")
def get_schema_cache_settings(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    configured_ttl = int(
        get_setting(
            session,
            "schema_cache_ttl_seconds",
            str(settings.gaard_schema_cache_ttl_seconds),
        )
    )

    return {
        "ttl_seconds": configured_ttl,
        "runtime_ttl_seconds": schema_context_cache.ttl_seconds,
        "cache_key": get_runtime_schema_cache_key(session),
        "viewer": user.username,
    }


@router.put("/schema-cache")
def update_schema_cache_settings(
    request: SchemaCacheSettingsRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    schema_context_cache.ttl_seconds = request.ttl_seconds
    set_setting(session, "schema_cache_ttl_seconds", str(request.ttl_seconds), user.username)
    record_admin_audit(
        session=session,
        actor=user.username,
        action="schema_cache.ttl.update",
        resource_type="admin_setting",
        resource_id="schema_cache_ttl_seconds",
        details={"ttl_seconds": request.ttl_seconds},
    )
    session.commit()

    return {
        "ttl_seconds": request.ttl_seconds,
        "runtime_ttl_seconds": schema_context_cache.ttl_seconds,
    }


@router.post("/schema-cache/invalidate")
def invalidate_schema_cache(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    cache_key = get_runtime_schema_cache_key(session)
    schema_context_cache.invalidate(cache_key)
    record_admin_audit(
        session=session,
        actor=user.username,
        action="schema_cache.invalidate",
        resource_type="schema_cache",
        resource_id=cache_key,
    )
    session.commit()

    return {
        "status": "invalidated",
        "cache_key": cache_key,
    }


@router.get("/integrations")
def get_integration_stubs(user: AdminUser = Depends(get_current_admin)) -> dict[str, Any]:
    return {
        "items": [
            {
                "key": "freeipa",
                "name": "FreeIPA identity connector",
                "status": "planned",
            },
            {
                "key": "sql_validation_rules",
                "name": "SQL validation rules",
                "status": "planned",
            },
            {
                "key": "result_interpretation_policies",
                "name": "Result interpretation policies",
                "status": "planned",
            },
        ],
        "viewer": user.username,
    }


@router.get("/license")
def get_license(user: AdminUser = Depends(get_current_admin)) -> dict[str, Any]:
    return {
        **license_service.status(),
        "managed_features": {
            "freeipa": False,
            "datasource_connectors": license_service.state.features.get("non_sql_sources", False),
            "sql_validation_rules": False,
            "result_interpretation_policies": False,
        },
        "viewer": user.username,
    }


@router.get("/license/status")
def get_license_status(user: AdminUser = Depends(get_current_admin)) -> dict[str, Any]:
    return license_service.status()


@router.post("/license/check")
def check_license_now(user: AdminUser = Depends(get_current_admin)) -> dict[str, Any]:
    return license_service.refresh(force=True).serialize_status()


@router.put("/license/key")
def update_license_key(
    request: LicenseKeyRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        if request.clear_license_key:
            state = license_service.clear_license_key(user.username)
            details: dict[str, bool | str] = {"cleared": True}
        else:
            if request.license_key is None:
                raise ValueError("License key is required.")
            state = license_service.set_license_key(request.license_key, user.username)
            details = {
                "cleared": False,
                "license_key_preview": redact_license_key(request.license_key),
            }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    record_admin_audit(
        session=session,
        actor=user.username,
        action="license.key.update",
        resource_type="license",
        resource_id=state.plan,
        details=details,
    )
    session.commit()

    return state.serialize_status()


def package_update_audit_details(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result["status"],
        "plan": result["plan"],
        "installed_count": result["installed_count"],
        "packs": [
            {
                "pack": pack_result.get("pack"),
                "status": pack_result.get("status"),
                "packages": [
                    {
                        "name": package.get("name"),
                        "action": package.get("action"),
                        "available_version": package.get("available_version"),
                    }
                    for package in pack_result.get("packages", [])
                ],
            }
            for pack_result in result["packs"]
        ],
    }


def run_package_update_job(
    *,
    job_id: str,
    actor: str,
    license_state: Any,
    license_key: str,
    instance_id: str,
) -> None:
    def report(stage: str, percent: int, message: str) -> None:
        package_update_jobs.update(
            job_id,
            stage=stage,
            percent=percent,
            message=message,
        )

    try:
        result = package_update_service.update_packages(
            license_state=license_state,
            license_key=license_key,
            instance_id=instance_id,
            progress=report,
        )
        with create_session() as session:
            record_admin_audit(
                session=session,
                actor=actor,
                action="license.packages.update",
                resource_type="license",
                resource_id=license_state.plan,
                details=package_update_audit_details(result),
            )
            session.commit()
        package_update_jobs.complete(job_id, result)
    except Exception as exc:
        package_update_jobs.fail(job_id, exc)


@router.post("/license/packages/update", status_code=status.HTTP_202_ACCEPTED)
def update_license_packages(
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    license_state, license_key, instance_id = license_service.package_download_context()
    job = package_update_jobs.create()
    thread = threading.Thread(
        target=run_package_update_job,
        kwargs={
            "job_id": job.job_id,
            "actor": user.username,
            "license_state": license_state,
            "license_key": license_key,
            "instance_id": instance_id,
        },
        name=f"gaard-package-update-{job.job_id}",
        daemon=True,
    )
    thread.start()

    return job.serialize()


@router.get("/license/packages/update/{job_id}")
def get_license_package_update_job(
    job_id: str,
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    job = package_update_jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Package update job was not found.",
        )
    return job.serialize()
