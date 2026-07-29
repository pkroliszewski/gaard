from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gaard_plugin_api import ExtensionContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from gaard_connectors.odbc.config import (
    TESTED_DRIVER_NAMES,
    parse_odbc_sqlalchemy_url,
    sql_dialect_for_sqlalchemy_drivername,
    validate_odbc_database_url,
)
from gaard_connectors.odbc.connection_string import parse_odbc_connection_string
from gaard_connectors.odbc.diagnostics import collect_diagnostics
from gaard_connectors.odbc.exceptions import OdbcConnectorError, OdbcErrorCode
from gaard_connectors.registry import ConnectorDefinition, ConnectorRegistry, ConnectorRegistryError
from gaard_connectors.sqlalchemy.executor import SQLAlchemyQueryExecutor
from gaard_connectors.sqlalchemy.introspector import SQLAlchemySchemaIntrospector

CONNECTOR_TYPE_KEY = "odbc"


@dataclass(frozen=True, slots=True)
class OdbcConnectionTestResult:
    success: bool
    connection_success: bool
    schema_introspection_success: bool | None
    dialect_name: str | None = None
    driver_name: str | None = None
    server_version: str | None = None
    installed_odbc_drivers: list[str] | None = None
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    diagnostic_details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OdbcCapabilities:
    can_connect: bool
    can_execute_sql: bool
    can_inspect_schemas: bool
    can_inspect_tables: bool
    can_inspect_views: bool
    can_inspect_columns: bool
    can_inspect_primary_keys: bool
    can_inspect_foreign_keys: bool
    can_inspect_indexes: bool
    supports_transactions: bool | None


class OdbcQueryExecutor(SQLAlchemyQueryExecutor):
    def __init__(self, database_url: str, max_rows: int = 100) -> None:
        super().__init__(database_url=database_url, max_rows=max_rows)

    def _create_engine(self, database_url: str) -> Engine:
        return create_odbc_engine(database_url)


class OdbcSchemaIntrospector(SQLAlchemySchemaIntrospector):
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine = create_odbc_engine(database_url)


def register(context: ExtensionContext) -> None:
    if not isinstance(context.registry, ConnectorRegistry):
        raise ConnectorRegistryError("ODBC connector requires a ConnectorRegistry.")

    context.registry.register(create_connector_definition())


def create_connector_definition() -> ConnectorDefinition:
    return ConnectorDefinition(
        type_key=CONNECTOR_TYPE_KEY,
        label="ODBC / unixODBC",
        description="Connect to relational data sources through SQLAlchemy, pyodbc and unixODBC.",
        sql_dialects=("tsql", "postgres", "mysql", "oracle", "db2", "teradata"),
        url_prefixes=("mssql+pyodbc://",),
        database_url_validator=validate_odbc_database_url,
        executor_factory=lambda database_url, max_rows: OdbcQueryExecutor(
            database_url=database_url,
            max_rows=max_rows,
        ),
        introspector_factory=lambda database_url: OdbcSchemaIntrospector(
            database_url=database_url,
        ),
        connection_tester=test_odbc_connection,
        config_schema={
            "type": "object",
            "properties": {
                "connection_mode": {
                    "type": "string",
                    "title": "Connection mode",
                    "enum": ["dsn", "dsnless"],
                    "default": "dsn",
                },
                "sqlalchemy_drivername": {
                    "type": "string",
                    "title": "SQLAlchemy dialect",
                    "default": "mssql+pyodbc",
                    "description": "Use a real SQLAlchemy dialect plus pyodbc, for example mssql+pyodbc.",
                },
                "dsn": {"type": "string", "title": "DSN"},
                "odbc_driver": {
                    "type": "string",
                    "title": "ODBC driver",
                    "default": "ODBC Driver 18 for SQL Server",
                },
                "host": {
                    "type": "string",
                    "title": "Host",
                    "description": "Remote database host or ODBC bridge endpoint reachable from GAARD.",
                },
                "port": {"type": "integer", "title": "Port", "default": 1433},
                "database": {"type": "string", "title": "Database"},
                "username": {"type": "string", "title": "Username"},
                "password": {"type": "string", "title": "Password", "format": "password"},
                "connect_timeout_seconds": {
                    "type": "integer",
                    "title": "Connection timeout",
                    "default": 15,
                },
                "query_timeout_seconds": {"type": "integer", "title": "Query timeout"},
                "pool_recycle_seconds": {
                    "type": "integer",
                    "title": "Pool recycle",
                    "default": 1800,
                },
                "pool_pre_ping": {
                    "type": "boolean",
                    "title": "Pool pre-ping",
                    "default": True,
                },
                "pyodbc_pooling": {
                    "type": "boolean",
                    "title": "pyodbc pooling",
                    "default": False,
                },
                "extra_odbc_options": {
                    "type": "object",
                    "title": "Additional ODBC options",
                    "description": "Enter key=value lines, for example Encrypt=yes.",
                },
                "raw_odbc_connection_string": {
                    "type": "string",
                    "title": "Raw ODBC connection string",
                    "format": "password",
                },
            },
            "required": ["connection_mode", "sqlalchemy_drivername"],
            "tested_driver_names": list(TESTED_DRIVER_NAMES),
        },
    )


def create_odbc_engine(database_url: str) -> Engine:
    validate_odbc_database_url(database_url)
    parsed = make_url(database_url)
    _drivername, connection_string = parse_odbc_sqlalchemy_url(database_url)
    _configure_pyodbc_pooling(_query_bool(parsed.query, "gaard_pyodbc_pooling", default=False))
    return create_engine(
        database_url,
        pool_pre_ping=_query_bool(parsed.query, "gaard_pool_pre_ping", default=True),
        pool_recycle=_query_int(parsed.query, "gaard_pool_recycle_seconds", default=1800),
        connect_args=_build_connect_args(parsed.query, connection_string),
    )


def test_odbc_connection(database_url: str) -> None:
    result = run_odbc_connection_test(database_url)
    if result.success:
        return
    raise OdbcConnectorError(
        result.error_message or "ODBC connection test failed.",
        code=OdbcErrorCode(result.error_code or OdbcErrorCode.CONNECTION_FAILED),
        diagnostic_details=result.diagnostic_details,
    )


def run_odbc_connection_test(database_url: str) -> OdbcConnectionTestResult:
    diagnostics = collect_diagnostics()
    installed_drivers = diagnostics.installed_drivers

    if diagnostics.pyodbc_version is None:
        return OdbcConnectionTestResult(
            success=False,
            connection_success=False,
            schema_introspection_success=None,
            installed_odbc_drivers=installed_drivers,
            error_code=OdbcErrorCode.DEPENDENCY_MISSING.value,
            error_message=(
                'The Python package "pyodbc" is not installed. '
                "Install the ODBC connector dependencies and try again."
            ),
            diagnostic_details=diagnostics.serialize(),
        )

    try:
        drivername, connection_string = parse_odbc_sqlalchemy_url(database_url)
        validate_odbc_database_url(database_url)
    except OdbcConnectorError as exc:
        return _failed_result(exc, installed_drivers=installed_drivers)

    configured_driver = _configured_odbc_driver(connection_string)
    if configured_driver and configured_driver not in installed_drivers:
        return OdbcConnectionTestResult(
            success=False,
            connection_success=False,
            schema_introspection_success=None,
            installed_odbc_drivers=installed_drivers,
            error_code=OdbcErrorCode.DRIVER_NOT_FOUND.value,
            error_message=f'ODBC driver "{configured_driver}" was not found.',
            diagnostic_details={
                "configured_driver": configured_driver,
                "installed_drivers": installed_drivers,
            },
        )

    warnings = []
    if drivername not in TESTED_DRIVER_NAMES:
        warnings.append(
            f"SQLAlchemy drivername '{drivername}' is available but not officially tested by GAARD."
        )

    engine = create_odbc_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
            dialect = connection.dialect
            schema_success = None
            try:
                inspect(connection).get_schema_names()
                schema_success = True
            except SQLAlchemyError:
                schema_success = False
            return OdbcConnectionTestResult(
                success=True,
                connection_success=True,
                schema_introspection_success=schema_success,
                dialect_name=sql_dialect_for_sqlalchemy_drivername(drivername),
                driver_name=drivername,
                server_version=str(getattr(dialect, "server_version_info", "") or "") or None,
                installed_odbc_drivers=installed_drivers,
                warnings=warnings,
            )
    except SQLAlchemyError as exc:
        return OdbcConnectionTestResult(
            success=False,
            connection_success=False,
            schema_introspection_success=None,
            installed_odbc_drivers=installed_drivers,
            warnings=warnings,
            error_code=_classify_sqlalchemy_error(exc).value,
            error_message=_safe_connection_error_message(exc),
            diagnostic_details=_safe_sqlalchemy_diagnostics(exc),
        )
    finally:
        engine.dispose()


def capabilities_from_test_result(result: OdbcConnectionTestResult) -> OdbcCapabilities:
    return OdbcCapabilities(
        can_connect=result.connection_success,
        can_execute_sql=result.connection_success,
        can_inspect_schemas=bool(result.schema_introspection_success),
        can_inspect_tables=bool(result.schema_introspection_success),
        can_inspect_views=bool(result.schema_introspection_success),
        can_inspect_columns=bool(result.schema_introspection_success),
        can_inspect_primary_keys=bool(result.schema_introspection_success),
        can_inspect_foreign_keys=bool(result.schema_introspection_success),
        can_inspect_indexes=bool(result.schema_introspection_success),
        supports_transactions=None,
    )


def _configure_pyodbc_pooling(enabled: bool) -> None:
    try:
        import pyodbc  # type: ignore[import-not-found]
    except ImportError as exc:
        raise OdbcConnectorError(
            'The Python package "pyodbc" is not installed. '
            "Install the ODBC connector dependencies and try again.",
            code=OdbcErrorCode.DEPENDENCY_MISSING,
        ) from exc
    pyodbc.pooling = enabled


def _build_connect_args(query: Any, connection_string: str) -> dict[str, int]:
    configured_timeout = _query_int(query, "gaard_connect_timeout_seconds", default=15)
    for key, value in parse_odbc_connection_string(connection_string):
        if key.strip().casefold() in {"connection timeout", "connecttimeout", "timeout"}:
            try:
                return {"timeout": int(value)}
            except ValueError:
                return {"timeout": configured_timeout}
    return {"timeout": configured_timeout}


def _query_value(query: Any, key: str) -> str | None:
    value = query.get(key)
    if isinstance(value, tuple):
        value = value[0] if value else None
    if value in (None, ""):
        return None
    return str(value)


def _query_int(query: Any, key: str, *, default: int) -> int:
    value = _query_value(query, key)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(0, parsed)


def _query_bool(query: Any, key: str, *, default: bool) -> bool:
    value = _query_value(query, key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _configured_odbc_driver(connection_string: str) -> str | None:
    for key, value in parse_odbc_connection_string(connection_string):
        if key.strip().casefold() == "driver":
            return value
    return None


def _failed_result(
    exc: OdbcConnectorError,
    *,
    installed_drivers: list[str],
) -> OdbcConnectionTestResult:
    return OdbcConnectionTestResult(
        success=False,
        connection_success=False,
        schema_introspection_success=None,
        installed_odbc_drivers=installed_drivers,
        error_code=exc.code.value,
        error_message=str(exc),
        diagnostic_details=exc.diagnostic_details,
    )


def _classify_sqlalchemy_error(exc: SQLAlchemyError) -> OdbcErrorCode:
    sqlstate = _extract_sqlstate(exc)
    if sqlstate in {"28000", "28P01"}:
        return OdbcErrorCode.AUTHENTICATION_FAILED
    if sqlstate and sqlstate.startswith("08"):
        return OdbcErrorCode.CONNECTION_FAILED
    if sqlstate in {"HYT00", "HYT01"}:
        return OdbcErrorCode.TIMEOUT
    return OdbcErrorCode.CONNECTION_FAILED


def _extract_sqlstate(exc: BaseException) -> str | None:
    original = getattr(exc, "orig", None)
    args = getattr(original, "args", ()) or ()
    if args and isinstance(args[0], str) and args[0]:
        return args[0]
    return None


def _safe_sqlalchemy_diagnostics(exc: SQLAlchemyError) -> dict[str, Any]:
    details: dict[str, Any] = {}
    sqlstate = _extract_sqlstate(exc)
    if sqlstate:
        details["sqlstate"] = sqlstate
    return details


def _safe_connection_error_message(exc: SQLAlchemyError) -> str:
    sqlstate = _extract_sqlstate(exc)
    if sqlstate:
        return (
            "Could not connect through unixODBC. "
            f"SQLSTATE: {sqlstate}. Verify the server address, port, installed ODBC "
            "driver and network access."
        )
    return (
        "Could not connect through unixODBC. Verify the server address, port, installed "
        "ODBC driver and network access."
    )
