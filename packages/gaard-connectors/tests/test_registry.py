import pytest

from gaard_plugin_api import ExtensionContext, ExtensionManager, ExtensionManifest

from gaard_connectors import (
    ConnectorDefinition,
    ConnectorNotFoundError,
    ConnectorRegistry,
    DuplicateConnectorTypeError,
    create_builtin_connector_registry,
)


class FakeEntryPoint:
    name = "private-warehouse"

    def load(self) -> ExtensionManifest:
        return ExtensionManifest(
            id="private-warehouse",
            version="1.0.0",
            contributions={"connectors": "private_warehouse:register"},
        )


def test_builtin_registry_exposes_existing_datasource_types() -> None:
    registry = create_builtin_connector_registry()

    assert [definition.type_key for definition in registry.list()] == [
        "ibm_db2",
        "mssql",
        "mysql",
        "odbc",
        "oracle",
        "postgresql",
        "sqlite",
        "teradata",
    ]
    assert registry.get("postgresql").default_sql_dialect == "postgres"
    assert registry.get("mssql").default_sql_dialect == "tsql"
    assert registry.get("ibm_db2").default_sql_dialect == "db2"
    assert registry.detect_from_database_url("postgresql+psycopg://localhost/demo").type_key == (
        "postgresql"
    )
    assert registry.detect_from_database_url("oracle+oracledb://user:pass@host").type_key == (
        "oracle"
    )
    assert registry.detect_from_database_url("mssql+pyodbc://user:pass@host/db").type_key == (
        "mssql"
    )
    assert registry.detect_from_database_url("db2+ibm_db://user:pass@host/db").type_key == (
        "ibm_db2"
    )
    assert registry.detect_from_database_url("teradatasql://user:pass@host").type_key == (
        "teradata"
    )


def test_registry_validates_url_and_sql_dialect_for_the_selected_connector() -> None:
    registry = create_builtin_connector_registry()

    assert registry.validate("sqlite", "sqlite:///demo.db", "sqlite").type_key == "sqlite"

    with pytest.raises(ValueError, match="must start with one of"):
        registry.validate("sqlite", "postgresql://localhost/demo", "sqlite")

    with pytest.raises(ValueError, match="supports SQL dialects"):
        registry.validate("sqlite", "sqlite:///demo.db", "postgres")


def test_registry_rejects_duplicate_and_unknown_connector_types() -> None:
    registry = ConnectorRegistry()
    definition = ConnectorDefinition(
        type_key="example",
        label="Example",
        sql_dialects=("example",),
        url_prefixes=("example://",),
        executor_factory=lambda database_url, max_rows: None,  # type: ignore[arg-type]
        introspector_factory=lambda database_url: None,  # type: ignore[arg-type]
        connection_tester=lambda database_url: None,
    )
    registry.register(definition)

    with pytest.raises(DuplicateConnectorTypeError):
        registry.register(definition)

    with pytest.raises(ConnectorNotFoundError, match="Unsupported datasource type"):
        registry.get("missing")


def test_private_connector_contribution_registers_through_extension_manager() -> None:
    registry = create_builtin_connector_registry()
    definition = ConnectorDefinition(
        type_key="private-warehouse",
        label="Private Warehouse",
        sql_dialects=("postgres",),
        url_prefixes=("warehouse://",),
        executor_factory=lambda database_url, max_rows: None,  # type: ignore[arg-type]
        introspector_factory=lambda database_url: None,  # type: ignore[arg-type]
        connection_tester=lambda database_url: None,
        description="A connector supplied by a private extension package.",
    )

    def register(context: ExtensionContext) -> None:
        context.registry.register(definition)

    manager = ExtensionManager(
        entry_point_items=[FakeEntryPoint()],
        contribution_loader=lambda target: register,
    )

    activated = manager.activate("connectors", registry)

    assert [record.manifest.id for record in activated if record.manifest] == ["private-warehouse"]
    assert registry.get("private-warehouse") == definition
