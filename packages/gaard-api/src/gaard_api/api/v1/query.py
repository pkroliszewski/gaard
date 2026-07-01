import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import sqlglot
from sqlglot import Dialects, expressions as exp

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
    QueryResult,
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
    format_table_for_prompt,
    get_active_business_logic_prompt_safe,
    get_datasource_schema_contexts_safe,
    get_llm_runtime_config_safe,
    get_query_runtime_config_safe,
    json_loads,
    learn_business_logic_from_sql_error,
    LlmRuntimeConfig,
    QueryRuntimeConfig,
    record_data_query_access_error_audit,
    record_data_query_audit,
    record_data_query_pipeline_error_audit,
    record_data_query_sql_error_audit,
    selected_schema_from_cache,
)
from gaard_api.core.settings import settings
from gaard_api.extensions import get_connector_registry

router = APIRouter()

DatasourceContext = tuple[DatasourceConnector, DatasourceSchemaCache]
DatasourceContexts = list[DatasourceContext]
INFORMATION_SCHEMA_ROUTE_KEY = "__information_schema__"


@dataclass(frozen=True)
class SqlDialectPlan:
    prompt_dialect: str
    sqlglot_read_dialect: str | None

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
            datasource_context = get_datasource_schema_contexts_safe()

        if datasource_context is not None:
            datasource_contexts = normalize_datasource_contexts(datasource_context)
            if datasource_contexts:
                formatted_schema = format_connected_datasource_schemas(datasource_contexts)
                dialect_plan = dialect_plan or resolve_sql_dialect_plan(datasource_contexts)
                print("LlmSqlGenerator",formatted_schema)
                print("dialect",dialect_plan.prompt_dialect)
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
) -> QueryPipeline:
    if datasource_context is None:
        datasource_context = get_datasource_schema_contexts_safe()

    runtime_config = get_query_runtime_config_safe()
    datasource_contexts = normalize_datasource_contexts(datasource_context)
    dialect_plan = resolve_sql_dialect_plan(datasource_contexts)
    executor = create_datasource_executor(datasource_contexts, runtime_config, dialect_plan)
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
    if datasource_context is None:
        return []

    if isinstance(datasource_context, tuple):
        return [datasource_context]

    return list(datasource_context)


def resolve_sql_dialect_plan(datasource_contexts: DatasourceContexts) -> SqlDialectPlan:
    if not datasource_contexts:
        return SqlDialectPlan(
            prompt_dialect=settings.gaard_sql_dialect,
            sqlglot_read_dialect=sqlglot_read_dialect(settings.gaard_sql_dialect),
        )

    dialects = [
        connector.sql_dialect
        for connector, _cache in datasource_contexts
    ]
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


def sqlglot_read_dialect(dialect: str) -> str | None:
    return dialect if is_sqlglot_dialect(dialect) else None


def is_sqlglot_dialect(dialect: str) -> bool:
    return dialect in {item.value for item in Dialects if item.value}


def create_datasource_executor(
    datasource_contexts: DatasourceContexts,
    runtime_config: QueryRuntimeConfig,
    dialect_plan: SqlDialectPlan,
):
    if datasource_contexts:
        return RoutingDatasourceExecutor(
            datasource_contexts,
            runtime_config.query_max_rows,
            dialect_plan,
        )

    raise ConfigurationError(
        "No active data sources are selected. Query execution requires at least one "
        "active datasource."
    )


class RoutingDatasourceExecutor:
    def __init__(
        self,
        datasource_contexts: DatasourceContexts,
        max_rows: int,
        dialect_plan: SqlDialectPlan,
    ) -> None:
        self.datasource_contexts = datasource_contexts
        self.dialect_plan = dialect_plan
        self.max_rows = max_rows
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
            connector.connector_key: get_connector_registry()
            .get(connector.database_type)
            .executor_factory(connector.database_url, max_rows)
            for connector, _cache in datasource_contexts
        }

    def execute(self, sql: str) -> QueryResult:
        datasource_key, cleaned_sql = self._route_and_clean_sql(sql)
        if not datasource_key:
            return self._execute_tableless_sql(cleaned_sql)
        if datasource_key == INFORMATION_SCHEMA_ROUTE_KEY:
            return self._execute_information_schema_sql(
                cleaned_sql,
                information_schema_datasource_keys(sql, self.datasource_contexts, self.dialect_plan),
            )

        return self.executors_by_key[datasource_key].execute(cleaned_sql)

    def _execute_tableless_sql(self, sql: str) -> QueryResult:
        metadata_definition = get_connector_registry().detect_from_database_url(
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
                schema = selected_schema_from_cache(cache)
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
            columns = list(rows[0].keys()) if rows else [item[0] for item in cursor.description or []]
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
        metadata_definition = get_connector_registry().detect_from_database_url(
            settings.gaard_metadata_database_url
        )
        metadata_dialect = metadata_definition.default_sql_dialect

        if self.dialect_plan.sqlglot_read_dialect and is_sqlglot_dialect(metadata_dialect):
            return expression.sql(dialect=metadata_dialect)

        return expression.sql()


def detect_datasource_ids_from_sql(
    sql: str,
    datasource_contexts: DatasourceContexts,
    dialect_plan: SqlDialectPlan,
) -> list[str]:
    if not sql.strip() or not datasource_contexts:
        return []

    contexts_by_key = {
        connector.connector_key: (connector, cache)
        for connector, cache in datasource_contexts
    }
    table_names_by_key = {
        connector.connector_key: {
            table.name for table in selected_schema_from_cache(cache).tables
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


def is_tableless_sql(sql: str, dialect_plan: SqlDialectPlan) -> bool:
    if not sql.strip():
        return False

    try:
        expression = sqlglot.parse_one(sql, read=dialect_plan.sqlglot_read_dialect)
    except Exception:
        return False

    return not any(expression.find_all(exp.Table))


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
        if datasource_key and datasource_key in active_keys and datasource_key not in datasource_keys:
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


def format_connected_datasource_schemas(datasource_contexts: DatasourceContexts) -> str:
    sections: list[str] = [
        "Use datasource-qualified table names in SQL: datasource_key.table_name.",
        "Cross-datasource queries are not supported. Use tables from exactly one datasource.",
        (
            "For schema metadata questions, prefer the provided schema text. If SQL is needed, "
            "use datasource_key.information_schema.columns or datasource_key.information_schema.tables."
        ),
    ]

    for connector, cache in datasource_contexts:
        datasource_schema = format_datasource_schema_for_prompt(connector, cache)
        datasource_schema = append_business_logic_to_schema(datasource_schema, connector.id)
        sections.append(
            f"Datasource: {connector.connector_key}\n"
            f"Dialect: {connector.sql_dialect}\n"
            f"{datasource_schema}"
        )

    return "\n\n".join(sections)


def format_datasource_schema_for_prompt(
    connector: DatasourceConnector,
    cache: DatasourceSchemaCache,
) -> str:
    schema = selected_schema_from_cache(cache)
    table_settings = json_loads(cache.table_settings_json).get("tables", {})
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
        sections.append(format_table_for_prompt(prefixed_table, table_settings.get(table.name, {})))

    if not sections:
        return "No tables or views available."

    return "\n\n".join(sections)


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
) -> QueryResponse:
    extra_metadata = extra_metadata or {}
    datasource_contexts = normalize_datasource_contexts(datasource_context)
    dialect_plan = resolve_sql_dialect_plan(datasource_contexts)
    extra_metadata = {
        **extra_metadata,
        "llm_sql_language": dialect_plan.prompt_dialect,
    }
    learning_connector_id = (
        datasource_contexts[0][0].id if len(datasource_contexts) == 1 else None
    )
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
    pipeline = create_pipeline(datasource_context, interpret=effective_request.interpret)
    try:
        response = pipeline.handle(effective_request)
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
) -> tuple[QueryRequest, DatasourceContexts]:
    datasource_ids = resolve_requested_datasource_ids(request)
    datasource_contexts = get_datasource_schema_contexts_safe(datasource_ids)
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

    return effective_request, datasource_contexts


def resolve_requested_datasource_ids(request: QueryRequest) -> list[str]:
    if request.datasource_ids:
        return request.datasource_ids

    if request.datasource_id and request.datasource_id != "default":
        return [request.datasource_id]

    return []


def ndjson_line(payload: dict[str, Any]) -> str:
    return f"{json.dumps(payload, ensure_ascii=False)}\n"


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    effective_request, datasource_context = effective_query_request(request)
    active_datasource_ids = [
        connector.connector_key for connector, _cache in datasource_context
    ]
    if not active_datasource_ids:
        return build_no_active_datasources_response(effective_request)

    return run_sql_request(
        effective_request,
        datasource_context,
        {"active_datasource_ids": active_datasource_ids} if active_datasource_ids else None,
    )


@router.post("/query/stream")
def query_stream(request: QueryRequest) -> StreamingResponse:
    effective_request, datasource_context = effective_query_request(request)
    active_datasource_ids = [
        connector.connector_key for connector, _cache in datasource_context
    ]
    extra_metadata = (
        {"active_datasource_ids": active_datasource_ids} if active_datasource_ids else None
    )

    def single_response() -> Iterator[str]:
        if not active_datasource_ids:
            yield ndjson_line(
                {"final": build_no_active_datasources_response(effective_request).model_dump(
                    mode="json"
                )}
            )
            return

        yield ndjson_line(
            {
                "final": run_sql_request(
                    effective_request,
                    datasource_context,
                    extra_metadata,
                ).model_dump(mode="json")
            }
        )

    return StreamingResponse(
        single_response(),
        media_type="application/x-ndjson",
    )
