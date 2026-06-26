from pathlib import Path

from sqlalchemy import create_engine, text

from gaard_connectors.registry import ConnectorDefinition, ConnectorRegistry, ConnectorRegistryError
from gaard_connectors.sqlalchemy.executor import SQLAlchemyQueryExecutor
from gaard_connectors.sqlalchemy.introspector import SQLAlchemySchemaIntrospector
from gaard_plugin_api import ExtensionContext


CONNECTOR_TYPE_KEY = "example-custom-sqlite"
CUSTOM_SQLITE_URL_PREFIX = "example-custom-sqlite://"


def register(context: ExtensionContext) -> None:
    if not isinstance(context.registry, ConnectorRegistry):
        raise ConnectorRegistryError("Example custom connector requires a ConnectorRegistry.")

    context.registry.register(create_connector_definition())


def create_connector_definition() -> ConnectorDefinition:
    return ConnectorDefinition(
        type_key=CONNECTOR_TYPE_KEY,
        label="Example Custom SQLite",
        description=(
            "Copyable public connector example. It uses a custom URL scheme "
            "and delegates execution to SQLite."
        ),
        sql_dialects=("sqlite",),
        url_prefixes=(CUSTOM_SQLITE_URL_PREFIX,),
        executor_factory=lambda database_url, max_rows: SQLAlchemyQueryExecutor(
            database_url=to_sqlite_url(database_url),
            max_rows=max_rows,
        ),
        introspector_factory=lambda database_url: SQLAlchemySchemaIntrospector(
            database_url=to_sqlite_url(database_url),
        ),
        connection_tester=test_connection,
        config_schema={
            "type": "object",
            "properties": {
                "database_url": {
                    "type": "string",
                    "format": "uri",
                    "title": "Example custom SQLite URL",
                    "description": (
                        "Use example-custom-sqlite:///absolute/path/to/database.db"
                    ),
                }
            },
            "required": ["database_url"],
        },
    )


def to_custom_sqlite_url(database_path: Path) -> str:
    return f"{CUSTOM_SQLITE_URL_PREFIX}{database_path.resolve()}"


def to_sqlite_url(database_url: str) -> str:
    if not database_url.startswith(CUSTOM_SQLITE_URL_PREFIX):
        raise ValueError(
            f"Example custom SQLite URL must start with {CUSTOM_SQLITE_URL_PREFIX}."
        )

    path = database_url.removeprefix(CUSTOM_SQLITE_URL_PREFIX)
    if not path:
        raise ValueError("Example custom SQLite URL must include a database path.")

    if path == "/:memory:":
        return "sqlite:///:memory:"

    return f"sqlite:///{path}"


def test_connection(database_url: str) -> None:
    engine = create_engine(
        to_sqlite_url(database_url),
        connect_args={"check_same_thread": False},
    )

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()
