from collections.abc import Iterator
import json
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import Engine
from sqlalchemy import inspect, text, Table
from sqlalchemy.orm import Session, sessionmaker

from gaard_api.admin.defaults import DEFAULT_GOVERNANCE_POLICY_CONFIG, DEFAULT_PROMPTS
from gaard_api.admin.models import (
    AdminSetting,
    AdminSession,
    AdminUser,
    Base,
    DataQueryAuditLog,
    DataQueryAuditType,
    DatasourceConnector,
    OverviewWidget,
    OverviewWidgetTag,
    PromptTemplate,
    UserSavedMetric,
    WidgetTag,
)
from gaard_api.admin.security import hash_password
from gaard_api.core.settings import settings

from typing import cast


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_engine_url: str | None = None
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
    with _init_lock:
        engine = get_engine()
        Base.metadata.create_all(engine)
        ensure_admin_session_schema(engine)
        ensure_admin_user_schema(engine)
        ensure_data_query_audit_schema(engine)
        ensure_overview_widget_schema(engine)

        if _session_factory is None:
            raise RuntimeError("Admin metadata session factory is not initialized.")

        with _session_factory() as session:
            seed_admin_user(session)
            seed_settings(session)
            apply_runtime_settings(session)
            seed_prompts(session)
            seed_datasource_connectors(session)
            seed_overview_widgets(session)
            backfill_overview_widget_tags(session)
            backfill_data_query_audit_types(session)
            session.commit()


def clear_expired_admin_sessions(session: Session) -> int:
    """Remove sessions inactive for 30 days; called once during application startup."""
    cutoff = datetime.now(UTC) - timedelta(days=30)
    result = session.execute(
        delete(AdminSession)
        .where(AdminSession.last_seen < cutoff)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def seed_admin_user(session: Session) -> None:
    user = session.scalar(select(AdminUser).where(AdminUser.username == "admin"))

    if user is not None:
        return

    session.add(
        AdminUser(
            username="admin",
            password_hash=hash_password("admin"),
            must_change_password=True,
            enterprise_access=True,
        )
    )


def ensure_admin_user_schema(engine: Engine) -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("admin_users")}
    additions = {
        "display_name": "ALTER TABLE admin_users ADD COLUMN display_name VARCHAR(255) NOT NULL DEFAULT ''",
        "auth_provider": "ALTER TABLE admin_users ADD COLUMN auth_provider VARCHAR(255) NOT NULL DEFAULT 'local'",
        "role": "ALTER TABLE admin_users ADD COLUMN role VARCHAR(50) NOT NULL DEFAULT 'admin'",
        "enterprise_access": "ALTER TABLE admin_users ADD COLUMN enterprise_access BOOLEAN NOT NULL DEFAULT 0",
        "is_provisioned": "ALTER TABLE admin_users ADD COLUMN is_provisioned BOOLEAN NOT NULL DEFAULT 0",
    }
    with engine.begin() as connection:
        for name, sql in additions.items():
            if name not in columns:
                connection.execute(text(sql))
        # Administrator accounts always retain Enterprise access and count toward the seat limit.
        connection.execute(text(
            "UPDATE admin_users SET enterprise_access = 1 WHERE role = 'admin'"
        ))
        if engine.dialect.name == "sqlite" and (
            admin_user_has_global_username_constraint(engine)
            or not admin_user_has_provider_username_constraint(engine)
        ):
            rebuild_sqlite_admin_users(connection)
        else:
            drop_global_admin_username_constraint(connection, engine)
            normalize_external_admin_usernames(connection, engine.dialect.name)
            ensure_admin_user_provider_username_constraint(connection, engine)


def admin_user_has_global_username_constraint(engine: Engine) -> bool:
    return ["username"] in admin_user_unique_column_sets(engine)


def admin_user_has_provider_username_constraint(engine: Engine) -> bool:
    return ["auth_provider", "username"] in admin_user_unique_column_sets(engine)


def admin_user_unique_column_sets(engine: Engine) -> list[list[str]]:
    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            return [
                [row[2] for row in connection.execute(text(f"PRAGMA index_info({index_name!r})"))]
                for _sequence, index_name, is_unique, *_rest in connection.execute(
                    text("PRAGMA index_list('admin_users')")
                )
                if is_unique
            ]

    inspector = inspect(engine)
    return [
        cast(list[str], constraint_column_names)
        for constraint in inspector.get_unique_constraints("admin_users")
        if isinstance((constraint_column_names := constraint.get("column_names")), list)
        and all(isinstance(column_name, str) for column_name in constraint_column_names)
    ] + [
        cast(list[str], index_column_names)
        for index in inspector.get_indexes("admin_users")
        if index.get("unique")
        and isinstance((index_column_names := index.get("column_names")), list)
        and all(isinstance(column_name, str) for column_name in index_column_names)
    ]


def rebuild_sqlite_admin_users(connection) -> None:
    connection.execute(text("""
        CREATE TABLE admin_users__migrated (
            id INTEGER NOT NULL PRIMARY KEY,
            username VARCHAR(255) NOT NULL,
            display_name VARCHAR(255) NOT NULL DEFAULT '',
            auth_provider VARCHAR(255) NOT NULL DEFAULT 'local',
            role VARCHAR(50) NOT NULL DEFAULT 'admin',
            enterprise_access BOOLEAN NOT NULL DEFAULT 0,
            password_hash TEXT NOT NULL,
            must_change_password BOOLEAN NOT NULL,
            is_provisioned BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            CONSTRAINT uq_admin_users_auth_provider_username UNIQUE (auth_provider, username)
        )
    """))
    connection.execute(text("""
        INSERT INTO admin_users__migrated
            (id, username, display_name, auth_provider, role, enterprise_access, password_hash, must_change_password, is_provisioned, created_at, updated_at)
        SELECT
            id,
            CASE WHEN password_hash = 'external$disabled' AND instr(username, ':') > 0
                    AND (auth_provider = 'local' OR substr(username, 1, instr(username, ':') - 1) = auth_provider)
                THEN substr(username, instr(username, ':') + 1) ELSE username END,
            display_name,
            CASE WHEN auth_provider = 'local' AND password_hash = 'external$disabled' AND instr(username, ':') > 0
                THEN substr(username, 1, instr(username, ':') - 1) ELSE auth_provider END,
            role,
            CASE WHEN role = 'admin' THEN 1 ELSE 0 END,
            password_hash, must_change_password, 0, created_at, updated_at
        FROM admin_users
    """))
    connection.execute(text("DROP TABLE admin_users"))
    connection.execute(text("ALTER TABLE admin_users__migrated RENAME TO admin_users"))
    connection.execute(text("CREATE INDEX ix_admin_users_username ON admin_users (username)"))
    connection.execute(text("CREATE INDEX ix_admin_users_auth_provider ON admin_users (auth_provider)"))


def drop_global_admin_username_constraint(connection, engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    inspector = inspect(engine)
    quote = connection.dialect.identifier_preparer.quote
    for constraint in inspector.get_unique_constraints("admin_users"):
        if constraint.get("column_names") == ["username"]:
            connection.execute(text(f"ALTER TABLE admin_users DROP CONSTRAINT {quote(constraint['name'])}"))


def normalize_external_admin_usernames(connection, dialect_name: str) -> None:
    if dialect_name == "sqlite":
        username = "substr(username, instr(username, ':') + 1)"
        provider = "substr(username, 1, instr(username, ':') - 1)"
        has_prefix = "instr(username, ':') > 0"
        prefix_matches_provider = "substr(username, 1, instr(username, ':') - 1) = auth_provider"
    elif dialect_name == "postgresql":
        username = "substring(username from position(':' in username) + 1)"
        provider = "substring(username from 1 for position(':' in username) - 1)"
        has_prefix = "position(':' in username) > 0"
        prefix_matches_provider = "substring(username from 1 for position(':' in username) - 1) = auth_provider"
    else:
        return
    connection.execute(text(
        "UPDATE admin_users "
        f"SET username = {username}, auth_provider = CASE WHEN auth_provider = 'local' THEN {provider} ELSE auth_provider END "
        "WHERE password_hash = 'external$disabled' "
        f"AND {has_prefix} AND (auth_provider = 'local' OR {prefix_matches_provider})"
    ))


def ensure_admin_user_provider_username_constraint(connection, engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    constraints = inspect(engine).get_unique_constraints("admin_users")
    if not any(
        constraint.get("column_names") == ["auth_provider", "username"]
        for constraint in constraints
    ):
        connection.execute(text(
            "ALTER TABLE admin_users ADD CONSTRAINT uq_admin_users_auth_provider_username "
            "UNIQUE (auth_provider, username)"
        ))


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
    migrate_postgres_sql_dialect(session)

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


def migrate_postgres_sql_dialect(session: Session) -> None:
    for connector in session.scalars(
        select(DatasourceConnector).where(DatasourceConnector.sql_dialect == "postgresql")
    ):
        connector.sql_dialect = "postgres"


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

    for item in defaults:
        existing = session.scalar(
            select(OverviewWidget).where(OverviewWidget.widget_key == item["widget_key"])
        )

        if existing is None:
            session.add(OverviewWidget(**item))
        elif not existing.sql and existing.updated_by == "system":
            existing.sql = str(item["sql"])

        if (
            existing is not None
            and item["widget_key"] == "runtime_daily_queries"
            and existing.updated_by == "system"
        ):
            existing.active = False

        if existing is not None and existing.updated_by == "system":
            existing.position = int(cast(int,item["position"])) 
            existing.grid_width = int(cast(int,item["grid_width"]))
            existing.result_mode = str(item["result_mode"])


def ensure_data_query_audit_schema(engine: Engine) -> None:
    inspector = inspect(engine)

    if "data_query_audit_logs" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("data_query_audit_logs")}

    if "type" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE data_query_audit_logs "
                    "ADD COLUMN type VARCHAR(50) NOT NULL DEFAULT 'info'"
                )
            )

    if "output_classification" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE data_query_audit_logs "
                    "ADD COLUMN output_classification VARCHAR(50) "
                    "NOT NULL DEFAULT 'unknown'"
                )
            )

    if "llm_sql_language" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE data_query_audit_logs "
                    "ADD COLUMN llm_sql_language VARCHAR(50) DEFAULT ''"
                )
            )

    with engine.begin() as connection:

        audit_log_table = cast(Table, DataQueryAuditLog.__table__)

        for index in audit_log_table.indexes:
            index.create(bind=connection, checkfirst=True)


def ensure_admin_session_schema(engine: Engine) -> None:
    inspector = inspect(engine)

    if "admin_sessions" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("admin_sessions")}

    additions = {
        "username": "ALTER TABLE admin_sessions ADD COLUMN username VARCHAR(255) DEFAULT ''",
        "role": "ALTER TABLE admin_sessions ADD COLUMN role VARCHAR(50) DEFAULT 'admin'",
        "auth_provider": (
            "ALTER TABLE admin_sessions ADD COLUMN auth_provider VARCHAR(255) DEFAULT 'local'"
        ),
        # SQLite cannot add a column with CURRENT_TIMESTAMP as its default.  Backfill
        # legacy rows below, while newly created sessions use the ORM default.
        "last_seen": "ALTER TABLE admin_sessions ADD COLUMN last_seen DATETIME",
    }
    with engine.begin() as connection:
        for column_name, sql in additions.items():
            if column_name not in columns:
                connection.execute(text(sql))

        if "last_seen" not in columns:
            connection.execute(
                text("UPDATE admin_sessions SET last_seen = created_at WHERE last_seen IS NULL")
            )

        session_table = cast(Table, AdminSession.__table__)
        for index in session_table.indexes:
            index.create(bind=connection, checkfirst=True)


def backfill_data_query_audit_types(session: Session) -> None:
    logs = session.scalars(
        select(DataQueryAuditLog).where(
            DataQueryAuditLog.metadata_json.like('%"audit_type"%')
        )
    )

    for log in logs:
        try:
            metadata = json.loads(log.metadata_json or "{}")
        except json.JSONDecodeError:
            continue

        if not isinstance(metadata, dict):
            continue

        audit_type = coerce_legacy_data_query_audit_type(metadata.get("audit_type"))

        if audit_type is None:
            continue

        metadata.pop("audit_type", None)
        log.type = audit_type
        log.metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)


def coerce_legacy_data_query_audit_type(value: object) -> DataQueryAuditType | None:
    if isinstance(value, DataQueryAuditType):
        return value

    if not isinstance(value, str):
        return None

    normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {item.value: item for item in DataQueryAuditType}

    return aliases.get(normalized)


def ensure_overview_widget_schema(engine: Engine) -> None:
    inspector = inspect(engine)

    if "overview_widgets" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("overview_widgets")}

    if "sql" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE overview_widgets ADD COLUMN sql TEXT DEFAULT ''")
            )

    if "grid_width" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE overview_widgets ADD COLUMN grid_width INTEGER DEFAULT 1")
            )

    if "grid_height" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE overview_widgets ADD COLUMN grid_height INTEGER DEFAULT 2")
            )
            connection.execute(
                text(
                    "UPDATE overview_widgets SET grid_height = 4 "
                    "WHERE widget_type <> 'scalar'"
                )
            )

    if "result_mode" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE overview_widgets "
                    "ADD COLUMN result_mode VARCHAR(50) DEFAULT 'data'"
                )
            )

def backfill_overview_widget_tags(session: Session) -> None:
    """Give legacy widgets public and saved-metric owner tags."""
    if session.scalar(select(WidgetTag).where(WidgetTag.name == "public")) is None:
        session.add(WidgetTag(name="public"))
        session.flush()
    widget_ids_with_tags = set(session.scalars(select(OverviewWidgetTag.widget_id)))
    for widget_id in session.scalars(select(OverviewWidget.id)):
        if widget_id not in widget_ids_with_tags:
            session.add(OverviewWidgetTag(widget_id=widget_id, tag_name="public"))

    existing_assignments = set(
        session.execute(select(OverviewWidgetTag.widget_id, OverviewWidgetTag.tag_name))
    )
    for widget_id, owner_username in session.execute(
        select(OverviewWidget.id, UserSavedMetric.owner_username).join(
            UserSavedMetric,
            UserSavedMetric.widget_key == OverviewWidget.widget_key,
        )
    ):
        if not owner_username:
            continue
        if session.get(WidgetTag, owner_username) is None:
            session.add(WidgetTag(name=owner_username))
            session.flush()
        if (widget_id, owner_username) not in existing_assignments:
            session.add(OverviewWidgetTag(widget_id=widget_id, tag_name=owner_username))


def reset_metadata_store_for_tests() -> None:
    global _engine, _engine_url, _session_factory

    if _engine is not None:
        _engine.dispose()

    _engine = None
    _engine_url = None
    _session_factory = None
