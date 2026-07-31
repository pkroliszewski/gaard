from typing import Any

from sqlalchemy import create_engine, text

from gaard_connectors.odbc.connector import create_connector_definition as create_odbc_definition
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
        create_odbc_definition(),
        _sqlalchemy_connector(
            type_key="oracle",
            label="Oracle Database",
            sql_dialect="oracle",
            url_prefixes=("oracle://", "oracle+oracledb://", "oracle+cx_oracle://"),
            description="Oracle Database accessed through SQLAlchemy.",
        ),
        _sqlalchemy_connector(
            type_key="mssql",
            label="Microsoft SQL Server",
            sql_dialect="tsql",
            url_prefixes=("mssql://", "mssql+pyodbc://", "mssql+pymssql://"),
            description="Microsoft SQL Server database accessed through SQLAlchemy.",
        ),
        _sqlalchemy_connector(
            type_key="ibm_db2",
            label="IBM Db2",
            sql_dialect="db2",
            url_prefixes=("db2+ibm_db://", "ibm_db_sa://"),
            description="IBM Db2 database accessed through SQLAlchemy.",
        ),
        _sqlalchemy_connector(
            type_key="teradata",
            label="Teradata",
            sql_dialect="teradata",
            url_prefixes=("teradatasql://", "teradata://"),
            description="Teradata database accessed through SQLAlchemy.",
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
        config_schema=_sqlalchemy_config_schema(type_key),
        description=description,
    )


def _sqlalchemy_config_schema(type_key: str) -> dict[str, object]:
    database_url = {
        "type": "string",
        "format": "uri",
        "title": "Database URL",
    }

    properties: dict[str, dict[str, Any]] = {}

    if type_key == "sqlite":
        properties = {
            "database_url": database_url,
            "database_path": {
                "type": "string",
                "title": "Database path",
                "description": (
                    "Filesystem path to the database file. GAARD builds the SQLAlchemy URL "
                    "automatically."
                ),
            },
        }
        required = ["database_path"]
    elif type_key == "postgresql":
        properties = {
            "database_url": database_url,
            "host": {"type": "string", "title": "Host", "default": "localhost"},
            "port": {"type": "integer", "title": "Port", "default": 5432},
            "database": {"type": "string", "title": "Database"},
            "username": {"type": "string", "title": "Username"},
            "password": {"type": "string", "title": "Password", "format": "password"},
            "sslmode": {"type": "string", "title": "SSL mode", "default": ""},
        }
        required = ["host", "port", "database", "username"]
    elif type_key == "mysql":
        properties = {
            "database_url": database_url,
            "host": {"type": "string", "title": "Host", "default": "localhost"},
            "port": {"type": "integer", "title": "Port", "default": 3306},
            "database": {"type": "string", "title": "Database"},
            "username": {"type": "string", "title": "Username"},
            "password": {"type": "string", "title": "Password", "format": "password"},
            "charset": {"type": "string", "title": "Charset", "default": "utf8mb4"},
        }
        required = ["host", "port", "database", "username"]
    elif type_key == "oracle":
        properties = {
            "database_url": database_url,
            "host": {"type": "string", "title": "Host", "default": "localhost"},
            "port": {"type": "integer", "title": "Port", "default": 1521},
            "service_name": {"type": "string", "title": "Service name"},
            "username": {"type": "string", "title": "Username"},
            "password": {"type": "string", "title": "Password", "format": "password"},
        }
        required = ["host", "port", "service_name", "username"]
    elif type_key == "mssql":
        properties = {
            "database_url": database_url,
            "host": {"type": "string", "title": "Host", "default": "localhost"},
            "port": {"type": "integer", "title": "Port", "default": 1433},
            "database": {"type": "string", "title": "Database"},
            "username": {"type": "string", "title": "Username"},
            "password": {"type": "string", "title": "Password", "format": "password"},
            "driver": {
                "type": "string",
                "title": "ODBC driver",
                "default": "ODBC Driver 18 for SQL Server",
            },
            "Encrypt": {"type": "string", "title": "Encrypt", "default": "yes"},
            "TrustServerCertificate": {
                "type": "string",
                "title": "Trust server certificate",
                "default": "no",
            },
        }
        required = ["host", "port", "database", "username"]
    elif type_key == "ibm_db2":
        properties = {
            "database_url": database_url,
            "host": {"type": "string", "title": "Host", "default": "localhost"},
            "port": {"type": "integer", "title": "Port", "default": 50000},
            "database": {"type": "string", "title": "Database"},
            "username": {"type": "string", "title": "Username"},
            "password": {"type": "string", "title": "Password", "format": "password"},
        }
        required = ["host", "port", "database", "username"]
    elif type_key == "teradata":
        properties = {
            "database_url": database_url,
            "host": {"type": "string", "title": "Host", "default": "localhost"},
            "dbs_port": {"type": "integer", "title": "Port", "default": 1025},
            "database": {"type": "string", "title": "Database"},
            "username": {"type": "string", "title": "Username"},
            "password": {"type": "string", "title": "Password", "format": "password"},
            "tmode": {"type": "string", "title": "Transaction mode", "default": "DEFAULT"},
        }
        required = ["host", "dbs_port", "username"]
    else:
        properties = {"database_url": database_url}
        required = ["database_url"]

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _test_sqlalchemy_connection(database_url: str) -> None:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()
