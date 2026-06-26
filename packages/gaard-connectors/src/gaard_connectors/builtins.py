from sqlalchemy import create_engine, text

from gaard_connectors.registry import ConnectorDefinition, ConnectorRegistry
from gaard_connectors.sqlalchemy.executor import SQLAlchemyQueryExecutor
from gaard_connectors.sqlalchemy.introspector import SQLAlchemySchemaIntrospector


def create_builtin_connector_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()

    for definition in (
        _sqlalchemy_connector(
            type_key="sqlite",
            label="SQLite",
            sql_dialect="sqlite",
            url_prefixes=("sqlite://",),
            description="SQLite database accessed through SQLAlchemy.",
        ),
        _sqlalchemy_connector(
            type_key="postgresql",
            label="PostgreSQL",
            sql_dialect="postgres",
            url_prefixes=("postgresql://", "postgresql+psycopg://"),
            description="PostgreSQL database accessed through SQLAlchemy.",
        ),
        _sqlalchemy_connector(
            type_key="mysql",
            label="MySQL",
            sql_dialect="mysql",
            url_prefixes=("mysql://", "mysql+pymysql://"),
            description="MySQL database accessed through SQLAlchemy.",
        ),
    ):
        registry.register(definition)

    return registry


def _sqlalchemy_connector(
    type_key: str,
    label: str,
    sql_dialect: str,
    url_prefixes: tuple[str, ...],
    description: str,
) -> ConnectorDefinition:
    return ConnectorDefinition(
        type_key=type_key,
        label=label,
        sql_dialects=(sql_dialect,),
        url_prefixes=url_prefixes,
        executor_factory=lambda database_url, max_rows: SQLAlchemyQueryExecutor(
            database_url=database_url,
            max_rows=max_rows,
        ),
        introspector_factory=lambda database_url: SQLAlchemySchemaIntrospector(
            database_url=database_url,
        ),
        connection_tester=_test_sqlalchemy_connection,
        config_schema={
            "type": "object",
            "properties": {
                "database_url": {
                    "type": "string",
                    "format": "uri",
                    "title": "Database URL",
                }
            },
            "required": ["database_url"],
        },
        description=description,
    )


def _test_sqlalchemy_connection(database_url: str) -> None:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()
