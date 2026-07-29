from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from gaard_connectors import create_builtin_connector_registry
from gaard_connectors.odbc.config import (
    OdbcConnectorConfig,
    build_odbc_sqlalchemy_url_from_config,
    parse_odbc_sqlalchemy_url,
    sql_dialect_for_sqlalchemy_drivername,
)
from gaard_connectors.odbc.connection_string import (
    OdbcConnectionStringBuilder,
    parse_odbc_connection_string,
)
from gaard_connectors.odbc.exceptions import OdbcConnectorError
from gaard_connectors.odbc.security import redact_odbc_connection_string


def test_odbc_dsn_connection_string_is_deterministic() -> None:
    config = OdbcConnectorConfig.from_mapping(
        {
            "connection_mode": "dsn",
            "sqlalchemy_drivername": "mssql+pyodbc",
            "dsn": "test",
            "username": "user",
            "password": "pass",
        }
    )

    assert OdbcConnectionStringBuilder().build(config) == "DSN=test;UID=user;PWD=pass;"


def test_odbc_dsnless_connection_string_orders_managed_and_extra_options() -> None:
    config = OdbcConnectorConfig.from_mapping(
        {
            "connection_mode": "dsnless",
            "sqlalchemy_drivername": "mssql+pyodbc",
            "odbc_driver": "ODBC Driver 18 for SQL Server",
            "host": "sql01",
            "port": 1433,
            "database": "ERP",
            "extra_odbc_options": {
                "TrustServerCertificate": "yes",
                "Encrypt": "yes",
            },
        }
    )

    assert OdbcConnectionStringBuilder().build(config) == (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        "SERVER=sql01,1433;"
        "DATABASE=ERP;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )


def test_odbc_connection_string_round_trips_special_characters_through_sqlalchemy_url() -> None:
    password = "P@ss:w/ord;ąćę% + {}"
    connection_config = {
        "connection_mode": "dsn",
        "sqlalchemy_drivername": "mssql+pyodbc",
        "dsn": "hospital_reporting",
        "username": "gaard_reader",
        "password": password,
    }
    OdbcConnectorConfig.from_mapping(connection_config)

    database_url = build_odbc_sqlalchemy_url_from_config(connection_config)
    parsed = make_url(database_url)
    connection_string = parsed.query["odbc_connect"]

    assert dict(parse_odbc_connection_string(str(connection_string)))["PWD"] == password


def test_odbc_rejects_protected_extra_options() -> None:
    with pytest.raises(OdbcConnectorError, match="managed by GAARD"):
        OdbcConnectorConfig.from_mapping(
            {
                "connection_mode": "dsn",
                "sqlalchemy_drivername": "mssql+pyodbc",
                "dsn": "test",
                "extra_odbc_options": {"pwd": "override"},
            }
        )


def test_odbc_redacts_secret_options_case_insensitively() -> None:
    assert redact_odbc_connection_string(
        "UID=gaard;PWD=secret;Password=secret2;Token=secret3;ACCESS_TOKEN=secret4;SERVER=db;"
    ) == (
        "UID=gaard;"
        "PWD=***;"
        "Password=***;"
        "Token=***;"
        "ACCESS_TOKEN=***;"
        "SERVER=db;"
    )


def test_odbc_rejects_invalid_mode_and_drivername() -> None:
    with pytest.raises(OdbcConnectorError, match="connection mode"):
        OdbcConnectorConfig.from_mapping(
            {
                "connection_mode": "invalid",
                "sqlalchemy_drivername": "mssql+pyodbc",
            }
        )

    with pytest.raises(OdbcConnectorError, match="dialect\\+driver"):
        OdbcConnectorConfig.from_mapping(
            {
                "connection_mode": "dsn",
                "sqlalchemy_drivername": "pyodbc",
                "dsn": "test",
            }
        )


def test_odbc_registry_exposes_community_connector_type() -> None:
    registry = create_builtin_connector_registry()
    definition = registry.get("odbc")

    assert definition.label == "ODBC / unixODBC"
    assert definition.default_sql_dialect == "tsql"
    assert "mssql+pyodbc" in definition.config_schema["tested_driver_names"]


def test_odbc_database_url_validation_accepts_sqlalchemy_odbc_url() -> None:
    database_url = build_odbc_sqlalchemy_url_from_config(
        {
            "connection_mode": "dsn",
            "sqlalchemy_drivername": "mssql+pyodbc",
            "dsn": "hospital_reporting",
            "connect_timeout_seconds": 7,
            "pool_recycle_seconds": 99,
            "pool_pre_ping": False,
        }
    )

    drivername, connection_string = parse_odbc_sqlalchemy_url(database_url)
    parsed = make_url(database_url)

    assert drivername == "mssql+pyodbc"
    assert connection_string == "DSN=hospital_reporting;"
    assert sql_dialect_for_sqlalchemy_drivername(drivername) == "tsql"
    assert parsed.query["gaard_connect_timeout_seconds"] == "7"
    assert parsed.query["gaard_pool_recycle_seconds"] == "99"
    assert parsed.query["gaard_pool_pre_ping"] == "false"
