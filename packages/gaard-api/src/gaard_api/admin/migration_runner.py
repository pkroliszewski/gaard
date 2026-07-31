"""Generic interpreter for append-only, tagged SQL update files."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Table, inspect, select
from sqlalchemy.engine import Connection, Engine

_TAG_PATTERN = re.compile(r"^--\s*tag\s*:\s*(\S+)\s*$", re.IGNORECASE)
_DIALECT_PATTERN = re.compile(r"^--\s*dialect\s*:\s*(\S+)\s*$", re.IGNORECASE)
_TABLE_PATTERN = re.compile(r"^--\s*table\s*:\s*(\S+)\s*$", re.IGNORECASE)
_PHASE_PATTERN = re.compile(r"^--\s*phase\s*:\s*(\S+)\s*$", re.IGNORECASE)
_COLUMN_EXISTS_PATTERN = re.compile(
    r"^--\s*column-exists\s*:\s*(\S+)\s*$", re.IGNORECASE
)
_COLUMN_MISSING_PATTERN = re.compile(
    r"^--\s*column-missing\s*:\s*(\S+)\s*$", re.IGNORECASE
)
LEGACY_SQL_PHASES = frozenset({"before-initial", "after-initial"})


@dataclass(frozen=True, slots=True)
class SqlUpdateCommand:
    sql: str
    dialect: str | None = None
    required_table: str | None = None


@dataclass(frozen=True, slots=True)
class TaggedSqlUpdate:
    tag: str
    commands: tuple[SqlUpdateCommand, ...]


@dataclass(frozen=True, slots=True)
class ConditionalSqlCommand:
    sql: str
    phase: str
    dialect: str | None = None
    required_table: str | None = None
    required_columns: tuple[str, ...] = ()
    missing_columns: tuple[str, ...] = ()


def parse_sql_updates(contents: str) -> tuple[TaggedSqlUpdate, ...]:
    """Parse one SQL command per line, grouped by ``-- tag: value`` markers."""
    updates: list[TaggedSqlUpdate] = []
    current_tag: str | None = None
    current_dialect: str | None = None
    current_table: str | None = None
    current_commands: list[SqlUpdateCommand] = []

    def finish_current_update() -> None:
        nonlocal current_commands
        if current_tag is None:
            return
        if not current_commands:
            raise ValueError(f"Database update {current_tag!r} has no SQL commands.")
        updates.append(TaggedSqlUpdate(current_tag, tuple(current_commands)))
        current_commands = []

    for line_number, source_line in enumerate(contents.splitlines(), start=1):
        line = source_line.strip()
        if not line:
            continue

        tag_match = _TAG_PATTERN.fullmatch(line)
        if tag_match:
            finish_current_update()
            current_tag = tag_match.group(1)
            current_dialect = None
            current_table = None
            continue

        dialect_match = _DIALECT_PATTERN.fullmatch(line)
        if dialect_match:
            if current_tag is None:
                raise ValueError(
                    f"Dialect marker before the first tag on line {line_number}."
                )
            dialect = dialect_match.group(1).lower()
            current_dialect = None if dialect == "all" else dialect
            continue

        table_match = _TABLE_PATTERN.fullmatch(line)
        if table_match:
            if current_tag is None:
                raise ValueError(f"Table marker before the first tag on line {line_number}.")
            table_name = table_match.group(1)
            current_table = None if table_name.lower() == "all" else table_name
            continue

        if line.startswith("--"):
            continue
        if current_tag is None:
            raise ValueError(f"SQL command before the first tag on line {line_number}.")
        if not line.endswith(";"):
            raise ValueError(
                f"SQL command on line {line_number} must end with a semicolon."
            )
        sql = line[:-1].strip()
        if not sql:
            raise ValueError(f"Empty SQL command on line {line_number}.")
        current_commands.append(
            SqlUpdateCommand(
                sql=sql,
                dialect=current_dialect,
                required_table=current_table,
            )
        )

    finish_current_update()
    _validate_unique_tags(updates)
    return tuple(updates)


def parse_initial_sql(contents: str) -> tuple[SqlUpdateCommand, ...]:
    """Parse an initial-schema file containing one SQL command per line."""
    commands: list[SqlUpdateCommand] = []
    current_dialect: str | None = None
    for line_number, source_line in enumerate(contents.splitlines(), start=1):
        line = source_line.strip()
        if not line:
            continue
        dialect_match = _DIALECT_PATTERN.fullmatch(line)
        if dialect_match:
            dialect = dialect_match.group(1).lower()
            current_dialect = None if dialect == "all" else dialect
            continue
        if line.startswith("--"):
            continue
        if not line.endswith(";"):
            raise ValueError(
                f"SQL command on line {line_number} must end with a semicolon."
            )
        sql = line[:-1].strip()
        if not sql:
            raise ValueError(f"Empty SQL command on line {line_number}.")
        commands.append(SqlUpdateCommand(sql=sql, dialect=current_dialect))
    if not commands:
        raise ValueError("Initial database schema has no SQL commands.")
    return tuple(commands)


def parse_legacy_sql(contents: str) -> tuple[ConditionalSqlCommand, ...]:
    """Parse the conditional, pre-first-tag metadata repair file."""
    commands: list[ConditionalSqlCommand] = []
    current_phase: str | None = None
    current_dialect: str | None = None
    current_table: str | None = None
    required_columns: list[str] = []
    missing_columns: list[str] = []

    for line_number, source_line in enumerate(contents.splitlines(), start=1):
        line = source_line.strip()
        if not line:
            continue

        phase_match = _PHASE_PATTERN.fullmatch(line)
        if phase_match:
            phase = phase_match.group(1).lower()
            if phase not in LEGACY_SQL_PHASES:
                raise ValueError(f"Unknown legacy SQL phase {phase!r} on line {line_number}.")
            current_phase = phase
            continue

        dialect_match = _DIALECT_PATTERN.fullmatch(line)
        if dialect_match:
            dialect = dialect_match.group(1).lower()
            current_dialect = None if dialect == "all" else dialect
            continue

        table_match = _TABLE_PATTERN.fullmatch(line)
        if table_match:
            table_name = table_match.group(1)
            current_table = None if table_name.lower() == "all" else table_name
            continue

        column_exists_match = _COLUMN_EXISTS_PATTERN.fullmatch(line)
        if column_exists_match:
            required_columns.append(column_exists_match.group(1))
            continue

        column_missing_match = _COLUMN_MISSING_PATTERN.fullmatch(line)
        if column_missing_match:
            missing_columns.append(column_missing_match.group(1))
            continue

        if line.startswith("--"):
            continue
        if current_phase is None:
            raise ValueError(f"SQL command before the first phase on line {line_number}.")
        if not line.endswith(";"):
            raise ValueError(
                f"SQL command on line {line_number} must end with a semicolon."
            )
        sql = line[:-1].strip()
        if not sql:
            raise ValueError(f"Empty SQL command on line {line_number}.")
        if (required_columns or missing_columns) and current_table is None:
            raise ValueError(
                f"Column condition without a table marker on line {line_number}."
            )
        commands.append(
            ConditionalSqlCommand(
                sql=sql,
                phase=current_phase,
                dialect=current_dialect,
                required_table=current_table,
                required_columns=tuple(required_columns),
                missing_columns=tuple(missing_columns),
            )
        )
        required_columns = []
        missing_columns = []

    if not commands:
        raise ValueError("Legacy database repair file has no SQL commands.")
    return tuple(commands)


def execute_initial_sql(engine: Engine, commands: Sequence[SqlUpdateCommand]) -> None:
    """Execute an initial schema atomically for the active database dialect."""
    with engine.begin() as connection:
        _begin_transaction_before_sqlite_ddl(connection)
        for command in commands:
            if command.dialect is None or command.dialect == connection.dialect.name.lower():
                connection.exec_driver_sql(command.sql)


def execute_legacy_sql_phase(
    engine: Engine,
    commands: Sequence[ConditionalSqlCommand],
    phase: str,
) -> None:
    """Execute one conditional legacy-repair phase atomically."""
    if phase not in LEGACY_SQL_PHASES:
        raise ValueError(f"Unknown legacy SQL phase: {phase!r}.")
    with engine.begin() as connection:
        _begin_transaction_before_sqlite_ddl(connection)
        for command in commands:
            if command.phase != phase or not _conditional_command_applies(
                connection, command
            ):
                continue
            connection.exec_driver_sql(command.sql)


def has_applicable_legacy_repairs(
    engine: Engine,
    commands: Sequence[ConditionalSqlCommand],
) -> bool:
    """Return whether any column-conditional legacy repair still applies."""
    with engine.connect() as connection:
        return any(
            (command.required_columns or command.missing_columns)
            and _conditional_command_applies(connection, command)
            for command in commands
        )

def apply_pending_sql_updates(
    engine: Engine,
    updates: Sequence[TaggedSqlUpdate],
    migration_tags_table: Table,
) -> tuple[str, ...]:
    """Apply missing tagged groups atomically in file order."""
    _validate_unique_tags(updates)
    tag_column = migration_tags_table.c.tag
    with engine.connect() as connection:
        applied_tags = set(connection.scalars(select(tag_column)).all())

    newly_applied: list[str] = []
    for update in updates:
        if update.tag in applied_tags:
            continue
        with engine.begin() as connection:
            _begin_transaction_before_sqlite_ddl(connection)
            _execute_update(connection, update)
            connection.execute(migration_tags_table.insert().values(tag=update.tag))
        newly_applied.append(update.tag)
        applied_tags.add(update.tag)
    return tuple(newly_applied)


def stamp_sql_updates(
    engine: Engine,
    updates: Sequence[TaggedSqlUpdate],
    migration_tags_table: Table,
) -> None:
    """Mark the current append-only file as included in an initial/baseline schema."""
    _validate_unique_tags(updates)
    tag_column = migration_tags_table.c.tag
    with engine.begin() as connection:
        applied_tags = set(connection.scalars(select(tag_column)).all())
        for update in updates:
            if update.tag not in applied_tags:
                connection.execute(migration_tags_table.insert().values(tag=update.tag))


def _execute_update(connection: Connection, update: TaggedSqlUpdate) -> None:
    dialect = connection.dialect.name.lower()
    for command in update.commands:
        if command.dialect is not None and command.dialect != dialect:
            continue
        if command.required_table is not None and not inspect(connection).has_table(
            command.required_table
        ):
            continue
        connection.exec_driver_sql(command.sql)


def _conditional_command_applies(
    connection: Connection,
    command: ConditionalSqlCommand,
) -> bool:
    dialect = connection.dialect.name.lower()
    if command.dialect is not None and command.dialect != dialect:
        return False
    if command.required_table is None:
        return True

    inspector = inspect(connection)
    if not inspector.has_table(command.required_table):
        return False
    if not command.required_columns and not command.missing_columns:
        return True

    column_names = {
        str(column["name"])
        for column in inspector.get_columns(command.required_table)
    }
    return all(column in column_names for column in command.required_columns) and all(
        column not in column_names for column in command.missing_columns
    )


def _begin_transaction_before_sqlite_ddl(connection: Connection) -> None:
    if connection.dialect.name.lower() == "sqlite":
        # Python's sqlite driver otherwise starts a transaction only on the
        # first DML statement, leaving preceding DDL outside rollback.
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def _validate_unique_tags(updates: Iterable[TaggedSqlUpdate]) -> None:
    seen: set[str] = set()
    for update in updates:
        tag = update.tag.strip()
        if not tag or tag != update.tag:
            raise ValueError("Database update tags must be non-empty and contain no whitespace.")
        if tag in seen:
            raise ValueError(f"Duplicate database update tag: {tag}")
        if not update.commands:
            raise ValueError(f"Database update {tag!r} has no SQL commands.")
        seen.add(tag)


def as_table(value: object) -> Table:
    """Narrow a declarative model's ``__table__`` value for typed callers."""
    return cast(Table, value)
