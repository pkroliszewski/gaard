from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
import json

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.orm import Session


SessionFactory = Callable[[], Session]

metadata = MetaData()

extract_unstructured_source_models = Table(
    "extract_unstructured_source_models",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("datasource_connector_id", Integer, nullable=False),
    Column("datasource_connector_key", String(255), nullable=False),
    Column("main_table", String(255), nullable=False),
    Column("table_roles_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_by", String(255), nullable=False, server_default=text("'system'")),
    UniqueConstraint(
        "datasource_connector_id",
        name="uq_extract_unstructured_source_models_datasource",
    ),
)

extract_chunking_configs = Table(
    "extract_chunking_configs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("mode", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_by", String(255), nullable=False, server_default=text("'system'")),
)

extract_embedding_configs = Table(
    "extract_embedding_configs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("enabled", Boolean, nullable=False),
    Column("provider", String(64), nullable=False),
    Column("base_url", String(2048), nullable=False),
    Column("api_key", Text, nullable=False),
    Column("model", String(255), nullable=False),
    Column("timeout_seconds", Integer, nullable=False),
    Column("extra_body_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_by", String(255), nullable=False, server_default=text("'system'")),
)

extract_blueprints = Table(
    "extract_blueprints",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("blueprint_key", String(255), nullable=False, unique=True),
    Column("name", String(255), nullable=False),
    Column("description", Text),
    Column("domain_description", Text, nullable=False),
    Column("case_grain_description", Text, nullable=False),
    Column("language", String(32), nullable=False, server_default=text("'pl'")),
    Column("status", String(64), nullable=False, server_default=text("'draft'")),
    Column("config_json", Text, nullable=False),
    Column("json_schema_json", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_by", String(255), nullable=False, server_default=text("'system'")),
)

extract_job_configs = Table(
    "extract_job_configs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("source_model_json", Text, nullable=False),
    Column("chunking_json", Text, nullable=False),
    Column("embedding_json", Text, nullable=False),
    Column("blueprint_json", Text, nullable=False),
    Column("json_schema_json", Text, nullable=False),
    Column("origin", String(64), nullable=False, server_default=text("'current'")),
    Column("source_job_id", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_by", String(255), nullable=False, server_default=text("'system'")),
)

extract_jobs = Table(
    "extract_jobs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("config_id", String(64), nullable=False),
    Column("status", String(64), nullable=False, server_default=text("'queued'")),
    Column("progress_current", Integer, nullable=False, server_default=text("0")),
    Column("progress_total", Integer, nullable=False, server_default=text("0")),
    Column("cases_total", Integer, nullable=False, server_default=text("0")),
    Column("chunks_total", Integer, nullable=False, server_default=text("0")),
    Column("items_total", Integer, nullable=False, server_default=text("0")),
    Column("output_path", Text),
    Column("output_datasource_id", Integer),
    Column("output_datasource_key", String(255)),
    Column("error_message", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_by", String(255), nullable=False, server_default=text("'system'")),
)

extract_job_events = Table(
    "extract_job_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", String(64), nullable=False),
    Column("level", String(32), nullable=False, server_default=text("'info'")),
    Column("message", Text, nullable=False),
    Column("details_json", Text, nullable=False, server_default=text("'{}'")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)


def init_database(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        bind = session.get_bind()
        if needs_source_models_rebuild(bind):
            rebuild_source_models_table(session)
            session.commit()

        metadata.create_all(bind)
        migrate_blueprints_table(session)
        session.commit()
        mark_interrupted_jobs(session)
        session.commit()


def needs_source_models_rebuild(bind: Any) -> bool:
    inspector = inspect(bind)
    if "extract_unstructured_source_models" not in inspector.get_table_names():
        return False

    column_names = {
        column["name"]
        for column in inspector.get_columns("extract_unstructured_source_models")
    }
    return (
        "table_roles_json" not in column_names
        or "case_id_column" in column_names
        or "content_column" in column_names
    )


def rebuild_source_models_table(session: Session) -> None:
    bind = session.get_bind()
    column_names = {
        column["name"]
        for column in inspect(bind).get_columns("extract_unstructured_source_models")
    }
    rows = read_legacy_source_model_rows(session, column_names)

    session.execute(text("DROP TABLE IF EXISTS extract_unstructured_source_models_legacy"))
    session.execute(
        text(
            "ALTER TABLE extract_unstructured_source_models "
            "RENAME TO extract_unstructured_source_models_legacy"
        )
    )
    metadata.create_all(bind)

    for row in rows:
        session.execute(
            extract_unstructured_source_models.insert().values(
                id=row.get("id"),
                datasource_connector_id=row["datasource_connector_id"],
                datasource_connector_key=row["datasource_connector_key"],
                main_table=row["main_table"],
                table_roles_json=row["table_roles_json"],
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
                updated_by=row.get("updated_by") or "system",
            )
        )

    session.execute(text("DROP TABLE extract_unstructured_source_models_legacy"))


def read_legacy_source_model_rows(
    session: Session,
    column_names: set[str],
) -> list[dict[str, Any]]:
    selected_columns = [
        "id",
        "datasource_connector_id",
        "datasource_connector_key",
        "main_table",
        "created_at",
        "updated_at",
        "updated_by",
    ]
    for optional_column in ("table_roles_json", "case_id_column", "content_column"):
        if optional_column in column_names:
            selected_columns.append(optional_column)

    rows = session.execute(
        text(
            "SELECT "
            + ", ".join(selected_columns)
            + " FROM extract_unstructured_source_models"
        )
    ).mappings()

    return [normalize_legacy_source_model_row(dict(row)) for row in rows]


def normalize_legacy_source_model_row(row: dict[str, Any]) -> dict[str, Any]:
    if not row.get("table_roles_json"):
        main_table = row["main_table"]
        case_id_column = row.get("case_id_column") or ""
        content_column = row.get("content_column") or ""
        row["table_roles_json"] = json.dumps(
            {
                main_table: {
                    "case_id_column": case_id_column,
                    "content_column": content_column,
                }
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    for timestamp_key in ("created_at", "updated_at"):
        value = row.get(timestamp_key)
        if isinstance(value, str):
            row[timestamp_key] = datetime.fromisoformat(value)

    return row


def migrate_blueprints_table(session: Session) -> None:
    bind = session.get_bind()
    inspector = inspect(bind)
    if "extract_blueprints" not in inspector.get_table_names():
        return

    column_names = {
        column["name"]
        for column in inspector.get_columns("extract_blueprints")
    }
    if "json_schema_json" not in column_names:
        session.execute(text("ALTER TABLE extract_blueprints ADD COLUMN json_schema_json TEXT"))
        if "json_schema" in column_names:
            session.execute(
                text(
                    "UPDATE extract_blueprints "
                    "SET json_schema_json = json_schema "
                    "WHERE json_schema_json IS NULL"
                )
            )

    if "updated_by" not in column_names:
        session.execute(
            text(
                "ALTER TABLE extract_blueprints "
                "ADD COLUMN updated_by VARCHAR(255) NOT NULL DEFAULT 'system'"
            )
        )


def mark_interrupted_jobs(session: Session) -> None:
    bind = session.get_bind()
    inspector = inspect(bind)
    if "extract_jobs" not in inspector.get_table_names():
        return

    session.execute(
        text(
            "UPDATE extract_jobs "
            "SET status = 'failed', "
            "error_message = 'Job interrupted by API restart.', "
            "finished_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE status = 'running'"
        )
    )
