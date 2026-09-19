from collections.abc import Mapping
from typing import Any, cast

import sqlglot
from gaard_core.errors import QueryExecutionError
from gaard_core.json_utils import to_jsonable
from gaard_core.query_pipeline.models import QueryResult
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlglot import exp
from sqlglot.dialects import Dialect
from sqlglot.errors import ErrorLevel, SqlglotError


class SQLAlchemyQueryExecutor:
    def __init__(self, database_url: str, max_rows: int = 100) -> None:
        self.database_url = database_url
        self.max_rows = max_rows
        self.engine: Engine = self._create_engine(database_url)

    def _create_engine(self, database_url: str) -> Engine:
        return create_engine(database_url)

    def execute(self, sql: str) -> QueryResult:
        limited_sql = self._apply_limit(sql)

        try:
            with self.engine.connect() as connection:
                result = connection.execute(text(limited_sql))
                rows = result.mappings().fetchmany(self.max_rows)
        except SQLAlchemyError as exc:
            raise QueryExecutionError(
                f"Query execution failed. SQL: {limited_sql}. Error: {exc}",
                sql=limited_sql,
                error_detail=str(exc),
            ) from exc

        normalized_rows = [self._normalize_row(row) for row in rows]
        columns = list(normalized_rows[0].keys()) if normalized_rows else []

        return QueryResult(
            columns=columns,
            rows=normalized_rows,
        )

    def _apply_limit(self, sql: str) -> str:
        normalized = sql.strip().rstrip(";")

        engine_dialect = self.engine.dialect.name
        dialect = {"mssql": "tsql", "postgresql": "postgres"}.get(
            engine_dialect, engine_dialect,
        )
        if dialect not in Dialect.classes:
            return normalized

        try:
            statements = sqlglot.parse(normalized, read=dialect)
            if len(statements) != 1 or not isinstance(statements[0], exp.Query):
                return normalized
            query = statements[0]
            if query.args.get("limit") is None:
                query = query.limit(self.max_rows)
            return query.sql(dialect=dialect, unsupported_level=ErrorLevel.RAISE)
        except SqlglotError:
            # Leave vendor-specific SQL intact; fetchmany still bounds returned rows.
            return normalized

    def _normalize_row(self, row: Mapping[str, Any] | RowMapping) -> dict[str, Any]:
        return cast(dict[str, Any], to_jsonable(dict(row)))
