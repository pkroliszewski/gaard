import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import cast

from sqlalchemy import create_engine, delete, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from gaard_api.admin.defaults import DEFAULT_GOVERNANCE_POLICY_CONFIG, DEFAULT_PROMPTS
from gaard_api.admin.migration_runner import (
    apply_pending_sql_updates,
    as_table,
    execute_initial_sql,
    execute_legacy_sql_phase,
    parse_initial_sql,
    parse_legacy_sql,
    parse_sql_updates,
    stamp_sql_updates,
)
from gaard_api.admin.models import (
    AdminSession,
    AdminSetting,
    AdminUser,
    DatabaseMigrationTag,
    DatasourceConnector,
    OverviewWidget,
    OverviewWidgetTag,
    PromptTemplate,
    WidgetTag,
)
from gaard_api.admin.security import hash_password
from gaard_api.core.settings import settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_engine_url: str | None = None
_initialized_url: str | None = None
_init_lock = threading.RLock()
LEGACY_PROMPT_KEYS = {"investigation_readiness"}


def get_engine() -> Engine:
    global _engine, _engine_url, _session_factory

    database_url = settings.gaard_metadata_database_url

    if _engine is not None and _engine_url == database_url:
        return _engine

    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    _engine = create_engine(database_url, connect_args=connect_args)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    _engine_url = database_url

    return _engine


def get_session() -> Iterator[Session]:
    init_metadata_store()

    if _session_factory is None:
        raise RuntimeError("Admin metadata session factory is not initialized.")

    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def create_session() -> Session:
    init_metadata_store()

    if _session_factory is None:
        raise RuntimeError("Admin metadata session factory is not initialized.")

    return _session_factory()


def init_metadata_store() -> None:
    global _initialized_url

    with _init_lock:
        engine = get_engine()
        if _initialized_url == _engine_url:
            return

        updates = parse_sql_updates(
            files("gaard_api.admin").joinpath("database_updates.sql").read_text("utf-8")
        )
        if not updates:
            raise RuntimeError("The database update file must contain its baseline tag.")
        initial_commands = parse_initial_sql(
            files("gaard_api.admin").joinpath("database_initial.sql").read_text("utf-8")
        )
        existing_tables = set(inspect(engine).get_table_names())
        migration_tags = as_table(DatabaseMigrationTag.__table__)
        if not existing_tables:
            execute_initial_sql(engine, initial_commands)
            stamp_sql_updates(engine, updates, migration_tags)
        else:
            migration_tags.create(engine, checkfirst=True)
            legacy_commands = parse_legacy_sql(
                files("gaard_api.admin")
                .joinpath("database_legacy_updates.sql")
                .read_text("utf-8")
            )
            with engine.connect() as connection:
                first_tag_applied = connection.scalar(
                    select(migration_tags.c.tag).where(
                        migration_tags.c.tag == updates[0].tag
                    )
                )
            if first_tag_applied is None:
                execute_legacy_sql_phase(
                    engine,
                    legacy_commands,
                    "before-initial",
                )
                execute_initial_sql(engine, initial_commands)
                execute_legacy_sql_phase(
                    engine,
                    legacy_commands,
                    "after-initial",
                )
            apply_pending_sql_updates(engine, updates, migration_tags)

        if _session_factory is None:
            raise RuntimeError("Admin metadata session factory is not initialized.")

        with _session_factory() as session:
            seed_admin_user(session)
            seed_settings(session)
            apply_runtime_settings(session)
            seed_prompts(session)
            seed_datasource_connectors(session)
            seed_overview_widgets(session)
            session.commit()
        _initialized_url = _engine_url


def clear_expired_admin_sessions(session: Session) -> None:
    """Remove sessions inactive for 30 days; called once during application startup."""
    cutoff = datetime.now(UTC) - timedelta(days=30)
    session.execute(
        delete(AdminSession)
        .where(AdminSession.last_seen < cutoff)
        .execution_options(synchronize_session=False)
    )


def seed_admin_user(session: Session) -> None:
    user = session.scalar(
        select(AdminUser).where(
            AdminUser.username == "admin",
            AdminUser.auth_provider == "local",
        )
    )

    if user is not None:
        return

    session.add(
        AdminUser(
            username="admin",
            password_hash=hash_password("admin"),
            must_change_password=True,
            is_system_admin=True,
            enterprise_access=True,
        )
    )


def seed_settings(session: Session) -> None:
    defaults = {
        "gaard_intent_classification_mode": settings.gaard_intent_classification_mode,
        "gaard_sql_generation_mode": settings.gaard_sql_generation_mode,
        "gaard_result_interpretation_mode": settings.gaard_result_interpretation_mode,
        "gaard_output_classification_mode": settings.gaard_output_classification_mode,
        "gaard_query_max_rows": str(settings.gaard_query_max_rows),
        "gaard_query_timeout_seconds": str(settings.gaard_query_timeout_seconds),
        "gaard_analysis_loop_count": str(settings.gaard_analysis_loop_count),
        "gaard_analysis_auto_enable_business_logic": (
            "true" if settings.gaard_analysis_auto_enable_business_logic else "false"
        ),
        "gaard_llm_provider": settings.gaard_llm_provider,
        "gaard_llm_base_url": settings.gaard_llm_base_url,
        "gaard_llm_api_key": settings.gaard_llm_api_key,
        "gaard_llm_model": settings.gaard_llm_model,
        "gaard_llm_timeout_seconds": str(settings.gaard_llm_timeout_seconds),
        "gaard_llm_extra_body": json.dumps(
            settings.gaard_llm_extra_body,
            ensure_ascii=False,
            sort_keys=True,
        ),
        "gaard_governance_policy": json.dumps(
            DEFAULT_GOVERNANCE_POLICY_CONFIG,
            ensure_ascii=False,
            sort_keys=True,
        ),
        "data_query_audit_retention_days": str(settings.gaard_audit_retention_days),
        "schema_cache_ttl_seconds": str(settings.gaard_schema_cache_ttl_seconds),
    }

    for key, value in defaults.items():
        setting = session.get(AdminSetting, key)
        if setting is None:
            session.add(AdminSetting(key=key, value=value))
        elif setting.updated_by == "system" and setting.value != value:
            setting.value = value

    if session.get(AdminSetting, "license_edition") is None:
        session.add(AdminSetting(key="license_edition", value="community"))


def apply_runtime_settings(session: Session) -> None:
    schema_cache_ttl = session.get(AdminSetting, "schema_cache_ttl_seconds")

    if schema_cache_ttl is None:
        return

    try:
        ttl_seconds = max(1, int(schema_cache_ttl.value))
    except (TypeError, ValueError):
        return

    from gaard_api.core.schema_cache import schema_context_cache

    schema_context_cache.ttl_seconds = ttl_seconds


def seed_prompts(session: Session) -> None:
    session.execute(
        delete(PromptTemplate).where(PromptTemplate.prompt_key.in_(LEGACY_PROMPT_KEYS))
    )

    for prompt in DEFAULT_PROMPTS:
        existing = session.scalar(
            select(PromptTemplate).where(PromptTemplate.prompt_key == prompt["prompt_key"])
        )

        if existing is None:
            session.add(PromptTemplate(**prompt))
            continue

        if existing.updated_by != "system":
            continue

        changed = any(
            getattr(existing, field) != prompt[field]
            for field in (
                "name",
                "description",
                "system_prompt",
                "user_prompt_template",
            )
        )
        if not changed:
            continue

        existing.name = prompt["name"]
        existing.description = prompt["description"]
        existing.system_prompt = prompt["system_prompt"]
        existing.user_prompt_template = prompt["user_prompt_template"]
        existing.active = True
        existing.version += 1
        existing.updated_by = "system"


def seed_datasource_connectors(session: Session) -> None:
    metadata_connector = session.scalar(
        select(DatasourceConnector).where(DatasourceConnector.connector_key == "metadata-db")
    )
    database_type, sql_dialect = infer_datasource_type(settings.gaard_metadata_database_url)

    if metadata_connector is None:
        session.add(
            DatasourceConnector(
                connector_key="metadata-db",
                name="GAARD Metadata DB",
                database_type=database_type,
                database_url=settings.gaard_metadata_database_url,
                sql_dialect=sql_dialect,
                active=False,
            )
        )
    else:
        metadata_connector.name = "GAARD Metadata DB"
        metadata_connector.database_type = database_type
        metadata_connector.database_url = settings.gaard_metadata_database_url
        metadata_connector.sql_dialect = sql_dialect
        metadata_connector.active = False
        metadata_connector.updated_by = "system"


def infer_datasource_type(database_url: str) -> tuple[str, str]:
    if database_url.startswith("sqlite"):
        return "sqlite", "sqlite"

    if database_url.startswith("postgresql"):
        return "postgresql", "postgres"

    if database_url.startswith("mysql"):
        return "mysql", "mysql"

    return "postgresql", "postgres"


def seed_overview_widgets(session: Session) -> None:
    _database_type, metadata_sql_dialect = infer_datasource_type(settings.gaard_metadata_database_url)
    runtime_sql = (
        "SELECT DATE(occurred_at) AS day, datasource_id, COUNT(*) AS query_count "
        "FROM data_query_audit_logs "
        "GROUP BY DATE(occurred_at), datasource_id "
        "ORDER BY day, datasource_id"
        if metadata_sql_dialect == "sqlite"
        else "SELECT occurred_at::date AS day, datasource_id, COUNT(*) AS query_count "
        "FROM data_query_audit_logs "
        "GROUP BY occurred_at::date, datasource_id "
        "ORDER BY day, datasource_id"
    )
    defaults = [
        {
            "widget_key": "prompts_count",
            "label": "Prompts",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": (
                "How many prompt templates are configured in GAARD metadata? "
                "Return exactly one numeric value."
            ),
            "sql": "SELECT COUNT(*) AS value FROM prompt_templates",
            "result_mode": "data",
            "position": 10,
            "grid_width": 1,
            "grid_height": 2,
        },
        {
            "widget_key": "audit_retention",
            "label": "Audit retention",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": (
                "What is the value of the admin setting named "
                "data_query_audit_retention_days? Return exactly one numeric value."
            ),
            "sql": (
                "SELECT CAST(value AS INTEGER) AS value "
                "FROM admin_settings "
                "WHERE key = 'data_query_audit_retention_days'"
            ),
            "result_mode": "data",
            "position": 20,
            "grid_width": 1,
            "grid_height": 2,
        },
        {
            "widget_key": "schema_cache_ttl",
            "label": "Schema cache TTL",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": (
                "What is the value of the admin setting named schema_cache_ttl_seconds? "
                "Return exactly one numeric value."
            ),
            "sql": (
                "SELECT CAST(value AS INTEGER) AS value "
                "FROM admin_settings "
                "WHERE key = 'schema_cache_ttl_seconds'"
            ),
            "result_mode": "data",
            "position": 30,
            "grid_width": 1,
            "grid_height": 2,
        },
        {
            "widget_key": "license_edition",
            "label": "License",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": (
                "What is the value of the admin setting named license_edition? "
                "Return exactly one text value."
            ),
            "sql": "SELECT value AS value FROM admin_settings WHERE key = 'license_edition'",
            "result_mode": "data",
            "position": 40,
            "grid_width": 1,
            "grid_height": 2,
        },
        {
            "widget_key": "runtime_daily_queries",
            "label": "Runtime",
            "widget_type": "timeseries",
            "datasource_key": "metadata-db",
            "question": (
                "For each day and datasource_id in data_query_audit_logs, how many "
                "query records exist? Return columns day, datasource_id, query_count "
                "ordered by day and datasource_id."
            ),
            "sql": runtime_sql,
            "result_mode": "data",
            "position": 250,
            "grid_width": 12,
            "grid_height": 4,
            "active": False,
        },
        {
            "widget_key": "prompt_templates_table",
            "label": "Prompt templates",
            "widget_type": "table",
            "datasource_key": "metadata-db",
            "question": (
                "List configured prompt templates with prompt_key, name, version and "
                "active status, ordered by prompt_key."
            ),
            "sql": (
                "SELECT prompt_key, name, version, active "
                "FROM prompt_templates "
                "ORDER BY prompt_key"
            ),
            "result_mode": "data",
            "position": 130,
            "grid_width": 12,
            "grid_height": 4,
        },
    ]

    public_tag = session.get(WidgetTag, "public")
    if public_tag is None:
        session.add(WidgetTag(name="public"))
        session.flush()

    for item in defaults:
        existing = session.scalar(
            select(OverviewWidget).where(OverviewWidget.widget_key == item["widget_key"])
        )

        if existing is None:
            existing = OverviewWidget(**item)
            session.add(existing)
            session.flush()
        elif not existing.sql and existing.updated_by == "system":
            existing.sql = str(item["sql"])

        if (
            existing is not None
            and item["widget_key"] == "runtime_daily_queries"
            and existing.updated_by == "system"
        ):
            existing.active = False

        if existing is not None and existing.updated_by == "system":
            existing.position = int(cast(int, item["position"]))
            existing.grid_width = int(cast(int, item["grid_width"]))
            existing.result_mode = str(item["result_mode"])

        if session.get(
            OverviewWidgetTag,
            {"widget_id": existing.id, "tag_name": "public"},
        ) is None:
            session.add(OverviewWidgetTag(widget_id=existing.id, tag_name="public"))


def reset_metadata_store_for_tests() -> None:
    global _engine, _engine_url, _initialized_url, _session_factory

    if _engine is not None:
        _engine.dispose()

    _engine = None
    _engine_url = None
    _initialized_url = None
    _session_factory = None
