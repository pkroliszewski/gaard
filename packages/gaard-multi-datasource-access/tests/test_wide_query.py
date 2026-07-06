from __future__ import annotations

from gaard_core.query_pipeline.models import QueryRequest, QueryResult
from gaard_plugin_api import ExtensionContext

from gaard_api.admin.models import DatasourceConnector, DatasourceSchemaCache
from gaard_api.admin.services import json_loads, selected_schema_from_cache
from gaard_api.api_registry import ApiRegistry
from gaard_api.query_hooks import EffectiveQueryContext

from gaard_multi_datasource_access.api import register as register_api
from gaard_multi_datasource_access.hooks import RoutingDatasourceExecutor, WideQueryHook
from gaard_multi_datasource_access.plugin import extension


def connector(
    connector_id: int,
    connector_key: str,
    database_type: str,
    database_url: str,
    sql_dialect: str,
) -> DatasourceConnector:
    return DatasourceConnector(
        id=connector_id,
        connector_key=connector_key,
        name=connector_key,
        database_type=database_type,
        database_url=database_url,
        sql_dialect=sql_dialect,
        active=True,
    )


def cache(connector_id: int, table_name: str = "orders") -> DatasourceSchemaCache:
    return DatasourceSchemaCache(
        connector_id=connector_id,
        schema_json=(
            '{"tables":[{"name":"'
            + table_name
            + '","columns":[{"name":"name","type":"text"}]}]}'
        ),
        table_settings_json='{"tables":{}}',
    )


def hook_for_contexts(contexts):
    return WideQueryHook(
        {
            "datasource_contexts": lambda datasource_ids=None: [
                context
                for context in contexts
                if not datasource_ids or context[0].connector_key in datasource_ids
            ],
            "connector_registry": lambda: FakeRegistry(),
            "selected_schema_from_cache": selected_schema_from_cache,
            "json_loads": json_loads,
            "active_business_logic_prompt": lambda connector_id: "",
            "list_datasource_connectors": lambda session: [context[0] for context in contexts],
        }
    )


def test_extension_manifest_registers_query_and_api_contributions() -> None:
    manifest = extension()

    assert manifest.id == "datasource-access"
    assert manifest.contributions == {
        "api": "gaard_multi_datasource_access.api:register",
        "query": "gaard_multi_datasource_access.hooks:register",
    }


def test_api_contribution_registers_datasource_access_extension_section() -> None:
    registry = ApiRegistry()
    register_api(
        ExtensionContext(
            extension_id="datasource-access",
            capability="api",
            registry=registry,
        )
    )

    sections = registry.list_admin_sections()

    assert len(sections) == 1
    assert sections[0].label == "Multi Datasource Access"
    assert sections[0].section_key == "datasource-access"
    assert "multiple active datasources" in sections[0].description


class FakeExecutor:
    def __init__(self, executed_sql: list[str]) -> None:
        self.executed_sql = executed_sql

    def execute(self, sql: str) -> QueryResult:
        self.executed_sql.append(sql)
        return QueryResult(columns=["ok"], rows=[{"ok": True}])


class FakeDefinition:
    def __init__(self, executed_sql: list[str] | None = None) -> None:
        self.executed_sql = executed_sql if executed_sql is not None else []
        self.default_sql_dialect = "sqlite"

    def executor_factory(self, database_url: str, max_rows: int) -> FakeExecutor:
        return FakeExecutor(self.executed_sql)


class FakeRegistry:
    def __init__(self, executed_sql: list[str] | None = None) -> None:
        self.executed_sql = executed_sql if executed_sql is not None else []

    def get(self, type_key: str) -> FakeDefinition:
        return FakeDefinition(self.executed_sql)

    def detect_from_database_url(self, database_url: str) -> FakeDefinition:
        return FakeDefinition(self.executed_sql)


def test_hook_selects_multiple_requested_datasources() -> None:
    contexts = [
        (
            connector(1, "con_a", "sqlite", "sqlite:///a.db", "sqlite"),
            cache(1, "patients"),
        ),
        (
            connector(2, "con_b", "sqlite", "sqlite:///b.db", "sqlite"),
            cache(2, "orders"),
        ),
    ]

    result = hook_for_contexts(contexts).resolve_effective_query_context(
        QueryRequest(
            question="How many orders?",
            datasource_ids=["con_a", "con_b"],
        )
    )

    assert isinstance(result, EffectiveQueryContext)
    assert result.request.datasource_id == "con_a,con_b"
    assert result.request.datasource_ids == ["con_a", "con_b"]
    assert [context[0].connector_key for context in result.datasource_contexts] == [
        "con_a",
        "con_b",
    ]


def test_hook_formats_datasource_qualified_schema() -> None:
    contexts = [
        (
            connector(1, "con_a", "sqlite", "sqlite:///a.db", "sqlite"),
            cache(1, "patients"),
        ),
        (
            connector(2, "con_b", "sqlite", "sqlite:///b.db", "sqlite"),
            cache(2, "orders"),
        ),
    ]

    formatted = hook_for_contexts(contexts).format_datasource_schemas(contexts)

    assert "Datasource: con_a" in formatted
    assert "Table: con_a.patients" in formatted
    assert "Datasource: con_b" in formatted
    assert "Table: con_b.orders" in formatted


def test_hook_activation_allows_multiple_active_datasources() -> None:
    first = connector(1, "con_a", "sqlite", "sqlite:///a.db", "sqlite")
    second = connector(2, "con_b", "sqlite", "sqlite:///b.db", "sqlite")
    contexts = [(first, cache(1)), (second, cache(2))]
    hook = hook_for_contexts(contexts)

    hook.set_active_datasource_connector(object(), first, "admin")
    hook.set_active_datasource_connector(object(), second, "admin")

    assert first.active is True
    assert second.active is True
    assert first.updated_by == "admin"
    assert second.updated_by == "admin"


def test_sql_dialect_plan_uses_shared_active_dialect() -> None:
    contexts = [
        (
            connector(1, "mysql_a", "mysql", "mysql://user:pass@example.test/a", "mysql"),
            cache(1),
        ),
        (
            connector(2, "mysql_b", "mysql", "mysql://user:pass@example.test/b", "mysql"),
            cache(2),
        ),
    ]

    plan = hook_for_contexts(contexts).resolve_sql_dialect_plan(contexts)

    assert plan.prompt_dialect == "mysql"
    assert plan.sqlglot_read_dialect == "mysql"


def test_sql_dialect_plan_chooses_first_supported_dialect_for_transpilation() -> None:
    contexts = [
        (
            connector(1, "pg", "postgresql", "postgresql://user:pass@example.test/db", "postgres"),
            cache(1),
        ),
        (
            connector(2, "sqlite_db", "sqlite", "sqlite:///example.db", "sqlite"),
            cache(2),
        ),
    ]

    plan = hook_for_contexts(contexts).resolve_sql_dialect_plan(contexts)

    assert plan.prompt_dialect == "postgres"
    assert plan.sqlglot_read_dialect == "postgres"


def test_sql_dialect_plan_uses_generic_sql_when_any_dialect_is_unsupported() -> None:
    contexts = [
        (
            connector(1, "pg", "postgresql", "postgresql://user:pass@example.test/db", "postgres"),
            cache(1),
        ),
        (
            connector(2, "custom", "custom", "custom://example", "vendor_sql"),
            cache(2),
        ),
    ]

    plan = hook_for_contexts(contexts).resolve_sql_dialect_plan(contexts)

    assert plan.prompt_dialect == "sql"
    assert plan.sqlglot_read_dialect is None


def test_routing_executor_transpiles_from_generation_dialect_to_target() -> None:
    executed_sql: list[str] = []
    contexts = [
        (
            connector(1, "pg", "postgresql", "postgresql://user:pass@example.test/db", "postgres"),
            cache(1),
        ),
        (
            connector(2, "sqlite_db", "sqlite", "sqlite:///example.db", "sqlite"),
            cache(2),
        ),
    ]
    hook = hook_for_contexts(contexts)
    plan = hook.resolve_sql_dialect_plan(contexts)
    executor = RoutingDatasourceExecutor(
        contexts,
        max_rows=100,
        dialect_plan=plan,
        connector_registry=FakeRegistry(executed_sql),
        selected_schema_from_cache=selected_schema_from_cache,
    )

    result = executor.execute("SELECT * FROM sqlite_db.orders WHERE name ILIKE '%a%'")

    assert result.rows == [{"ok": True}]
    assert executed_sql == [
        "SELECT * FROM orders WHERE LOWER(name) LIKE LOWER('%a%')",
    ]
