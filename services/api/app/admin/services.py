import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy import delete, desc, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from gaard_connectors.sqlalchemy.introspector import SQLAlchemySchemaIntrospector
from gaard_core.errors import LlmProviderError
from gaard_core.llm_output import remove_thinking_blocks
from gaard_core.query_pipeline.models import OutputClassification, QueryRequest, QueryResponse
from gaard_core.schema.models import ColumnInfo, DatabaseSchema, TableInfo
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage

from app.admin.defaults import DEFAULT_GOVERNANCE_POLICY_CONFIG
from app.admin.database import create_session
from app.admin.models import (
    AdminAuditLog,
    AdminSetting,
    BusinessLogicSuggestion,
    BusinessKnowledgeClaim,
    DataQueryAuditLog,
    DataQueryAuditType,
    DatasourceConnector,
    DatasourceSchemaCache,
    OverviewWidget,
    PromptTemplate,
)
from app.core.settings import settings


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str) -> Any:
    return json.loads(value or "{}")


SUPPORTED_DATABASE_TYPES = {
    "sqlite": ("sqlite://", "sqlite"),
    "postgresql": ("postgresql://", "postgresql+psycopg://"),
    "mysql": ("mysql://", "mysql+pymysql://"),
}


def validate_datasource_url(database_type: str, database_url: str) -> None:
    prefixes = SUPPORTED_DATABASE_TYPES.get(database_type)

    if prefixes is None:
        raise ValueError("Unsupported datasource type.")

    if not database_url.startswith(prefixes):
        raise ValueError(
            f"Datasource URL for {database_type} must start with one of: "
            f"{', '.join(prefixes)}"
        )


def mask_database_url(database_url: str) -> str:
    if "://" not in database_url or "@" not in database_url:
        return database_url

    scheme, rest = database_url.split("://", 1)
    credentials, host = rest.split("@", 1)

    if ":" not in credentials:
        return f"{scheme}://***@{host}"

    username, _password = credentials.split(":", 1)
    return f"{scheme}://{username}:***@{host}"


def record_admin_audit(
    session: Session,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AdminAuditLog(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details_json=json_dumps(details or {}),
        )
    )


DATA_QUERY_AUDIT_INFO = DataQueryAuditType.INFO.value
DATA_QUERY_AUDIT_SQL_ERROR = DataQueryAuditType.SQL_ERROR.value
DATA_QUERY_AUDIT_ACCESS_ERROR = DataQueryAuditType.ACCESS_ERROR.value

ACCESS_ERROR_INTENT_CLASSIFICATION = "access.intent_classification"
ACCESS_ERROR_SQL_VALIDATION = "access.sql_validation"

SQL_ERROR_SCHEMA_MISSING_TABLE = "schema.missing_table"
SQL_ERROR_SCHEMA_MISSING_COLUMN = "schema.missing_column"
SQL_ERROR_DIALECT_SYNTAX = "dialect.syntax"
SQL_ERROR_PERMISSION_ACCESS_DENIED = "permission.access_denied"
SQL_ERROR_RUNTIME_DATA_TYPE = "runtime.data_type"
SQL_ERROR_LLM_PROVIDER = "llm.provider_error"
SQL_ERROR_UNKNOWN = "unknown"

BUSINESS_LOGIC_STATUS_PENDING = "pending"
BUSINESS_LOGIC_STATUS_ACTIVE = "active"
BUSINESS_LOGIC_SAFETY_SAFE = "safe"
BUSINESS_LOGIC_SAFETY_REVIEW = "review"
BUSINESS_LOGIC_LEARNING_STATUS_SKIPPED = "skipped"

OVERVIEW_WIDGET_SCALAR = "scalar"
OVERVIEW_WIDGET_TIMESERIES = "timeseries"
OVERVIEW_WIDGET_TABLE = "table"
OVERVIEW_WIDGET_RESULT_DATA = "data"
OVERVIEW_WIDGET_RESULT_INTERPRETATION = "interpretation"

LLM_SETTING_PROVIDER = "gaard_llm_provider"
LLM_SETTING_BASE_URL = "gaard_llm_base_url"
LLM_SETTING_API_KEY = "gaard_llm_api_key"
LLM_SETTING_MODEL = "gaard_llm_model"
LLM_SETTING_TIMEOUT_SECONDS = "gaard_llm_timeout_seconds"
LLM_SETTING_EXTRA_BODY = "gaard_llm_extra_body"

INTENT_CLASSIFICATION_MODE_SETTING = "gaard_intent_classification_mode"
SQL_GENERATION_MODE_SETTING = "gaard_sql_generation_mode"
RESULT_INTERPRETATION_MODE_SETTING = "gaard_result_interpretation_mode"
OUTPUT_CLASSIFICATION_MODE_SETTING = "gaard_output_classification_mode"
INVESTIGATION_MODE_SETTING = "gaard_investigation_mode"
INVESTIGATION_AMBIGUITY_MODE_SETTING = "gaard_investigation_ambiguity_mode"
QUERY_MAX_ROWS_SETTING = "gaard_query_max_rows"
QUERY_TIMEOUT_SECONDS_SETTING = "gaard_query_timeout_seconds"
GOVERNANCE_POLICY_SETTING = "gaard_governance_policy"

SYSTEM_DATASOURCE_CONNECTOR_KEYS = {"metadata-db"}


@dataclass(frozen=True)
class LlmRuntimeConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    extra_body: dict[str, Any]
    timeout_seconds: int


@dataclass(frozen=True)
class QueryRuntimeConfig:
    intent_classification_mode: str
    sql_generation_mode: str
    result_interpretation_mode: str
    output_classification_mode: str
    investigation_mode: str
    investigation_ambiguity_mode: str
    query_max_rows: int
    query_timeout_seconds: int


def record_data_query_audit(
    request: QueryRequest,
    response: QueryResponse,
) -> DataQueryAuditLog | None:
    output_classification = coerce_output_classification(
        response.metadata.get("output_classification")
    )

    return _record_data_query_audit(
        request=request,
        answer=response.answer,
        sql=response.sql,
        audit_type=DataQueryAuditType.INFO,
        output_classification=output_classification,
        metadata=response.metadata,
    )


def record_data_query_sql_error_audit(
    request: QueryRequest,
    sql: str,
    error_code: str,
    error_message: str,
    error_detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> DataQueryAuditLog | None:
    audit_metadata = {
        "error_category": SQL_ERROR_UNKNOWN,
        "error_code": error_code,
        "error_message": error_message,
        "error_detail": error_detail,
        "datasource_id": request.datasource_id,
        "user_id": request.user_id,
    }
    audit_metadata.update(metadata or {})

    return _record_data_query_audit(
        request=request,
        answer=error_message,
        sql=sql,
        audit_type=DataQueryAuditType.SQL_ERROR,
        output_classification=OutputClassification.UNKNOWN,
        metadata=audit_metadata,
    )


def record_data_query_pipeline_error_audit(
    request: QueryRequest,
    sql: str,
    error_code: str,
    error_message: str,
    pipeline_phase: str,
    error_detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> DataQueryAuditLog | None:
    audit_metadata = {
        "error_category": SQL_ERROR_LLM_PROVIDER
        if error_code == "LLM_PROVIDER_ERROR"
        else SQL_ERROR_UNKNOWN,
        "error_code": error_code,
        "error_message": error_message,
        "error_detail": error_detail,
        "pipeline_phase": pipeline_phase,
        "datasource_id": request.datasource_id,
        "user_id": request.user_id,
    }
    audit_metadata.update(metadata or {})

    return _record_data_query_audit(
        request=request,
        answer=error_message,
        sql=sql,
        audit_type=DataQueryAuditType.SQL_ERROR,
        output_classification=OutputClassification.UNKNOWN,
        metadata=audit_metadata,
    )


def record_data_query_access_error_audit(
    request: QueryRequest,
    answer: str,
    reason: str,
    sql: str = "",
    error_code: str = "ACCESS_ERROR",
    error_detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> DataQueryAuditLog | None:
    audit_metadata = {
        "error_category": reason,
        "error_code": error_code,
        "error_message": answer,
        "error_detail": error_detail,
        "datasource_id": request.datasource_id,
        "user_id": request.user_id,
    }
    audit_metadata.update(metadata or {})

    return _record_data_query_audit(
        request=request,
        answer=answer,
        sql=sql,
        audit_type=DataQueryAuditType.ACCESS_ERROR,
        output_classification=OutputClassification.UNKNOWN,
        metadata=audit_metadata,
    )


def _record_data_query_audit(
    request: QueryRequest,
    answer: str,
    sql: str,
    audit_type: DataQueryAuditType | str,
    output_classification: OutputClassification | str,
    metadata: dict[str, Any],
) -> DataQueryAuditLog | None:
    try:
        session = create_session()
    except SQLAlchemyError:
        return None

    try:
        apply_data_query_audit_retention(session)
        audit_metadata = dict(metadata)
        audit_metadata.pop("audit_type", None)
        audit_metadata.pop("output_classification", None)
        log = DataQueryAuditLog(
            type=coerce_data_query_audit_type(audit_type),
            output_classification=coerce_output_classification(output_classification),
            user_id=request.user_id,
            datasource_id=request.datasource_id,
            question=request.question,
            answer=answer,
            sql=sql,
            metadata_json=json_dumps(audit_metadata),
        )
        session.add(log)
        session.commit()
        return log
    except SQLAlchemyError:
        session.rollback()
        return None
    finally:
        session.close()


def get_setting(session: Session, key: str, default: str) -> str:
    setting = session.get(AdminSetting, key)

    if setting is None:
        return default

    return setting.value


def get_int_setting(
    session: Session,
    key: str,
    default: int,
    minimum: int = 1,
) -> int:
    value = get_setting(session, key, str(default))

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default

    return max(minimum, parsed)


def set_setting(session: Session, key: str, value: str, actor: str) -> AdminSetting:
    setting = session.get(AdminSetting, key)

    if setting is None:
        setting = AdminSetting(key=key, value=value, updated_by=actor)
        session.add(setting)
    else:
        setting.value = value
        setting.updated_by = actor

    return setting


def default_governance_policy_config() -> dict[str, Any]:
    return json.loads(json_dumps(DEFAULT_GOVERNANCE_POLICY_CONFIG))


def normalize_bool_setting(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value

    raise ValueError(f"{field_name} must be a boolean.")


def normalize_string_list(value: Any, field_name: str, *, lower: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings.")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} must contain only strings.")
        text = item.strip()
        if not text:
            continue
        text = text.lower() if lower else text
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)

    return normalized


def normalize_forbidden_columns(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ValueError("privacy.forbidden_columns must be an object.")

    normalized: dict[str, list[str]] = {}
    for table_name, columns in value.items():
        if not isinstance(table_name, str) or not table_name.strip():
            raise ValueError("privacy.forbidden_columns table names must be non-empty strings.")
        normalized_columns = normalize_string_list(
            columns,
            f"privacy.forbidden_columns.{table_name}",
        )
        if normalized_columns:
            normalized[table_name.strip()] = normalized_columns

    return normalized


def normalize_pii_column_names(value: Any) -> dict[str, list[str]]:
    if isinstance(value, list):
        return {"default": normalize_string_list(value, "pii_column_names", lower=True)}

    if not isinstance(value, dict):
        raise ValueError("pii_column_names must be an object of string lists.")

    normalized: dict[str, list[str]] = {}
    for category, column_names in value.items():
        if not isinstance(category, str) or not category.strip():
            raise ValueError("pii_column_names categories must be non-empty strings.")
        normalized_columns = normalize_string_list(
            column_names,
            f"pii_column_names.{category}",
            lower=True,
        )
        if normalized_columns:
            normalized[category.strip()] = normalized_columns

    return normalized


def normalize_governance_policy_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Governance policy must be a JSON object.")

    defaults = default_governance_policy_config()
    final_answer_input = value.get("final_answer", {})
    sql_input = value.get("sql", {})
    privacy_input = value.get("privacy", {})

    if not isinstance(final_answer_input, dict):
        raise ValueError("final_answer must be an object.")
    if not isinstance(sql_input, dict):
        raise ValueError("sql must be an object.")
    if not isinstance(privacy_input, dict):
        raise ValueError("privacy must be an object.")

    tenant_column = sql_input.get("tenant_column", defaults["sql"]["tenant_column"])
    if tenant_column is None:
        normalized_tenant_column = None
    elif isinstance(tenant_column, str):
        normalized_tenant_column = tenant_column.strip() or None
    else:
        raise ValueError("sql.tenant_column must be null or a string.")

    return {
        "final_answer": {
            "record_level_pii_allowed": normalize_bool_setting(
                final_answer_input.get(
                    "record_level_pii_allowed",
                    defaults["final_answer"]["record_level_pii_allowed"],
                ),
                "final_answer.record_level_pii_allowed",
            ),
            "prefer_aggregates_for_sensitive_domains": normalize_bool_setting(
                final_answer_input.get(
                    "prefer_aggregates_for_sensitive_domains",
                    defaults["final_answer"]["prefer_aggregates_for_sensitive_domains"],
                ),
                "final_answer.prefer_aggregates_for_sensitive_domains",
            ),
        },
        "sql": {
            "read_only": normalize_bool_setting(
                sql_input.get("read_only", defaults["sql"]["read_only"]),
                "sql.read_only",
            ),
            "select_star_allowed": normalize_bool_setting(
                sql_input.get(
                    "select_star_allowed",
                    defaults["sql"]["select_star_allowed"],
                ),
                "sql.select_star_allowed",
            ),
            "tenant_filter_required": normalize_bool_setting(
                sql_input.get(
                    "tenant_filter_required",
                    defaults["sql"]["tenant_filter_required"],
                ),
                "sql.tenant_filter_required",
            ),
            "tenant_column": normalized_tenant_column,
        },
        "privacy": {
            "forbidden_columns": normalize_forbidden_columns(
                privacy_input.get(
                    "forbidden_columns",
                    defaults["privacy"]["forbidden_columns"],
                )
            ),
            "record_level_forbidden": normalize_bool_setting(
                privacy_input.get(
                    "record_level_forbidden",
                    defaults["privacy"]["record_level_forbidden"],
                ),
                "privacy.record_level_forbidden",
            ),
        },
        "pii_column_names": normalize_pii_column_names(
            value.get("pii_column_names", defaults["pii_column_names"])
        ),
    }


def get_governance_policy_config(session: Session) -> dict[str, Any]:
    value = get_setting(
        session,
        GOVERNANCE_POLICY_SETTING,
        json_dumps(default_governance_policy_config()),
    )
    return normalize_governance_policy_config(json_loads(value))


def set_governance_policy_config(
    session: Session,
    config: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    normalized = normalize_governance_policy_config(config)
    set_setting(session, GOVERNANCE_POLICY_SETTING, json_dumps(normalized), actor)
    return normalized


def get_governance_policy_sources(session: Session) -> dict[str, str]:
    return {
        "governance_policy": (
            "metadata"
            if session.get(AdminSetting, GOVERNANCE_POLICY_SETTING) is not None
            else "default"
        )
    }


def flatten_pii_column_names(config: dict[str, Any]) -> set[str]:
    column_names: set[str] = set()
    for names in config.get("pii_column_names", {}).values():
        column_names.update(str(name).lower() for name in names)
    return column_names


def infer_configured_forbidden_columns(
    schema_summary: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, list[str]]:
    pii_column_names = flatten_pii_column_names(config)
    if not pii_column_names:
        return {}

    forbidden: dict[str, list[str]] = {}
    for table_name, table in schema_summary.get("tables", {}).items():
        columns = [
            column_name
            for column_name in table.get("columns", {})
            if column_name.lower() in pii_column_names
        ]
        if columns:
            forbidden[table_name] = columns

    return forbidden


def merge_forbidden_columns(
    configured: dict[str, list[str]],
    inferred: dict[str, list[str]],
) -> dict[str, list[str]]:
    merged = {table: [*columns] for table, columns in configured.items()}

    for table_name, columns in inferred.items():
        existing = merged.setdefault(table_name, [])
        seen = set(existing)
        for column_name in columns:
            if column_name in seen:
                continue
            existing.append(column_name)
            seen.add(column_name)

    return {table: columns for table, columns in merged.items() if columns}


def build_governance_policy_from_config(
    schema_summary: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_governance_policy_config(config)
    privacy = {
        **normalized["privacy"],
        "forbidden_columns": merge_forbidden_columns(
            normalized["privacy"]["forbidden_columns"],
            infer_configured_forbidden_columns(schema_summary, normalized),
        ),
    }

    return {
        "final_answer": normalized["final_answer"],
        "sql": normalized["sql"],
        "privacy": privacy,
    }


def get_governance_policy_for_schema(
    session: Session,
    schema_summary: dict[str, Any],
) -> dict[str, Any]:
    return build_governance_policy_from_config(
        schema_summary,
        get_governance_policy_config(session),
    )


def get_governance_policy_for_schema_safe(
    schema_summary: dict[str, Any],
) -> dict[str, Any]:
    fallback = build_governance_policy_from_config(
        schema_summary,
        default_governance_policy_config(),
    )

    try:
        session = create_session()
    except SQLAlchemyError:
        return fallback

    try:
        return get_governance_policy_for_schema(session, schema_summary)
    except (SQLAlchemyError, ValueError, TypeError, json.JSONDecodeError):
        return fallback
    finally:
        session.close()


def get_query_runtime_config(session: Session) -> QueryRuntimeConfig:
    return QueryRuntimeConfig(
        intent_classification_mode=get_setting(
            session,
            INTENT_CLASSIFICATION_MODE_SETTING,
            settings.gaard_intent_classification_mode,
        ),
        sql_generation_mode=get_setting(
            session,
            SQL_GENERATION_MODE_SETTING,
            settings.gaard_sql_generation_mode,
        ),
        result_interpretation_mode=get_setting(
            session,
            RESULT_INTERPRETATION_MODE_SETTING,
            settings.gaard_result_interpretation_mode,
        ),
        output_classification_mode=get_setting(
            session,
            OUTPUT_CLASSIFICATION_MODE_SETTING,
            settings.gaard_output_classification_mode,
        ),
        investigation_mode=get_setting(
            session,
            INVESTIGATION_MODE_SETTING,
            settings.gaard_investigation_mode,
        ),
        investigation_ambiguity_mode=get_setting(
            session,
            INVESTIGATION_AMBIGUITY_MODE_SETTING,
            settings.gaard_investigation_ambiguity_mode,
        ),
        query_max_rows=get_int_setting(
            session,
            QUERY_MAX_ROWS_SETTING,
            settings.gaard_query_max_rows,
        ),
        query_timeout_seconds=get_int_setting(
            session,
            QUERY_TIMEOUT_SECONDS_SETTING,
            settings.gaard_query_timeout_seconds,
        ),
    )


def get_query_runtime_config_safe() -> QueryRuntimeConfig:
    fallback = QueryRuntimeConfig(
        intent_classification_mode=settings.gaard_intent_classification_mode,
        sql_generation_mode=settings.gaard_sql_generation_mode,
        result_interpretation_mode=settings.gaard_result_interpretation_mode,
        output_classification_mode=settings.gaard_output_classification_mode,
        investigation_mode=settings.gaard_investigation_mode,
        investigation_ambiguity_mode=settings.gaard_investigation_ambiguity_mode,
        query_max_rows=settings.gaard_query_max_rows,
        query_timeout_seconds=settings.gaard_query_timeout_seconds,
    )

    try:
        session = create_session()
    except SQLAlchemyError:
        return fallback

    try:
        return get_query_runtime_config(session)
    except (SQLAlchemyError, ValueError, TypeError):
        return fallback
    finally:
        session.close()


def set_query_runtime_config(
    session: Session,
    intent_classification_mode: str,
    sql_generation_mode: str,
    result_interpretation_mode: str,
    output_classification_mode: str,
    investigation_mode: str,
    investigation_ambiguity_mode: str,
    query_max_rows: int,
    query_timeout_seconds: int,
    actor: str,
) -> QueryRuntimeConfig:
    set_setting(session, INTENT_CLASSIFICATION_MODE_SETTING, intent_classification_mode, actor)
    set_setting(session, SQL_GENERATION_MODE_SETTING, sql_generation_mode, actor)
    set_setting(session, RESULT_INTERPRETATION_MODE_SETTING, result_interpretation_mode, actor)
    set_setting(session, OUTPUT_CLASSIFICATION_MODE_SETTING, output_classification_mode, actor)
    set_setting(session, INVESTIGATION_MODE_SETTING, investigation_mode, actor)
    set_setting(
        session,
        INVESTIGATION_AMBIGUITY_MODE_SETTING,
        investigation_ambiguity_mode,
        actor,
    )
    set_setting(session, QUERY_MAX_ROWS_SETTING, str(query_max_rows), actor)
    set_setting(session, QUERY_TIMEOUT_SECONDS_SETTING, str(query_timeout_seconds), actor)

    return get_query_runtime_config(session)


def get_llm_runtime_config(session: Session) -> LlmRuntimeConfig:
    extra_body = json_loads(
        get_setting(
            session,
            LLM_SETTING_EXTRA_BODY,
            json_dumps(settings.gaard_llm_extra_body),
        )
    )

    if not isinstance(extra_body, dict):
        extra_body = {}

    return LlmRuntimeConfig(
        provider=get_setting(session, LLM_SETTING_PROVIDER, settings.gaard_llm_provider),
        base_url=get_setting(session, LLM_SETTING_BASE_URL, settings.gaard_llm_base_url),
        api_key=get_setting(session, LLM_SETTING_API_KEY, settings.gaard_llm_api_key),
        model=get_setting(session, LLM_SETTING_MODEL, settings.gaard_llm_model),
        extra_body=extra_body,
        timeout_seconds=get_int_setting(
            session,
            LLM_SETTING_TIMEOUT_SECONDS,
            settings.gaard_llm_timeout_seconds,
        ),
    )


def get_llm_runtime_config_safe() -> LlmRuntimeConfig:
    try:
        session = create_session()
    except SQLAlchemyError:
        return LlmRuntimeConfig(
            provider=settings.gaard_llm_provider,
            base_url=settings.gaard_llm_base_url,
            api_key=settings.gaard_llm_api_key,
            model=settings.gaard_llm_model,
            extra_body=settings.gaard_llm_extra_body,
            timeout_seconds=settings.gaard_llm_timeout_seconds,
        )

    try:
        return get_llm_runtime_config(session)
    except (SQLAlchemyError, ValueError, TypeError):
        return LlmRuntimeConfig(
            provider=settings.gaard_llm_provider,
            base_url=settings.gaard_llm_base_url,
            api_key=settings.gaard_llm_api_key,
            model=settings.gaard_llm_model,
            extra_body=settings.gaard_llm_extra_body,
            timeout_seconds=settings.gaard_llm_timeout_seconds,
        )
    finally:
        session.close()


def get_llm_config_sources(session: Session) -> dict[str, str]:
    keys = {
        "provider": LLM_SETTING_PROVIDER,
        "base_url": LLM_SETTING_BASE_URL,
        "api_key": LLM_SETTING_API_KEY,
        "model": LLM_SETTING_MODEL,
        "timeout_seconds": LLM_SETTING_TIMEOUT_SECONDS,
        "extra_body": LLM_SETTING_EXTRA_BODY,
        "intent_classification_mode": INTENT_CLASSIFICATION_MODE_SETTING,
        "sql_generation_mode": SQL_GENERATION_MODE_SETTING,
        "result_interpretation_mode": RESULT_INTERPRETATION_MODE_SETTING,
        "output_classification_mode": OUTPUT_CLASSIFICATION_MODE_SETTING,
        "investigation_mode": INVESTIGATION_MODE_SETTING,
        "investigation_ambiguity_mode": INVESTIGATION_AMBIGUITY_MODE_SETTING,
        "query_max_rows": QUERY_MAX_ROWS_SETTING,
        "query_timeout_seconds": QUERY_TIMEOUT_SECONDS_SETTING,
    }

    return {
        field: "metadata" if session.get(AdminSetting, key) is not None else "default"
        for field, key in keys.items()
    }


def set_llm_runtime_config(
    session: Session,
    provider: str,
    base_url: str,
    api_key: str | None,
    model: str,
    timeout_seconds: int,
    extra_body: dict[str, Any],
    actor: str,
) -> LlmRuntimeConfig:
    set_setting(session, LLM_SETTING_PROVIDER, provider, actor)
    set_setting(session, LLM_SETTING_BASE_URL, base_url, actor)
    if api_key is not None:
        set_setting(session, LLM_SETTING_API_KEY, api_key, actor)
    set_setting(session, LLM_SETTING_MODEL, model, actor)
    set_setting(session, LLM_SETTING_TIMEOUT_SECONDS, str(timeout_seconds), actor)
    set_setting(session, LLM_SETTING_EXTRA_BODY, json_dumps(extra_body), actor)

    return get_llm_runtime_config(session)


def get_data_query_audit_retention_days(session: Session) -> int:
    value = get_setting(
        session,
        "data_query_audit_retention_days",
        str(settings.gaard_audit_retention_days),
    )

    return max(1, int(value))


def apply_data_query_audit_retention(session: Session) -> None:
    retention_days = get_data_query_audit_retention_days(session)
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    session.execute(
        delete(DataQueryAuditLog).where(DataQueryAuditLog.occurred_at < cutoff)
    )


def list_data_query_audit_logs(
    session: Session,
    limit: int = 100,
    audit_type: DataQueryAuditType | str | None = None,
    output_classification: OutputClassification | str | None = None,
    sql_contains: str | None = None,
) -> list[DataQueryAuditLog]:
    apply_data_query_audit_retention(session)

    query = select(DataQueryAuditLog).order_by(desc(DataQueryAuditLog.occurred_at))

    if audit_type is not None:
        query = query.where(DataQueryAuditLog.type == coerce_data_query_audit_type(audit_type))

    if output_classification is not None:
        query = query.where(
            DataQueryAuditLog.output_classification
            == coerce_output_classification(output_classification)
        )

    if sql_contains is not None and sql_contains.strip():
        query = query.where(DataQueryAuditLog.sql.contains(sql_contains.strip()))

    return list(session.scalars(query.limit(limit)))


def get_data_query_audit_type(log: DataQueryAuditLog) -> str:
    return data_query_audit_type_value(log.type)


def data_query_audit_type_value(value: DataQueryAuditType | str | None) -> str:
    if isinstance(value, DataQueryAuditType):
        return value.value

    if isinstance(value, str) and value:
        try:
            return coerce_data_query_audit_type(value).value
        except ValueError:
            return value

    return DATA_QUERY_AUDIT_INFO


def coerce_data_query_audit_type(value: DataQueryAuditType | str) -> DataQueryAuditType:
    if isinstance(value, DataQueryAuditType):
        return value

    normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {item.value: item for item in DataQueryAuditType}

    if normalized in aliases:
        return aliases[normalized]

    raise ValueError("Unsupported data query audit type.")


def coerce_output_classification(value: object) -> OutputClassification:
    if isinstance(value, OutputClassification):
        return value

    if not isinstance(value, str) or not value.strip():
        return OutputClassification.UNKNOWN

    normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {item.value: item for item in OutputClassification}

    return aliases.get(normalized, OutputClassification.UNKNOWN)


def list_business_logic_suggestions(
    session: Session,
    connector_id: int,
) -> list[BusinessLogicSuggestion]:
    return list(
        session.scalars(
            select(BusinessLogicSuggestion)
            .where(BusinessLogicSuggestion.connector_id == connector_id)
            .order_by(
                BusinessLogicSuggestion.enabled.desc(),
                desc(BusinessLogicSuggestion.updated_at),
            )
        )
    )


def get_business_logic_suggestion(
    session: Session,
    suggestion_id: int,
) -> BusinessLogicSuggestion | None:
    return session.get(BusinessLogicSuggestion, suggestion_id)


def set_business_logic_suggestion_enabled(
    session: Session,
    suggestion: BusinessLogicSuggestion,
    enabled: bool,
    actor: str,
) -> BusinessLogicSuggestion:
    suggestion.enabled = enabled
    suggestion.status = (
        BUSINESS_LOGIC_STATUS_ACTIVE
        if enabled
        else BUSINESS_LOGIC_STATUS_PENDING
    )
    suggestion.updated_by = actor

    return suggestion


def update_business_logic_suggestion_content(
    suggestion: BusinessLogicSuggestion,
    title: str | None,
    rule_text: str | None,
    actor: str,
) -> BusinessLogicSuggestion:
    if title is not None:
        suggestion.title = truncate_text(title.strip(), 255)

    if rule_text is not None:
        suggestion.rule_text = rule_text.strip()

    suggestion.updated_by = actor

    return suggestion


def delete_business_logic_suggestion(
    session: Session,
    suggestion: BusinessLogicSuggestion,
) -> None:
    session.delete(suggestion)


def learn_business_logic_from_sql_error(
    connector_id: int | None,
    audit_id: int | None,
    actor: str = "system",
) -> BusinessLogicSuggestion | None:
    if audit_id is None:
        return None

    try:
        session = create_session()
    except SQLAlchemyError:
        return None

    try:
        audit_log = session.get(DataQueryAuditLog, audit_id)
        if audit_log is None:
            return None

        metadata = safe_json_object(audit_log.metadata_json)
        exclusion_reason = business_logic_learning_exclusion_reason(metadata)
        if exclusion_reason:
            mark_business_logic_learning_skipped(
                session=session,
                audit_log=audit_log,
                reason=exclusion_reason,
            )
            return None

        if connector_id is None:
            mark_business_logic_learning_skipped(
                session=session,
                audit_log=audit_log,
                reason="No active datasource connector was available for this SQL error.",
            )
            return None

        connector = session.get(DatasourceConnector, connector_id)
        if connector is None:
            mark_business_logic_learning_skipped(
                session=session,
                audit_log=audit_log,
                reason="Datasource connector was not found for this SQL error.",
            )
            return None

        cache = get_datasource_schema_cache(session, connector_id)
        if cache is None:
            mark_business_logic_learning_skipped(
                session=session,
                audit_log=audit_log,
                reason="Datasource schema cache was not available for LLM learning.",
            )
            return None

        try:
            llm_config = get_llm_runtime_config(session)
        except (SQLAlchemyError, ValueError, TypeError) as exc:
            mark_business_logic_learning_skipped(
                session=session,
                audit_log=audit_log,
                reason=f"LLM configuration could not be loaded: {exc}",
            )
            return None

        skip_reason = validate_business_logic_learning_llm_config(llm_config)
        if skip_reason:
            mark_business_logic_learning_skipped(
                session=session,
                audit_log=audit_log,
                reason=skip_reason,
            )
            return None

        try:
            lesson = request_business_logic_lesson(
                llm_config=llm_config,
                connector=connector,
                audit_log=audit_log,
                schema_cache=cache,
            )
        except (LlmProviderError, ValueError, TypeError) as exc:
            mark_business_logic_learning_skipped(
                session=session,
                audit_log=audit_log,
                reason=f"LLM learning failed: {exc}",
            )
            return None

        if not lesson.create_suggestion:
            mark_business_logic_learning_skipped(
                session=session,
                audit_log=audit_log,
                reason=lesson.skip_reason
                or "LLM did not find a durable SQL-generation lesson.",
                llm_response=lesson.raw,
            )
            return None

        if not lesson.rule_text:
            mark_business_logic_learning_skipped(
                session=session,
                audit_log=audit_log,
                reason="LLM did not return a rule_text for business logic learning.",
                llm_response=lesson.raw,
            )
            return None

        suggestion = upsert_llm_business_logic_suggestion(
            session=session,
            connector=connector,
            audit_log=audit_log,
            lesson=lesson,
            actor=actor,
        )
        record_business_logic_learning_suggestion(
            audit_log=audit_log,
            suggestion=suggestion,
            lesson=lesson,
        )
        session.commit()

        return suggestion
    except Exception:
        session.rollback()
        return None
    finally:
        session.close()


def business_logic_learning_exclusion_reason(metadata: dict[str, Any]) -> str:
    pipeline_phase = str(metadata.get("pipeline_phase") or "")
    route = str(metadata.get("route") or metadata.get("execution_route") or "")
    required_evidence_type = str(metadata.get("required_evidence_type") or "")
    primary_error_category = str(metadata.get("primary_error_category") or "")
    failed_identifier = str(metadata.get("failed_identifier") or "")
    error_categories = {
        str(category)
        for category in metadata.get("error_categories", [])
        if str(category).strip()
    }
    if primary_error_category:
        error_categories.add(primary_error_category)

    non_sql_routes = {
        "answer_from_schema_summary",
        "answer_from_policy_or_governance",
        "ask_clarification",
        "answer_from_reasoning",
        "cannot_answer_safely",
    }
    non_record_evidence = {
        "schema_metadata",
        "governance_policy",
        "clarification",
        "reasoning_only",
    }
    pipeline_design_phases = {
        "intent_classification",
    }
    non_business_categories = {
        "schema_metadata.unavailable",
        "governance_policy.unavailable",
        "clarification.unavailable",
        "reasoning.unavailable",
        "intent.ambiguous_requires_clarification",
    }

    if route in non_sql_routes:
        return "Business logic learning is skipped for non-SQL routes."
    if required_evidence_type in non_record_evidence:
        return "Business logic learning is skipped for non-record evidence outcomes."
    if pipeline_phase in pipeline_design_phases:
        return (
            "Business logic learning is skipped for query routing, modeling, or "
            "preflight failures."
        )
    if error_categories & non_business_categories:
        return (
            "Business logic learning is skipped because the failure is not a durable "
            "business-logic gap."
        )
    return ""


@dataclass(frozen=True)
class BusinessLogicLesson:
    create_suggestion: bool
    title: str
    rule_text: str
    error_category: str
    failed_identifier: str
    repaired_identifier: str
    confidence: float
    terms: list[str]
    join_hints: list[str]
    skip_reason: str
    raw: dict[str, Any]


def validate_business_logic_learning_llm_config(config: LlmRuntimeConfig) -> str:
    if config.provider != "openai-compatible":
        return f"Unsupported LLM provider for business logic learning: {config.provider}."

    if not config.base_url:
        return "LLM base URL is not configured for business logic learning."

    if not config.model:
        return "LLM model is not configured for business logic learning."

    if not config.api_key or config.api_key == "change-me":
        return "LLM API key is not configured for business logic learning."

    return ""


def request_business_logic_lesson(
    llm_config: LlmRuntimeConfig,
    connector: DatasourceConnector,
    audit_log: DataQueryAuditLog,
    schema_cache: DatasourceSchemaCache,
) -> BusinessLogicLesson:
    client = OpenAICompatibleClient(
        base_url=llm_config.base_url,
        api_key=llm_config.api_key,
        timeout_seconds=llm_config.timeout_seconds,
    )
    system_prompt, user_prompt = build_business_logic_learning_prompt(
        connector=connector,
        audit_log=audit_log,
        schema_cache=schema_cache,
    )
    response = client.create_chat_completion(
        ChatCompletionRequest(
            model=llm_config.model,
            temperature=0.0,
            extra_body=llm_config.extra_body,
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt),
            ],
        )
    )

    return parse_business_logic_lesson_response(response.content)


def build_business_logic_learning_prompt(
    connector: DatasourceConnector,
    audit_log: DataQueryAuditLog,
    schema_cache: DatasourceSchemaCache,
) -> tuple[str, str]:
    metadata = safe_json_object(audit_log.metadata_json)
    error_message = str(metadata.get("error_message") or audit_log.answer)
    error_detail = str(metadata.get("error_detail") or "")
    formatted_schema = schema_cache.formatted_schema.strip() or schema_cache.schema_json

    system_prompt = """You diagnose failed generated SQL and turn the diagnosis into durable business logic for future SQL generation.

Analyze every SQL execution error type yourself. Do not rely on pre-classified metadata.
Create a business logic suggestion when a future SQL generator can avoid this error by following a durable rule about schema usage, joins, aliases, dialect, functions, grouping, filtering, or business terminology.
Do not create a suggestion for transient infrastructure failures, permissions that require administrator action, missing privileges, unavailable databases, timeouts, or cases where there is no durable SQL-generation lesson.
The lesson must be a direct instruction for future SQL generation, not a postmortem.

Return JSON only. Use this exact shape:
{
  "create_suggestion": true,
  "error_category": "short.category",
  "title": "short title for an admin",
  "rule_text": "durable instruction for future SQL generation",
  "failed_identifier": "optional table, column, function or concept that caused the error",
  "repaired_identifier": "optional preferred table, column, function or concept",
  "confidence": 0.0,
  "terms": ["optional", "search", "terms"],
  "join_hints": ["optional join or alias hints"],
  "skip_reason": ""
}

If there is no durable lesson, return the same JSON shape with "create_suggestion": false, an empty rule_text, and a clear skip_reason."""

    user_prompt = f"""Datasource:
- key: {connector.connector_key}
- dialect: {connector.sql_dialect}

Database schema and approved business logic:
{formatted_schema}

User question:
{audit_log.question}

Generated SQL:
{audit_log.sql}

SQL execution error:
{error_message}

SQL error detail:
{error_detail}

Audit metadata:
{json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True)}

Return the JSON lesson only."""

    return system_prompt, user_prompt


def parse_business_logic_lesson_response(value: str) -> BusinessLogicLesson:
    cleaned = remove_thinking_blocks(value).strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()

    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM returned non-JSON business logic learning output.") from exc

    if not isinstance(payload, dict):
        raise ValueError("LLM returned invalid business logic learning output.")

    rule_text = str(payload.get("rule_text") or payload.get("lesson") or "").strip()
    create_suggestion = bool(payload.get("create_suggestion"))

    return BusinessLogicLesson(
        create_suggestion=create_suggestion,
        title=str(payload.get("title") or "").strip(),
        rule_text=rule_text,
        error_category=coerce_short_text(payload.get("error_category"), SQL_ERROR_UNKNOWN, 100),
        failed_identifier=coerce_short_text(payload.get("failed_identifier"), "", 255),
        repaired_identifier=coerce_short_text(payload.get("repaired_identifier"), "", 255),
        confidence=coerce_confidence(payload.get("confidence")),
        terms=coerce_text_list(payload.get("terms")),
        join_hints=coerce_text_list(payload.get("join_hints")),
        skip_reason=str(payload.get("skip_reason") or "").strip(),
        raw=payload,
    )


def upsert_llm_business_logic_suggestion(
    session: Session,
    connector: DatasourceConnector,
    audit_log: DataQueryAuditLog,
    lesson: BusinessLogicLesson,
    actor: str,
) -> BusinessLogicSuggestion:
    if not lesson.rule_text:
        raise ValueError("LLM did not return a rule_text for business logic learning.")

    metadata = safe_json_object(audit_log.metadata_json)
    title = truncate_text(
        lesson.title
        or f"SQL lesson for {lesson.error_category.replace('_', ' ')}",
        255,
    )
    failed_identifier = lesson.failed_identifier or str(metadata.get("failed_identifier") or "")
    repaired_identifier = lesson.repaired_identifier
    terms = lesson.terms or build_business_logic_terms(
        audit_log.question,
        failed_identifier,
        repaired_identifier or title,
    )

    existing = session.scalar(
        select(BusinessLogicSuggestion).where(
            BusinessLogicSuggestion.connector_id == connector.id,
            BusinessLogicSuggestion.source_audit_id == audit_log.id,
        )
    )

    if existing is None and (failed_identifier or repaired_identifier):
        existing = session.scalar(
            select(BusinessLogicSuggestion).where(
                BusinessLogicSuggestion.connector_id == connector.id,
                BusinessLogicSuggestion.error_category == lesson.error_category,
                BusinessLogicSuggestion.failed_identifier == failed_identifier,
                BusinessLogicSuggestion.repaired_identifier == repaired_identifier,
            )
        )

    if existing is None:
        existing = BusinessLogicSuggestion(
            connector_id=connector.id,
            source_audit_id=audit_log.id,
            status=BUSINESS_LOGIC_STATUS_PENDING,
            safety=BUSINESS_LOGIC_SAFETY_REVIEW,
            enabled=False,
            error_category=lesson.error_category,
            title=title,
            rule_text=lesson.rule_text,
            terms_json=json_dumps(terms),
            join_hints_json=json_dumps(lesson.join_hints),
            failed_identifier=failed_identifier,
            repaired_identifier=repaired_identifier,
            confidence=lesson.confidence,
            updated_by=actor,
        )
        session.add(existing)
        session.flush()
        return existing

    existing.source_audit_id = audit_log.id
    existing.status = BUSINESS_LOGIC_STATUS_PENDING
    existing.safety = BUSINESS_LOGIC_SAFETY_REVIEW
    existing.enabled = False
    existing.error_category = lesson.error_category
    existing.title = title
    existing.rule_text = lesson.rule_text
    existing.terms_json = json_dumps(terms)
    existing.join_hints_json = json_dumps(lesson.join_hints)
    existing.failed_identifier = failed_identifier
    existing.repaired_identifier = repaired_identifier
    existing.confidence = lesson.confidence
    existing.updated_by = actor

    return existing


def record_business_logic_learning_suggestion(
    audit_log: DataQueryAuditLog,
    suggestion: BusinessLogicSuggestion,
    lesson: BusinessLogicLesson,
) -> None:
    metadata = safe_json_object(audit_log.metadata_json)
    metadata["error_category"] = lesson.error_category
    metadata["failed_identifier"] = lesson.failed_identifier
    metadata["repaired_identifier"] = lesson.repaired_identifier
    metadata["business_logic_learning"] = {
        "status": "pending_approval",
        "suggestion_id": suggestion.id,
        "message": (
            "Nauczyłem się propozycji rozwiązania tego błędu, ale musisz ją "
            "zatwierdzić w Sugestiach logiki biznesowej."
        ),
        "admin_section": "business-logic",
        "error_category": lesson.error_category,
        "confidence": lesson.confidence,
    }
    audit_log.metadata_json = json_dumps(metadata)


def mark_business_logic_learning_skipped(
    session: Session,
    audit_log: DataQueryAuditLog,
    reason: str,
    llm_response: dict[str, Any] | None = None,
) -> None:
    metadata = safe_json_object(audit_log.metadata_json)
    learning: dict[str, Any] = {
        "status": BUSINESS_LOGIC_LEARNING_STATUS_SKIPPED,
        "reason": reason,
        "message": f"Nauka logiki biznesowej została pominięta: {reason}",
        "admin_section": "business-logic",
    }

    if llm_response:
        learning["llm_response"] = llm_response

    metadata["business_logic_learning"] = learning
    audit_log.metadata_json = json_dumps(metadata)
    session.commit()


def safe_json_object(value: str) -> dict[str, Any]:
    try:
        payload = json_loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}

    return payload if isinstance(payload, dict) else {}


def coerce_short_text(value: object, default: str, max_length: int) -> str:
    text_value = str(value or default).strip()
    return truncate_text(text_value, max_length)


def truncate_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value

    return value[: max_length - 3].rstrip() + "..."


def coerce_confidence(value: object) -> float:
    if not isinstance(value, (int, float, str)):
        return 0.0

    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0

    return max(0.0, min(1.0, confidence))


def coerce_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    items: list[str] = []
    for item in value:
        text_value = str(item or "").strip()
        if text_value and text_value not in items:
            items.append(text_value)

    return items


def build_business_logic_terms(
    question: str,
    failed_identifier: str,
    repaired_identifier: str,
) -> list[str]:
    terms = {
        failed_identifier,
        *failed_identifier.split("_"),
        *repaired_identifier.split("_"),
    }

    for token in re.findall(r"[A-Za-zÀ-ž0-9_]{4,}", question.lower()):
        terms.add(token)

    return sorted(term for term in terms if term)


def get_active_business_logic_prompt_safe(connector_id: int) -> str:
    try:
        session = create_session()
    except SQLAlchemyError:
        return ""

    try:
        return format_business_logic_prompt(
            [
                suggestion
                for suggestion in list_business_logic_suggestions(session, connector_id)
                if suggestion.enabled
            ]
        )
    except SQLAlchemyError:
        return ""
    finally:
        session.close()


def format_business_logic_prompt(
    suggestions: list[BusinessLogicSuggestion],
) -> str:
    active = [suggestion for suggestion in suggestions if suggestion.enabled]

    if not active:
        return ""

    lines = ["Business logic:"]

    for suggestion in active:
        lines.append(f"- {suggestion.rule_text}")

    return "\n".join(lines)


INVESTIGATION_ANALYSIS_CATEGORIES = {
    "dictionary_value",
    "relationship_logic",
    "filter_logic",
    "aggregation_logic",
    "entity_mapping",
    "unknown",
}


def upsert_investigation_analysis_business_logic_suggestion(
    connector_id: int | None,
    source_audit_id: int | None,
    missing_information: str,
    required_analysis: str,
    category: str,
    analysis_response: QueryResponse,
    actor: str = "system",
) -> dict[str, Any]:
    if connector_id is None:
        return {
            "status": "skipped",
            "reason": "Datasource connector is unavailable.",
        }

    normalized_category = normalize_investigation_analysis_category(category)
    normalized_missing = normalize_fingerprint_text(missing_information)
    normalized_analysis = normalize_fingerprint_text(required_analysis)
    result_signature = investigation_analysis_result_signature(analysis_response)
    fingerprint = investigation_analysis_fingerprint(
        category=normalized_category,
        missing_information=normalized_missing,
        required_analysis=normalized_analysis,
        result_signature=result_signature,
    )

    try:
        session = create_session()
    except SQLAlchemyError:
        return {
            "status": "skipped",
            "reason": "Metadata store is unavailable.",
            "fingerprint": fingerprint,
        }

    error_category = f"investigation.analysis.{normalized_category}"
    failed_identifier = truncate_text(normalized_missing, 255)

    try:
        existing = session.scalar(
            select(BusinessLogicSuggestion).where(
                BusinessLogicSuggestion.connector_id == connector_id,
                BusinessLogicSuggestion.error_category == error_category,
                BusinessLogicSuggestion.failed_identifier == failed_identifier,
                BusinessLogicSuggestion.repaired_identifier == fingerprint,
            )
        )
        similar_existing = session.scalars(
            select(BusinessLogicSuggestion).where(
                BusinessLogicSuggestion.connector_id == connector_id,
                BusinessLogicSuggestion.error_category == error_category,
                BusinessLogicSuggestion.failed_identifier == failed_identifier,
                BusinessLogicSuggestion.repaired_identifier != fingerprint,
            )
        ).all()

        if existing is not None:
            return {
                "status": "existing",
                "suggestion_id": existing.id,
                "fingerprint": fingerprint,
                "similar_existing_suggestion_ids": [
                    item.id for item in similar_existing
                ],
            }

        compact_result = compact_investigation_analysis_result(analysis_response)
        rule_text = (
            f"[{normalized_category}] {missing_information.strip()} => {compact_result}"
        )
        suggestion = BusinessLogicSuggestion(
            connector_id=connector_id,
            source_audit_id=source_audit_id,
            status=BUSINESS_LOGIC_STATUS_PENDING,
            safety=BUSINESS_LOGIC_SAFETY_REVIEW,
            enabled=False,
            error_category=error_category,
            title=truncate_text(
                f"Investigation analysis: {missing_information.strip()}",
                255,
            ),
            rule_text=rule_text,
            terms_json=json_dumps(
                build_investigation_analysis_terms(
                    missing_information,
                    required_analysis,
                    compact_result,
                )
            ),
            join_hints_json=json_dumps([]),
            failed_identifier=failed_identifier,
            repaired_identifier=fingerprint,
            confidence=coerce_confidence(
                analysis_response.metadata.get("confidence")
            ),
            updated_by=actor,
        )
        session.add(suggestion)
        session.commit()
        return {
            "status": "created",
            "suggestion_id": suggestion.id,
            "fingerprint": fingerprint,
            "similar_existing_suggestion_ids": [
                item.id for item in similar_existing
            ],
        }
    except SQLAlchemyError:
        session.rollback()
        return {
            "status": "skipped",
            "reason": "Could not store investigation analysis business logic.",
            "fingerprint": fingerprint,
        }
    finally:
        session.close()


def normalize_investigation_analysis_category(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in INVESTIGATION_ANALYSIS_CATEGORIES else "unknown"


def normalize_fingerprint_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def investigation_analysis_result_signature(response: QueryResponse) -> dict[str, Any]:
    sorted_rows = sorted(
        response.rows,
        key=lambda item: json_dumps(item),
    )
    return {
        "row_count": len(response.rows),
        "rows": sorted_rows[:50],
        "answer": response.answer if not response.rows else "",
    }


def investigation_analysis_fingerprint(
    category: str,
    missing_information: str,
    required_analysis: str,
    result_signature: dict[str, Any],
) -> str:
    payload = {
        "category": category,
        "missing_information": missing_information,
        "required_analysis": required_analysis,
        "result_signature": result_signature,
    }
    return hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()


def compact_investigation_analysis_result(response: QueryResponse) -> str:
    if response.rows:
        return truncate_text(json_dumps(response.rows[:20]), 2_000)

    if response.answer:
        return truncate_text(response.answer, 2_000)

    return "no rows"


def build_investigation_analysis_terms(
    missing_information: str,
    required_analysis: str,
    compact_result: str,
) -> list[str]:
    terms: set[str] = set()
    for value in (missing_information, required_analysis, compact_result):
        for token in re.findall(r"[A-Za-zÀ-ž0-9_]{4,}", value.lower()):
            terms.add(token)

    return sorted(terms)


def record_candidate_business_knowledge(
    connector_id: int | None,
    knowledge_items: list[dict[str, Any]],
    actor: str = "system",
) -> list[int]:
    if connector_id is None or not knowledge_items:
        return []

    try:
        session = create_session()
    except SQLAlchemyError:
        return []

    created_ids: list[int] = []
    try:
        for item in knowledge_items:
            if not isinstance(item, dict):
                continue

            claim = str(item.get("claim") or "").strip()
            if not claim:
                continue

            subject = {
                "datasource_id": item.get("datasource_id"),
                "tables": item.get("tables") or [],
                "columns": item.get("columns") or [],
                "values": item.get("values") or [],
            }
            row = BusinessKnowledgeClaim(
                connector_id=connector_id,
                knowledge_type=str(item.get("knowledge_type") or "business_semantic"),
                status=str(item.get("status") or "candidate"),
                claim_text=claim,
                subject_json=json_dumps(subject),
                evidence_json=json_dumps(item.get("evidence") or []),
                confidence=coerce_confidence(item.get("confidence")),
                source=str(item.get("source") or "query_pipeline"),
                request_id=str(item.get("request_id") or ""),
                audit_reference=str(item.get("audit_event_id") or ""),
                requires_approval=bool(item.get("requires_approval", True)),
                updated_by=actor,
            )
            session.add(row)
            session.flush()
            created_ids.append(row.id)

        session.commit()
        return created_ids
    except SQLAlchemyError:
        session.rollback()
        return []
    finally:
        session.close()


def list_admin_audit_logs(session: Session, limit: int = 100) -> list[AdminAuditLog]:
    return list(
        session.scalars(
            select(AdminAuditLog).order_by(desc(AdminAuditLog.occurred_at)).limit(limit)
        )
    )


def list_prompt_templates(session: Session) -> list[PromptTemplate]:
    return list(
        session.scalars(
            select(PromptTemplate).order_by(PromptTemplate.prompt_key)
        )
    )


def get_prompt_template(session: Session, prompt_key: str) -> PromptTemplate | None:
    return session.scalar(
        select(PromptTemplate).where(PromptTemplate.prompt_key == prompt_key)
    )


def get_active_prompt_template_safe(prompt_key: str) -> PromptTemplate | None:
    try:
        session = create_session()
    except SQLAlchemyError:
        return None

    try:
        return session.scalar(
            select(PromptTemplate).where(
                PromptTemplate.prompt_key == prompt_key,
                PromptTemplate.active.is_(True),
            )
        )
    except SQLAlchemyError:
        return None
    finally:
        session.close()


def list_datasource_connectors(session: Session) -> list[DatasourceConnector]:
    return list(
        session.scalars(
            select(DatasourceConnector).order_by(
                DatasourceConnector.active.desc(),
                DatasourceConnector.name,
            )
        )
    )


def is_system_datasource_connector(connector: DatasourceConnector) -> bool:
    return connector.connector_key in SYSTEM_DATASOURCE_CONNECTOR_KEYS


def get_datasource_connector(
    session: Session,
    connector_id: int,
) -> DatasourceConnector | None:
    return session.get(DatasourceConnector, connector_id)


def get_datasource_connector_by_key(
    session: Session,
    connector_key: str,
) -> DatasourceConnector | None:
    return session.scalar(
        select(DatasourceConnector).where(DatasourceConnector.connector_key == connector_key)
    )


def get_active_datasource_connector(session: Session) -> DatasourceConnector | None:
    return session.scalar(
        select(DatasourceConnector).where(DatasourceConnector.active.is_(True))
    )


def get_active_datasource_connector_safe() -> DatasourceConnector | None:
    try:
        session = create_session()
    except SQLAlchemyError:
        return None

    try:
        return get_active_datasource_connector(session)
    except SQLAlchemyError:
        return None
    finally:
        session.close()


def set_active_datasource_connector(
    session: Session,
    connector: DatasourceConnector,
    actor: str,
) -> None:
    for item in list_datasource_connectors(session):
        if is_system_datasource_connector(item):
            item.active = False
            continue

        item.active = item.id == connector.id
        item.updated_by = actor if item.id == connector.id else item.updated_by


def test_datasource_connection(connector: DatasourceConnector) -> None:
    connect_args = (
        {"check_same_thread": False}
        if connector.database_url.startswith("sqlite")
        else {}
    )
    engine = create_engine(connector.database_url, connect_args=connect_args)

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def get_datasource_schema_cache(
    session: Session,
    connector_id: int,
) -> DatasourceSchemaCache | None:
    return session.get(DatasourceSchemaCache, connector_id)


def get_or_create_datasource_schema_cache(
    session: Session,
    connector: DatasourceConnector,
    actor: str,
) -> DatasourceSchemaCache:
    cache = get_datasource_schema_cache(session, connector.id)

    if cache is not None:
        return cache

    return introspect_datasource_connector(session, connector, actor)


def introspect_datasource_connector(
    session: Session,
    connector: DatasourceConnector,
    actor: str,
) -> DatasourceSchemaCache:
    schema = SQLAlchemySchemaIntrospector(connector.database_url).introspect()
    existing = get_datasource_schema_cache(session, connector.id)
    existing_settings = (
        json_loads(existing.table_settings_json) if existing is not None else {}
    )
    table_settings = build_table_settings(schema, existing_settings)
    formatted_schema = format_schema_for_prompt(schema, table_settings)
    schema_json = json_dumps(schema.model_dump())

    if existing is None:
        existing = DatasourceSchemaCache(
            connector_id=connector.id,
            schema_json=schema_json,
            table_settings_json=json_dumps(table_settings),
            formatted_schema=formatted_schema,
            updated_by=actor,
        )
        session.add(existing)
    else:
        existing.schema_json = schema_json
        existing.table_settings_json = json_dumps(table_settings)
        existing.formatted_schema = formatted_schema
        existing.introspected_at = datetime.now(UTC)
        existing.updated_by = actor

    return existing


def build_table_settings(
    schema: DatabaseSchema,
    existing_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing_tables = (existing_settings or {}).get("tables", {})
    tables: dict[str, Any] = {}

    for table in schema.tables:
        existing = existing_tables.get(table.name, {})
        tables[table.name] = {
            "selected": bool(existing.get("selected", True)),
            "description": str(existing.get("description", "")),
            "primary_key_prompt": str(existing.get("primary_key_prompt", "")),
            "foreign_key_prompt": str(existing.get("foreign_key_prompt", "")),
            "join_logic": str(existing.get("join_logic", "")),
        }

    return {"tables": tables}


def parse_schema_cache(cache: DatasourceSchemaCache) -> DatabaseSchema:
    return DatabaseSchema.model_validate(json_loads(cache.schema_json))


def selected_schema_from_cache(cache: DatasourceSchemaCache) -> DatabaseSchema:
    schema = parse_schema_cache(cache)
    table_settings = json_loads(cache.table_settings_json)
    selected_tables = table_settings.get("tables", {})

    tables = [
        table
        for table in schema.tables
        if selected_tables.get(table.name, {}).get("selected", True)
    ]

    return DatabaseSchema(tables=tables)


def format_schema_for_prompt(
    schema: DatabaseSchema,
    table_settings: dict[str, Any],
) -> str:
    selected_tables = table_settings.get("tables", {})
    sections: list[str] = []

    for table in sorted(schema.tables, key=lambda item: item.name):
        table_config = selected_tables.get(table.name, {})

        if not table_config.get("selected", True):
            continue

        sections.append(format_table_for_prompt(table, table_config))

    if not sections:
        return "No tables or views available."

    return "\n\n".join(sections)


def format_table_for_prompt(table: TableInfo, table_config: dict[str, Any]) -> str:
    object_label = "View" if table.object_type == "view" else "Table"
    lines: list[str] = [f"{object_label}: {table.name}"]

    description = str(table_config.get("description", "")).strip()
    if description:
        lines.append(f"Description: {description}")

    lines.append("Columns:")

    if not table.columns:
        lines.append("- No columns available.")
    else:
        for column in table.columns:
            lines.append(format_column_for_prompt(column))

    primary_key_prompt = str(table_config.get("primary_key_prompt", "")).strip()
    if primary_key_prompt:
        lines.append("Primary key guidance:")
        lines.append(primary_key_prompt)

    if table.foreign_keys:
        lines.append("Foreign keys:")
        for foreign_key in table.foreign_keys:
            constrained = ", ".join(foreign_key.constrained_columns)
            referred = ", ".join(foreign_key.referred_columns)
            lines.append(f"- {constrained} -> {foreign_key.referred_table}.{referred}")

    foreign_key_prompt = str(table_config.get("foreign_key_prompt", "")).strip()
    if foreign_key_prompt:
        lines.append("Foreign key guidance:")
        lines.append(foreign_key_prompt)

    join_logic = str(table_config.get("join_logic", "")).strip()
    if join_logic:
        lines.append("Join logic:")
        lines.append(join_logic)

    return "\n".join(lines)


def format_column_for_prompt(column: ColumnInfo) -> str:
    modifiers: list[str] = []

    if column.primary_key:
        modifiers.append("primary key")

    if not column.nullable:
        modifiers.append("not null")

    modifier_text = f" ({', '.join(modifiers)})" if modifiers else ""
    return f"- {column.name}: {column.type}{modifier_text}"


def update_schema_table_settings(
    session: Session,
    cache: DatasourceSchemaCache,
    table_settings: dict[str, Any],
    actor: str,
) -> DatasourceSchemaCache:
    schema = parse_schema_cache(cache)
    merged = build_table_settings(schema, table_settings)
    cache.table_settings_json = json_dumps(merged)
    cache.formatted_schema = format_schema_for_prompt(schema, merged)
    cache.updated_by = actor

    return cache


def list_overview_widgets(session: Session) -> list[OverviewWidget]:
    return list(
        session.scalars(
            select(OverviewWidget)
            .where(OverviewWidget.active.is_(True))
            .order_by(OverviewWidget.position.asc(), OverviewWidget.id.asc())
        )
    )


def list_all_overview_widgets(session: Session) -> list[OverviewWidget]:
    return list(
        session.scalars(
            select(OverviewWidget)
            .order_by(OverviewWidget.position.asc(), OverviewWidget.id.asc())
        )
    )


def get_overview_widget(session: Session, widget_key: str) -> OverviewWidget | None:
    return session.scalar(
        select(OverviewWidget).where(OverviewWidget.widget_key == widget_key)
    )


def get_datasource_schema_context_safe() -> tuple[DatasourceConnector, DatasourceSchemaCache] | None:
    try:
        session = create_session()
    except SQLAlchemyError:
        return None

    try:
        connector = get_active_datasource_connector(session)

        if connector is None:
            return None

        cache = get_datasource_schema_cache(session, connector.id)

        if cache is None:
            cache = introspect_datasource_connector(session, connector, "system")
            session.commit()

        return connector, cache
    except SQLAlchemyError:
        session.rollback()
        return None
    finally:
        session.close()
