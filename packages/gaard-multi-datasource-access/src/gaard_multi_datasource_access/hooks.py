from __future__ import annotations

import sqlite3
from typing import Any

import sqlglot
from sqlglot import expressions as exp

from gaard_core.errors import QueryExecutionError
from gaard_core.query_pipeline.models import QueryRequest, QueryResult
from gaard_plugin_api import ExtensionContext

from gaard_api.admin.models import DatasourceConnector, DatasourceSchemaCache
from gaard_api.admin.services import format_table_for_prompt
from gaard_api.core.settings import settings
from gaard_api.query_hooks import (
    DatasourceContext,
    DatasourceContexts,
    EffectiveQueryContext,
    QueryHookRegistry,
    SqlDialectPlan,
    is_sqlglot_dialect,
    sqlglot_read_dialect,
)


INFORMATION_SCHEMA_ROUTE_KEY = "__information_schema__"


def register(context: ExtensionContext) -> None:
    if not isinstance(context.registry, QueryHookRegistry):
        raise TypeError("multi-datasource-access requires a QueryHookRegistry.")

    context.registry.register(WideQueryHook(context.services))


class WideQueryHook:
    def __init__(self, services: dict[str, Any]) -> None:
        self.services = services

    def resolve_effective_query_context(
        self,
        request: QueryRequest,
    ) -> EffectiveQueryContext | None:
        datasource_ids = resolve_requested_datasource_ids(request)
        datasource_contexts = self._datasource_contexts(datasource_ids)
        resolved_datasource_ids = [
            connector.connector_key for connector, _cache in datasource_contexts
        ]
        missing_datasource_ids = [
            datasource_id
            for datasource_id in datasource_ids
            if datasource_id not in resolved_datasource_ids
        ]

        if missing_datasource_ids:
            raise QueryExecutionError(
                "Requested datasource was not found.",
                error_detail=(
                    "Unknown datasource ids: "
                    f"{', '.join(missing_datasource_ids)}."
                ),
            )

        effective_request = request
        if datasource_contexts:
            effective_request = request.model_copy(
                update={
                    "datasource_id": ",".join(resolved_datasource_ids),
                    "datasource_ids": resolved_datasource_ids,
                }
            )

        return EffectiveQueryContext(effective_request, datasource_contexts)

    def format_datasource_schemas(self, datasource_contexts: DatasourceContexts) -> str | None:
        if not datasource_contexts:
            return None

        sections: list[str] = [
            "Use datasource-qualified table names in SQL: datasource_key.table_name.",
            "Cross-datasource queries are not supported. Use tables from exactly one datasource.",
            (
                "For schema metadata questions, prefer the provided schema text. If SQL is needed, "
                "use datasource_key.information_schema.columns or "
                "datasource_key.information_schema.tables. "
                "For information_schema never use table_schema."
            ),
        ]

        for connector, cache in datasource_contexts:
            datasource_schema = self._format_datasource_schema_for_prompt(connector, cache)
            datasource_schema = self._append_business_logic_to_schema(
                datasource_schema,
                connector.id,
            )
            sections.append(
                f"Datasource: {connector.connector_key}\n"
                f"Dialect: {connector.sql_dialect}\n"
                f"{datasource_schema}"
            )

        return "\n\n".join(sections)

    def resolve_sql_dialect_plan(
        self,
        datasource_contexts: DatasourceContexts,
    ) -> SqlDialectPlan | None:
        if not datasource_contexts:
            return SqlDialectPlan(
                prompt_dialect=settings.gaard_sql_dialect,
                sqlglot_read_dialect=sqlglot_read_dialect(settings.gaard_sql_dialect),
            )

        dialects = [connector.sql_dialect for connector, _cache in datasource_contexts]
        unique_dialects = list(dict.fromkeys(dialects))

        if len(unique_dialects) == 1:
            dialect = unique_dialects[0]
            return SqlDialectPlan(
                prompt_dialect=dialect,
                sqlglot_read_dialect=sqlglot_read_dialect(dialect),
            )

        if all(is_sqlglot_dialect(dialect) for dialect in unique_dialects):
            dialect = unique_dialects[0]
            return SqlDialectPlan(
                prompt_dialect=dialect,
                sqlglot_read_dialect=dialect,
            )

        return SqlDialectPlan(prompt_dialect="sql", sqlglot_read_dialect=None)

    def create_datasource_executor(
        self,
        datasource_contexts: DatasourceContexts,
        max_rows: int,
        dialect_plan: SqlDialectPlan,
    ) -> RoutingDatasourceExecutor | None:
        if not datasource_contexts:
            return None

        return RoutingDatasourceExecutor(
            datasource_contexts=datasource_contexts,
            max_rows=max_rows,
            dialect_plan=dialect_plan,
            connector_registry=self._connector_registry(),
            selected_schema_from_cache=self._selected_schema_from_cache,
        )

    def detect_datasource_ids_from_sql(
        self,
        sql: str,
        datasource_contexts: DatasourceContexts,
        dialect_plan: SqlDialectPlan,
    ) -> list[str] | None:
        if not sql.strip() or not datasource_contexts:
            return []

        contexts_by_key = {
            connector.connector_key: (connector, cache)
            for connector, cache in datasource_contexts
        }
        table_names_by_key = {
            connector.connector_key: {
                table.name for table in self._selected_schema_from_cache(cache).tables
            }
            for connector, cache in datasource_contexts
        }

        try:
            expression = sqlglot.parse_one(sql, read=dialect_plan.sqlglot_read_dialect)
        except Exception:
            return []

        datasource_ids: list[str] = []
        for table in expression.find_all(exp.Table):
            try:
                datasource_id = resolve_table_datasource(
                    table,
                    sql,
                    contexts_by_key,
                    table_names_by_key,
                    datasource_contexts,
                )
            except QueryExecutionError:
                continue

            if datasource_id is not None and datasource_id not in datasource_ids:
                datasource_ids.append(datasource_id)

        return datasource_ids

    def is_tableless_sql(self, sql: str, dialect_plan: SqlDialectPlan) -> bool | None:
        if not sql.strip():
            return False

        try:
            expression = sqlglot.parse_one(sql, read=dialect_plan.sqlglot_read_dialect)
        except Exception:
            return False

        return not any(expression.find_all(exp.Table))

    def set_active_datasource_connector(
        self,
        session,
        connector: DatasourceConnector,
        actor: str,
    ) -> bool | None:
        if connector.connector_key == "metadata-db":
            connector.active = False
            return True

        if connector.connector_key != "default":
            for item in self._list_datasource_connectors(session):
                if item.connector_key == "default":
                    item.active = False

        connector.active = True
        connector.updated_by = actor
        return True

    def _datasource_contexts(self, datasource_ids: list[str]) -> DatasourceContexts:
        resolver = self.services["datasource_contexts"]
        return resolver(datasource_ids or None)

    def _connector_registry(self):
        return self.services["connector_registry"]()

    def _selected_schema_from_cache(self, cache: DatasourceSchemaCache):
        return self.services["selected_schema_from_cache"](cache)

    def _json_loads(self, value: str):
        return self.services["json_loads"](value)

    def _active_business_logic_prompt(self, connector_id: int) -> str:
        return self.services["active_business_logic_prompt"](connector_id)

    def _list_datasource_connectors(self, session):
        return self.services["list_datasource_connectors"](session)

    def _format_datasource_schema_for_prompt(
        self,
        connector: DatasourceConnector,
        cache: DatasourceSchemaCache,
    ) -> str:
        schema = self._selected_schema_from_cache(cache)
        table_settings = self._json_loads(cache.table_settings_json).get("tables", {})
        sections: list[str] = []

        for table in sorted(schema.tables, key=lambda item: item.name):
            prefixed_table = table.model_copy(
                update={
                    "name": f"{connector.connector_key}.{table.name}",
                    "foreign_keys": [
                        foreign_key.model_copy(
                            update={
                                "referred_table": (
                                    f"{connector.connector_key}.{foreign_key.referred_table}"
                                )
                            }
                        )
                        for foreign_key in table.foreign_keys
                    ],
                }
            )
            sections.append(
                format_table_for_prompt(prefixed_table, table_settings.get(table.name, {}))
            )

        if not sections:
            return "No tables or views available."

        return "\n\n".join(sections)

    def _append_business_logic_to_schema(self, formatted_schema: str, connector_id: int) -> str:
        business_logic = self._active_business_logic_prompt(connector_id)
        if not business_logic:
            return formatted_schema

        return f"{formatted_schema}\n\n{business_logic}"


class RoutingDatasourceExecutor:
    def __init__(
        self,
        datasource_contexts: DatasourceContexts,
        max_rows: int,
        dialect_plan: SqlDialectPlan,
        connector_registry,
        selected_schema_from_cache,
    ) -> None:
        self.datasource_contexts = datasource_contexts
        self.dialect_plan = dialect_plan
        self.max_rows = max_rows
        self.connector_registry = connector_registry
        self.selected_schema_from_cache = selected_schema_from_cache
        self.contexts_by_key = {
            connector.connector_key: (connector, cache)
            for connector, cache in datasource_contexts
        }
        self.table_names_by_key = {
            connector.connector_key: {
                table.name for table in selected_schema_from_cache(cache).tables
            }
            for connector, cache in datasource_contexts
        }
        self.executors_by_key = {
            connector.connector_key: connector_registry.get(
                connector.database_type
            ).executor_factory(connector.database_url, max_rows)
            for connector, _cache in datasource_contexts
        }

    def execute(self, sql: str) -> QueryResult:
        datasource_key, cleaned_sql = self._route_and_clean_sql(sql)
        if not datasource_key:
            return self._execute_tableless_sql(cleaned_sql)
        if datasource_key == INFORMATION_SCHEMA_ROUTE_KEY:
            return self._execute_information_schema_sql(
                cleaned_sql,
                information_schema_datasource_keys(
                    sql,
                    self.datasource_contexts,
                    self.dialect_plan,
                ),
            )

        return self.executors_by_key[datasource_key].execute(cleaned_sql)

    def _execute_tableless_sql(self, sql: str) -> QueryResult:
        metadata_definition = self.connector_registry.detect_from_database_url(
            settings.gaard_metadata_database_url
        )
        executor = metadata_definition.executor_factory(
            settings.gaard_metadata_database_url,
            self.max_rows,
        )
        return executor.execute(sql)

    def _execute_information_schema_sql(
        self,
        sql: str,
        datasource_keys: list[str],
    ) -> QueryResult:
        selected_keys = set(datasource_keys) if datasource_keys else set(self.contexts_by_key)
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                """
                CREATE TABLE columns (
                    table_catalog TEXT,
                    table_schema TEXT,
                    table_name TEXT,
                    column_name TEXT,
                    ordinal_position INTEGER,
                    data_type TEXT,
                    is_nullable TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE tables (
                    table_catalog TEXT,
                    table_schema TEXT,
                    table_name TEXT,
                    table_type TEXT
                )
                """
            )

            for connector, cache in self.datasource_contexts:
                if connector.connector_key not in selected_keys:
                    continue
                schema = self.selected_schema_from_cache(cache)
                for table in schema.tables:
                    connection.execute(
                        "INSERT INTO tables VALUES (?, ?, ?, ?)",
                        (
                            connector.connector_key,
                            "main",
                            table.name,
                            "BASE TABLE",
                        ),
                    )
                    for position, column in enumerate(table.columns, start=1):
                        connection.execute(
                            "INSERT INTO columns VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                connector.connector_key,
                                "main",
                                table.name,
                                column.name,
                                position,
                                column.type,
                                "NO" if column.primary_key else "YES",
                            ),
                        )

            cursor = connection.execute(sql)
            rows = [dict(row) for row in cursor.fetchall()]
            columns = list(rows[0].keys()) if rows else [
                item[0] for item in cursor.description or []
            ]
            return QueryResult(columns=columns, rows=rows)
        finally:
            connection.close()

    def _route_and_clean_sql(self, sql: str) -> tuple[str, str]:
        try:
            expression = sqlglot.parse_one(sql, read=self.dialect_plan.sqlglot_read_dialect)
        except Exception as exc:
            raise QueryExecutionError(
                f"Could not route SQL to a datasource. SQL: {sql}. Error: {exc}",
                sql=sql,
                error_detail=str(exc),
            ) from exc

        tables = list(expression.find_all(exp.Table))

        if tables and all(is_information_schema_table(table) for table in tables):
            for table in tables:
                table.set("db", None)
                table.set("catalog", None)
            return INFORMATION_SCHEMA_ROUTE_KEY, expression.sql(dialect="sqlite")
        if any(is_information_schema_table(table) for table in tables):
            raise QueryExecutionError(
                "Generated SQL cannot mix information_schema with datasource tables.",
                sql=sql,
                error_detail=(
                    "information_schema is served from schema metadata and cannot be joined "
                    "with live datasource tables."
                ),
            )

        datasource_keys: set[str] = set()
        table_count = 0

        for table in tables:
            table_count += 1
            datasource_key = resolve_table_datasource(
                table,
                sql,
                self.contexts_by_key,
                self.table_names_by_key,
                self.datasource_contexts,
            )
            if datasource_key is not None:
                datasource_keys.add(datasource_key)
                table.set("db", None)
                table.set("catalog", None)

        if len(datasource_keys) > 1:
            raise QueryExecutionError(
                "Cross-datasource SQL is not supported yet.",
                sql=sql,
                error_detail=(
                    "The generated SQL references multiple datasources: "
                    f"{', '.join(sorted(datasource_keys))}."
                ),
            )

        if datasource_keys:
            datasource_key = next(iter(datasource_keys))
        elif table_count == 0:
            return "", self._tableless_sql(expression)
        elif len(self.datasource_contexts) == 1:
            datasource_key = self.datasource_contexts[0][0].connector_key
        else:
            raise QueryExecutionError(
                "Generated SQL must qualify tables with a datasource.",
                sql=sql,
                error_detail="No datasource-qualified table reference was found.",
            )

        connector = self.contexts_by_key[datasource_key][0]
        if self.dialect_plan.sqlglot_read_dialect and is_sqlglot_dialect(connector.sql_dialect):
            return datasource_key, expression.sql(dialect=connector.sql_dialect)

        return datasource_key, expression.sql()

    def _tableless_sql(self, expression: exp.Expression) -> str:
        metadata_definition = self.connector_registry.detect_from_database_url(
            settings.gaard_metadata_database_url
        )
        metadata_dialect = metadata_definition.default_sql_dialect

        if self.dialect_plan.sqlglot_read_dialect and is_sqlglot_dialect(metadata_dialect):
            return expression.sql(dialect=metadata_dialect)

        return expression.sql()


def resolve_requested_datasource_ids(request: QueryRequest) -> list[str]:
    if request.datasource_ids:
        return request.datasource_ids

    if request.datasource_id and request.datasource_id != "default":
        return [request.datasource_id]

    return []


def is_information_schema_table(table: exp.Table) -> bool:
    return str(table.db or "").lower() == "information_schema"


def explicit_datasource_key_for_table(table: exp.Table) -> str:
    if is_information_schema_table(table) and table.catalog:
        return str(table.catalog)

    return str(table.db or table.catalog or "")


def information_schema_datasource_keys(
    sql: str,
    datasource_contexts: DatasourceContexts,
    dialect_plan: SqlDialectPlan | None = None,
) -> list[str]:
    read_dialect = dialect_plan.sqlglot_read_dialect if dialect_plan else None
    try:
        expression = sqlglot.parse_one(sql, read=read_dialect)
    except Exception:
        return []

    active_keys = {connector.connector_key for connector, _cache in datasource_contexts}
    datasource_keys: list[str] = []
    for table in expression.find_all(exp.Table):
        if not is_information_schema_table(table):
            continue
        datasource_key = explicit_datasource_key_for_table(table)
        if (
            datasource_key
            and datasource_key in active_keys
            and datasource_key not in datasource_keys
        ):
            datasource_keys.append(datasource_key)

    return datasource_keys


def resolve_table_datasource(
    table: exp.Table,
    sql: str,
    contexts_by_key: dict[str, DatasourceContext],
    table_names_by_key: dict[str, set[str]],
    datasource_contexts: DatasourceContexts,
) -> str | None:
    explicit_key = explicit_datasource_key_for_table(table)

    if explicit_key:
        if explicit_key not in contexts_by_key:
            raise QueryExecutionError(
                f"Unknown datasource {explicit_key!r} in generated SQL.",
                sql=sql,
                error_detail=f"Datasource {explicit_key!r} is not active.",
            )

        return explicit_key

    matching_keys = [
        datasource_key
        for datasource_key, table_names in table_names_by_key.items()
        if table.name in table_names
    ]

    if len(matching_keys) == 1:
        return matching_keys[0]

    if len(datasource_contexts) == 1:
        return datasource_contexts[0][0].connector_key

    raise QueryExecutionError(
        "Generated SQL must qualify tables with a datasource.",
        sql=sql,
        error_detail=f"Table {table.name!r} is ambiguous or unavailable.",
    )
