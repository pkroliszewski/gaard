import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlglot
from gaard_core.errors import QueryExecutionError

from gaard_connectors.sqlalchemy.executor import SQLAlchemyQueryExecutor


@pytest.mark.parametrize(
    ("engine_dialect", "sqlglot_dialect", "sql", "expected_limit"),
    [
        ("mssql", "tsql", "SELECT * FROM abc", 100),
        ("mssql", "tsql", "SELECT TOP (10) * FROM abc", 10),
        ("mssql", "tsql", "SELECT * FROM abc LIMIT 100", 100),
        ("mssql", "tsql", "SELECT DISTINCT name FROM abc", 100),
        ("mssql", "tsql", "WITH a AS (SELECT TOP (2) * FROM abc) SELECT * FROM a", 100),
        ("mssql", "tsql", "SELECT * FROM abc ORDER BY id OFFSET 5 ROWS", 100),
        ("oracle", "oracle", "SELECT * FROM abc", 100),
        ("postgresql", "postgres", "SELECT * FROM abc", 100),
        ("mysql", "mysql", "SELECT * FROM abc", 100),
        ("sqlite", "sqlite", "SELECT 'LIMIT 10' AS label FROM abc", 100),
    ],
)
def test_executor_applies_native_row_limit(
    monkeypatch: pytest.MonkeyPatch,
    engine_dialect: str,
    sqlglot_dialect: str,
    sql: str,
    expected_limit: int,
) -> None:
    monkeypatch.setattr(
        SQLAlchemyQueryExecutor, "_create_engine",
        lambda self, url: SimpleNamespace(dialect=SimpleNamespace(name=engine_dialect)),
    )
    executor = SQLAlchemyQueryExecutor("unused://", max_rows=100)
    limited = executor._apply_limit(sql)
    parsed = sqlglot.parse_one(limited, read=sqlglot_dialect)
    limit = parsed.args["limit"]
    count = limit.args.get("count") or limit.expression
    assert int(count.this) == expected_limit
    if engine_dialect in {"mssql", "oracle"}:
        assert "LIMIT" not in limited.upper()


def test_executor_bounds_fetch_without_rewriting_unknown_dialect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []
    fetched: list[int] = []

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def execute(self, statement: Any) -> "Connection":
            executed.append(str(statement))
            return self

        def mappings(self) -> "Connection":
            return self

        def fetchmany(self, size: int) -> list[dict[str, int]]:
            fetched.append(size)
            return [{"id": 1}]

    monkeypatch.setattr(
        SQLAlchemyQueryExecutor, "_create_engine",
        lambda self, url: SimpleNamespace(
            dialect=SimpleNamespace(name="ibm_db_sa"), connect=Connection,
        ),
    )
    executor = SQLAlchemyQueryExecutor("unused://", max_rows=10)
    result = executor.execute("SELECT * FROM abc FETCH FIRST 5 ROWS ONLY")
    assert executed == ["SELECT * FROM abc FETCH FIRST 5 ROWS ONLY"]
    assert fetched == [10]
    assert result.rows == [{"id": 1}]


def test_sqlalchemy_executor_wraps_database_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE patients (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    executor = SQLAlchemyQueryExecutor(
        database_url=f"sqlite:///{db_path}",
        max_rows=100,
    )

    with pytest.raises(QueryExecutionError):
        executor.execute("SELECT missing_column FROM patients")


def test_executor_caps_rows_even_when_sql_has_a_larger_limit() -> None:
    executor = SQLAlchemyQueryExecutor("sqlite:///:memory:", max_rows=2)
    with executor.engine.begin() as connection:
        from sqlalchemy import text

        connection.execute(text("CREATE TABLE abc (id INTEGER)"))
        connection.execute(text("INSERT INTO abc VALUES (1), (2), (3)"))
    result = executor.execute("SELECT id FROM abc ORDER BY id LIMIT 1000")
    assert result.rows == [{"id": 1}, {"id": 2}]


def test_sqlalchemy_executor_normalizes_database_values_to_jsonable_rows() -> None:
    executor = SQLAlchemyQueryExecutor(
        database_url="sqlite:///:memory:",
        max_rows=100,
    )

    row = executor._normalize_row(
        {
            "total_minutes": Decimal(42),
            "ratio": Decimal("12.5"),
            "created_on": date(2026, 5, 24),
            "created_at": datetime(2026, 5, 24, 10, 15, 30, tzinfo=UTC).replace(tzinfo=None),
            "payload": b"hello",
            "binary_payload": b"\xff",
            "nested": {"amount": Decimal("7.25")},
        }
    )

    assert row == {
        "total_minutes": 42,
        "ratio": 12.5,
        "created_on": "2026-05-24",
        "created_at": "2026-05-24T10:15:30",
        "payload": "hello",
        "binary_payload": "ff",
        "nested": {"amount": 7.25},
    }


def test_sqlalchemy_executor_does_not_add_duplicate_limit_with_newline(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE patients (id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
        connection.execute("INSERT INTO patients (status) VALUES ('active')")
        connection.commit()
    finally:
        connection.close()

    executor = SQLAlchemyQueryExecutor(
        database_url=f"sqlite:///{db_path}",
        max_rows=100,
    )

    result = executor.execute(
        """
        SELECT COUNT(*) AS total_active_patients
        FROM patients
        WHERE status = 'active'
        LIMIT 100
        """
    )

    assert result.rows == [{"total_active_patients": 1}]
