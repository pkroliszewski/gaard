from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Protocol
from uuid import uuid4

import httpx2 as httpx
from gaard_api.admin import services as admin_services
from gaard_api.admin.models import DatasourceConnector
from gaard_api.core.settings import settings as gaard_settings
from gaard_core.errors import LlmProviderError
from gaard_core.llm_output import remove_thinking_blocks
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from gaard_extract import db

CHUNKING_MODES = {
    "none",
    "fixed_size",
    "semantic",
}
DEFAULT_CHUNKING_MODE = "fixed_size"
DEFAULT_EMBEDDING_CONFIG = {
    "enabled": False,
    "provider": "openai-compatible",
    "base_url": "https://api.openai.com/v1",
    "api_key": "change-me",
    "model": "text-embedding-3-small",
    "timeout_seconds": 60,
    "extra_body": {},
}
BLUEPRINT_STATUSES = {
    "draft",
    "generated",
    "approved",
    "archived",
}
INFORMATION_KINDS = {
    "entity",
    "event",
    "fact",
    "relation",
}
INFORMATION_KIND_GUIDANCE = {
    "entity": (
        "a stable thing mentioned in the text, such as a person, company, place, "
        "product, account, document, or case object"
    ),
    "event": (
        "an occurrence, action, decision, change, interaction, or milestone that "
        "happens at a point in time or over a period"
    ),
    "fact": (
        "a state, assertion, classification, status, value, observation, or "
        "attribute that is true for the case or an entity"
    ),
    "relation": (
        "a connection, dependency, ownership, responsibility, cause, membership, "
        "or link between two extracted items or concepts"
    ),
}
FIELD_TYPES = {
    "string",
    "number",
    "date",
    "datetime",
    "boolean",
    "enum",
    "array",
    "object",
}
FIELD_TYPE_ALIASES = {
    "bool": "boolean",
    "dict": "object",
    "float": "number",
    "int": "number",
    "integer": "number",
    "list": "array",
    "timestamp": "datetime",
}
BLUEPRINT_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_GLOBAL_RULES = [
    "Nie zgaduj informacji, których nie ma w tekście.",
    "Każdy element musi mieć evidence_text.",
    "Daty normalizuj do ISO-8601, gdy jest to możliwe.",
    "Jeżeli data jest względna lub niepełna, oznacz element jako wymagający review.",
]
DEFAULT_REVIEW_POLICY = {
    "auto_approve_threshold": 0.9,
    "needs_review_threshold": 0.6,
    "reject_below_threshold": 0.3,
}
DEFAULT_EXTRACTION_SCOPE = {
    "source_mode": "active_source_model",
    "content_tables": [],
    "chunk_selection": "all_chunks",
    "use_embeddings": False,
    "max_neighbor_chunks": 3,
    "min_similarity": 0.75,
    "max_chunks_per_case": None,
    "include_case_metadata": True,
    "require_evidence_text": True,
}
JOB_STATUSES = {
    "queued",
    "running",
    "succeeded",
    "failed",
}
OUTPUT_CANONICAL_TABLES = (
    "extract_runs",
    "extract_cases",
    "extract_documents",
    "extract_chunks",
    "extract_items",
    "extract_attributes",
    "extract_warnings",
    "extract_errors",
)
FIXED_CHUNK_SIZE = 2000
FIXED_CHUNK_OVERLAP = 300
SEMANTIC_CHUNK_TARGET_SIZE = 2500
DEFAULT_EXTRACTION_PROMPT_CHAR_BUDGET = 60_000
RETRY_EXTRACTION_PROMPT_CHAR_BUDGET = 30_000
LLM_TIMEOUT_RETRY_COUNT = 1
MIN_TRUNCATED_CHUNK_CHARS = 800
RESERVED_EXTRACTION_ATTRIBUTE_NAMES = {
    "chunk_id",
    "chunk_ids",
    "confidence",
    "evidence",
    "evidence_text",
    "kind",
    "needs_review",
    "reason",
    "review",
    "source",
    "source_table",
    "type",
}
SessionFactory = Callable[[], Session]

_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_worker_stop = threading.Event()


class DatasourceHostService(Protocol):
    def list_datasources(self, *, include_system: bool = False) -> list[dict[str, Any]]:
        ...

    def get_datasource(self, connector_id: int) -> dict[str, Any] | None:
        ...

    def get_schema(self, connector_id: int) -> dict[str, Any] | None:
        ...


def list_datasources(datasource_service: DatasourceHostService) -> list[dict[str, Any]]:
    return datasource_service.list_datasources(include_system=False)


def get_datasource_schema(
    datasource_service: DatasourceHostService,
    datasource_connector_id: int,
) -> dict[str, Any]:
    datasource = require_datasource(datasource_service, datasource_connector_id)
    schema = datasource_service.get_schema(datasource_connector_id)
    if schema is None:
        raise ValueError(
            f"Datasource '{datasource['connector_key']}' has no schema cache. "
            "Run Schema introspection in Configuration → Data sources first."
        )
    return schema


def list_source_models(session: Session) -> list[dict[str, Any]]:
    row = session.execute(
        select(db.extract_unstructured_source_models).order_by(
            db.extract_unstructured_source_models.c.updated_at.desc(),
            db.extract_unstructured_source_models.c.id.desc(),
        )
    ).mappings().first()
    return [serialize_source_model(row)] if row is not None else []


def get_source_model(session: Session, datasource_connector_id: int) -> dict[str, Any] | None:
    row = session.execute(
        select(db.extract_unstructured_source_models).where(
            db.extract_unstructured_source_models.c.datasource_connector_id
            == datasource_connector_id
        )
    ).mappings().first()
    return serialize_source_model(row) if row is not None else None


def get_active_source_model(session: Session) -> dict[str, Any] | None:
    row = session.execute(
        select(db.extract_unstructured_source_models).order_by(
            db.extract_unstructured_source_models.c.updated_at.desc(),
            db.extract_unstructured_source_models.c.id.desc(),
        )
    ).mappings().first()
    return serialize_source_model(row) if row is not None else None


def get_chunking_config(session: Session) -> dict[str, Any]:
    row = session.execute(
        select(db.extract_chunking_configs).order_by(
            db.extract_chunking_configs.c.updated_at.desc(),
            db.extract_chunking_configs.c.id.desc(),
        )
    ).mappings().first()
    if row is None:
        return {
            "mode": DEFAULT_CHUNKING_MODE,
            "persisted": False,
        }
    item = serialize_timestamps(row)
    item["persisted"] = True
    return item


def upsert_chunking_config(
    session: Session,
    *,
    mode: str,
    updated_by: str,
) -> dict[str, Any]:
    normalized_mode = validate_chunking_mode(mode)
    existing = session.execute(
        select(db.extract_chunking_configs).order_by(
            db.extract_chunking_configs.c.updated_at.desc(),
            db.extract_chunking_configs.c.id.desc(),
        )
    ).mappings().first()

    values = {
        "mode": normalized_mode,
        "updated_at": utc_now(),
        "updated_by": updated_by,
    }

    if existing is None:
        session.execute(db.extract_chunking_configs.insert().values(**values))
    else:
        session.execute(
            delete(db.extract_chunking_configs).where(
                db.extract_chunking_configs.c.id != existing["id"]
            )
        )
        session.execute(
            update(db.extract_chunking_configs)
            .where(db.extract_chunking_configs.c.id == existing["id"])
            .values(**values)
        )

    return get_chunking_config(session)


def validate_chunking_mode(mode: str) -> str:
    normalized_mode = str(mode or "").strip()
    if normalized_mode not in CHUNKING_MODES:
        raise ValueError(
            "Chunking mode must be one of: " + ", ".join(sorted(CHUNKING_MODES)) + "."
        )
    return normalized_mode


def get_embedding_config(session: Session) -> dict[str, Any]:
    row = session.execute(
        select(db.extract_embedding_configs).order_by(
            db.extract_embedding_configs.c.updated_at.desc(),
            db.extract_embedding_configs.c.id.desc(),
        )
    ).mappings().first()
    if row is None:
        return serialize_embedding_config(
            {
                **DEFAULT_EMBEDDING_CONFIG,
                "extra_body_json": json_dumps(DEFAULT_EMBEDDING_CONFIG["extra_body"]),
            },
            persisted=False,
        )
    return serialize_embedding_config(row, persisted=True)


def get_llm_extracting_config(session: Session) -> dict[str, Any]:
    row = session.execute(
        select(db.extract_blueprints).order_by(
            db.extract_blueprints.c.updated_at.desc(),
            db.extract_blueprints.c.blueprint_key.asc(),
        )
    ).mappings().first()
    if row is None:
        return default_llm_extracting_config(session)
    return serialize_llm_extracting_config(row, persisted=True)


def upsert_llm_extracting_config(
    session: Session,
    *,
    blueprint_key: str,
    name: str,
    description: str | None,
    domain_description: str,
    case_grain_description: str,
    language: str,
    status: str,
    information_types: list[Any],
    global_rules: list[Any],
    review_policy: dict[str, Any],
    extraction_scope: dict[str, Any],
    json_schema: dict[str, Any] | None,
    updated_by: str,
) -> dict[str, Any]:
    normalized_key = normalize_blueprint_key(blueprint_key)
    normalized_name = str(name or "").strip()
    normalized_description = normalize_optional_text(description)
    normalized_domain_description = str(domain_description or "").strip()
    normalized_case_grain_description = str(case_grain_description or "").strip()
    normalized_language = str(language or "pl").strip() or "pl"
    normalized_status = normalize_blueprint_status(status)
    normalized_information_types = normalize_information_types(information_types)
    normalized_global_rules = normalize_global_rules(global_rules)
    normalized_review_policy = normalize_review_policy(review_policy)
    normalized_extraction_scope = normalize_extraction_scope(session, extraction_scope)

    if not normalized_name:
        raise ValueError("Blueprint name is required.")
    if normalized_status == "approved":
        if not normalized_domain_description:
            raise ValueError("Domain description is required before approving the blueprint.")
        if not normalized_case_grain_description:
            raise ValueError("Case grain description is required before approving the blueprint.")

    normalized_schema = normalize_json_schema(json_schema)
    if normalized_schema is None:
        normalized_schema = build_extraction_json_schema(normalized_information_types)

    existing = session.execute(
        select(db.extract_blueprints).order_by(
            db.extract_blueprints.c.updated_at.desc(),
            db.extract_blueprints.c.blueprint_key.asc(),
        )
    ).mappings().first()

    values = {
        "blueprint_key": normalized_key,
        "name": normalized_name,
        "description": normalized_description,
        "domain_description": normalized_domain_description,
        "case_grain_description": normalized_case_grain_description,
        "language": normalized_language,
        "status": normalized_status,
        "config_json": json_dumps(
            {
                "information_types": normalized_information_types,
                "global_rules": normalized_global_rules,
                "review_policy": normalized_review_policy,
                "extraction_scope": normalized_extraction_scope,
            }
        ),
        "json_schema_json": json_dumps(normalized_schema),
        "updated_at": utc_now(),
        "updated_by": updated_by,
    }

    if existing is None:
        session.execute(
            db.extract_blueprints.insert().values(
                **values,
                id=str(uuid4()),
            )
        )
    else:
        session.execute(
            delete(db.extract_blueprints).where(
                db.extract_blueprints.c.id != existing["id"]
            )
        )
        session.execute(
            update(db.extract_blueprints)
            .where(db.extract_blueprints.c.id == existing["id"])
            .values(**values)
        )

    return get_llm_extracting_config(session)


def start_job_worker(session_factory: SessionFactory) -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_stop.clear()
        _worker_thread = threading.Thread(
            target=job_worker_loop,
            args=(session_factory,),
            name="gaard-extract-worker",
            daemon=True,
        )
        _worker_thread.start()


def job_worker_loop(session_factory: SessionFactory) -> None:
    while not _worker_stop.is_set():
        try:
            ran_job = run_next_job(session_factory)
        except Exception:
            ran_job = False
        _worker_stop.wait(0.5 if ran_job else 2.0)


def list_jobs(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(db.extract_jobs).order_by(
            db.extract_jobs.c.created_at.desc(),
            db.extract_jobs.c.id.desc(),
        )
    ).mappings()
    return [serialize_job(row) for row in rows]


def get_job(session: Session, job_id: str) -> dict[str, Any]:
    row = get_job_row(session, job_id)
    return serialize_job(row, events=list_job_events(session, job_id))


def get_job_config(session: Session, job_id: str) -> dict[str, Any]:
    job = get_job_row(session, job_id)
    config = get_job_config_row(session, job["config_id"])
    return deserialize_job_config(config)


def list_job_events(session: Session, job_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        select(db.extract_job_events)
        .where(db.extract_job_events.c.job_id == job_id)
        .order_by(db.extract_job_events.c.id.asc())
    ).mappings()
    return [serialize_job_event(row) for row in rows]


def create_job_from_current_config(
    session: Session,
    *,
    updated_by: str,
) -> dict[str, Any]:
    config_id = create_job_config_snapshot(
        session,
        origin="current",
        source_job_id=None,
        updated_by=updated_by,
    )
    job_id = insert_job(session, config_id=config_id, updated_by=updated_by)
    record_job_event(session, job_id, "info", "Job queued from current configuration.")
    return get_job(session, job_id)


def refresh_job_from_history(
    session: Session,
    *,
    source_job_id: str,
    updated_by: str,
) -> dict[str, Any]:
    source_job = get_job_row(session, source_job_id)
    if source_job["status"] != "succeeded":
        raise ValueError("Only succeeded Extract jobs can be refreshed.")
    source_config = get_job_config_row(session, source_job["config_id"])
    config_id = copy_job_config_snapshot(
        session,
        source_config,
        origin="refresh",
        source_job_id=source_job_id,
        updated_by=updated_by,
    )
    job_id = insert_job(session, config_id=config_id, updated_by=updated_by)
    record_job_event(
        session,
        job_id,
        "info",
        f"Job queued from historical job {source_job_id}.",
    )
    return get_job(session, job_id)


def create_job_config_snapshot(
    session: Session,
    *,
    origin: str,
    source_job_id: str | None,
    updated_by: str,
) -> str:
    source_model = get_active_source_model(session)
    if source_model is None:
        raise ValueError("Configure Source before running Extract.")

    chunking_config = get_chunking_config(session)
    embedding_config = get_embedding_config(session)
    blueprint = get_llm_extracting_config(session)
    validate_job_snapshot(source_model, chunking_config, embedding_config, blueprint, session)

    return insert_job_config_snapshot(
        session,
        source_model=source_model,
        chunking_config=chunking_config,
        embedding_config=embedding_config,
        blueprint=blueprint,
        json_schema=blueprint["json_schema"],
        origin=origin,
        source_job_id=source_job_id,
        updated_by=updated_by,
    )


def copy_job_config_snapshot(
    session: Session,
    source_config: Any,
    *,
    origin: str,
    source_job_id: str | None,
    updated_by: str,
) -> str:
    payload = deserialize_job_config(source_config)
    validate_job_snapshot(
        payload["source_model"],
        payload["chunking_config"],
        payload["embedding_config"],
        payload["llm_config"],
        session,
    )
    return insert_job_config_snapshot(
        session,
        source_model=payload["source_model"],
        chunking_config=payload["chunking_config"],
        embedding_config=payload["embedding_config"],
        blueprint=payload["llm_config"],
        json_schema=payload["llm_config"]["json_schema"],
        origin=origin,
        source_job_id=source_job_id,
        updated_by=updated_by,
    )


def insert_job_config_snapshot(
    session: Session,
    *,
    source_model: dict[str, Any],
    chunking_config: dict[str, Any],
    embedding_config: dict[str, Any],
    blueprint: dict[str, Any],
    json_schema: dict[str, Any],
    origin: str,
    source_job_id: str | None,
    updated_by: str,
) -> str:
    config_id = str(uuid4())
    session.execute(
        db.extract_job_configs.insert().values(
            id=config_id,
            source_model_json=json_dumps(strip_runtime_metadata(source_model)),
            chunking_json=json_dumps(strip_runtime_metadata(chunking_config)),
            embedding_json=json_dumps(sanitize_embedding_snapshot(embedding_config)),
            blueprint_json=json_dumps(strip_runtime_metadata(blueprint)),
            json_schema_json=json_dumps(json_schema),
            origin=origin,
            source_job_id=source_job_id,
            updated_by=updated_by,
        )
    )
    return config_id


def insert_job(session: Session, *, config_id: str, updated_by: str) -> str:
    job_id = str(uuid4())
    session.execute(
        db.extract_jobs.insert().values(
            id=job_id,
            config_id=config_id,
            status="queued",
            progress_current=0,
            progress_total=0,
            cases_total=0,
            chunks_total=0,
            items_total=0,
            updated_at=utc_now(),
            updated_by=updated_by,
        )
    )
    return job_id


def validate_job_snapshot(
    source_model: dict[str, Any],
    chunking_config: dict[str, Any],
    embedding_config: dict[str, Any],
    blueprint: dict[str, Any],
    session: Session,
) -> None:
    if not source_model.get("table_roles"):
        raise ValueError("Configure Source before running Extract.")
    validate_chunking_mode(chunking_config.get("mode") or DEFAULT_CHUNKING_MODE)
    information_types = blueprint.get("information_types") or []
    normalize_information_types(information_types)
    normalize_json_schema(blueprint.get("json_schema"))
    scope = blueprint.get("extraction_scope") or {}
    use_embeddings = bool(scope.get("use_embeddings")) or scope.get("chunk_selection") == "embedding_neighbors"
    if use_embeddings and not embedding_config.get("enabled"):
        raise ValueError("Enable embeddings before running Extract with embedding context.")
    if use_embeddings and not get_embedding_api_key(session):
        raise ValueError("Embedding API key is required before running Extract with embedding context.")
    llm_config = get_extract_llm_runtime_config(session)
    validate_suggestion_llm_config(llm_config, "extraction jobs")


def sanitize_embedding_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    item = strip_runtime_metadata(config)
    item.pop("api_key", None)
    item["api_key_configured"] = bool(config.get("api_key_configured"))
    item["api_key_preview"] = config.get("api_key_preview")
    return item


def strip_runtime_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if key not in {"created_at", "updated_at", "updated_by", "persisted"}
    }


def get_job_row(session: Session, job_id: str) -> Any:
    row = session.execute(
        select(db.extract_jobs).where(db.extract_jobs.c.id == job_id)
    ).mappings().first()
    if row is None:
        raise KeyError("Extract job does not exist.")
    return row


def get_job_config_row(session: Session, config_id: str) -> Any:
    row = session.execute(
        select(db.extract_job_configs).where(db.extract_job_configs.c.id == config_id)
    ).mappings().first()
    if row is None:
        raise KeyError("Extract job configuration does not exist.")
    return row


def deserialize_job_config(row: Any) -> dict[str, Any]:
    source_model = json_loads(row["source_model_json"], {})
    chunking_config = json_loads(row["chunking_json"], {})
    embedding_config = json_loads(row["embedding_json"], {})
    blueprint = json_loads(row["blueprint_json"], {})
    json_schema = json_loads(row["json_schema_json"], {})
    blueprint["json_schema"] = json_schema
    blueprint["json_schema_json"] = json_dumps_pretty(json_schema)
    return {
        "id": row["id"],
        "origin": row["origin"],
        "source_job_id": row["source_job_id"],
        "created_at": serialize_datetime(row.get("created_at")),
        "source_model": source_model,
        "chunking_config": chunking_config,
        "embedding_config": embedding_config,
        "llm_config": blueprint,
    }


def record_job_event(
    session: Session,
    job_id: str,
    level: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    session.execute(
        db.extract_job_events.insert().values(
            job_id=job_id,
            level=level,
            message=message,
            details_json=json_dumps(details or {}),
        )
    )


def serialize_job(row: Any, *, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    item = serialize_timestamps(row)
    if events is not None:
        item["events"] = events
    return item


def serialize_job_event(row: Any) -> dict[str, Any]:
    item = serialize_timestamps(row)
    item["details"] = json_loads(item.pop("details_json"), {})
    return item


def run_next_job(session_factory: SessionFactory) -> bool:
    with session_factory() as session:
        job = session.execute(
            select(db.extract_jobs)
            .where(db.extract_jobs.c.status == "queued")
            .order_by(db.extract_jobs.c.created_at.asc(), db.extract_jobs.c.id.asc())
        ).mappings().first()
        if job is None:
            return False
        job_id = job["id"]
        session.execute(
            update(db.extract_jobs)
            .where(db.extract_jobs.c.id == job_id)
            .values(
                status="running",
                started_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        record_job_event(session, job_id, "info", "Job started.")
        session.commit()

    try:
        execute_job(session_factory, job_id)
    except Exception as exc:
        with session_factory() as session:
            session.execute(
                update(db.extract_jobs)
                .where(db.extract_jobs.c.id == job_id)
                .values(
                    status="failed",
                    error_message=str(exc),
                    finished_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
            record_job_event(session, job_id, "error", str(exc))
            session.commit()
    return True


def execute_job(session_factory: SessionFactory, job_id: str) -> None:
    with session_factory() as session:
        job = get_job_row(session, job_id)
        config = deserialize_job_config(get_job_config_row(session, job["config_id"]))
        source_model = config["source_model"]
        blueprint = config["llm_config"]
        source_connector = session.get(
            DatasourceConnector,
            int(source_model["datasource_connector_id"]),
        )
        if source_connector is None:
            raise ValueError("Configured source datasource does not exist.")

        llm_config = get_extract_llm_runtime_config(session)
        validate_suggestion_llm_config(llm_config, "extraction jobs")
        embedding_runtime = get_job_embedding_runtime(session, config["embedding_config"])

    documents = load_source_documents(source_connector, config["source_model"], blueprint)
    cases = build_case_chunks(documents, config["chunking_config"])
    total_chunks = sum(len(case["chunks"]) for case in cases)
    with session_factory() as session:
        session.execute(
            update(db.extract_jobs)
            .where(db.extract_jobs.c.id == job_id)
            .values(
                progress_total=len(cases),
                cases_total=len(cases),
                chunks_total=total_chunks,
                updated_at=utc_now(),
            )
        )
        record_job_event(
            session,
            job_id,
            "info",
            f"Loaded {len(cases)} cases and {total_chunks} chunks from source.",
        )
        session.commit()

    selected_chunks_by_case = select_chunks_for_cases(cases, blueprint, embedding_runtime)
    output_path = output_database_path(job_id)
    initialize_output_database(output_path, job_id, config, cases)
    with session_factory() as session:
        session.execute(
            update(db.extract_jobs)
            .where(db.extract_jobs.c.id == job_id)
            .values(
                output_path=str(output_path),
                updated_at=utc_now(),
            )
        )
        session.commit()

    client = OpenAICompatibleClient(
        base_url=llm_config.base_url,
        api_key=llm_config.api_key,
        timeout_seconds=llm_config.timeout_seconds,
    )
    total_items = 0
    for index, case in enumerate(cases, start=1):
        selected_chunks = selected_chunks_by_case.get(case["case_id"]) or case["chunks"]
        try:
            response = request_case_extraction(
                client,
                llm_config,
                blueprint,
                case,
                selected_chunks,
            )
            parsed = parse_extraction_response(response, blueprint)
            item_count = write_case_extraction(output_path, job_id, case, parsed, blueprint)
        except Exception as exc:
            write_case_error(output_path, job_id, case["case_id"], str(exc))
            with session_factory() as session:
                record_job_event(
                    session,
                    job_id,
                    "error",
                    f"Case {case['case_id']} failed: {exc}",
                    {"case_id": case["case_id"]},
                )
                session.commit()
            raise
        total_items += item_count
        with session_factory() as session:
            session.execute(
                update(db.extract_jobs)
                .where(db.extract_jobs.c.id == job_id)
                .values(
                    progress_current=index,
                    items_total=total_items,
                    updated_at=utc_now(),
                )
            )
            if index == len(cases) or index % 10 == 0:
                record_job_event(session, job_id, "info", f"Processed {index}/{len(cases)} cases.")
            session.commit()

    create_output_views(output_path, blueprint)
    with session_factory() as session:
        datasource = register_output_datasource(session, job_id, blueprint, output_path)
        session.execute(
            update(db.extract_jobs)
            .where(db.extract_jobs.c.id == job_id)
            .values(
                status="succeeded",
                progress_current=len(cases),
                progress_total=len(cases),
                cases_total=len(cases),
                chunks_total=total_chunks,
                items_total=total_items,
                output_path=str(output_path),
                output_datasource_id=datasource.id,
                output_datasource_key=datasource.connector_key,
                finished_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        record_job_event(
            session,
            job_id,
            "info",
            f"Job succeeded. Datasource {datasource.connector_key} was registered.",
        )
        session.commit()


def load_source_documents(
    source_connector: DatasourceConnector,
    source_model: dict[str, Any],
    blueprint: dict[str, Any],
) -> list[dict[str, Any]]:
    table_roles = source_model.get("table_roles") or {}
    scope = blueprint.get("extraction_scope") or {}
    content_tables = scope.get("content_tables") or [
        table_name
        for table_name, roles in table_roles.items()
        if roles.get("content_column")
    ]
    engine = create_engine(source_connector.database_url)
    documents: list[dict[str, Any]] = []
    try:
        with engine.connect() as connection:
            quote_source_identifier = connection.dialect.identifier_preparer.quote
            for table_name in content_tables:
                roles = table_roles.get(table_name) or {}
                case_id_column = roles.get("case_id_column")
                content_column = roles.get("content_column")
                if not case_id_column or not content_column:
                    continue
                sql = build_source_content_query(
                    quote_source_identifier,
                    table_name=table_name,
                    case_id_column=case_id_column,
                    content_column=content_column,
                )
                rows = connection.execute(text(sql)).mappings().all()
                for row_index, row in enumerate(rows, start=1):
                    content = str(row["content"] or "").strip()
                    if not content:
                        continue
                    case_id = str(row["case_id"])
                    documents.append(
                        {
                            "document_id": f"{table_name}:{row_index}",
                            "case_id": case_id,
                            "source_table": table_name,
                            "source_row_index": row_index,
                            "content": content,
                        }
                    )
    finally:
        engine.dispose()
    if not documents:
        raise ValueError("No source content rows were found for Extract.")
    return documents


def build_source_content_query(
    quote_identifier: Callable[[str], str],
    *,
    table_name: str,
    case_id_column: str,
    content_column: str,
) -> str:
    return (
        f"SELECT {quote_identifier(case_id_column)} AS case_id, "
        f"{quote_identifier(content_column)} AS content "
        f"FROM {quote_identifier(table_name)} "
        f"WHERE {quote_identifier(content_column)} IS NOT NULL"
    )


def build_case_chunks(
    documents: list[dict[str, Any]],
    chunking_config: dict[str, Any],
) -> list[dict[str, Any]]:
    mode = validate_chunking_mode(chunking_config.get("mode") or DEFAULT_CHUNKING_MODE)
    case_map: dict[str, dict[str, Any]] = {}
    for document in documents:
        case = case_map.setdefault(
            document["case_id"],
            {
                "case_id": document["case_id"],
                "documents": [],
                "chunks": [],
            },
        )
        case["documents"].append(document)
        for chunk_index, chunk_text in enumerate(chunk_text_value(document["content"], mode), start=1):
            chunk = {
                "chunk_id": f"{document['document_id']}:{chunk_index}",
                "document_id": document["document_id"],
                "case_id": document["case_id"],
                "source_table": document["source_table"],
                "chunk_index": chunk_index,
                "text": chunk_text,
            }
            case["chunks"].append(chunk)
    return list(case_map.values())


def chunk_text_value(value: str, mode: str) -> list[str]:
    text_value = str(value or "").strip()
    if not text_value:
        return []
    if mode == "none":
        return [text_value]
    if mode == "semantic":
        return semantic_chunks(text_value)
    return fixed_size_chunks(text_value)


def fixed_size_chunks(value: str) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(value):
        end = min(len(value), start + FIXED_CHUNK_SIZE)
        chunks.append(value[start:end].strip())
        if end >= len(value):
            break
        start = max(0, end - FIXED_CHUNK_OVERLAP)
    return [chunk for chunk in chunks if chunk]


def semantic_chunks(value: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", value) if part.strip()]
    if not paragraphs:
        return fixed_size_chunks(value)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if not current:
            current = paragraph
            continue
        if len(current) + len(paragraph) + 2 <= SEMANTIC_CHUNK_TARGET_SIZE:
            current = f"{current}\n\n{paragraph}"
        else:
            chunks.extend(fixed_size_chunks(current))
            current = paragraph
    if current:
        chunks.extend(fixed_size_chunks(current))
    return chunks


def select_chunks_for_cases(
    cases: list[dict[str, Any]],
    blueprint: dict[str, Any],
    embedding_runtime: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    scope = blueprint.get("extraction_scope") or {}
    use_embeddings = bool(scope.get("use_embeddings")) or scope.get("chunk_selection") == "embedding_neighbors"
    max_chunks_per_case = scope.get("max_chunks_per_case")
    if not use_embeddings:
        return {
            case["case_id"]: limit_chunks(case["chunks"], max_chunks_per_case)
            for case in cases
        }
    if embedding_runtime is None:
        raise ValueError("Embedding configuration is required for embedding chunk selection.")

    key_texts = [
        f"{item.get('key')}: {item.get('description') or item.get('kind')}"
        for item in blueprint.get("information_types") or []
    ]
    key_vectors = request_embeddings(embedding_runtime, key_texts)
    min_similarity = float(scope.get("min_similarity") or 0.75)
    neighbor_count = int(scope.get("max_neighbor_chunks") or 0)
    selected_by_case: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        chunks = case["chunks"]
        chunk_vectors = request_embeddings(embedding_runtime, [chunk["text"] for chunk in chunks])
        selected_indexes: set[int] = set()
        for chunk_index, chunk_vector in enumerate(chunk_vectors):
            best = max((cosine_similarity(chunk_vector, key_vector) for key_vector in key_vectors), default=0.0)
            if best >= min_similarity:
                for offset in range(-neighbor_count, neighbor_count + 1):
                    neighbor_index = chunk_index + offset
                    if 0 <= neighbor_index < len(chunks):
                        selected_indexes.add(neighbor_index)
        selected_chunks = [chunks[index] for index in sorted(selected_indexes)] or chunks[:1]
        selected_by_case[case["case_id"]] = limit_chunks(selected_chunks, max_chunks_per_case)
    return selected_by_case


def limit_chunks(chunks: list[dict[str, Any]], max_chunks: Any) -> list[dict[str, Any]]:
    if max_chunks in (None, ""):
        return chunks
    return chunks[: int(max_chunks)]


def get_runtime_embedding_config(session: Session) -> dict[str, Any] | None:
    row = get_raw_embedding_config(session)
    if row is None:
        return None
    config = dict(row)
    config["extra_body"] = json_loads(config.get("extra_body_json"), {})
    return config


def get_job_embedding_runtime(
    session: Session,
    embedding_config: dict[str, Any],
) -> dict[str, Any] | None:
    if not embedding_config.get("enabled"):
        return None
    extra_body = embedding_config.get("extra_body")
    if not isinstance(extra_body, dict):
        extra_body = json_loads(embedding_config.get("extra_body_json"), {})
    if not isinstance(extra_body, dict):
        extra_body = {}
    return {
        "provider": embedding_config.get("provider") or "openai-compatible",
        "base_url": embedding_config.get("base_url") or DEFAULT_EMBEDDING_CONFIG["base_url"],
        "api_key": get_embedding_api_key(session),
        "model": embedding_config.get("model") or DEFAULT_EMBEDDING_CONFIG["model"],
        "timeout_seconds": embedding_config.get("timeout_seconds")
        or DEFAULT_EMBEDDING_CONFIG["timeout_seconds"],
        "extra_body": extra_body,
    }


def get_embedding_api_key(session: Session) -> str:
    runtime = get_runtime_embedding_config(session)
    if not runtime:
        return ""
    api_key = str(runtime.get("api_key") or "")
    return "" if api_key == DEFAULT_EMBEDDING_CONFIG["api_key"] else api_key


def request_embeddings(config: dict[str, Any], texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    api_key_value = str(config.get("api_key") or "")
    if not api_key_value or api_key_value == DEFAULT_EMBEDDING_CONFIG["api_key"]:
        raise ValueError("Embedding API key is required for embedding chunk selection.")
    payload = {
        "model": config["model"],
        "input": texts,
    }
    payload.update(config.get("extra_body") or {})
    response = httpx.post(
        f"{str(config['base_url']).rstrip('/')}/embeddings",
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key_value}",
            "Content-Type": "application/json",
        },
        timeout=int(config.get("timeout_seconds") or 60),
    )
    if response.status_code >= 400:
        detail = response.text.strip()
        detail_suffix = f" {detail[:500]}" if detail else ""
        raise ValueError(f"Embedding provider returned HTTP {response.status_code}.{detail_suffix}")
    data = response.json()
    values = data.get("data") if isinstance(data, dict) else None
    if not isinstance(values, list) or len(values) != len(texts):
        raise ValueError("Embedding provider returned invalid embedding response.")
    vectors = []
    for item in values:
        embedding = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(embedding, list) or not embedding:
            raise ValueError("Embedding provider returned invalid embedding vector.")
        vectors.append([float(value) for value in embedding])
    return vectors


def cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def request_case_extraction(
    client: OpenAICompatibleClient,
    llm_config: admin_services.LlmRuntimeConfig,
    blueprint: dict[str, Any],
    case: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> str:
    budgets = [
        DEFAULT_EXTRACTION_PROMPT_CHAR_BUDGET,
        *([RETRY_EXTRACTION_PROMPT_CHAR_BUDGET] * LLM_TIMEOUT_RETRY_COUNT),
    ]
    last_error: LlmProviderError | None = None
    for attempt, prompt_char_budget in enumerate(budgets, start=1):
        fitted_chunks, prompt_chars = fit_chunks_to_prompt_budget(
            blueprint,
            case,
            chunks,
            prompt_char_budget,
        )
        try:
            response = client.create_chat_completion(
                ChatCompletionRequest(
                    model=llm_config.model,
                    temperature=0.0,
                    extra_body=llm_config.extra_body,
                    messages=[
                        ChatMessage(role="system", content=build_extraction_system_prompt()),
                        ChatMessage(
                            role="user",
                            content=build_extraction_user_prompt(blueprint, case, fitted_chunks),
                        ),
                    ],
                )
            )
            return response.content
        except LlmProviderError as exc:
            last_error = exc
            if attempt < len(budgets) and is_llm_timeout_error(exc):
                continue
            raise ValueError(
                describe_llm_provider_error(
                    exc,
                    llm_config=llm_config,
                    case=case,
                    chunks=fitted_chunks,
                    available_chunks=len(chunks),
                    prompt_chars=prompt_chars,
                    prompt_char_budget=prompt_char_budget,
                    attempt=attempt,
                    max_attempts=len(budgets),
                )
            ) from exc
    if last_error is not None:
        raise ValueError(str(last_error)) from last_error
    raise ValueError("LLM extraction request was not attempted.")


def is_llm_timeout_error(exc: LlmProviderError) -> bool:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, httpx.TimeoutException):
            return True
        class_name = cause.__class__.__name__.lower()
        message = str(cause).lower()
        if "timeout" in class_name or "timed out" in message or "timeout" in message:
            return True
        cause = cause.__cause__
    return False


def fit_chunks_to_prompt_budget(
    blueprint: dict[str, Any],
    case: dict[str, Any],
    chunks: list[dict[str, Any]],
    budget: int,
) -> tuple[list[dict[str, Any]], int]:
    fitted: list[dict[str, Any]] = []
    prompt_chars = len(build_extraction_user_prompt(blueprint, case, fitted))
    for chunk in chunks:
        candidate = [*fitted, chunk]
        candidate_chars = len(build_extraction_user_prompt(blueprint, case, candidate))
        if candidate_chars <= budget:
            fitted = candidate
            prompt_chars = candidate_chars
            continue

        truncated = truncate_chunk_to_fit_prompt_budget(blueprint, case, fitted, chunk, budget)
        if truncated is not None:
            fitted = [*fitted, truncated]
            prompt_chars = len(build_extraction_user_prompt(blueprint, case, fitted))
        break

    if not fitted and chunks:
        first_chunk = truncate_chunk_to_fit_prompt_budget(blueprint, case, [], chunks[0], budget)
        if first_chunk is not None:
            fitted = [first_chunk]
            prompt_chars = len(build_extraction_user_prompt(blueprint, case, fitted))
    return fitted, prompt_chars


def truncate_chunk_to_fit_prompt_budget(
    blueprint: dict[str, Any],
    case: dict[str, Any],
    existing_chunks: list[dict[str, Any]],
    chunk: dict[str, Any],
    budget: int,
) -> dict[str, Any] | None:
    text_value = str(chunk.get("text") or "")
    if len(text_value) <= MIN_TRUNCATED_CHUNK_CHARS:
        return None

    low = MIN_TRUNCATED_CHUNK_CHARS
    high = len(text_value)
    best: dict[str, Any] | None = None
    while low <= high:
        size = (low + high) // 2
        candidate = {
            **chunk,
            "text": text_value[:size].rstrip() + "\n[TRUNCATED_TO_FIT_LLM_CONTEXT]",
        }
        prompt_chars = len(build_extraction_user_prompt(blueprint, case, [*existing_chunks, candidate]))
        if prompt_chars <= budget:
            best = candidate
            low = size + 1
        else:
            high = size - 1
    return best


def describe_llm_provider_error(
    exc: LlmProviderError,
    *,
    llm_config: admin_services.LlmRuntimeConfig,
    case: dict[str, Any],
    chunks: list[dict[str, Any]],
    available_chunks: int,
    prompt_chars: int,
    prompt_char_budget: int,
    attempt: int,
    max_attempts: int,
) -> str:
    cause = exc.__cause__
    details = str(exc)
    if details == "LLM provider request failed." and cause is not None:
        cause_message = str(cause).strip()
        details = (
            "LLM provider request failed"
            f" ({cause.__class__.__name__}"
            f"{': ' + cause_message if cause_message else ''})."
        )
    return (
        f"{details} "
        f"Case={case.get('case_id')}; "
        f"model={llm_config.model}; "
        f"base_url={llm_config.base_url}; "
        f"timeout={llm_config.timeout_seconds}s; "
        f"chunks_sent={len(chunks)}/{available_chunks}; "
        f"prompt_chars={prompt_chars}/{prompt_char_budget}; "
        f"attempt={attempt}/{max_attempts}."
    )


def build_extraction_system_prompt() -> str:
    return """You are GAARD Extract.

Extract only facts supported by the provided chunks.
Return JSON only. Do not use markdown.
Every extracted item must include type, attributes, evidence_text, and confidence.
Use exactly the configured type keys and attribute field names.
The attributes object may contain only configured business fields, never type, evidence_text, confidence, source, chunk_id, or review fields.
Do not add unconfigured attribute fields. Put uncertainty or comments into warnings, not attributes.
For enum fields, use exactly one allowed enum value from the schema. Do not invent labels or expanded variants.
If evidence is missing or uncertain, omit the item or add a warning."""


def build_extraction_user_prompt(
    blueprint: dict[str, Any],
    case: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> str:
    payload = {
        "blueprint": {
            "blueprint_key": blueprint.get("blueprint_key"),
            "domain_description": blueprint.get("domain_description"),
            "case_grain_description": blueprint.get("case_grain_description"),
            "language": blueprint.get("language"),
            "information_types": blueprint.get("information_types") or [],
            "global_rules": blueprint.get("global_rules") or [],
            "json_schema": blueprint.get("json_schema"),
        },
        "case": {
            "case_id": case["case_id"],
            "chunks": [
                {
                    "chunk_id": chunk["chunk_id"],
                    "source_table": chunk["source_table"],
                    "text": chunk["text"],
                }
                for chunk in chunks
            ],
        },
    }
    return (
        "Extract configured data from this case.\n\n"
        f"Input JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}"
    )


def parse_extraction_response(value: str, blueprint: dict[str, Any]) -> dict[str, Any]:
    cleaned = strip_llm_json_fence(value)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM returned non-JSON extraction output.") from exc
    if not isinstance(payload, dict):
        raise ValueError("LLM returned invalid extraction output.")

    type_by_key = {
        item["key"]: item
        for item in normalize_information_types(blueprint.get("information_types") or [])
    }
    parsed: dict[str, Any] = {"items": [], "warnings": []}
    for kind, array_key in (
        ("entity", "entities"),
        ("event", "events"),
        ("fact", "facts"),
        ("relation", "relations"),
    ):
        values = payload.get(array_key) or []
        if not isinstance(values, list):
            raise ValueError(f"LLM extraction output field '{array_key}' must be a list.")
        for value in values:
            if not isinstance(value, dict):
                raise ValueError(f"LLM extraction output field '{array_key}' contains invalid item.")
            type_key = str(value.get("type") or "").strip()
            definition = type_by_key.get(type_key)
            if definition is None:
                raise ValueError(f"LLM returned unknown extraction type '{type_key}'.")
            if definition["kind"] != kind:
                raise ValueError(
                    f"LLM returned extraction type '{type_key}' in the wrong output collection."
                )
            attributes = value.get("attributes") or {}
            if not isinstance(attributes, dict):
                raise ValueError(f"LLM returned invalid attributes for '{type_key}'.")
            attributes = normalize_extraction_attributes(attributes)
            reserved_evidence_text = attributes.pop("_reserved_evidence_text", "")
            reserved_confidence = attributes.pop("_reserved_confidence", None)
            evidence_value = value.get("evidence_text")
            if not evidence_value:
                evidence_value = reserved_evidence_text
            confidence_value = value.get("confidence")
            if confidence_value in (None, ""):
                confidence_value = reserved_confidence
            evidence_text = str(evidence_value or "").strip()
            if not evidence_text:
                raise ValueError(f"LLM returned empty evidence_text for '{type_key}'.")
            parsed["items"].append(
                {
                    "kind": definition["kind"] or kind,
                    "type": type_key,
                    "attributes": validate_item_attributes(
                        definition,
                        attributes,
                        parsed["warnings"],
                    ),
                    "evidence_text": evidence_text,
                    "confidence": coerce_confidence(confidence_value),
                }
            )
    warnings = payload.get("warnings") or []
    if isinstance(warnings, list):
        parsed["warnings"].extend(
            str(warning) for warning in warnings if str(warning).strip()
        )
    return parsed


def coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def normalize_extraction_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(attributes)
    reserved_confidence = normalized.pop("confidence", None)
    reserved_evidence_text = normalized.pop("evidence_text", None)
    if reserved_evidence_text in (None, ""):
        reserved_evidence_text = normalized.pop("evidence", None)
    for name in RESERVED_EXTRACTION_ATTRIBUTE_NAMES:
        normalized.pop(name, None)
    if reserved_confidence not in (None, ""):
        normalized["_reserved_confidence"] = reserved_confidence
    if reserved_evidence_text not in (None, ""):
        normalized["_reserved_evidence_text"] = reserved_evidence_text
    return normalized


def validate_item_attributes(
    definition: dict[str, Any],
    attributes: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    fields = {field["name"]: field for field in definition.get("fields") or []}
    unknown_fields = sorted(set(attributes) - set(fields))
    for unknown_field in unknown_fields:
        if warnings is not None:
            warnings.append(
                f"LLM returned unknown attribute '{unknown_field}' for "
                f"'{definition['key']}'; ignored value "
                f"{json_dumps(attributes.get(unknown_field))}."
            )

    normalized: dict[str, Any] = {}
    for field_name, field in fields.items():
        value = attributes.get(field_name)
        if field.get("required") and value in (None, ""):
            raise ValueError(f"LLM omitted required attribute '{field_name}'.")
        if value in (None, "") and not field.get("required"):
            continue
        normalized[field_name] = validate_attribute_value(
            definition["key"],
            field,
            value,
            warnings,
        )
    return normalized


def validate_attribute_value(
    type_key: str,
    field: dict[str, Any],
    value: Any,
    warnings: list[str] | None = None,
) -> Any:
    field_name = field["name"]
    field_type = field["type"]
    if field_type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"Attribute '{field_name}' for '{type_key}' must be a number.")
        return value
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"Attribute '{field_name}' for '{type_key}' must be boolean.")
        return value
    if field_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"Attribute '{field_name}' for '{type_key}' must be an array.")
        return value
    if field_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"Attribute '{field_name}' for '{type_key}' must be an object.")
        return value
    if field_type in {"string", "date", "datetime", "enum"}:
        if not isinstance(value, str):
            raise ValueError(f"Attribute '{field_name}' for '{type_key}' must be a string.")
        if field_type == "enum":
            return normalize_enum_attribute_value(type_key, field, value, warnings)
        return value
    return value


def normalize_enum_attribute_value(
    type_key: str,
    field: dict[str, Any],
    value: str,
    warnings: list[str] | None = None,
) -> str:
    field_name = field["name"]
    allowed_values = [str(item) for item in field.get("values", [])]
    if value in allowed_values:
        return value

    value_lower = value.lower()
    for allowed_value in allowed_values:
        if allowed_value.lower() == value_lower:
            if warnings is not None:
                warnings.append(
                    f"Enum attribute '{field_name}' for '{type_key}' was normalized "
                    f"from {json_dumps(value)} to {json_dumps(allowed_value)}."
                )
            return allowed_value

    fallback = next((item for item in allowed_values if item.lower() == "other"), None)
    if fallback is not None:
        if warnings is not None:
            warnings.append(
                f"Enum attribute '{field_name}' for '{type_key}' got unsupported value "
                f"{json_dumps(value)}; stored {json_dumps(fallback)}. "
                f"Allowed values: {json_dumps(allowed_values)}."
            )
        return fallback

    raise ValueError(
        f"Attribute '{field_name}' for '{type_key}' must match enum values. "
        f"Got {json_dumps(value)} ({value.__class__.__name__}); "
        f"allowed values: {json_dumps(allowed_values)}."
    )


def output_database_path(job_id: str) -> Path:
    root = Path(os.environ.get("GAARD_EXTRACT_OUTPUT_DIR") or "./gaard-extract-runs")
    return (root / job_id / "extract.db").resolve()


def initialize_output_database(
    output_path: Path,
    job_id: str,
    config: dict[str, Any],
    cases: list[dict[str, Any]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(output_path)
    try:
        create_output_schema(connection)
        blueprint = config["llm_config"]
        connection.execute(
            "INSERT INTO extract_runs (run_id, job_id, blueprint_key, created_at, config_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                job_id,
                job_id,
                blueprint.get("blueprint_key") or "",
                utc_now().isoformat(),
                json_dumps(config),
            ),
        )
        for case in cases:
            connection.execute(
                "INSERT INTO extract_cases (case_id, run_id) VALUES (?, ?)",
                (case["case_id"], job_id),
            )
            for document in case["documents"]:
                connection.execute(
                    "INSERT INTO extract_documents "
                    "(document_id, case_id, run_id, source_table, source_row_index, content) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        document["document_id"],
                        case["case_id"],
                        job_id,
                        document["source_table"],
                        document["source_row_index"],
                        document["content"],
                    ),
                )
            for chunk in case["chunks"]:
                connection.execute(
                    "INSERT INTO extract_chunks "
                    "(chunk_id, document_id, case_id, run_id, source_table, chunk_index, text) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk["chunk_id"],
                        chunk["document_id"],
                        case["case_id"],
                        job_id,
                        chunk["source_table"],
                        chunk["chunk_index"],
                        chunk["text"],
                    ),
                )
        connection.commit()
    finally:
        connection.close()


def create_output_schema(connection: sqlite3.Connection) -> None:
    for table_name in OUTPUT_CANONICAL_TABLES:
        connection.execute(f"DROP TABLE IF EXISTS {table_name}")
    connection.executescript(
        """
        CREATE TABLE extract_runs (
            run_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            blueprint_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            config_json TEXT NOT NULL
        );
        CREATE TABLE extract_cases (
            case_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            PRIMARY KEY (case_id, run_id)
        );
        CREATE TABLE extract_documents (
            document_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_row_index INTEGER NOT NULL,
            content TEXT NOT NULL
        );
        CREATE TABLE extract_chunks (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            source_table TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE extract_items (
            item_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            type TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_text TEXT NOT NULL,
            needs_review INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE extract_attributes (
            item_id TEXT NOT NULL,
            name TEXT NOT NULL,
            value_json TEXT NOT NULL
        );
        CREATE TABLE extract_warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            warning TEXT NOT NULL
        );
        CREATE TABLE extract_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            case_id TEXT,
            error TEXT NOT NULL
        );
        """
    )


def write_case_extraction(
    output_path: Path,
    job_id: str,
    case: dict[str, Any],
    parsed: dict[str, Any],
    blueprint: dict[str, Any],
) -> int:
    policy = {**DEFAULT_REVIEW_POLICY, **(blueprint.get("review_policy") or {})}
    connection = sqlite3.connect(output_path)
    try:
        for warning in parsed.get("warnings") or []:
            connection.execute(
                "INSERT INTO extract_warnings (run_id, case_id, warning) VALUES (?, ?, ?)",
                (job_id, case["case_id"], warning),
            )
        item_count = 0
        for item in parsed.get("items") or []:
            item_id = str(uuid4())
            confidence = float(item["confidence"])
            connection.execute(
                "INSERT INTO extract_items "
                "(item_id, run_id, case_id, kind, type, confidence, evidence_text, needs_review) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    job_id,
                    case["case_id"],
                    item["kind"],
                    item["type"],
                    confidence,
                    item["evidence_text"],
                    1 if confidence < float(policy["needs_review_threshold"]) else 0,
                ),
            )
            for name, value in (item.get("attributes") or {}).items():
                connection.execute(
                    "INSERT INTO extract_attributes (item_id, name, value_json) VALUES (?, ?, ?)",
                    (item_id, str(name), json_dumps(value)),
                )
            item_count += 1
        connection.commit()
        return item_count
    finally:
        connection.close()


def write_case_error(output_path: Path, job_id: str, case_id: str, error: str) -> None:
    connection = sqlite3.connect(output_path)
    try:
        connection.execute(
            "INSERT INTO extract_errors (run_id, case_id, error) VALUES (?, ?, ?)",
            (job_id, case_id, error),
        )
        connection.commit()
    finally:
        connection.close()


def create_output_views(output_path: Path, blueprint: dict[str, Any]) -> None:
    connection = sqlite3.connect(output_path)
    try:
        for info_type in blueprint.get("information_types") or []:
            key = normalize_identifier(info_type.get("key"), "Information type key")
            view_name = quote_sql_identifier(f"v_extract_{key}")
            connection.execute(f"DROP VIEW IF EXISTS {view_name}")
            field_selects = []
            for field in info_type.get("fields") or []:
                field_name = normalize_identifier(field.get("name"), "Field name")
                field_selects.append(
                    "MAX(CASE WHEN a.name = "
                    + sql_literal(field_name)
                    + " THEN json_extract(a.value_json, '$') END) AS "
                    + quote_sql_identifier(field_name)
                )
            field_sql = ",\n  " + ",\n  ".join(field_selects) if field_selects else ""
            connection.execute(
                f"""
                CREATE VIEW {view_name} AS
                SELECT
                  i.run_id,
                  i.case_id,
                  i.item_id,
                  i.confidence,
                  i.evidence_text,
                  i.needs_review{field_sql}
                FROM extract_items i
                LEFT JOIN extract_attributes a ON a.item_id = i.item_id
                WHERE i.type = {sql_literal(key)}
                GROUP BY i.run_id, i.case_id, i.item_id, i.confidence, i.evidence_text, i.needs_review
                """
            )
        connection.commit()
    finally:
        connection.close()


def register_output_datasource(
    session: Session,
    job_id: str,
    blueprint: dict[str, Any],
    output_path: Path,
) -> DatasourceConnector:
    blueprint_key = str(blueprint.get("blueprint_key") or "extract").replace("_", "-")
    connector_key = f"extract-{blueprint_key}-{job_id[:8]}".lower()
    connector = DatasourceConnector(
        connector_key=connector_key,
        name=f"Extract {blueprint.get('name') or blueprint_key} {job_id[:8]}",
        database_type="sqlite",
        database_url=f"sqlite:///{output_path.as_posix()}",
        sql_dialect="sqlite",
        active=False,
        updated_by="gaard-extract",
    )
    session.add(connector)
    session.flush()
    admin_services.introspect_datasource_connector(session, connector, "gaard-extract")
    return connector


def quote_sql_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def suggest_key_description(
    session: Session,
    *,
    key: str,
    kind: str,
    domain_description: str,
    case_grain_description: str,
    language: str,
) -> dict[str, Any]:
    normalized_key = normalize_identifier(key, "Key")
    normalized_kind = str(kind or "").strip()
    if normalized_kind not in INFORMATION_KINDS:
        raise ValueError(
            "Kind must be one of: " + ", ".join(sorted(INFORMATION_KINDS)) + "."
        )

    llm_config = get_extract_llm_runtime_config(session)
    validate_suggestion_llm_config(llm_config, "description suggestions")

    client = OpenAICompatibleClient(
        base_url=llm_config.base_url,
        api_key=llm_config.api_key,
        timeout_seconds=llm_config.timeout_seconds,
    )
    system_prompt, user_prompt = build_key_description_prompt(
        key=normalized_key,
        kind=normalized_kind,
        domain_description=domain_description,
        case_grain_description=case_grain_description,
        language=language,
    )

    try:
        response = client.create_chat_completion(
            ChatCompletionRequest(
                model=llm_config.model,
                temperature=0.2,
                extra_body=llm_config.extra_body,
                messages=[
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=user_prompt),
                ],
            )
        )
    except LlmProviderError as exc:
        raise ValueError(str(exc)) from exc

    description = parse_key_description_suggestion(response.content)
    if not description:
        raise ValueError("LLM returned an empty description suggestion.")

    return {
        "description": description,
        "model": response.model or llm_config.model,
    }


def suggest_key_fields(
    session: Session,
    *,
    key: str,
    kind: str,
    description: str,
    domain_description: str,
    case_grain_description: str,
    language: str,
) -> dict[str, Any]:
    normalized_key = normalize_identifier(key, "Key")
    normalized_kind = str(kind or "").strip()
    if normalized_kind not in INFORMATION_KINDS:
        raise ValueError(
            "Kind must be one of: " + ", ".join(sorted(INFORMATION_KINDS)) + "."
        )

    llm_config = get_extract_llm_runtime_config(session)
    validate_suggestion_llm_config(llm_config, "field suggestions")

    client = OpenAICompatibleClient(
        base_url=llm_config.base_url,
        api_key=llm_config.api_key,
        timeout_seconds=llm_config.timeout_seconds,
    )
    system_prompt, user_prompt = build_key_fields_prompt(
        key=normalized_key,
        kind=normalized_kind,
        description=description,
        domain_description=domain_description,
        case_grain_description=case_grain_description,
        language=language,
    )

    try:
        response = client.create_chat_completion(
            ChatCompletionRequest(
                model=llm_config.model,
                temperature=0.2,
                extra_body=llm_config.extra_body,
                messages=[
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=user_prompt),
                ],
            )
        )
    except LlmProviderError as exc:
        raise ValueError(str(exc)) from exc

    fields = parse_key_fields_suggestion(response.content, normalized_key)
    if not fields:
        raise ValueError("LLM returned no field suggestions.")

    return {
        "fields": fields,
        "model": response.model or llm_config.model,
    }


def get_extract_llm_runtime_config(session: Session) -> admin_services.LlmRuntimeConfig:
    try:
        return admin_services.get_llm_runtime_config(session)
    except SQLAlchemyError:
        return admin_services.LlmRuntimeConfig(
            provider=gaard_settings.gaard_llm_provider,
            base_url=gaard_settings.gaard_llm_base_url,
            api_key=gaard_settings.gaard_llm_api_key,
            model=gaard_settings.gaard_llm_model,
            extra_body=gaard_settings.gaard_llm_extra_body,
            timeout_seconds=gaard_settings.gaard_llm_timeout_seconds,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"LLM configuration could not be loaded: {exc}") from exc


def validate_suggestion_llm_config(
    config: admin_services.LlmRuntimeConfig,
    feature_label: str,
) -> None:
    if config.provider != "openai-compatible":
        raise ValueError(
            f"Unsupported LLM provider for {feature_label}: {config.provider}."
        )
    if not config.base_url:
        raise ValueError(f"LLM base URL is not configured for {feature_label}.")
    if not config.model:
        raise ValueError(f"LLM model is not configured for {feature_label}.")
    if not config.api_key or config.api_key == "change-me":
        raise ValueError(f"LLM API key is not configured for {feature_label}.")


def build_key_description_prompt(
    *,
    key: str,
    kind: str,
    domain_description: str,
    case_grain_description: str,
    language: str,
) -> tuple[str, str]:
    output_language = str(language or "pl").strip() or "pl"
    context = {
        "key": key,
        "kind": kind,
        "kind_meaning": INFORMATION_KIND_GUIDANCE[kind],
        "domain_description": str(domain_description or "").strip(),
        "case_grain_description": str(case_grain_description or "").strip(),
        "output_language": output_language,
    }

    system_prompt = """You help configure GAARD Extract blueprints.

Suggest a model-facing description for one extraction key.
The description will be used by an LLM during extraction, so it must tell the model what to extract, when to emit the item, and what boundaries to respect.
Do not invent fields. Do not describe UI behavior. Keep it concise and operational.
Return JSON only in this exact shape:
{"description":"one concise description"}"""

    user_prompt = (
        "Suggest a description for this extraction key.\n\n"
        f"Input JSON:\n{json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)}"
    )
    return system_prompt, user_prompt


def parse_key_description_suggestion(value: str) -> str:
    cleaned = strip_llm_json_fence(value)

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return cleaned.strip().strip('"').strip()

    if isinstance(payload, dict):
        return str(payload.get("description") or "").strip()
    if isinstance(payload, str):
        return payload.strip()
    return ""


def build_key_fields_prompt(
    *,
    key: str,
    kind: str,
    description: str,
    domain_description: str,
    case_grain_description: str,
    language: str,
) -> tuple[str, str]:
    output_language = str(language or "pl").strip() or "pl"
    context = {
        "key": key,
        "kind": kind,
        "kind_meaning": INFORMATION_KIND_GUIDANCE[kind],
        "key_description": str(description or "").strip(),
        "domain_description": str(domain_description or "").strip(),
        "case_grain_description": str(case_grain_description or "").strip(),
        "allowed_field_types": sorted(FIELD_TYPES),
        "output_language": output_language,
    }

    system_prompt = """You help configure GAARD Extract blueprints.

Suggest attribute fields for one extraction key.
Return only fields that should live under the key attributes object.
Do not include system fields such as type, evidence_text, confidence, source, chunk_id, or review flags.
Use short snake_case field names.
Use only these field types: string, number, date, datetime, boolean, enum, array, object.
Use enum only when you can propose a small stable value list.
Mark a field required only when the extracted item is not useful without it.
Prefer 3 to 7 useful fields.
Return JSON only in this exact shape:
{"fields":[{"name":"field_name","type":"string","required":false,"values":[]}]}"""

    user_prompt = (
        "Suggest fields for this extraction key.\n\n"
        f"Input JSON:\n{json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)}"
    )
    return system_prompt, user_prompt


def parse_key_fields_suggestion(value: str, key: str) -> list[dict[str, Any]]:
    cleaned = strip_llm_json_fence(value)

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM returned non-JSON field suggestions.") from exc

    raw_fields = payload.get("fields") if isinstance(payload, dict) else payload
    if not isinstance(raw_fields, list):
        raise ValueError("LLM returned invalid field suggestions.")

    return normalize_fields(key, coerce_suggested_fields(raw_fields))


def coerce_suggested_fields(values: list[Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        field_type = str(value.get("type") or "string").strip().lower()
        field_type = FIELD_TYPE_ALIASES.get(field_type, field_type)
        enum_values = value.get("values", value.get("enum", []))
        fields.append(
            {
                "name": str(value.get("name") or value.get("key") or "").strip(),
                "type": field_type,
                "required": bool(value.get("required")),
                "values": enum_values,
            }
        )
    return fields


def strip_llm_json_fence(value: str) -> str:
    cleaned = remove_thinking_blocks(value).strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()
    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()

    return cleaned


def default_llm_extracting_config(session: Session) -> dict[str, Any]:
    information_types = [
        {
            "key": "case_status",
            "kind": "fact",
            "description": "Status sprawy wynikający z notatki.",
            "fields": [
                {
                    "name": "status",
                    "type": "string",
                    "required": True,
                    "values": [],
                },
                {
                    "name": "valid_from",
                    "type": "date",
                    "required": False,
                    "values": [],
                },
            ],
        }
    ]
    embedding_config = get_embedding_config(session)
    extraction_scope = {
        **DEFAULT_EXTRACTION_SCOPE,
        "content_tables": active_content_tables(session),
        "use_embeddings": bool(embedding_config.get("enabled")),
        "chunk_selection": "embedding_neighbors"
        if embedding_config.get("enabled")
        else "all_chunks",
    }
    return {
        "id": None,
        "blueprint_key": "generic_case_notes",
        "name": "Generic Case Notes",
        "description": "",
        "domain_description": "",
        "case_grain_description": "Jeden case reprezentuje jedną sprawę. Dokumenty i chunki są powiązane przez skonfigurowane case_id.",
        "language": "pl",
        "status": "draft",
        "information_types": information_types,
        "global_rules": DEFAULT_GLOBAL_RULES,
        "review_policy": DEFAULT_REVIEW_POLICY,
        "extraction_scope": extraction_scope,
        "json_schema": build_extraction_json_schema(information_types),
        "json_schema_json": json_dumps_pretty(build_extraction_json_schema(information_types)),
        "persisted": False,
    }


def normalize_blueprint_status(status: str) -> str:
    normalized = str(status or "draft").strip()
    if normalized not in BLUEPRINT_STATUSES:
        raise ValueError(
            "Blueprint status must be one of: "
            + ", ".join(sorted(BLUEPRINT_STATUSES))
            + "."
        )
    return normalized


def normalize_information_types(values: list[Any]) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ValueError("Information types must be a list.")
    if not values:
        raise ValueError("At least one information type is required.")

    normalized: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, value in enumerate(values, start=1):
        if not isinstance(value, dict):
            raise ValueError(f"Information type #{index} must be an object.")
        key = normalize_identifier(value.get("key"), f"Information type #{index} key")
        if key in seen_keys:
            raise ValueError(f"Information type key '{key}' is duplicated.")
        seen_keys.add(key)

        kind = str(value.get("kind") or "").strip()
        if kind not in INFORMATION_KINDS:
            raise ValueError(
                f"Information type '{key}' kind must be one of: "
                + ", ".join(sorted(INFORMATION_KINDS))
                + "."
            )

        normalized.append(
            {
                "key": key,
                "kind": kind,
                "description": str(value.get("description") or "").strip(),
                "fields": normalize_fields(key, value.get("fields") or []),
            }
        )

    return normalized


def normalize_fields(type_key: str, values: list[Any]) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ValueError(f"Fields for '{type_key}' must be a list.")

    normalized: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, value in enumerate(values, start=1):
        if not isinstance(value, dict):
            raise ValueError(f"Field #{index} for '{type_key}' must be an object.")
        name = normalize_identifier(value.get("name"), f"Field #{index} for '{type_key}'")
        if name in seen_names:
            raise ValueError(f"Field '{name}' is duplicated in '{type_key}'.")
        seen_names.add(name)

        field_type = str(value.get("type") or "").strip()
        if field_type not in FIELD_TYPES:
            raise ValueError(
                f"Field '{name}' type must be one of: "
                + ", ".join(sorted(FIELD_TYPES))
                + "."
            )

        enum_values = normalize_enum_values(value.get("values") or [])
        if field_type == "enum" and not enum_values:
            raise ValueError(f"Enum field '{name}' must define at least one value.")

        normalized.append(
            {
                "name": name,
                "type": field_type,
                "required": bool(value.get("required")),
                "values": enum_values,
            }
        )

    return normalized


def normalize_enum_values(values: Any) -> list[str]:
    if isinstance(values, str):
        raw_values = values.split(",")
    elif isinstance(values, list):
        raw_values = values
    else:
        raise ValueError("Enum values must be a list or comma-separated string.")

    normalized: list[str] = []
    for value in raw_values:
        normalized_value = str(value or "").strip()
        if normalized_value and normalized_value not in normalized:
            normalized.append(normalized_value)
    return normalized


def normalize_global_rules(values: list[Any]) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("Global rules must be a list.")
    normalized = [str(value or "").strip() for value in values]
    return [value for value in normalized if value]


def normalize_review_policy(value: dict[str, Any]) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("Review policy must be an object.")
    policy = {
        key: normalize_threshold(value.get(key, DEFAULT_REVIEW_POLICY[key]), key)
        for key in DEFAULT_REVIEW_POLICY
    }
    if not (
        policy["reject_below_threshold"]
        <= policy["needs_review_threshold"]
        <= policy["auto_approve_threshold"]
    ):
        raise ValueError(
            "Review thresholds must satisfy reject <= needs review <= auto approve."
        )
    return policy


def normalize_threshold(value: Any, label: str) -> float:
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if threshold < 0 or threshold > 1:
        raise ValueError(f"{label} must be between 0 and 1.")
    return threshold


def normalize_extraction_scope(session: Session, value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Extraction scope must be an object.")

    scope = {**DEFAULT_EXTRACTION_SCOPE, **value}
    if scope["source_mode"] != "active_source_model":
        raise ValueError("Only active_source_model extraction scope is supported.")

    available_tables = active_content_tables(session)
    requested_tables = scope.get("content_tables") or available_tables
    if not isinstance(requested_tables, list):
        raise ValueError("Extraction content tables must be a list.")
    normalized_tables = []
    for table_name in requested_tables:
        normalized_table = str(table_name or "").strip()
        if not normalized_table:
            continue
        if available_tables and normalized_table not in available_tables:
            raise ValueError(
                f"Content table '{normalized_table}' is not configured in the active source model."
            )
        if normalized_table not in normalized_tables:
            normalized_tables.append(normalized_table)

    chunk_selection = str(scope.get("chunk_selection") or "all_chunks").strip()
    if chunk_selection not in {"all_chunks", "embedding_neighbors"}:
        raise ValueError("Chunk selection must be all_chunks or embedding_neighbors.")

    use_embeddings = bool(scope.get("use_embeddings"))
    if chunk_selection == "embedding_neighbors":
        use_embeddings = True
    embedding_config = get_embedding_config(session)
    if use_embeddings and not embedding_config.get("enabled"):
        raise ValueError("Enable embeddings before using embedding context in LLM extracting.")

    max_neighbor_chunks = normalize_int_range(
        scope.get("max_neighbor_chunks", 3),
        "Max neighbor chunks",
        minimum=0,
        maximum=20,
    )
    min_similarity = normalize_threshold(scope.get("min_similarity", 0.75), "Min similarity")
    max_chunks_per_case = normalize_optional_int_range(
        scope.get("max_chunks_per_case"),
        "Max chunks per case",
        minimum=1,
        maximum=10000,
    )

    return {
        "source_mode": "active_source_model",
        "content_tables": normalized_tables,
        "chunk_selection": chunk_selection,
        "use_embeddings": use_embeddings,
        "max_neighbor_chunks": max_neighbor_chunks,
        "min_similarity": min_similarity,
        "max_chunks_per_case": max_chunks_per_case,
        "include_case_metadata": bool(scope.get("include_case_metadata", True)),
        "require_evidence_text": bool(scope.get("require_evidence_text", True)),
    }


def normalize_int_range(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer.") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return number


def normalize_optional_int_range(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if value in (None, ""):
        return None
    return normalize_int_range(value, label, minimum=minimum, maximum=maximum)


def normalize_json_schema(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("JSON Schema must be an object.")
    if not value:
        return None
    return value


def normalize_identifier(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required.")
    if not KEY_PATTERN.match(normalized):
        raise ValueError(
            f"{label} must start with a letter or underscore and contain only letters, numbers, and underscores."
        )
    return normalized


def normalize_blueprint_key(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("Blueprint key is required.")
    if not BLUEPRINT_KEY_PATTERN.match(normalized):
        raise ValueError(
            "Blueprint key must start with a letter or underscore and contain only "
            "letters, numbers, underscores, and hyphens."
        )
    return normalized


def normalize_optional_text(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def active_content_tables(session: Session) -> list[str]:
    source_model = get_active_source_model(session)
    if not source_model:
        return []
    return [
        table_name
        for table_name, roles in (source_model.get("table_roles") or {}).items()
        if roles.get("content_column")
    ]


def build_extraction_json_schema(information_types: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["entities", "events", "facts", "relations", "warnings"],
        "properties": {
            "entities": build_kind_array_schema(information_types, "entity"),
            "events": build_kind_array_schema(information_types, "event"),
            "facts": build_kind_array_schema(information_types, "fact"),
            "relations": build_kind_array_schema(information_types, "relation"),
            "warnings": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }


def build_kind_array_schema(
    information_types: list[dict[str, Any]],
    kind: str,
) -> dict[str, Any]:
    matching_types = [item for item in information_types if item["kind"] == kind]
    if not matching_types:
        return {"type": "array", "maxItems": 0}
    return {
        "type": "array",
        "items": {
            "oneOf": [build_information_type_schema(item) for item in matching_types],
        },
    }


def build_information_type_schema(information_type: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "attributes", "evidence_text", "confidence"],
        "properties": {
            "type": {"const": information_type["key"]},
            "attributes": build_attributes_schema(information_type["fields"]),
            "evidence_text": {"type": "string", "minLength": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


def build_attributes_schema(fields: list[dict[str, Any]]) -> dict[str, Any]:
    required_fields = [field["name"] for field in fields if field.get("required")]
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            field["name"]: build_field_schema(field)
            for field in fields
        },
    }
    if required_fields:
        schema["required"] = required_fields
    return schema


def build_field_schema(field: dict[str, Any]) -> dict[str, Any]:
    field_type = field["type"]
    if field_type == "enum":
        return {"type": "string", "enum": field["values"]}
    if field_type == "number":
        return {"type": "number"}
    if field_type == "boolean":
        return {"type": "boolean"}
    if field_type == "array":
        return {"type": "array"}
    if field_type == "object":
        return {"type": "object"}
    if field_type == "date":
        return {"type": "string", "format": "date"}
    if field_type == "datetime":
        return {"type": "string", "format": "date-time"}
    return {"type": "string"}


def upsert_embedding_config(
    session: Session,
    *,
    enabled: bool,
    provider: str,
    base_url: str,
    api_key: str | None,
    clear_api_key: bool,
    model: str,
    timeout_seconds: int | None,
    extra_body: dict[str, Any],
    updated_by: str,
) -> dict[str, Any]:
    existing = get_raw_embedding_config(session)
    current = (
        dict(existing)
        if existing is not None
        else {
            **DEFAULT_EMBEDDING_CONFIG,
            "extra_body_json": json_dumps(DEFAULT_EMBEDDING_CONFIG["extra_body"]),
        }
    )

    normalized_provider = str(provider or "").strip()
    if normalized_provider != "openai-compatible":
        raise ValueError("Only openai-compatible embedding provider is supported.")

    normalized_base_url = str(base_url or "").strip()
    normalized_model = str(model or "").strip()
    if not normalized_base_url:
        raise ValueError("Embedding Base URL is required.")
    if not normalized_model:
        raise ValueError("Embedding model is required.")
    if timeout_seconds is not None and timeout_seconds < 1:
        raise ValueError("Embedding timeout seconds must be greater than 0.")
    if not isinstance(extra_body, dict):
        raise ValueError("Embedding Extra body JSON must be an object.")

    next_api_key = current.get("api_key") or DEFAULT_EMBEDDING_CONFIG["api_key"]
    if clear_api_key:
        next_api_key = DEFAULT_EMBEDDING_CONFIG["api_key"]
    elif api_key is not None and api_key.strip():
        next_api_key = api_key.strip()

    values = {
        "enabled": bool(enabled),
        "provider": normalized_provider,
        "base_url": normalized_base_url,
        "api_key": next_api_key,
        "model": normalized_model,
        "timeout_seconds": timeout_seconds or int(current["timeout_seconds"]),
        "extra_body_json": json_dumps(extra_body),
        "updated_at": utc_now(),
        "updated_by": updated_by,
    }

    if existing is None:
        session.execute(db.extract_embedding_configs.insert().values(**values))
    else:
        session.execute(
            delete(db.extract_embedding_configs).where(
                db.extract_embedding_configs.c.id != existing["id"]
            )
        )
        session.execute(
            update(db.extract_embedding_configs)
            .where(db.extract_embedding_configs.c.id == existing["id"])
            .values(**values)
        )

    return get_embedding_config(session)


def test_embedding_config(
    session: Session,
    *,
    enabled: bool,
    provider: str,
    base_url: str,
    api_key: str | None,
    clear_api_key: bool,
    model: str,
    timeout_seconds: int | None,
    extra_body: dict[str, Any],
) -> dict[str, Any]:
    if not enabled:
        raise ValueError("Enable embeddings before testing the embedding configuration.")

    config = build_embedding_config_values(
        session,
        enabled=enabled,
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        clear_api_key=clear_api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        extra_body=extra_body,
    )
    api_key_value = config["api_key"]
    if not api_key_value or api_key_value == DEFAULT_EMBEDDING_CONFIG["api_key"]:
        raise ValueError("Embedding API key is required for the connection test.")

    url = f"{config['base_url'].rstrip('/')}/embeddings"
    payload = {
        "model": config["model"],
        "input": ["GAARD Extract embedding connection test."],
    }
    payload.update(extra_body)
    headers = {
        "Authorization": f"Bearer {api_key_value}",
        "Content-Type": "application/json",
    }

    try:
        response = httpx.post(
            url,
            json=payload,
            headers=headers,
            timeout=config["timeout_seconds"],
        )
    except httpx.HTTPError as exc:
        raise ValueError("Embedding provider request failed.") from exc

    if response.status_code >= 400:
        detail = response.text.strip()
        detail_suffix = f" {detail[:500]}" if detail else ""
        raise ValueError(
            f"Embedding provider returned HTTP {response.status_code}.{detail_suffix}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError("Embedding provider returned non-JSON response.") from exc

    embedding = extract_embedding_from_response(data)
    return {
        "ok": True,
        "model": data.get("model") or config["model"],
        "embedding_dimensions": len(embedding),
    }


def build_embedding_config_values(
    session: Session,
    *,
    enabled: bool,
    provider: str,
    base_url: str,
    api_key: str | None,
    clear_api_key: bool,
    model: str,
    timeout_seconds: int | None,
    extra_body: dict[str, Any],
) -> dict[str, Any]:
    existing = get_raw_embedding_config(session)
    current = (
        dict(existing)
        if existing is not None
        else {
            **DEFAULT_EMBEDDING_CONFIG,
            "extra_body_json": json_dumps(DEFAULT_EMBEDDING_CONFIG["extra_body"]),
        }
    )

    normalized_provider = str(provider or "").strip()
    if normalized_provider != "openai-compatible":
        raise ValueError("Only openai-compatible embedding provider is supported.")

    normalized_base_url = str(base_url or "").strip()
    normalized_model = str(model or "").strip()
    if not normalized_base_url:
        raise ValueError("Embedding Base URL is required.")
    if not normalized_model:
        raise ValueError("Embedding model is required.")
    if timeout_seconds is not None and timeout_seconds < 1:
        raise ValueError("Embedding timeout seconds must be greater than 0.")
    if not isinstance(extra_body, dict):
        raise ValueError("Embedding Extra body JSON must be an object.")

    next_api_key = current.get("api_key") or DEFAULT_EMBEDDING_CONFIG["api_key"]
    if clear_api_key:
        next_api_key = DEFAULT_EMBEDDING_CONFIG["api_key"]
    elif api_key is not None and api_key.strip():
        next_api_key = api_key.strip()

    return {
        "enabled": bool(enabled),
        "provider": normalized_provider,
        "base_url": normalized_base_url,
        "api_key": next_api_key,
        "model": normalized_model,
        "timeout_seconds": timeout_seconds or int(current["timeout_seconds"]),
        "extra_body_json": json_dumps(extra_body),
    }


def extract_embedding_from_response(data: Any) -> list[Any]:
    try:
        embedding = data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Embedding provider returned invalid embedding response.") from exc
    if (
        not isinstance(embedding, list)
        or not embedding
        or not all(isinstance(value, int | float) for value in embedding)
    ):
        raise ValueError("Embedding provider returned invalid embedding vector.")
    return embedding


def get_raw_embedding_config(session: Session) -> Any | None:
    return session.execute(
        select(db.extract_embedding_configs).order_by(
            db.extract_embedding_configs.c.updated_at.desc(),
            db.extract_embedding_configs.c.id.desc(),
        )
    ).mappings().first()


def upsert_source_model(
    session: Session,
    datasource_service: DatasourceHostService,
    *,
    datasource_connector_id: int,
    main_table: str,
    table_roles: dict[str, Any],
    updated_by: str,
) -> dict[str, Any]:
    datasource = require_datasource(datasource_service, datasource_connector_id)
    schema = get_datasource_schema(datasource_service, datasource_connector_id)
    normalized_table_roles = validate_source_model_schema(
        schema,
        main_table=main_table,
        table_roles=table_roles,
    )

    existing = get_active_source_model(session)
    values = {
        "datasource_connector_id": datasource_connector_id,
        "datasource_connector_key": datasource["connector_key"],
        "main_table": main_table,
        "table_roles_json": json_dumps(normalized_table_roles),
        "updated_at": utc_now(),
        "updated_by": updated_by,
    }

    if existing is None:
        session.execute(
            db.extract_unstructured_source_models.insert().values(
                **values,
            )
        )
    else:
        session.execute(
            delete(db.extract_unstructured_source_models).where(
                db.extract_unstructured_source_models.c.id != existing["id"]
            )
        )
        session.execute(
            update(db.extract_unstructured_source_models)
            .where(
                db.extract_unstructured_source_models.c.id == existing["id"]
            )
            .values(**values)
        )

    item = get_source_model(session, datasource_connector_id)
    if item is None:
        raise RuntimeError("Source model was not saved.")
    return item


def require_datasource(
    datasource_service: DatasourceHostService,
    datasource_connector_id: int,
) -> dict[str, Any]:
    datasource = datasource_service.get_datasource(datasource_connector_id)
    if datasource is None:
        raise KeyError("Datasource does not exist.")
    if datasource.get("system_managed"):
        raise ValueError("The metadata datasource cannot be used as an Extract source.")
    return datasource


def validate_source_model_schema(
    schema: dict[str, Any],
    *,
    main_table: str,
    table_roles: dict[str, Any],
) -> dict[str, dict[str, str]]:
    main_table = main_table.strip()

    if not main_table:
        raise ValueError("Main table is required.")

    tables = schema.get("tables") or []
    table_by_name = {table.get("name"): table for table in tables}
    if main_table not in table_by_name:
        raise ValueError(f"Table '{main_table}' does not exist in datasource schema.")

    normalized_roles = normalize_table_roles(table_roles)
    if not normalized_roles:
        raise ValueError("At least one table role mapping is required.")

    content_tables = []
    for table_name, roles in normalized_roles.items():
        table = table_by_name.get(table_name)
        if table is None:
            raise ValueError(f"Table '{table_name}' does not exist in datasource schema.")

        column_names = {column.get("name") for column in table.get("columns") or []}
        case_id_column = roles.get("case_id_column", "")
        content_column = roles.get("content_column", "")
        if case_id_column and case_id_column not in column_names:
            raise ValueError(f"Column '{case_id_column}' does not exist in table '{table_name}'.")
        if content_column and content_column not in column_names:
            raise ValueError(f"Column '{content_column}' does not exist in table '{table_name}'.")
        if case_id_column and content_column and case_id_column == content_column:
            raise ValueError(
                f"case_id and content must use different columns in table '{table_name}'."
            )
        if content_column and not case_id_column:
            raise ValueError(
                f"Table '{table_name}' has content but no case_id column assigned."
            )
        if content_column:
            content_tables.append(table_name)

    main_table_roles = normalized_roles.get(main_table) or {}
    if not main_table_roles.get("case_id_column"):
        raise ValueError("Main table must have a case_id column assigned.")
    if not content_tables:
        raise ValueError("At least one table must have a content column assigned.")

    return normalized_roles


def normalize_table_roles(table_roles: dict[str, Any]) -> dict[str, dict[str, str]]:
    if not isinstance(table_roles, dict):
        raise ValueError("table_roles must be an object keyed by table name.")

    normalized: dict[str, dict[str, str]] = {}
    for table_name, roles in table_roles.items():
        if not isinstance(table_name, str) or not table_name.strip():
            raise ValueError("table_roles table names must be non-empty strings.")
        if not isinstance(roles, dict):
            raise ValueError(f"table_roles.{table_name} must be an object.")

        case_id_column = str(roles.get("case_id_column") or "").strip()
        content_column = str(roles.get("content_column") or "").strip()
        if not case_id_column and not content_column:
            continue

        normalized[table_name.strip()] = {
            "case_id_column": case_id_column,
            "content_column": content_column,
        }

    return normalized


def serialize_source_model(row: Any) -> dict[str, Any]:
    item = serialize_timestamps(row)
    item["table_roles"] = json_loads(item.pop("table_roles_json"), {})
    return item


def serialize_embedding_config(row: Any, *, persisted: bool) -> dict[str, Any]:
    item = serialize_timestamps(row)
    extra_body = json_loads(item.get("extra_body_json"), {})
    if not isinstance(extra_body, dict):
        extra_body = {}
    api_key = str(item.pop("api_key", "") or "")
    api_key_configured = bool(api_key and api_key != DEFAULT_EMBEDDING_CONFIG["api_key"])
    item["extra_body"] = extra_body
    item["extra_body_json"] = json_dumps_pretty(extra_body)
    item["api_key_configured"] = api_key_configured
    item["api_key_preview"] = mask_secret(api_key) if api_key_configured else None
    item["persisted"] = persisted
    return item


def serialize_llm_extracting_config(row: Any, *, persisted: bool) -> dict[str, Any]:
    item = serialize_timestamps(row)
    config = json_loads(item.pop("config_json"), {})
    if not isinstance(config, dict):
        config = {}

    information_types = config.get("information_types") or []
    json_schema = json_loads(item.pop("json_schema_json", None), None)
    if not isinstance(json_schema, dict):
        json_schema = build_extraction_json_schema(information_types)

    item["information_types"] = information_types
    item["global_rules"] = config.get("global_rules") or []
    item["review_policy"] = {
        **DEFAULT_REVIEW_POLICY,
        **(config.get("review_policy") or {}),
    }
    item["extraction_scope"] = {
        **DEFAULT_EXTRACTION_SCOPE,
        **(config.get("extraction_scope") or {}),
    }
    item["json_schema"] = json_schema
    item["json_schema_json"] = json_dumps_pretty(json_schema)
    item["persisted"] = persisted
    return item


def serialize_timestamps(row: Any) -> dict[str, Any]:
    item = dict(row)
    for key, value in list(item.items()):
        if isinstance(value, datetime):
            item[key] = value.isoformat()
    return item


def serialize_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_dumps_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def mask_secret(value: str) -> str:
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"
