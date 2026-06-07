import re
from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError

from gaard_core.errors import QueryExecutionError
from gaard_core.json_utils import to_jsonable
from gaard_core.query_pipeline.models import QueryResult


class SQLAlchemyQueryExecutor:
    def __init__(self, database_url: str, max_rows: int = 100) -> None:
        self.database_url = database_url
        self.max_rows = max_rows
        self.engine: Engine = create_engine(database_url)

    def execute(self, sql: str) -> QueryResult:
        limited_sql = self._apply_limit(sql)

        try:
            with self.engine.connect() as connection:
                result = connection.execute(text(limited_sql))
                rows = result.mappings().fetchall()
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

        if re.search(r"\blimit\s+\d+\b", normalized, flags=re.IGNORECASE):
            return normalized

        return f"{normalized} LIMIT {self.max_rows}"

    def _normalize_row(self, row: Mapping[str, Any] | RowMapping) -> dict[str, Any]:
        return cast(dict[str, Any], to_jsonable(dict(row)))
