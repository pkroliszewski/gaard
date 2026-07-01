import json
import re
from datetime import datetime
from typing import Any, Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from gaard_plugin_api import ExtensionRecord
from gaard_core.errors import (
    ConfigurationError,
    LlmProviderError,
    QueryExecutionError,
    SqlValidationError,
)
from gaard_core.query_pipeline.models import (
    OutputClassification,
    QueryRequest,
    QueryResponse,
    QueryResult,
)
from gaard_core.sql_validator.select_only import SelectOnlySqlValidator
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from gaard_api.admin.database import get_session
from gaard_api.admin.models import (
    AdminSession,
    AdminUser,
    BusinessLogicSuggestion,
    DatasourceConnector,
    DatasourceSchemaCache,
    OverviewWidget,
    PromptTemplate,
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
    list_business_logic_suggestions,
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
from gaard_api.extensions import get_api_registry, get_connector_registry, get_extension_manager

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    token: str
    username: str
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class MeResponse(BaseModel):
    username: str
    must_change_password: bool


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
    grid_width: int | None = Field(default=None, ge=1, le=4)
    active: bool | None = None


class OverviewWidgetCreateRequest(OverviewWidgetUpdateRequest):
    widget_key: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_-]+$")
    position: int = Field(default=100, ge=10)
    grid_width: int | None = Field(default=None, ge=1, le=4)
    active: bool = True


class OverviewWidgetStateRequest(BaseModel):
    active: bool
    position: int | None = Field(default=None, ge=10)
    grid_width: int | None = Field(default=None, ge=1, le=4)


class OverviewWidgetFromQueryRequest(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    widget_type: str = Field(default=OVERVIEW_WIDGET_TABLE, pattern=r"^(scalar|timeseries|table)$")
    datasource_key: str = Field(default="default", min_length=1, max_length=255)
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    result_mode: str = Field(
        default=OVERVIEW_WIDGET_RESULT_DATA, pattern=r"^(data|interpretation)$"
    )


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


class DatasourceSchemaTableSettingsRequest(BaseModel):
    tables: dict[str, dict[str, Any]]


class BusinessLogicSuggestionUpdateRequest(BaseModel):
    enabled: bool | None = None
    title: str | None = Field(default=None, min_length=1)
    rule_text: str | None = Field(default=None, min_length=1)


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
        return 4 if widget_type in {OVERVIEW_WIDGET_TABLE, OVERVIEW_WIDGET_TIMESERIES} else 1

    return max(1, min(4, int(grid_width)))


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
    SelectOnlySqlValidator(dialect=connector.sql_dialect).validate(sql)

    return (
        get_connector_registry()
        .get(connector.database_type)
        .executor_factory(
            connector.database_url,
            get_query_runtime_config(session).query_max_rows,
        )
        .execute(sql)
    )


def generate_overview_widget_sql(
    session: Session,
    connector: DatasourceConnector,
    query_request: QueryRequest,
    actor: str,
) -> str:
    schema_cache = get_or_create_datasource_schema_cache(session, connector, actor)
    session.commit()

    from gaard_api.api.v1.query import create_sql_generator

    generated_sql = create_sql_generator((connector, schema_cache)).generate(query_request)
    SelectOnlySqlValidator(dialect=connector.sql_dialect).validate(generated_sql.sql)

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


def get_current_admin_allow_password_change(
    authorization: Annotated[str | None, Header()] = None,
    session: Session = Depends(get_session),
) -> AdminUser:
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

    return user


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

    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    token = create_session_token()
    session.add(AdminSession(token_hash=hash_token(token), user_id=user.id))
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
    )


@router.get("/me", response_model=MeResponse)
def get_me(
    user: AdminUser = Depends(get_current_admin_allow_password_change),
) -> MeResponse:
    return MeResponse(
        username=user.username,
        must_change_password=user.must_change_password,
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
        "license": {
            "edition": "community",
            "status": "active",
        },
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
        validate_overview_widget_result(
            widget,
            execute_overview_sql(session, datasource, request.sql),
        )
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
    record_admin_audit(
        session=session,
        actor="client",
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
        "item": serialize_overview_widget_config(widget),
    }


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

    try:
        normalized_config = normalize_datasource_configuration(
            database_type=request.database_type,
            connection_config=request.connection_config,
            database_path=request.database_path,
            database_url=request.database_url,
            sql_dialect=request.sql_dialect,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

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

    try:
        normalized_config = normalize_datasource_configuration(
            database_type=request.database_type,
            connection_config=request.connection_config,
            database_path=request.database_path,
            database_url=request.database_url,
            sql_dialect=request.sql_dialect,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    connector.name = request.name
    connector.database_type = normalized_config.database_type
    connector.database_url = normalized_config.database_url
    connector.sql_dialect = normalized_config.sql_dialect
    connector.active = request.active
    connector.updated_by = user.username

    if request.active:
        set_active_datasource_connector(session, connector, user.username)

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
    try:
        normalized_config = normalize_datasource_configuration(
            database_type=request.database_type,
            connection_config=request.connection_config,
            database_path=request.database_path,
            database_url=request.database_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    connector = DatasourceConnector(
        connector_key="__preview__",
        name="__preview__",
        database_type=normalized_config.database_type,
        database_url=normalized_config.database_url,
        sql_dialect=normalized_config.sql_dialect,
        active=False,
    )
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
    connector = (
        get_datasource_connector(session, connector_id)
        if connector_id is not None
        else get_active_datasource_connector(session)
    )

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    return {
        "datasource": serialize_datasource(connector),
        "items": [
            serialize_business_logic_suggestion(suggestion)
            for suggestion in list_business_logic_suggestions(session, connector.id)
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
        "edition": "community",
        "status": "active",
        "managed_features": {
            "freeipa": False,
            "datasource_connectors": False,
            "sql_validation_rules": False,
            "result_interpretation_policies": False,
        },
        "viewer": user.username,
    }
