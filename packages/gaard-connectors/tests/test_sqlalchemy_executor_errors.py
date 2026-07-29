import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from gaard_core.errors import QueryExecutionError

from gaard_connectors.sqlalchemy.executor import SQLAlchemyQueryExecutor


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
