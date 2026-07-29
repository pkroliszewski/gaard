from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, cast

from gaard_core.errors import ConfigurationError, QueryExecutionError
from gaard_core.query_pipeline.models import QueryRequest, QueryResult
from sqlalchemy.orm import Session
from sqlglot import Dialects

from gaard_api.admin.models import DatasourceConnector, DatasourceSchemaCache
from gaard_api.auth_dependencies import identity_id_for_principal
from gaard_api.core.settings import settings

DatasourceContext = tuple[DatasourceConnector, DatasourceSchemaCache]
DatasourceContexts = list[DatasourceContext]


@dataclass(frozen=True)
class SqlDialectPlan:
    prompt_dialect: str
    sqlglot_read_dialect: str | None


@dataclass(frozen=True)
class EffectiveQueryContext:
    request: QueryRequest
    datasource_contexts: DatasourceContexts


class QueryExecutor(Protocol):
    def execute(self, sql: str) -> QueryResult:
        ...


class QueryBehaviorHook(Protocol):
    def is_enabled(self) -> bool:
        return True

    def filter_datasource_keys(
        self,
        identity_id: str | None,
        datasource_keys: list[str],
    ) -> list[str] | None:
        return None

    def filter_table_names(
        self,
        identity_id: str | None,
        datasource_key: str,
        table_names: list[str],
    ) -> list[str] | None:
        return None

    def resolve_effective_query_context(
        self,
        request: QueryRequest,
    ) -> EffectiveQueryContext | None:
        return None

    def format_datasource_schemas(self, datasource_contexts: DatasourceContexts) -> str | None:
        return None

    def resolve_sql_dialect_plan(
        self,
        datasource_contexts: DatasourceContexts,
    ) -> SqlDialectPlan | None:
        return None

    def create_datasource_executor(
        self,
        datasource_contexts: DatasourceContexts,
        max_rows: int,
        dialect_plan: SqlDialectPlan,
    ) -> QueryExecutor | None:
        return None

    def detect_datasource_ids_from_sql(
        self,
        sql: str,
        datasource_contexts: DatasourceContexts,
        dialect_plan: SqlDialectPlan,
    ) -> list[str] | None:
        return None

    def is_tableless_sql(self, sql: str, dialect_plan: SqlDialectPlan) -> bool | None:
        return None

    def set_active_datasource_connector(
        self,
        session: Session,
        connector: DatasourceConnector,
        actor: str,
    ) -> bool | None:
        return None


HookResultT = TypeVar("HookResultT")


class QueryHookRegistry:
    """Ordered query behavior hooks supplied by installed extensions."""

    def __init__(self) -> None:
        self._hooks: list[QueryBehaviorHook] = []

    def register(self, hook: QueryBehaviorHook) -> None:
        self._hooks.append(hook)

    def resolve_effective_query_context(self, request: QueryRequest) -> EffectiveQueryContext:
        return self._first_result(
            "resolve_effective_query_context",
            request,
            default=lambda: default_effective_query_context(request),
        )

    def filter_datasource_contexts(
        self,
        principal: Any | None,
        datasource_contexts: DatasourceContexts,
    ) -> DatasourceContexts:
        identity_id = principal_identity_id(principal)
        contexts = datasource_contexts
        for hook in self._hooks:
            if not self._is_enabled(hook):
                continue
            method = getattr(hook, "filter_datasource_keys", None)
            if method is None:
                continue
            keys = [connector.connector_key for connector, _cache in contexts]
            result = method(identity_id, keys)
            if result is not None:
                allowed = set(result)
                contexts = [
                    context for context in contexts if context[0].connector_key in allowed
                ]

        filtered: DatasourceContexts = []
        for connector, cache in contexts:
            allowed_tables = selected_table_names(cache)
            for hook in self._hooks:
                if not self._is_enabled(hook):
                    continue
                method = getattr(hook, "filter_table_names", None)
                if method is None:
                    continue
                result = method(identity_id, connector.connector_key, allowed_tables)
                if result is not None:
                    allowed_tables = result

            selected_tables = set(selected_table_names(cache))
            allowed_set = set(allowed_tables)
            denied_tables = selected_tables - allowed_set
            if not denied_tables:
                filtered.append((connector, cache))
                continue
            safe_cache = cast(Any, copy.copy(cache))
            raw_schema = json.loads(cache.schema_json)
            raw_schema["tables"] = [
                table for table in raw_schema.get("tables", []) if table.get("name") in allowed_set
            ]
            safe_cache.schema_json = json.dumps(raw_schema)
            safe_cache.formatted_schema = ""
            safe_cache.access_denied_table_names = denied_tables
            filtered.append((connector, safe_cache))
        return filtered

    def format_datasource_schemas(self, datasource_contexts: DatasourceContexts) -> str:
        return self._first_result(
            "format_datasource_schemas",
            datasource_contexts,
            default=lambda: default_format_datasource_schemas(datasource_contexts),
        )

    def resolve_sql_dialect_plan(self, datasource_contexts: DatasourceContexts) -> SqlDialectPlan:
        return self._first_result(
            "resolve_sql_dialect_plan",
            datasource_contexts,
            default=lambda: default_sql_dialect_plan(datasource_contexts),
        )

    def create_datasource_executor(
        self,
        datasource_contexts: DatasourceContexts,
        max_rows: int,
        dialect_plan: SqlDialectPlan,
    ) -> QueryExecutor:
        return self._first_result(
            "create_datasource_executor",
            datasource_contexts,
            max_rows,
            dialect_plan,
            default=lambda: default_datasource_executor(datasource_contexts, max_rows),
        )

    def detect_datasource_ids_from_sql(
        self,
        sql: str,
        datasource_contexts: DatasourceContexts,
        dialect_plan: SqlDialectPlan,
    ) -> list[str]:
        return self._first_result(
            "detect_datasource_ids_from_sql",
            sql,
            datasource_contexts,
            dialect_plan,
            default=lambda: default_detect_datasource_ids_from_sql(sql, datasource_contexts),
        )

    def is_tableless_sql(self, sql: str, dialect_plan: SqlDialectPlan) -> bool:
        return self._first_result(
            "is_tableless_sql",
            sql,
            dialect_plan,
            default=lambda: False,
        )

    def set_active_datasource_connector(
        self,
        session: Session,
        connector: DatasourceConnector,
        actor: str,
    ) -> None:
        self._first_result(
            "set_active_datasource_connector",
            session,
            connector,
            actor,
            default=lambda: default_set_active_datasource_connector(
                session,
                connector,
                actor,
            ),
        )

    def _first_result(
        self,
        method_name: str,
        *args: object,
        default: Callable[[], HookResultT],
    ) -> HookResultT:
        for hook in self._hooks:
            if not self._is_enabled(hook):
                continue
            method = getattr(hook, method_name, None)
            if method is None:
                continue
            result = cast(object | None, method(*args))
            if result is not None:
                return cast(HookResultT, result)

        return default()

    @staticmethod
    def _is_enabled(hook: QueryBehaviorHook) -> bool:
        method = getattr(hook, "is_enabled", None)
        return method is None or bool(method())


def normalize_datasource_contexts(
    datasource_context: DatasourceContext | DatasourceContexts | None,
) -> DatasourceContexts:
    if datasource_context is None:
        return []

    if isinstance(datasource_context, tuple):
        return [datasource_context]

    return list(datasource_context)


def principal_identity_id(principal: Any | None) -> str | None:
    return identity_id_for_principal(principal)


def selected_table_names(cache: DatasourceSchemaCache) -> list[str]:
    from gaard_api.admin.services import selected_schema_from_cache

    return [table.name for table in selected_schema_from_cache(cache).tables]


def default_effective_query_context(request: QueryRequest) -> EffectiveQueryContext:
    from gaard_api.admin.services import (
        get_datasource_schema_context_safe,
        get_datasource_schema_contexts_safe,
    )

    datasource_id = first_requested_datasource_id(request)
    if datasource_id:
        contexts = get_datasource_schema_contexts_safe([datasource_id])
        if not contexts:
            raise QueryExecutionError(
                "Requested datasource was not found.",
                error_detail=f"Unknown datasource ids: {datasource_id}.",
            )
    else:
        context = get_datasource_schema_context_safe()
        contexts = [context] if context is not None else []

    if not contexts:
        return EffectiveQueryContext(request=request, datasource_contexts=[])

    connector_key = contexts[0][0].connector_key
    return EffectiveQueryContext(
        request=request.model_copy(
            update={"datasource_id": connector_key, "datasource_ids": [connector_key]}
        ),
        datasource_contexts=contexts,
    )


def first_requested_datasource_id(request: QueryRequest) -> str:
    if request.datasource_ids:
        return request.datasource_ids[0]

    if request.datasource_id and request.datasource_id != "default":
        return request.datasource_id

    return ""


def default_format_datasource_schemas(datasource_contexts: DatasourceContexts) -> str:
    from gaard_api.admin.services import get_active_business_logic_prompt_safe

    if not datasource_contexts:
        return "No tables or views available."

    connector, cache = datasource_contexts[0]
    formatted_schema = cache.formatted_schema or cache.schema_json
    business_logic = get_active_business_logic_prompt_safe(connector.id)
    if business_logic:
        return f"{formatted_schema}\n\n{business_logic}"

    return formatted_schema


def default_sql_dialect_plan(datasource_contexts: DatasourceContexts) -> SqlDialectPlan:
    dialect = (
        datasource_contexts[0][0].sql_dialect
        if datasource_contexts
        else settings.gaard_sql_dialect
    )
    return SqlDialectPlan(
        prompt_dialect=dialect,
        sqlglot_read_dialect=sqlglot_read_dialect(dialect),
    )


def sqlglot_read_dialect(dialect: str) -> str | None:
    return dialect if is_sqlglot_dialect(dialect) else None


def is_sqlglot_dialect(dialect: str) -> bool:
    return dialect in {item.value for item in Dialects if item.value}


def default_datasource_executor(
    datasource_contexts: DatasourceContexts,
    max_rows: int,
) -> QueryExecutor:
    if not datasource_contexts:
        raise ConfigurationError(
            "No active data sources are selected. Query execution requires one active datasource."
        )

    connector = datasource_contexts[0][0]

    from gaard_api.extensions import get_connector_registry

    return get_connector_registry().get(connector.database_type).executor_factory(
        connector.database_url,
        max_rows,
    )


def default_detect_datasource_ids_from_sql(
    sql: str,
    datasource_contexts: DatasourceContexts,
) -> list[str]:
    if not sql.strip() or not datasource_contexts:
        return []

    return [datasource_contexts[0][0].connector_key]


def default_set_active_datasource_connector(
    session: Session,
    connector: DatasourceConnector,
    actor: str,
) -> bool:
    from gaard_api.admin.services import (
        is_system_datasource_connector,
        list_datasource_connectors,
    )

    if is_system_datasource_connector(connector):
        connector.active = False
        return True

    for item in list_datasource_connectors(session):
        if item.id != connector.id:
            item.active = False

    connector.active = True
    connector.updated_by = actor
    return True
