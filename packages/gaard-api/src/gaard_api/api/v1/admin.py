import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import sqlglot
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from gaard_connectors import ConnectorRegistryError
from gaard_connectors.odbc import collect_diagnostics, list_configured_dsns, list_odbc_drivers
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
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage
from gaard_plugin_api import ExtensionRecord
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    Integer,
    String,
    column,
    create_engine,
    delete,
    func,
    insert,
    inspect as sqlalchemy_inspect,
    select,
    table,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
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
    OverviewWidgetTag,
    PromptTemplate,
    UserDatasourceSelection,
    UserSavedMetric,
    WidgetTag,
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
    NormalizedDatasourceConfiguration,
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
    get_datasource_schema_cache,
    get_governance_policy_config,
    get_governance_policy_sources,
    get_llm_config_sources,
    get_llm_runtime_config,
    get_or_create_datasource_schema_cache,
    get_overview_widget,
    get_prompt_template,
    get_query_runtime_config,
    get_setting,
    introspect_datasource_connector,
    is_system_datasource_connector,
    json_loads,
    learn_business_logic_from_sql_error,
    list_admin_audit_logs,
    list_all_overview_widgets,
    list_business_logic_suggestions_for_connectors,
    list_data_query_audit_logs,
    list_datasource_connectors,
    list_overview_widgets,
    list_prompt_templates,
    mask_database_url,
    normalize_datasource_configuration,
    record_admin_audit,
    record_data_query_audit,
    record_data_query_sql_error_audit,
    selected_schema_from_cache,
    set_active_datasource_connector,
    set_business_logic_suggestion_enabled,
    set_governance_policy_config,
    set_llm_runtime_config,
    set_query_runtime_config,
    set_setting,
    test_datasource_connection,
    test_llm_runtime_config,
    update_business_logic_suggestion_content,
    update_schema_table_settings,
)
from gaard_api.api.v1.schema import get_schema_cache_key
from gaard_api.auth_dependencies import (
    AuthenticatedSession,
    ensure_user_license_access,
    get_current_admin,
    get_current_authenticated_session,
    get_current_enterprise_admin,
    get_current_enterprise_api_user,
    has_enterprise_user_access,
    identity_id_for_principal,
)
from gaard_api.core.schema_cache import schema_context_cache
from gaard_api.core.settings import settings
from gaard_api.extensions import (
    get_api_registry,
    get_auth_provider_registry,
    get_connector_registry,
    get_extension_manager,
)
from gaard_api.license import LicenseAccessError, redact_license_key
from gaard_api.license import license_service as license_service
from gaard_api.package_updates import package_update_jobs, package_update_service
from gaard_api.query_hooks import sqlglot_read_dialect

router = APIRouter()

# This table is owned and created by the optional identity-privileges extension.
# A lightweight table expression lets the API query it when that extension is active
# without importing the extension package or registering a duplicate ORM model.
IDENTITY_PRIVILEGE_DATASOURCE_PERMISSIONS = table(
    "identity_privilege_datasource_permissions",
    column("connector_id", Integer),
    column("identity_id", String(512)),
    column("allowed", Boolean),
)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    token: str
    username: str
    must_change_password: bool
    role: str = "admin"
    enterprise_access: bool


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class IdentityEnterpriseAccessRequest(BaseModel):
    enterprise_access: bool


SESSION_ACTIVITY_WRITE_INTERVAL = timedelta(minutes=5)


class MeResponse(BaseModel):
    username: str
    must_change_password: bool
    role: str = "admin"
    enterprise_access: bool


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


class LlmModelsRequest(BaseModel):
    provider: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)


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
    sql: str | None = None
    result_mode: str = Field(
        default=OVERVIEW_WIDGET_RESULT_DATA, pattern=r"^(data|interpretation)$"
    )
    position: int | None = Field(default=None, ge=10)
    grid_width: int | None = Field(default=None, ge=1, le=12)
    grid_height: int | None = Field(default=None, ge=2, le=24)
    active: bool | None = None
    tags: list[str] | None = None
    assigned_usernames: list[str] | None = None


class OverviewWidgetCreateRequest(OverviewWidgetUpdateRequest):
    widget_key: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_-]+$")
    position: int = Field(default=100, ge=10)
    grid_width: int | None = Field(default=None, ge=1, le=12)
    grid_height: int | None = Field(default=None, ge=2, le=24)
    active: bool = True


class OverviewWidgetSqlGenerationRequest(BaseModel):
    widget_key: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_-]+$")
    datasource_key: str = Field(min_length=1, max_length=255)
    question: str = Field(min_length=1)


class OverviewWidgetStateRequest(BaseModel):
    active: bool
    position: int | None = Field(default=None, ge=10)
    grid_width: int | None = Field(default=None, ge=1, le=12)
    grid_height: int | None = Field(default=None, ge=2, le=24)


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


class DatasourceSelectionRequest(BaseModel):
    datasource_ids: list[str] = Field(default_factory=list)


class DatasourceSchemaTableSettingsRequest(BaseModel):
    tables: dict[str, dict[str, Any]]


class DatasourceViewSqlGenerationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=20000)


class DatasourceViewExecuteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=20000)
    sql: str = Field(min_length=1, max_length=200000)


class DatasourceViewUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=20000)
    sql: str = Field(min_length=1, max_length=200000)


class DatasourceViewSettingsRequest(BaseModel):
    description: str = Field(default="", max_length=20000)


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
    database_url = (
        mask_database_url(connector.database_url)
        if connector.database_type == "odbc"
        else connector.database_url
    )
    return {
        "id": connector.id,
        "connector_key": connector.connector_key,
        "name": connector.name,
        "database_type": connector.database_type,
        "database_url": database_url,
        "masked_database_url": mask_database_url(connector.database_url),
        "sql_dialect": connector.sql_dialect,
        "active": connector.active,
        "system_managed": is_system_datasource_connector(connector),
        "updated_by": connector.updated_by,
        "updated_at": serialize_datetime(connector.updated_at),
    }


def identity_privileges_are_active() -> bool:
    """Whether the optional identity-privileges extension is active in this process."""
    return license_service.identity_management_allowed() and any(
        record.manifest is not None
        and record.manifest.id == "identity-privileges"
        and "api" in record.active_capabilities
        for record in get_extension_manager().records
    )


def filter_datasources_for_identity_privileges(
    session: Session,
    principal: AuthenticatedSession,
    connectors: list[DatasourceConnector],
) -> list[DatasourceConnector]:
    """Return only datasources granted to a non-admin identity when enabled."""
    if not identity_privileges_are_active():
        return connectors

    identity_id = identity_id_for_principal(principal)

    allowed_ids = set(
        session.scalars(
            select(IDENTITY_PRIVILEGE_DATASOURCE_PERMISSIONS.c.connector_id).where(
                IDENTITY_PRIVILEGE_DATASOURCE_PERMISSIONS.c.identity_id == identity_id,
                IDENTITY_PRIVILEGE_DATASOURCE_PERMISSIONS.c.allowed.is_(True),
            )
        )
    )
    return [connector for connector in connectors if connector.id in allowed_ids]


def grant_client_excel_datasource_permission(
    session: Session,
    connector_id: int,
    principal: AuthenticatedSession,
) -> None:
    """Make a client-uploaded workbook private to its uploader when enabled."""
    if not identity_privileges_are_active():
        return

    session.execute(
        insert(IDENTITY_PRIVILEGE_DATASOURCE_PERMISSIONS).values(
            connector_id=connector_id,
            identity_id=identity_id_for_principal(principal),
            allowed=True,
        )
    )


def remove_datasource_permissions(session: Session, connector_id: int) -> None:
    """Remove identity-privilege grants that point to a deleted datasource."""
    if not identity_privileges_are_active():
        return

    session.execute(
        delete(IDENTITY_PRIVILEGE_DATASOURCE_PERMISSIONS).where(
            IDENTITY_PRIVILEGE_DATASOURCE_PERMISSIONS.c.connector_id == connector_id
        )
    )


def multi_datasource_access_is_active() -> bool:
    return any(
        record.manifest is not None
        and record.manifest.id == "datasource-access"
        and "query" in record.manifest.contributions
        and "api" in record.active_capabilities
        for record in get_extension_manager().records
    )


def selected_datasource_ids(session: Session, principal: AuthenticatedSession) -> list[str]:
    selection = session.get(UserDatasourceSelection, str(principal.user.id))
    if selection is None:
        return []
    value = json_loads(selection.datasource_ids_json)
    return [str(item) for item in value] if isinstance(value, list) else []


def set_selected_datasource_ids(
    session: Session,
    principal: AuthenticatedSession,
    datasource_ids: list[str],
) -> None:
    owner_user_id = str(principal.user.id)
    selection = session.get(UserDatasourceSelection, owner_user_id)
    if selection is None:
        selection = UserDatasourceSelection(
            owner_user_id=owner_user_id,
            owner_username=principal.session.username or principal.user.username,
        )
        session.add(selection)
    selection.datasource_ids_json = json.dumps(datasource_ids)


def remove_datasource_from_selections(session: Session, connector_key: str) -> None:
    """Remove an unavailable datasource from every saved client selection."""
    for selection in session.scalars(select(UserDatasourceSelection)):
        selected_ids = json_loads(selection.datasource_ids_json)
        if not isinstance(selected_ids, list):
            continue
        filtered_ids = [
            str(source_id) for source_id in selected_ids if str(source_id) != connector_key
        ]
        if filtered_ids != selected_ids:
            selection.datasource_ids_json = json.dumps(filtered_ids)


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
        "analysis_auto_enable_business_logic": (query_config.analysis_auto_enable_business_logic),
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


def serialize_overview_widget_config(session: Session, widget: OverviewWidget) -> dict[str, Any]:
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
        "grid_height": normalize_overview_widget_grid_height(
            widget.widget_type,
            widget.grid_height,
        ),
        "active": widget.active,
        "updated_by": widget.updated_by,
        "updated_at": serialize_datetime(widget.updated_at),
        "assigned_usernames": get_overview_widget_user_assignments(session, widget),
        "tags": get_overview_widget_tags(session, widget),
    }


def normalize_widget_tags(tags: list[str]) -> list[str]:
    return sorted({tag.strip() for tag in tags if tag and tag.strip()})


def get_overview_widget_tags(session: Session, widget: OverviewWidget) -> list[str]:
    return list(
        session.scalars(
            select(OverviewWidgetTag.tag_name)
            .where(OverviewWidgetTag.widget_id == widget.id)
            .order_by(OverviewWidgetTag.tag_name)
        )
    )


def get_overview_widget_user_assignments(
    session: Session,
    widget: OverviewWidget,
) -> list[str]:
    return list(session.scalars(
        select(UserSavedMetric.owner_username)
        .where(UserSavedMetric.widget_key == widget.widget_key)
        .order_by(UserSavedMetric.owner_username)
    ))


def sync_overview_widget_user_assignments(
    session: Session,
    widget: OverviewWidget,
    usernames: list[str],
) -> list[str]:
    requested = {username.strip() for username in usernames if username.strip()}
    users = list(session.scalars(select(AdminUser).where(AdminUser.username.in_(requested))))
    resolved = {user.username: user for user in users}
    assignments = list(session.scalars(
        select(UserSavedMetric).where(UserSavedMetric.widget_key == widget.widget_key)
    ))
    for assignment in assignments:
        if assignment.owner_username not in resolved:
            session.delete(assignment)
    existing_usernames = {assignment.owner_username for assignment in assignments}
    for username, user in resolved.items():
        if username not in existing_usernames:
            session.add(UserSavedMetric(
                owner_user_id=str(user.id),
                owner_username=username,
                widget_key=widget.widget_key,
            ))
    return sorted(resolved)


def resolve_overview_widget_tags_and_assignments(
    session: Session,
    widget: OverviewWidget,
    tags: list[str],
    assigned_usernames: list[str],
) -> list[str]:
    requested_usernames = list(assigned_usernames)
    direct_tags: list[str] = []
    for tag in tags:
        if tag.lower().startswith("user:") and tag[5:].strip():
            username = tag[5:].strip()
            if session.scalar(select(AdminUser.id).where(AdminUser.username == username)):
                requested_usernames.append(username)
                continue
        direct_tags.append(tag)
    print(requested_usernames)
    return [
        *direct_tags,
        *sync_overview_widget_user_assignments(session, widget, requested_usernames),
    ]


def list_assigned_widget_tags(session: Session) -> list[str]:
    """Return only catalogue tags which are still in use by a widget."""
    return list(
        session.scalars(
            select(WidgetTag.name)
            .join(OverviewWidgetTag, OverviewWidgetTag.tag_name == WidgetTag.name)
            .distinct()
            .order_by(WidgetTag.name)
        )
    )


def set_overview_widget_tags(session: Session, widget: OverviewWidget, tags: list[str]) -> None:
    normalized = normalize_widget_tags(tags)
    session.execute(delete(OverviewWidgetTag).where(OverviewWidgetTag.widget_id == widget.id))
    for tag in normalized:
        if session.get(WidgetTag, tag) is None:
            session.add(WidgetTag(name=tag))
        session.add(OverviewWidgetTag(widget_id=widget.id, tag_name=tag))


def normalize_overview_widget_grid_width(
    widget_type: str,
    grid_width: int | None,
) -> int:
    if grid_width is None:
        return 12 if widget_type in {OVERVIEW_WIDGET_TABLE, OVERVIEW_WIDGET_TIMESERIES} else 1

    return max(1, min(12, int(grid_width)))


def normalize_overview_widget_grid_height(
    widget_type: str,
    grid_height: int | None,
) -> int:
    if grid_height is None:
        return 2 if widget_type == OVERVIEW_WIDGET_SCALAR else 4

    return max(2, min(24, int(grid_height)))


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


def suggest_metric_title_with_llm(
    session: Session, request: OverviewWidgetTitleSuggestionRequest
) -> str:
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
    existing_database_url: str | None = None,
) -> NormalizedDatasourceConfiguration:
    try:
        return normalize_datasource_configuration(
            database_type=database_type,
            connection_config=connection_config,
            database_path=database_path,
            database_url=database_url,
            sql_dialect=sql_dialect,
            existing_database_url=existing_database_url,
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
        **serialize_overview_widget_config(session, widget),
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

    for table_expression in expression.find_all(exp.Table):
        if (
            table_expression.args.get("db")
            and table_expression.args["db"].name == connector.connector_key
        ):
            table_expression.set("db", None)
        if (
            table_expression.args.get("catalog")
            and table_expression.args["catalog"].name == connector.connector_key
        ):
            table_expression.set("catalog", None)

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

def ensure_admin_can_manage_datasource_type(user: AdminUser, database_type: str) -> None:
    """Keep Excel/DuckDB datasource functionality behind an Enterprise seat."""
    if database_type == "duckdb-excel" and not user.enterprise_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Excel datasources require an assigned Enterprise user license.",
        )


def ensure_admin_can_manage_datasource(user: AdminUser, connector: DatasourceConnector) -> None:
    ensure_admin_can_manage_datasource_type(user, connector.database_type)


def ensure_admin_can_use_widget_datasource(
    user: AdminUser,
    connector: DatasourceConnector,
) -> None:
    """Apply per-user datasource entitlements to overview widget execution."""
    try:
        license_service.ensure_datasource_contexts_allowed(
            [(connector, None)],
            enterprise_access=user.enterprise_access,
        )
    except LicenseAccessError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def admin_can_use_widget_datasource(user: AdminUser, connector: DatasourceConnector) -> bool:
    try:
        ensure_admin_can_use_widget_datasource(user, connector)
    except HTTPException:
        return False
    return True


def get_current_datasource_viewer(
    principal: AuthenticatedSession = Depends(get_current_authenticated_session),
) -> AuthenticatedSession:
    """Allow administrators to manage datasource configuration without a seat."""
    if principal.user.role == "admin":
        if principal.user.must_change_password:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Password change is required before using the admin portal.",
            )
        return principal
    return get_current_enterprise_api_user(principal)

@router.post("/auth/login", response_model=LoginResponse)
def login(request: LoginRequest, session: Session = Depends(get_session)) -> LoginResponse:
    user = session.scalar(
        select(AdminUser).where(
            AdminUser.username == request.username,
            AdminUser.auth_provider == "local",
        )
    )

    if user is not None and verify_password(request.password, user.password_hash):
        ensure_user_license_access(user)
        token = create_session_token()
        session.add(
            AdminSession(
                token_hash=hash_token(token),
                user_id=user.id,
                username=user.username,
                role=user.role,
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
            role=user.role,
            enterprise_access=user.enterprise_access,
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
    ensure_user_license_access(user)
    token = create_session_token()
    session.add(
        AdminSession(
            token_hash=hash_token(token),
            user_id=user.id,
            username=identity.username,
            role=user.role,
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
            "role": user.role,
        },
    )
    session.commit()

    return LoginResponse(
        token=token,
        username=identity.username,
        must_change_password=False,
        role=user.role,
        enterprise_access=user.enterprise_access,
    )


def get_or_create_external_auth_user(
    session: Session,
    provider_id: str,
    username: str,
) -> AdminUser:
    user = session.scalar(
        select(AdminUser).where(
            AdminUser.username == username,
            AdminUser.auth_provider == provider_id,
        )
    )
    if user is not None:
        user.must_change_password = False
        user.is_provisioned = False
        return user

    user = AdminUser(
        username=username,
        password_hash="external$disabled",
        must_change_password=False,
        auth_provider=provider_id,
        is_provisioned=False,
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
        role=principal.user.role,
        enterprise_access=principal.user.enterprise_access,
    )


@router.post("/auth/logout")
def logout(
    principal: AuthenticatedSession = Depends(get_current_authenticated_session),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    session.delete(principal.session)
    session.commit()
    return {"status": "logged_out"}


@router.post("/auth/change-password", response_model=MeResponse)
def change_password(
    request: ChangePasswordRequest,
    principal: AuthenticatedSession = Depends(get_current_authenticated_session),
    session: Session = Depends(get_session),
) -> MeResponse:
    user = session.get(AdminUser, principal.user.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin session.")

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

    return MeResponse(
        username=user.username,
        must_change_password=False,
        role=user.role,
        enterprise_access=user.enterprise_access,
    )


@router.get("/identities")
def list_identities(
    refresh: bool = False,
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    with create_session() as session:
        identity_management_allowed = license_service.identity_management_allowed()
        session_counts: dict[int, int] = {
            user_id: count
            for user_id, count in session.execute(
                select(AdminSession.user_id, func.count(AdminSession.id)).group_by(AdminSession.user_id)
            ).tuples()
        }
        users_by_identity = {
            str(item.id): item for item in session.scalars(select(AdminUser)).all()
        }
        dashboards_by_owner: dict[str, list[dict[str, Any]]] = {}
        for dashboard in session.scalars(
            select(Dashboard).order_by(Dashboard.updated_at.desc(), Dashboard.id.desc())
        ).all():
            dashboards_by_owner.setdefault(dashboard.owner_user_id, []).append(
                serialize_admin_dashboard(dashboard)
            )
        items = [
            {
                "id": str(item.id),
                "username": item.username,
                "name": item.display_name or item.username,
                "role": item.role,
                "is_system_admin": item.is_system_admin,
                "provider": "Built-in",
                "provider_id": "local",
                "editable_name": True,
                "editable_password": True,
                "attributes": {},
                "sessions_count": session_counts.get(item.id, 0),
            }
            for item in session.scalars(
                select(AdminUser)
                .where(AdminUser.auth_provider == "local")
                .order_by(AdminUser.username)
            ).all()
        ]
        if identity_management_allowed:
            for provider in get_auth_provider_registry().identity_providers():
                items.extend(provider.list_users(session, refresh=refresh))
        for item in items:
            account = users_by_identity.get(str(item["id"]))
            owner_user_id = str(item["id"]) if account is not None else None
            item["dashboards"] = dashboards_by_owner.get(owner_user_id or "", [])
            item["enterprise_access"] = bool(
                item.get("is_system_admin")
                or (account is not None and account.enterprise_access)
            )
            item["enterprise_access_editable"] = bool(
                identity_management_allowed and not item.get("is_system_admin")
            )
            if item.get("provider_id") != "local":
                item["sessions_count"] = session_counts.get(account.id, 0) if account else 0
        mark_overshadowed_identities(items)
        return {
            "items": items,
            "can_manage_identities": bool(
                user.enterprise_access and identity_management_allowed
            ),
        }


def identity_user_for_session_action(session: Session, identity_id: str) -> AdminUser:
    try:
        user = session.get(AdminUser, int(identity_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Identity not found.") from exc
    if user is None:
        raise HTTPException(status_code=404, detail="Identity not found.")
    return user


@router.delete("/identities/{identity_id}/sessions")
def clear_identity_sessions(
    identity_id: str,
    principal: AuthenticatedSession = Depends(get_current_authenticated_session),
    user: AdminUser = Depends(get_current_enterprise_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    target = identity_user_for_session_action(session, identity_id)
    if target.is_system_admin and target.id != principal.user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The system administrator cannot be managed by another administrator.",
        )
    result = session.execute(
        delete(AdminSession).where(
            AdminSession.user_id == target.id,
            AdminSession.id != principal.session.id,
        )
    )
    cleared = int(cast(CursorResult[Any], result).rowcount or 0)
    record_admin_audit(
        session=session,
        actor=user.username,
        action="identity.sessions.clear",
        resource_type="admin_user",
        resource_id=target.username,
        details={"cleared_sessions": cleared, "auth_provider": target.auth_provider},
    )
    session.commit()
    return {"status": "cleared", "cleared_sessions": cleared}


@router.patch("/identities/{identity_id}/enterprise-access")
def update_identity_enterprise_access(
    identity_id: str,
    request: IdentityEnterpriseAccessRequest,
    actor: AdminUser = Depends(get_current_enterprise_admin),
    session: Session = Depends(get_session),
) -> dict[str, bool]:
    try:
        license_service.ensure_identity_management_allowed()
    except LicenseAccessError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    try:
        target = session.get(AdminUser, int(identity_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid identity id.")

    if target is None:
        raise HTTPException(status_code=404, detail="Identity was not found.")
    if target.is_system_admin:
        raise HTTPException(
            status_code=400,
            detail="Enterprise access for the system administrator cannot be changed.",
        )

    if request.enterprise_access and not target.enterprise_access:
        assigned_users = session.scalar(
            select(func.count())
            .select_from(AdminUser)
            .where(
                AdminUser.enterprise_access.is_(True),
            )
        ) or 0
        try:
            license_service.ensure_human_user_seat_available(assigned_users + 1)
        except LicenseAccessError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    target.enterprise_access = request.enterprise_access
    record_admin_audit(
        session=session,
        actor=actor.username,
        action="identity.enterprise_access.update",
        resource_type="admin_user",
        resource_id=target.username,
        details={
            "auth_provider": target.auth_provider,
            "enterprise_access": target.enterprise_access,
        },
    )
    session.commit()
    return {"enterprise_access": target.enterprise_access}


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


@router.get("/overview")
def get_overview(
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    prompts = list_prompt_templates(session)
    retention_days = get_data_query_audit_retention_days(session)
    widgets = []
    for widget in list_overview_widgets(session):
        datasource = get_datasource_connector_by_key(session, widget.datasource_key)
        if datasource is not None and admin_can_use_widget_datasource(user, datasource):
            widgets.append(serialize_overview_widget(session, widget))

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
            if admin_can_use_widget_datasource(user, connector)
        ],
        "tags": list_assigned_widget_tags(session),
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
            serialize_overview_widget_config(session, widget)
            for widget in list_all_overview_widgets(session)
            if (datasource := get_datasource_connector_by_key(session, widget.datasource_key))
            is not None
            and admin_can_use_widget_datasource(user, datasource)
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
            if admin_can_use_widget_datasource(user, connector)
        ],
        "tags": list_assigned_widget_tags(session),
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

    ensure_admin_can_use_widget_datasource(user, datasource)

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
    next_grid_height = normalize_overview_widget_grid_height(
        request.widget_type,
        request.grid_height,
    )
    next_result_mode = normalize_overview_widget_result_mode(request.result_mode)

    try:
        generated_sql = request.sql or generate_overview_widget_sql(
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
            grid_height=next_grid_height,
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
    session.flush()
    set_overview_widget_tags(
        session,
        widget,
        resolve_overview_widget_tags_and_assignments(
            session,
            widget,
            ["public", *(request.tags or [])],
            request.assigned_usernames or [],
        ),
    )
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
            "grid_height": widget.grid_height,
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
    principal: AuthenticatedSession = Depends(get_current_enterprise_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    datasource = get_datasource_connector_by_key(session, request.datasource_key)

    if datasource is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Datasource does not exist.",
        )

    ensure_admin_can_use_widget_datasource(principal.user, datasource)

    widget_key = build_client_widget_key(session, request.label, request.question)
    grid_width = normalize_overview_widget_grid_width(
        request.widget_type,
        None,
    )
    grid_height = normalize_overview_widget_grid_height(
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
        grid_height=grid_height,
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
    session.flush()
    set_overview_widget_tags(
        session,
        widget,
        ["public", principal.session.username or principal.user.username],
    )
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
            **serialize_overview_widget_config(session, widget),
            "result": result_payload,
        },
    }


@router.post("/overview/widgets/generate-sql")
def generate_overview_widget_sql_preview(
    request: OverviewWidgetSqlGenerationRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    datasource = get_datasource_connector_by_key(session, request.datasource_key)

    if datasource is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Datasource does not exist.",
        )

    ensure_admin_can_use_widget_datasource(user, datasource)

    query_request = build_overview_widget_query_request(
        connector=datasource,
        widget_key=request.widget_key,
        question=request.question,
    )
    try:
        generated_sql = generate_overview_widget_sql(
            session=session,
            connector=datasource,
            query_request=query_request,
            actor=user.username,
        )
    except (ConfigurationError, LlmProviderError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {"sql": generated_sql}


@router.post("/overview/widgets/title-suggestion")
def suggest_overview_widget_title(
    request: OverviewWidgetTitleSuggestionRequest,
    _principal: AuthenticatedSession = Depends(get_current_enterprise_api_user),
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

    ensure_admin_can_use_widget_datasource(user, datasource)

    query_request = build_overview_widget_query_request(
        connector=datasource,
        widget_key=widget.widget_key,
        question=request.question,
    )
    question_changed = request.question != widget.question
    generated_sql = widget.sql
    next_position = request.position if request.position is not None else widget.position
    next_grid_width = normalize_overview_widget_grid_width(
        request.widget_type,
        request.grid_width if request.grid_width is not None else widget.grid_width,
    )
    next_grid_height = normalize_overview_widget_grid_height(
        request.widget_type,
        request.grid_height if request.grid_height is not None else widget.grid_height,
    )
    next_result_mode = normalize_overview_widget_result_mode(request.result_mode)
    next_active = request.active if request.active is not None else widget.active

    try:
        if question_changed:
            generated_sql = request.sql or generate_overview_widget_sql(
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
            grid_height=next_grid_height,
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
    widget.grid_height = next_grid_height
    widget.active = next_active
    widget.updated_by = user.username
    if request.tags is not None or request.assigned_usernames is not None:
        current_assignments = get_overview_widget_user_assignments(session, widget)
        set_overview_widget_tags(
            session,
            widget,
            resolve_overview_widget_tags_and_assignments(
                session,
                widget,
                request.tags if request.tags is not None else get_overview_widget_tags(session, widget),
                request.assigned_usernames
                if request.assigned_usernames is not None
                else current_assignments,
            ),
        )

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
            "grid_height": widget.grid_height,
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

    if request.grid_height is not None:
        widget.grid_height = normalize_overview_widget_grid_height(
            widget.widget_type,
            request.grid_height,
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
            "grid_height": widget.grid_height,
            "active": widget.active,
        },
    )
    session.commit()

    return {
        "item": serialize_overview_widget_config(session, widget),
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
    available_only: bool = False,
    principal: AuthenticatedSession = Depends(get_current_datasource_viewer),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    enterprise_access = has_enterprise_user_access(principal)
    connectors = list_datasource_connectors(session)
    if available_only or principal.user.role != "admin":
        connectors = [
            connector
            for connector in connectors
            if connector.active and not is_system_datasource_connector(connector)
        ]
    if available_only or principal.user.role != "admin":
        connectors = filter_datasources_for_identity_privileges(session, principal, connectors)
    available_ids = {connector.connector_key for connector in connectors}
    return {
        "items": [
            {
                **serialize_datasource(connector),
                "enterprise_access_required": connector.database_type == "duckdb-excel",
            }
            for connector in connectors
        ],
        "selected_datasource_ids": [
            source_id
            for source_id in selected_datasource_ids(session, principal)
            if source_id in available_ids
        ],
        "multiple_selection_allowed": multi_datasource_access_is_active(),
        "excel_upload_allowed": enterprise_access,
        "viewer": principal.session.username or principal.user.username,
    }


@router.api_route("/datasources/selection", methods=["PUT", "POST"])
def update_datasource_selection(
    request: DatasourceSelectionRequest,
    principal: AuthenticatedSession = Depends(get_current_enterprise_api_user),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    selected_ids = list(
        dict.fromkeys(
            source_id.strip() for source_id in request.datasource_ids if source_id.strip()
        )
    )
    if len(selected_ids) > 1 and not multi_datasource_access_is_active():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selecting multiple datasources requires the multi-datasource access extension.",
        )

    connectors = list_datasource_connectors(session)
    available = [
        connector
        for connector in connectors
        if connector.active and not is_system_datasource_connector(connector)
    ]
    if identity_privileges_are_active():
        available = filter_datasources_for_identity_privileges(session, principal, available)
    if not has_enterprise_user_access(principal):
        available = [
            connector
            for connector in available
            if connector.database_type != "duckdb-excel"
        ]
    available_ids = {connector.connector_key for connector in available}
    unknown_ids = [source_id for source_id in selected_ids if source_id not in available_ids]
    if unknown_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="One or more selected datasources are unavailable to this user.",
        )

    set_selected_datasource_ids(session, principal, selected_ids)
    session.commit()
    return {
        "selected_datasource_ids": selected_ids,
        "multiple_selection_allowed": multi_datasource_access_is_active(),
    }


@router.get("/datasource-types")
def get_datasource_types(
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    return {
        "items": [definition.serialize() for definition in get_connector_registry().list()],
        "viewer": user.username,
    }


@router.get("/connectors/odbc/drivers")
def get_odbc_drivers(
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    return {
        "drivers": list_odbc_drivers(),
        "viewer": user.username,
    }


@router.get("/connectors/odbc/dsns")
def get_odbc_dsns(
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    dsns = list_configured_dsns()
    return {
        "system": dsns["system"],
        "user": dsns["user"],
        "viewer": user.username,
    }


@router.get("/connectors/odbc/diagnostics")
def get_odbc_diagnostics(
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    return {
        "item": collect_diagnostics().serialize(),
        "viewer": user.username,
    }


@router.get("/extensions")
def get_extensions(
    user: AdminUser = Depends(get_current_admin),
) -> dict[str, Any]:
    if not user.enterprise_access:
        return {
            "items": [serialize_extension_record(record) for record in get_extension_manager().records],
            "admin_sections": [],
            "admin_frontend_modules": [],
            "viewer": user.username,
        }
    registry = get_api_registry()
    return {
        "items": [serialize_extension_record(record) for record in get_extension_manager().records],
        "admin_sections": [
            section.serialize() for section in registry.list_admin_sections()
        ],
        "admin_frontend_modules": [
            {
                "extension_id": module.extension_id,
                "module_path": module.module_path,
            }
            for module in registry.list_admin_frontend_modules()
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

    ensure_admin_can_manage_datasource_type(user, normalized_config.database_type)
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
    principal: AuthenticatedSession = Depends(get_current_enterprise_api_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if not has_enterprise_user_access(principal):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Uploading Excel workbooks requires an assigned Enterprise user license.",
        )
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
        updated_by=principal.session.username or principal.user.username,
    )
    session.add(connector)
    session.flush()

    grant_client_excel_datasource_permission(session, connector.id, principal)

    if active:
        set_active_datasource_connector(
            session, connector, principal.session.username or principal.user.username
        )

    license_service.ensure_active_source_limit(list_datasource_connectors(session))

    record_admin_audit(
        session=session,
        actor=principal.session.username or principal.user.username,
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

    is_deactivation = not request.active
    if not is_deactivation:
        ensure_admin_can_manage_datasource(user, connector)
        license_service.ensure_datasource_type_allowed(connector.database_type)

    if request.active:
        set_active_datasource_connector(session, connector, user.username)
    else:
        connector.active = False
        connector.updated_by = user.username
        remove_datasource_from_selections(session, connector.connector_key)

    if not is_deactivation:
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

    ensure_admin_can_manage_datasource(user, connector)

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
        existing_database_url=connector.database_url,
    )

    ensure_admin_can_manage_datasource_type(user, normalized_config.database_type)
    license_service.ensure_datasource_type_allowed(normalized_config.database_type)

    connector.name = request.name
    connector.database_type = normalized_config.database_type
    connector.database_url = normalized_config.database_url
    connector.sql_dialect = normalized_config.sql_dialect
    connector.active = request.active
    connector.updated_by = user.username

    if request.active:
        set_active_datasource_connector(session, connector, user.username)
    else:
        remove_datasource_from_selections(session, connector.connector_key)

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

    ensure_admin_can_manage_datasource(user, connector)

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
    remove_datasource_from_selections(session, connector_key)
    remove_datasource_permissions(session, connector.id)
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

    ensure_admin_can_manage_datasource(user, connector)

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


def connection_test_failed_http_exception(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Connection test failed: {exc}",
    )


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

    ensure_admin_can_manage_datasource(user, connector)

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
        raise connection_test_failed_http_exception(exc) from exc

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
    user: AdminUser = Depends(get_current_admin),
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
    ensure_admin_can_manage_datasource_type(user, connector.database_type)
    license_service.ensure_datasource_type_allowed(connector.database_type)
    try:
        test_datasource_connection(connector)
    except Exception as exc:
        raise connection_test_failed_http_exception(exc) from exc
    return {"status": "ok"}


DATASOURCE_VIEW_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def normalize_datasource_view_name(name: str) -> str:
    normalized = name.strip()
    if not DATASOURCE_VIEW_NAME_RE.fullmatch(normalized):
        raise ValueError(
            "View name must start with a letter or underscore and contain only letters, "
            "numbers and underscores."
        )
    return normalized


def clean_sql_text(value: str) -> str:
    cleaned = remove_thinking_blocks(value).strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if cleaned.lower().startswith("sql\n"):
        cleaned = cleaned[4:].strip()
    return cleaned.strip().rstrip(";")


def create_admin_datasource_engine(connector: DatasourceConnector):
    if connector.database_type == "duckdb-excel" or connector.database_url.startswith(
        "duckdb-excel://"
    ):
        raise ValueError(
            "This datasource type does not support admin-managed SQL views."
        )
    connect_args = (
        {"check_same_thread": False}
        if connector.database_url.startswith("sqlite")
        else {}
    )
    return create_engine(connector.database_url, connect_args=connect_args)


def quote_datasource_identifier(engine, name: str) -> str:
    return engine.dialect.identifier_preparer.quote_identifier(name)


def extract_select_sql_for_view(
    connector: DatasourceConnector,
    view_name: str,
    sql: str,
) -> str:
    cleaned = clean_sql_text(sql)
    if not cleaned:
        raise ValueError("View SQL is required.")

    read_dialect = sqlglot_read_dialect(connector.sql_dialect)
    try:
        statements = sqlglot.parse(cleaned, read=read_dialect)
    except Exception as exc:
        raise ValueError(f"Invalid SQL syntax: {exc}") from exc

    if len(statements) != 1 or statements[0] is None:
        raise ValueError("Only a single SQL statement can be used to create a view.")

    statement = statements[0]
    if isinstance(statement, exp.Create):
        if str(statement.args.get("kind") or "").upper() != "VIEW":
            raise ValueError("Only CREATE VIEW statements are allowed here.")
        selectable = statement.args.get("expression")
        if isinstance(selectable, exp.Subquery):
            selectable = selectable.this
        if not isinstance(selectable, (exp.Select, exp.SetOperation)):
            raise ValueError("CREATE VIEW must be based on a SELECT query.")
        select_sql = selectable.sql(dialect=read_dialect) if read_dialect else selectable.sql()
    elif isinstance(statement, (exp.Select, exp.SetOperation)):
        select_sql = statement.sql(dialect=read_dialect) if read_dialect else statement.sql()
    else:
        raise ValueError(
            "View SQL must be a SELECT query or a CREATE VIEW ... AS SELECT statement."
        )

    select_sql = prepare_overview_sql_for_connector(connector, select_sql)
    SelectOnlySqlValidator(dialect=read_dialect).validate(select_sql)
    return select_sql.strip().rstrip(";")


def build_create_view_sql(
    connector: DatasourceConnector,
    view_name: str,
    sql: str,
    engine,
) -> str:
    select_sql = extract_select_sql_for_view(connector, view_name, sql)
    quoted_view_name = quote_datasource_identifier(engine, view_name)
    return f"CREATE VIEW {quoted_view_name} AS {select_sql}"


def view_sql_for_editor(
    connector: DatasourceConnector,
    view_name: str,
    definition: str | None,
) -> str:
    if not definition:
        return ""
    try:
        return extract_select_sql_for_view(connector, view_name, definition)
    except ValueError:
        return clean_sql_text(definition)


def find_existing_view_name(inspector, view_name: str) -> str | None:
    normalized = normalize_datasource_view_name(view_name)
    existing_views = {
        existing_name.lower(): existing_name
        for existing_name in inspector.get_view_names()
    }
    return existing_views.get(normalized.lower())


def datasource_view_description_from_cache(
    session: Session,
    connector: DatasourceConnector,
    view_name: str,
    actor: str,
) -> str:
    cache = get_datasource_schema_cache(session, connector.id)
    if cache is None:
        cache = introspect_datasource_connector(session, connector, actor)
    settings = json_loads(cache.table_settings_json)
    tables = settings.get("tables", {})
    direct = tables.get(view_name, {})
    if direct:
        return str(direct.get("description", ""))
    lowered = view_name.lower()
    for name, item in tables.items():
        if str(name).lower() == lowered:
            return str(item.get("description", ""))
    return ""


def get_datasource_view_definition(
    session: Session,
    connector: DatasourceConnector,
    view_name: str,
    actor: str,
) -> dict[str, str]:
    normalized_name = normalize_datasource_view_name(view_name)
    engine = create_admin_datasource_engine(connector)
    try:
        with engine.connect() as connection:
            inspector = sqlalchemy_inspect(connection)
            existing_name = find_existing_view_name(inspector, normalized_name)
            if not existing_name:
                raise ValueError(f"View '{normalized_name}' does not exist.")
            definition = inspector.get_view_definition(existing_name)
    finally:
        engine.dispose()

    return {
        "name": existing_name,
        "description": datasource_view_description_from_cache(
            session,
            connector,
            existing_name,
            actor,
        ),
        "sql": view_sql_for_editor(connector, existing_name, definition),
    }


def save_datasource_view_settings_to_cache(
    session: Session,
    cache: DatasourceSchemaCache,
    view_name: str,
    description: str,
    actor: str,
) -> DatasourceSchemaCache:
    schema = json_loads(cache.schema_json)
    view_names = {
        str(item.get("name")).lower(): str(item.get("name"))
        for item in schema.get("tables", [])
        if item.get("object_type") == "view"
    }
    actual_view_name = view_names.get(view_name.lower())
    if not actual_view_name:
        raise ValueError(f"View '{view_name}' is not present in the introspected schema.")

    settings = json_loads(cache.table_settings_json)
    table_settings = settings.setdefault("tables", {})
    existing = table_settings.setdefault(actual_view_name, {})
    existing["selected"] = True
    existing["description"] = description.strip()
    return update_schema_table_settings(
        session=session,
        cache=cache,
        table_settings={"tables": table_settings},
        actor=actor,
    )


def execute_datasource_view_sql(
    session: Session,
    connector: DatasourceConnector,
    view_name: str,
    description: str,
    sql: str,
    actor: str,
) -> DatasourceSchemaCache:
    normalized_name = normalize_datasource_view_name(view_name)
    engine = create_admin_datasource_engine(connector)
    try:
        create_view_sql = build_create_view_sql(connector, normalized_name, sql, engine)
        with engine.begin() as connection:
            inspector = sqlalchemy_inspect(connection)
            existing_name = find_existing_view_name(inspector, normalized_name)
            if existing_name:
                raise ValueError(
                    f"View '{existing_name}' already exists. Delete it before creating it again."
                )
            connection.exec_driver_sql(create_view_sql)
    finally:
        engine.dispose()

    cache = introspect_datasource_connector(session, connector, actor)
    return save_datasource_view_settings_to_cache(
        session,
        cache,
        normalized_name,
        description,
        actor,
    )


def save_datasource_view_settings(
    session: Session,
    connector: DatasourceConnector,
    view_name: str,
    description: str,
    actor: str,
) -> DatasourceSchemaCache:
    normalized_name = normalize_datasource_view_name(view_name)
    cache = get_datasource_schema_cache(session, connector.id)
    if cache is None:
        cache = introspect_datasource_connector(session, connector, actor)
    return save_datasource_view_settings_to_cache(
        session,
        cache,
        normalized_name,
        description,
        actor,
    )


def update_datasource_view_sql(
    session: Session,
    connector: DatasourceConnector,
    current_view_name: str,
    next_view_name: str,
    description: str,
    sql: str,
    actor: str,
) -> DatasourceSchemaCache:
    current_name = normalize_datasource_view_name(current_view_name)
    next_name = normalize_datasource_view_name(next_view_name)
    engine = create_admin_datasource_engine(connector)
    try:
        create_view_sql = build_create_view_sql(connector, next_name, sql, engine)
        with engine.begin() as connection:
            inspector = sqlalchemy_inspect(connection)
            existing_current_name = find_existing_view_name(inspector, current_name)
            if not existing_current_name:
                raise ValueError(f"View '{current_name}' does not exist.")
            existing_next_name = find_existing_view_name(inspector, next_name)
            if (
                existing_next_name
                and existing_next_name.lower() != existing_current_name.lower()
            ):
                raise ValueError(
                    f"View '{existing_next_name}' already exists. Choose another view name."
                )
            connection.exec_driver_sql(
                f"DROP VIEW {quote_datasource_identifier(engine, existing_current_name)}"
            )
            connection.exec_driver_sql(create_view_sql)
    finally:
        engine.dispose()

    cache = introspect_datasource_connector(session, connector, actor)
    return save_datasource_view_settings_to_cache(
        session,
        cache,
        next_name,
        description,
        actor,
    )


def delete_datasource_view(
    session: Session,
    connector: DatasourceConnector,
    view_name: str,
    actor: str,
) -> DatasourceSchemaCache:
    normalized_name = normalize_datasource_view_name(view_name)
    engine = create_admin_datasource_engine(connector)
    try:
        with engine.begin() as connection:
            inspector = sqlalchemy_inspect(connection)
            existing_name = find_existing_view_name(inspector, normalized_name)
            if not existing_name:
                raise ValueError(f"View '{normalized_name}' does not exist.")
            connection.exec_driver_sql(
                f"DROP VIEW {quote_datasource_identifier(engine, existing_name)}"
            )
    finally:
        engine.dispose()

    return introspect_datasource_connector(session, connector, actor)


def generate_datasource_view_sql(
    session: Session,
    connector: DatasourceConnector,
    view_name: str,
    description: str,
    actor: str,
) -> str:
    normalized_name = normalize_datasource_view_name(view_name)
    if not description.strip():
        raise ValueError("View description is required to generate SQL.")

    cache = get_or_create_datasource_schema_cache(session, connector, actor)
    session.commit()

    from gaard_api.api.v1.query import create_sql_generator, resolve_sql_dialect_plan

    datasource_context = (connector, cache)
    query_request = QueryRequest(
        question=(
            f"Create a SQL SELECT statement for a database view named {normalized_name}. "
            "The SELECT must implement this datasource logic:\n"
            f"{description.strip()}\n\n"
            "Return only one SELECT query. Do not create, drop, alter, insert, update or delete data."
        ),
        datasource_id=connector.connector_key,
        user_id=f"admin-view-config:{normalized_name}",
    )
    generated_sql = create_sql_generator(
        datasource_context,
        dialect_plan=resolve_sql_dialect_plan([datasource_context]),
    ).generate(query_request)

    engine = create_admin_datasource_engine(connector)
    try:
        return build_create_view_sql(connector, normalized_name, generated_sql.sql, engine)
    finally:
        engine.dispose()


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

    ensure_admin_can_manage_datasource(user, connector)

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

    ensure_admin_can_manage_datasource(user, connector)

    cache = get_datasource_schema_cache(session, connector.id)

    if cache is None:
        license_service.ensure_datasource_type_allowed(connector.database_type)
        try:
            cache = introspect_datasource_connector(session, connector, user.username)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Schema introspection failed: {exc}",
            ) from exc
        session.commit()

    return {
        "item": serialize_datasource_schema(cache),
        "viewer": user.username,
    }


@router.get("/datasources/{connector_id}/schema/views/{view_name}")
def get_datasource_view_definition_endpoint(
    connector_id: int,
    view_name: str,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    ensure_admin_can_manage_datasource(user, connector)
    license_service.ensure_datasource_type_allowed(connector.database_type)

    try:
        item = get_datasource_view_definition(
            session=session,
            connector=connector,
            view_name=view_name,
            actor=user.username,
        )
    except (SQLAlchemyError, ValueError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    session.commit()
    return {"item": item}


@router.post("/datasources/{connector_id}/schema/views/generate-sql")
def generate_datasource_view_sql_preview(
    connector_id: int,
    request: DatasourceViewSqlGenerationRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    ensure_admin_can_manage_datasource(user, connector)
    license_service.ensure_datasource_type_allowed(connector.database_type)

    try:
        generated_sql = generate_datasource_view_sql(
            session=session,
            connector=connector,
            view_name=request.name,
            description=request.description,
            actor=user.username,
        )
    except (
        ConfigurationError,
        LlmProviderError,
        SQLAlchemyError,
        SqlValidationError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {"sql": generated_sql}


@router.post("/datasources/{connector_id}/schema/views/execute")
def execute_datasource_view_sql_endpoint(
    connector_id: int,
    request: DatasourceViewExecuteRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    ensure_admin_can_manage_datasource(user, connector)
    license_service.ensure_datasource_type_allowed(connector.database_type)

    try:
        cache = execute_datasource_view_sql(
            session=session,
            connector=connector,
            view_name=request.name,
            description=request.description,
            sql=request.sql,
            actor=user.username,
        )
    except (SQLAlchemyError, SqlValidationError, ValueError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.view.execute",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
        details={"view": normalize_datasource_view_name(request.name)},
    )
    session.commit()

    return {"item": serialize_datasource_schema(cache)}


@router.put("/datasources/{connector_id}/schema/views/{view_name}/execute")
def update_datasource_view_sql_endpoint(
    connector_id: int,
    view_name: str,
    request: DatasourceViewUpdateRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    ensure_admin_can_manage_datasource(user, connector)
    license_service.ensure_datasource_type_allowed(connector.database_type)

    try:
        cache = update_datasource_view_sql(
            session=session,
            connector=connector,
            current_view_name=view_name,
            next_view_name=request.name,
            description=request.description,
            sql=request.sql,
            actor=user.username,
        )
    except (SQLAlchemyError, SqlValidationError, ValueError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.view.update",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
        details={
            "from": normalize_datasource_view_name(view_name),
            "to": normalize_datasource_view_name(request.name),
        },
    )
    session.commit()

    return {"item": serialize_datasource_schema(cache)}


@router.put("/datasources/{connector_id}/schema/views/{view_name}")
def save_datasource_view_settings_endpoint(
    connector_id: int,
    view_name: str,
    request: DatasourceViewSettingsRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    ensure_admin_can_manage_datasource(user, connector)
    license_service.ensure_datasource_type_allowed(connector.database_type)

    try:
        cache = save_datasource_view_settings(
            session=session,
            connector=connector,
            view_name=view_name,
            description=request.description,
            actor=user.username,
        )
    except (SQLAlchemyError, ValueError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.view.save",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
        details={"view": normalize_datasource_view_name(view_name)},
    )
    session.commit()

    return {"item": serialize_datasource_schema(cache)}


@router.delete("/datasources/{connector_id}/schema/views/{view_name}")
def delete_datasource_view_endpoint(
    connector_id: int,
    view_name: str,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    connector = get_datasource_connector(session, connector_id)

    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource connector not found.",
        )

    ensure_admin_can_manage_datasource(user, connector)
    license_service.ensure_datasource_type_allowed(connector.database_type)

    try:
        normalized_name = normalize_datasource_view_name(view_name)
        cache = delete_datasource_view(
            session=session,
            connector=connector,
            view_name=normalized_name,
            actor=user.username,
        )
    except (SQLAlchemyError, ValueError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    record_admin_audit(
        session=session,
        actor=user.username,
        action="datasource.view.delete",
        resource_type="datasource_connector",
        resource_id=connector.connector_key,
        details={"view": normalized_name},
    )
    session.commit()

    return {"item": serialize_datasource_schema(cache)}


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

    ensure_admin_can_manage_datasource(user, connector)

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
        "items": [serialize_business_logic_suggestion(suggestion) for suggestion in suggestions],
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


@router.post("/llm-config/models")
def list_llm_models(
    request: LlmModelsRequest,
    user: AdminUser = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Discover models without saving the draft LLM configuration."""
    if request.provider != "openai-compatible":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only openai-compatible LLM provider is supported.",
        )

    current_config = get_llm_runtime_config(session)
    api_key = (request.api_key or "").strip() or current_config.api_key
    if not api_key or api_key == "change-me":
        return {"items": [], "error": "Enter an API key to load available models."}

    try:
        items = OpenAICompatibleClient(
            base_url=request.base_url,
            api_key=api_key,
            timeout_seconds=request.timeout_seconds or current_config.timeout_seconds,
        ).list_models()
    except LlmProviderError:
        # Model discovery is optional: users can still enter a model identifier manually.
        return {"items": [], "error": "Could not load models. You can enter a model identifier manually."}

    return {"items": items, "error": None}


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
