from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.dialects import registry as sqlalchemy_dialect_registry
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import NoSuchModuleError

from gaard_connectors.odbc.connection_string import (
    OdbcConnectionStringBuilder,
    parse_odbc_connection_string,
)
from gaard_connectors.odbc.exceptions import OdbcConnectorError, OdbcErrorCode

OdbcConnectionMode = Literal["dsn", "dsnless"]
TESTED_DRIVER_NAMES = ("mssql+pyodbc",)
_SQLALCHEMY_DRIVERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\+[a-z][a-z0-9_]*)$")
_DIALECT_MAP = {
    "mssql": "tsql",
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "oracle": "oracle",
    "db2": "db2",
    "ibm_db": "db2",
    "teradata": "teradata",
}


@dataclass(slots=True)
class OdbcConnectorConfig:
    connection_mode: OdbcConnectionMode
    sqlalchemy_drivername: str
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    connect_timeout_seconds: int = 15
    query_timeout_seconds: int | None = None
    pool_pre_ping: bool = True
    pool_recycle_seconds: int = 1800
    extra_odbc_options: dict[str, str] = field(default_factory=dict)
    engine_options: dict[str, int | float | str | bool] = field(default_factory=dict)
    pyodbc_pooling: bool = False
    dsn: str | None = None
    odbc_driver: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    raw_odbc_connection_string: str | None = field(default=None, repr=False)

    @classmethod
    def from_mapping(
        cls,
        value: dict[str, Any],
        *,
        existing_database_url: str | None = None,
    ) -> OdbcConnectorConfig:
        connection_mode = str(value.get("connection_mode") or "dsn").strip().lower()
        if connection_mode not in {"dsn", "dsnless"}:
            raise OdbcConnectorError(
                "ODBC connection mode must be either 'dsn' or 'dsnless'.",
                code=OdbcErrorCode.CONFIGURATION_INVALID,
            )

        sqlalchemy_drivername = str(value.get("sqlalchemy_drivername") or "mssql+pyodbc").strip()
        validate_sqlalchemy_drivername(sqlalchemy_drivername)

        password = _optional_secret(value.get("password"))
        if password is None and existing_database_url:
            password = extract_odbc_secret(existing_database_url, "PWD")

        raw_connection_string = _optional_secret(value.get("raw_odbc_connection_string"))
        if raw_connection_string in {"***", redact_existing_odbc_connect(existing_database_url)}:
            raw_connection_string = extract_odbc_connect(existing_database_url)

        config = cls(
            connection_mode=connection_mode,  # type: ignore[arg-type]
            sqlalchemy_drivername=sqlalchemy_drivername,
            username=_optional_text(value.get("username")),
            password=password,
            connect_timeout_seconds=_int_option(
                value.get("connect_timeout_seconds"),
                default=15,
                label="connection timeout",
            ),
            query_timeout_seconds=_optional_int_option(
                value.get("query_timeout_seconds"),
                label="query timeout",
            ),
            pool_pre_ping=_bool_option(value.get("pool_pre_ping"), default=True),
            pool_recycle_seconds=_int_option(
                value.get("pool_recycle_seconds"),
                default=1800,
                label="pool recycle",
            ),
            pyodbc_pooling=_bool_option(value.get("pyodbc_pooling"), default=False),
            extra_odbc_options=parse_extra_odbc_options(value.get("extra_odbc_options")),
            engine_options=parse_engine_options(value.get("engine_options")),
            dsn=_optional_text(value.get("dsn")),
            odbc_driver=_optional_text(value.get("odbc_driver")),
            host=_optional_text(value.get("host")),
            port=_optional_int_option(value.get("port"), label="port"),
            database=_optional_text(value.get("database")),
            raw_odbc_connection_string=raw_connection_string,
        )
        validate_odbc_config(config)
        return config


def validate_odbc_config(config: OdbcConnectorConfig) -> None:
    if config.connect_timeout_seconds < 0:
        raise OdbcConnectorError(
            "ODBC connection timeout must not be negative.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        )
    if config.query_timeout_seconds is not None and config.query_timeout_seconds < 0:
        raise OdbcConnectorError(
            "ODBC query timeout must not be negative.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        )
    if config.pool_recycle_seconds < 0:
        raise OdbcConnectorError(
            "ODBC pool recycle must not be negative.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        )

    if config.raw_odbc_connection_string and (
        config.dsn
        or config.odbc_driver
        or config.host
        or config.port is not None
        or config.database
        or config.extra_odbc_options
    ):
        raise OdbcConnectorError(
            "Raw ODBC connection string cannot be combined with generated ODBC fields.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        )

    if config.raw_odbc_connection_string:
        return

    if config.connection_mode == "dsn" and not config.dsn:
        raise OdbcConnectorError("ODBC DSN is required.", code=OdbcErrorCode.CONFIGURATION_INVALID)
    if config.connection_mode == "dsnless" and not config.odbc_driver:
        raise OdbcConnectorError(
            "ODBC driver is required.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        )

    OdbcConnectionStringBuilder().build(config)


def validate_sqlalchemy_drivername(drivername: str) -> None:
    if not _SQLALCHEMY_DRIVERNAME_PATTERN.match(drivername):
        raise OdbcConnectorError(
            "ODBC SQLAlchemy drivername must use the 'dialect+driver' format.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        )

    dialect_name, dbapi_name = drivername.split("+", 1)
    if dbapi_name != "pyodbc":
        raise OdbcConnectorError(
            "ODBC connector currently requires a SQLAlchemy drivername ending with '+pyodbc'.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        )

    try:
        sqlalchemy_dialect_registry.load(f"{dialect_name}.{dbapi_name}")
    except NoSuchModuleError as exc:
        raise OdbcConnectorError(
            f"SQLAlchemy dialect '{drivername}' is not installed.",
            code=OdbcErrorCode.DIALECT_NOT_FOUND,
            diagnostic_details={"sqlalchemy_drivername": drivername},
        ) from exc


def validate_odbc_database_url(database_url: str) -> None:
    try:
        parsed = make_url(database_url)
    except Exception as exc:  # SQLAlchemy raises ArgumentError subclasses by version.
        raise OdbcConnectorError(
            "ODBC datasource URL must be a valid SQLAlchemy URL.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        ) from exc

    validate_sqlalchemy_drivername(parsed.drivername)
    odbc_connect = parsed.query.get("odbc_connect")
    if not odbc_connect:
        raise OdbcConnectorError(
            "ODBC datasource URL must contain an 'odbc_connect' query value.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        )


def build_sqlalchemy_url(
    *,
    sqlalchemy_drivername: str,
    odbc_connection_string: str,
    gaard_options: dict[str, str] | None = None,
) -> URL:
    validate_sqlalchemy_drivername(sqlalchemy_drivername)
    return URL.create(
        drivername=sqlalchemy_drivername,
        query={"odbc_connect": odbc_connection_string, **(gaard_options or {})},
    )


def build_odbc_sqlalchemy_url_from_config(
    connection_config: dict[str, Any],
    *,
    existing_database_url: str | None = None,
) -> str:
    config = OdbcConnectorConfig.from_mapping(
        connection_config,
        existing_database_url=existing_database_url,
    )
    connection_string = OdbcConnectionStringBuilder().build(config)
    return build_sqlalchemy_url(
        sqlalchemy_drivername=config.sqlalchemy_drivername,
        odbc_connection_string=connection_string,
        gaard_options=_safe_gaard_query_options(config),
    ).render_as_string(hide_password=False)


def parse_odbc_sqlalchemy_url(database_url: str) -> tuple[str, str]:
    try:
        parsed = make_url(database_url)
    except Exception as exc:  # SQLAlchemy raises ArgumentError subclasses by version.
        raise OdbcConnectorError(
            "ODBC datasource URL must be a valid SQLAlchemy URL.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        ) from exc
    odbc_connect = parsed.query.get("odbc_connect")
    if not odbc_connect:
        raise OdbcConnectorError(
            "ODBC datasource URL must contain an 'odbc_connect' query value.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        )
    return parsed.drivername, str(odbc_connect)


def extract_odbc_connect(database_url: str | None) -> str | None:
    if not database_url:
        return None
    try:
        _drivername, connection_string = parse_odbc_sqlalchemy_url(database_url)
    except OdbcConnectorError:
        return None
    return connection_string


def extract_odbc_secret(database_url: str | None, secret_key: str) -> str | None:
    connection_string = extract_odbc_connect(database_url)
    if not connection_string:
        return None

    expected = secret_key.strip().casefold()
    for key, value in parse_odbc_connection_string(connection_string):
        if key.strip().casefold() == expected and value not in {"", "***"}:
            return value
    return None


def redact_existing_odbc_connect(database_url: str | None) -> str | None:
    from gaard_connectors.odbc.security import redact_odbc_connection_string

    connection_string = extract_odbc_connect(database_url)
    if connection_string is None:
        return None
    return redact_odbc_connection_string(connection_string)


def sql_dialect_for_sqlalchemy_drivername(drivername: str) -> str:
    dialect = drivername.split("+", 1)[0].strip().lower()
    return _DIALECT_MAP.get(dialect, dialect)


def _safe_gaard_query_options(config: OdbcConnectorConfig) -> dict[str, str]:
    options = {
        "gaard_connect_timeout_seconds": str(config.connect_timeout_seconds),
        "gaard_pool_pre_ping": "true" if config.pool_pre_ping else "false",
        "gaard_pool_recycle_seconds": str(config.pool_recycle_seconds),
        "gaard_pyodbc_pooling": "true" if config.pyodbc_pooling else "false",
    }
    if config.query_timeout_seconds is not None:
        options["gaard_query_timeout_seconds"] = str(config.query_timeout_seconds)
    return options


def parse_extra_odbc_options(value: Any) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return {
            str(key).strip(): "" if option_value is None else str(option_value)
            for key, option_value in value.items()
        }
    if isinstance(value, str):
        options: dict[str, str] = {}
        for raw_item in _split_extra_options_text(value):
            if not raw_item:
                continue
            separator = "=" if "=" in raw_item else ":" if ":" in raw_item else ""
            if not separator:
                raise OdbcConnectorError(
                    "Additional ODBC options must use key=value lines.",
                    code=OdbcErrorCode.CONFIGURATION_INVALID,
                )
            key, option_value = raw_item.split(separator, 1)
            options[key.strip()] = option_value.strip()
        return options
    raise OdbcConnectorError(
        "Additional ODBC options must be an object or key=value text.",
        code=OdbcErrorCode.CONFIGURATION_INVALID,
    )


def parse_engine_options(value: Any) -> dict[str, int | float | str | bool]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise OdbcConnectorError(
            "ODBC engine options must be an object.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        )

    allowed = {"pool_size", "max_overflow", "pool_timeout", "pool_recycle", "pool_pre_ping", "echo"}
    parsed: dict[str, int | float | str | bool] = {}
    for key, option_value in value.items():
        normalized_key = str(key).strip()
        if normalized_key not in allowed:
            raise OdbcConnectorError(
                f"ODBC engine option '{normalized_key}' is not allowed.",
                code=OdbcErrorCode.CONFIGURATION_INVALID,
            )
        if not isinstance(option_value, (str, int, float, bool)):
            raise OdbcConnectorError(
                f"ODBC engine option '{normalized_key}' must be a simple value.",
                code=OdbcErrorCode.CONFIGURATION_INVALID,
            )
        parsed[normalized_key] = option_value
    return parsed


def _split_extra_options_text(value: str) -> list[str]:
    if "\n" in value:
        return [line.strip() for line in value.splitlines()]
    return [item.strip() for item in value.split(";")]


def _optional_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _optional_secret(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _int_option(value: Any, *, default: int, label: str) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise OdbcConnectorError(
            f"ODBC {label} must be a number.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        ) from exc
    if parsed < 0:
        raise OdbcConnectorError(
            f"ODBC {label} must not be negative.",
            code=OdbcErrorCode.CONFIGURATION_INVALID,
        )
    return parsed


def _optional_int_option(value: Any, *, label: str) -> int | None:
    if value in (None, ""):
        return None
    return _int_option(value, default=0, label=label)


def _bool_option(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise OdbcConnectorError(
        "ODBC boolean options must be true or false.",
        code=OdbcErrorCode.CONFIGURATION_INVALID,
    )
