from importlib.resources import files
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, inspect, select
from sqlalchemy.engine import Engine

from gaard_api.admin.database import (
    get_engine,
    init_metadata_store,
    reset_metadata_store_for_tests,
)
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
from gaard_api.admin.models import Base, DatabaseMigrationTag
from gaard_api.core.settings import settings


def update_file_contents() -> str:
    return files("gaard_api.admin").joinpath("database_updates.sql").read_text("utf-8")


def initial_file_contents() -> str:
    return files("gaard_api.admin").joinpath("database_initial.sql").read_text("utf-8")


def legacy_file_contents() -> str:
    return (
        files("gaard_api.admin")
        .joinpath("database_legacy_updates.sql")
        .read_text("utf-8")
    )


def create_test_tables(engine: Engine) -> tuple[Table, Table]:
    metadata = MetaData()
    values = Table("migration_test_values", metadata, Column("value", Integer, nullable=False))
    migration_tags = as_table(DatabaseMigrationTag.__table__)
    metadata.create_all(engine, tables=[values])
    migration_tags.create(engine, checkfirst=True)
    return values, migration_tags


def test_initial_schema_creates_all_tables(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
    reference_engine = create_engine("sqlite://")
    core_tables = sorted(
        (
            as_table(mapper.local_table)
            for mapper in Base.registry.mappers
            if mapper.class_.__module__ == "gaard_api.admin.models"
        ),
        key=lambda table: table.name,
    )

    execute_initial_sql(engine, parse_initial_sql(initial_file_contents()))
    Base.metadata.create_all(reference_engine, tables=core_tables)

    actual = inspect(engine)
    expected = inspect(reference_engine)
    assert set(actual.get_table_names()) == {table.name for table in core_tables}
    for table_name in actual.get_table_names():
        assert [
            (column["name"], str(column["type"]), column["nullable"])
            for column in actual.get_columns(table_name)
        ] == [
            (column["name"], str(column["type"]), column["nullable"])
            for column in expected.get_columns(table_name)
        ]
        assert sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in actual.get_indexes(table_name)
        ) == sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in expected.get_indexes(table_name)
        )
        assert sorted(
            tuple(constraint["column_names"])
            for constraint in actual.get_unique_constraints(table_name)
        ) == sorted(
            tuple(constraint["column_names"])
            for constraint in expected.get_unique_constraints(table_name)
        )


def test_repository_update_file_is_valid_and_has_unique_ordered_tags() -> None:
    updates = parse_sql_updates(update_file_contents())

    assert [update.tag for update in updates] == [
        "2026-07-31.identity-ids-use-admin-user-id.v1",
        "2026-08-01.dashboard-sharing.v1",
        "2026-08-12.duckdb-file-connector.initial.v1",
        "2026-08-14.duckdb-file-connector.migrate-duckdb-excel.v1",
    ]


def test_tagged_group_runs_atomically_once_and_in_file_order(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
    values, migration_tags = create_test_tables(engine)
    with engine.begin() as connection:
        connection.execute(values.insert().values(value=1))
    updates = parse_sql_updates("""
-- tag: add-two.v1
UPDATE migration_test_values SET value = value + 1;
UPDATE migration_test_values SET value = value + 1;
-- tag: multiply.v1
UPDATE migration_test_values SET value = value * 10;
""")

    first_run = apply_pending_sql_updates(engine, updates, migration_tags)
    second_run = apply_pending_sql_updates(engine, updates, migration_tags)

    assert first_run == ("add-two.v1", "multiply.v1")
    assert second_run == ()
    with engine.connect() as connection:
        assert connection.scalar(select(values.c.value)) == 30


def test_failed_group_rolls_back_commands_and_does_not_store_tag(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
    values, migration_tags = create_test_tables(engine)
    with engine.begin() as connection:
        connection.execute(values.insert().values(value=1))
    updates = parse_sql_updates("""
-- tag: failure.v1
ALTER TABLE migration_test_values ADD COLUMN changed INTEGER NOT NULL DEFAULT 0;
UPDATE migration_test_values SET value = 99, changed = 1;
UPDATE table_that_does_not_exist SET value = 1;
""")

    with pytest.raises(Exception, match="table_that_does_not_exist"):
        apply_pending_sql_updates(engine, updates, migration_tags)

    with engine.connect() as connection:
        assert connection.scalar(select(values.c.value)) == 1
        assert connection.scalar(
            select(migration_tags.c.tag).where(migration_tags.c.tag == "failure.v1")
        ) is None
    assert "changed" not in {
        column["name"] for column in inspect(engine).get_columns("migration_test_values")
    }


def test_parser_rejects_duplicate_tags_and_multiline_commands() -> None:
    with pytest.raises(ValueError, match="Duplicate database update tag"):
        parse_sql_updates("""
-- tag: duplicate.v1
SELECT 1;
-- tag: duplicate.v1
SELECT 2;
""")

    with pytest.raises(ValueError, match="must end with a semicolon"):
        parse_sql_updates("""
-- tag: multiline.v1
UPDATE example
SET value = 1;
""")


def test_dialect_commands_and_baseline_stamping(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
    values, migration_tags = create_test_tables(engine)
    with engine.begin() as connection:
        connection.execute(values.insert().values(value=1))
    updates = parse_sql_updates("""
-- tag: dialect.v1
-- dialect: postgresql
UPDATE migration_test_values SET value = 100;
-- dialect: sqlite
UPDATE migration_test_values SET value = 2;
""")

    stamp_sql_updates(engine, updates, migration_tags)
    assert apply_pending_sql_updates(engine, updates, migration_tags) == ()
    with engine.connect() as connection:
        assert connection.scalar(select(values.c.value)) == 1
        assert connection.scalar(select(migration_tags.c.tag)) == "dialect.v1"


def test_repository_update_normalizes_legacy_identity_permission_ids(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
    execute_initial_sql(engine, parse_initial_sql(initial_file_contents()))
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE identity_privilege_datasource_permissions (
                id INTEGER PRIMARY KEY,
                connector_id INTEGER NOT NULL,
                identity_id VARCHAR(512) NOT NULL,
                allowed BOOLEAN NOT NULL,
                UNIQUE (connector_id, identity_id)
            )
        """)
        connection.exec_driver_sql("""
            CREATE TABLE identity_privilege_table_permissions (
                id INTEGER PRIMARY KEY,
                connector_id INTEGER NOT NULL,
                table_name VARCHAR(512) NOT NULL,
                identity_id VARCHAR(512) NOT NULL,
                denied BOOLEAN NOT NULL,
                UNIQUE (connector_id, table_name, identity_id)
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO admin_users
                (id, username, display_name, auth_provider, role, is_system_admin,
                 enterprise_access, password_hash, must_change_password, is_provisioned,
                 created_at, updated_at)
            VALUES
                (7, 'admin', '', 'local', 'admin', 1, 1, 'hash', 0, 0,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (8, 'ada', 'Ada', 'ldap', 'user', 0, 1, 'external$disabled', 0, 0,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        connection.exec_driver_sql("""
            INSERT INTO identity_privilege_datasource_permissions
                (id, connector_id, identity_id, allowed)
            VALUES
                (1, 10, 'local:7', 1),
                (2, 10, 'local:admin', 1),
                (3, 10, '7', 1),
                (4, 11, 'ldap:ada', 1)
        """)
        connection.exec_driver_sql("""
            INSERT INTO identity_privilege_table_permissions
                (id, connector_id, table_name, identity_id, denied)
            VALUES
                (1, 10, 'orders', 'local:7', 1),
                (2, 10, 'orders', 'local:admin', 1),
                (3, 11, 'patients', 'ldap:ada', 1)
        """)

    migration_tags = as_table(DatabaseMigrationTag.__table__)
    applied = apply_pending_sql_updates(
        engine,
        parse_sql_updates(update_file_contents()),
        migration_tags,
    )

    assert applied == (
        "2026-07-31.identity-ids-use-admin-user-id.v1",
        "2026-08-01.dashboard-sharing.v1",
        "2026-08-12.duckdb-file-connector.initial.v1",
        "2026-08-14.duckdb-file-connector.migrate-duckdb-excel.v1",
    )
    with engine.connect() as connection:
        datasource_ids = connection.exec_driver_sql(
            "SELECT connector_id, identity_id "
            "FROM identity_privilege_datasource_permissions ORDER BY connector_id"
        ).all()
        table_ids = connection.exec_driver_sql(
            "SELECT connector_id, table_name, identity_id "
            "FROM identity_privilege_table_permissions ORDER BY connector_id"
        ).all()
    assert [tuple(row) for row in datasource_ids] == [(10, "7"), (11, "8")]
    assert [tuple(row) for row in table_ids] == [
        (10, "orders", "7"),
        (11, "patients", "8"),
    ]


def test_repository_update_can_be_applied_without_optional_permission_tables(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
    execute_initial_sql(engine, parse_initial_sql(initial_file_contents()))
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            INSERT INTO datasource_connectors
                (id, connector_key, name, database_type, database_url, sql_dialect,
                 active, created_at, updated_at, updated_by)
            VALUES
                (10, 'legacy-excel', 'Legacy Excel', 'duckdb-excel',
                 'duckdb-excel:///C:/uploads/cases.xlsx', 'duckdb', 0,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'test'),
                (11, 'sqlite-source', 'SQLite', 'sqlite', 'sqlite:///data.db',
                 'sqlite', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'test')
        """)
    migration_tags = as_table(DatabaseMigrationTag.__table__)

    applied = apply_pending_sql_updates(
        engine,
        parse_sql_updates(update_file_contents()),
        migration_tags,
    )

    assert applied == (
        "2026-07-31.identity-ids-use-admin-user-id.v1",
        "2026-08-01.dashboard-sharing.v1",
        "2026-08-12.duckdb-file-connector.initial.v1",
        "2026-08-14.duckdb-file-connector.migrate-duckdb-excel.v1",
    )
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT database_type, database_url FROM datasource_connectors "
            "WHERE connector_key = 'legacy-excel'"
        ).one() == (
            "duckdb-file",
            "duckdb-file:///C:/uploads/cases.xlsx",
        )
        assert connection.exec_driver_sql(
            "SELECT database_type, database_url FROM datasource_connectors "
            "WHERE connector_key = 'sqlite-source'"
        ).one() == ("sqlite", "sqlite:///data.db")


def test_initial_schema_normalizes_legacy_duckdb_excel_connectors(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
    commands = parse_initial_sql(initial_file_contents())
    execute_initial_sql(engine, commands)
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            INSERT INTO datasource_connectors
                (id, connector_key, name, database_type, database_url, sql_dialect,
                 active, created_at, updated_at, updated_by)
            VALUES
                (10, 'legacy-excel', 'Legacy Excel', 'duckdb-excel',
                 'duckdb-excel:///C:/uploads/cases.xlsx', 'duckdb', 0,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'test')
        """)

    execute_initial_sql(engine, commands)

    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT database_type, database_url FROM datasource_connectors "
            "WHERE connector_key = 'legacy-excel'"
        ).one() == (
            "duckdb-file",
            "duckdb-file:///C:/uploads/cases.xlsx",
        )


def test_current_updates_leave_legacy_file_import_metadata_unchanged(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
    migration_tags = as_table(DatabaseMigrationTag.__table__)
    migration_tags.create(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE duckdb_file_imports ("
            "id VARCHAR(36) PRIMARY KEY, mode VARCHAR(16) NOT NULL, "
            "original_filename VARCHAR(1024) NOT NULL, status VARCHAR(16) NOT NULL, "
            "database_url VARCHAR(2048), storage_key VARCHAR(255) NOT NULL UNIQUE, "
            "options_json TEXT NOT NULL, created_at DATETIME NOT NULL, "
            "started_at DATETIME, completed_at DATETIME, error_message TEXT)"
        )
        connection.exec_driver_sql(
            "INSERT INTO duckdb_file_imports "
            "(id, mode, original_filename, status, storage_key, options_json, created_at) "
            "VALUES ('import-1', 'file', 'C:/data/source', 'uploaded', 'source', '{}', "
            "CURRENT_TIMESTAMP)"
        )
        connection.execute(
            migration_tags.insert(),
            [
                {"tag": "2026-07-31.identity-ids-use-admin-user-id.v1"},
                {"tag": "2026-08-12.duckdb-file-connector.initial.v1"},
            ],
        )

    applied = apply_pending_sql_updates(
        engine,
        parse_sql_updates(update_file_contents()),
        migration_tags,
    )

    assert applied == (
        "2026-08-14.duckdb-file-connector.migrate-duckdb-excel.v1",
    )
    columns = {
        column["name"] for column in inspect(engine).get_columns("duckdb_file_imports")
    }
    assert columns >= {
        "mode",
        "original_filename",
        "status",
    }
    assert "source_directory" not in columns
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT mode, original_filename, status FROM duckdb_file_imports"
        ).one() == ("file", "C:/data/source", "uploaded")


def test_legacy_repair_adds_all_pre_tag_core_schema_changes(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE admin_users (
                id INTEGER NOT NULL PRIMARY KEY,
                username VARCHAR(255) NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                must_change_password BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO admin_users
                (id, username, password_hash, must_change_password, created_at, updated_at)
            VALUES (1, 'admin', 'hash', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        connection.exec_driver_sql("""
            CREATE TABLE admin_sessions (
                id INTEGER NOT NULL PRIMARY KEY,
                token_hash VARCHAR(128) NOT NULL,
                user_id INTEGER NOT NULL,
                created_at DATETIME NOT NULL
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO admin_sessions (id, token_hash, user_id, created_at)
            VALUES (1, 'legacy', 1, CURRENT_TIMESTAMP)
        """)
        connection.exec_driver_sql("""
            CREATE TABLE data_query_audit_logs (
                id INTEGER NOT NULL PRIMARY KEY,
                occurred_at DATETIME NOT NULL,
                user_id VARCHAR(255) NOT NULL,
                datasource_id VARCHAR(255) NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                sql TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
        """)
        connection.exec_driver_sql("""
            CREATE TABLE overview_widgets (
                id INTEGER NOT NULL PRIMARY KEY,
                widget_key VARCHAR(255) NOT NULL UNIQUE,
                label VARCHAR(255) NOT NULL,
                widget_type VARCHAR(50) NOT NULL,
                datasource_key VARCHAR(255) NOT NULL,
                question TEXT NOT NULL,
                position INTEGER NOT NULL,
                active BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                updated_by VARCHAR(255) NOT NULL
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO overview_widgets
                (id, widget_key, label, widget_type, datasource_key, question,
                 position, active, created_at, updated_at, updated_by)
            VALUES
                (1, 'legacy-table', 'Legacy', 'table', 'metadata-db', 'question',
                 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'system')
        """)

    legacy_commands = parse_legacy_sql(legacy_file_contents())
    execute_legacy_sql_phase(engine, legacy_commands, "before-initial")
    execute_initial_sql(engine, parse_initial_sql(initial_file_contents()))
    execute_legacy_sql_phase(engine, legacy_commands, "after-initial")

    inspector = inspect(engine)
    assert {
        "display_name",
        "auth_provider",
        "role",
        "is_system_admin",
        "enterprise_access",
        "is_provisioned",
    }.issubset(
        {column["name"] for column in inspector.get_columns("admin_users")}
    )
    assert {"username", "role", "auth_provider", "last_seen"}.issubset(
        {column["name"] for column in inspector.get_columns("admin_sessions")}
    )
    assert {"type", "output_classification", "llm_sql_language"}.issubset(
        {column["name"] for column in inspector.get_columns("data_query_audit_logs")}
    )
    assert {"sql", "grid_width", "grid_height", "result_mode"}.issubset(
        {column["name"] for column in inspector.get_columns("overview_widgets")}
    )
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT is_system_admin, enterprise_access FROM admin_users WHERE id = 1"
        ).one() == (1, 1)
        assert connection.exec_driver_sql(
            "SELECT last_seen FROM admin_sessions WHERE id = 1"
        ).scalar_one() is not None
        assert connection.exec_driver_sql(
            "SELECT grid_height FROM overview_widgets WHERE id = 1"
        ).scalar_one() == 4


@pytest.mark.parametrize("first_tag_already_present", [False, True])
def test_startup_repairs_legacy_database_before_app_queries_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_tag_already_present: bool,
) -> None:
    database_path = tmp_path / "metadata.db"
    legacy_engine = create_engine(f"sqlite:///{database_path}")
    with legacy_engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE admin_users (
                id INTEGER NOT NULL PRIMARY KEY,
                username VARCHAR(255) NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                must_change_password BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO admin_users
                (id, username, password_hash, must_change_password, created_at, updated_at)
            VALUES (1, 'admin', 'hash', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        if first_tag_already_present:
            connection.exec_driver_sql("""
                CREATE TABLE database_migration_tags (
                    tag VARCHAR(255) NOT NULL PRIMARY KEY,
                    applied_at DATETIME NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO database_migration_tags (tag, applied_at)
                VALUES (
                    '2026-07-31.identity-ids-use-admin-user-id.v1',
                    CURRENT_TIMESTAMP
                )
            """)
    legacy_engine.dispose()

    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{database_path}",
    )
    reset_metadata_store_for_tests()
    try:
        init_metadata_store()
        engine = get_engine()
        assert "is_system_admin" in {
            column["name"] for column in inspect(engine).get_columns("admin_users")
        }
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT is_system_admin FROM admin_users WHERE id = 1"
            ).scalar_one() == 1
            assert set(
                connection.exec_driver_sql(
                    "SELECT tag FROM database_migration_tags"
                ).scalars()
            ) == {
                "2026-07-31.identity-ids-use-admin-user-id.v1",
                "2026-08-01.dashboard-sharing.v1",
                "2026-08-12.duckdb-file-connector.initial.v1",
                "2026-08-14.duckdb-file-connector.migrate-duckdb-excel.v1",
            }
    finally:
        reset_metadata_store_for_tests()


def test_legacy_repair_centralizes_metadata_extension_alters(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE external_api_configs (id VARCHAR(64) PRIMARY KEY)"
        )
        connection.exec_driver_sql("""
            CREATE TABLE extract_blueprints (
                id VARCHAR(64) PRIMARY KEY,
                blueprint_key VARCHAR(255) NOT NULL UNIQUE,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                domain_description TEXT,
                case_grain_description TEXT,
                language VARCHAR(20) NOT NULL DEFAULT 'pl',
                status VARCHAR(50) NOT NULL DEFAULT 'draft',
                config_json TEXT NOT NULL,
                json_schema TEXT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO extract_blueprints
                (id, blueprint_key, name, domain_description,
                 case_grain_description, config_json, json_schema)
            VALUES
                ('bp-1', 'legacy', 'Legacy', 'domain', 'grain', '{}',
                 '{"type":"object"}')
        """)
        connection.exec_driver_sql("""
            CREATE TABLE extract_unstructured_source_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                datasource_connector_id INTEGER NOT NULL,
                datasource_connector_key VARCHAR(255) NOT NULL,
                main_table VARCHAR(255) NOT NULL,
                case_id_column VARCHAR(255) NOT NULL,
                content_column VARCHAR(255) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_by VARCHAR(255) NOT NULL DEFAULT 'system',
                UNIQUE (datasource_connector_id)
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO extract_unstructured_source_models
                (datasource_connector_id, datasource_connector_key, main_table,
                 case_id_column, content_column, updated_by)
            VALUES (1, 'notes-db', 'case_notes', 'case_id', 'note_text', 'tester')
        """)

    legacy_commands = parse_legacy_sql(legacy_file_contents())
    execute_legacy_sql_phase(engine, legacy_commands, "before-initial")
    execute_initial_sql(engine, parse_initial_sql(initial_file_contents()))
    execute_legacy_sql_phase(engine, legacy_commands, "after-initial")

    inspector = inspect(engine)
    assert "resources_json" in {
        column["name"] for column in inspector.get_columns("external_api_configs")
    }
    assert {"json_schema_json", "updated_by"}.issubset(
        {column["name"] for column in inspector.get_columns("extract_blueprints")}
    )
    source_columns = {
        column["name"]
        for column in inspector.get_columns("extract_unstructured_source_models")
    }
    assert "table_roles_json" in source_columns
    assert "case_id_column" not in source_columns
    assert "content_column" not in source_columns
    with engine.connect() as connection:
        blueprint = connection.exec_driver_sql(
            "SELECT json_schema_json, updated_by FROM extract_blueprints WHERE id = 'bp-1'"
        ).one()
        table_roles_json = connection.exec_driver_sql(
            "SELECT table_roles_json FROM extract_unstructured_source_models WHERE id = 1"
        ).scalar_one()
    assert blueprint == ('{"type":"object"}', "system")
    assert table_roles_json == (
        '{"case_notes":{"case_id_column":"case_id",'
        '"content_column":"note_text"}}'
    )
