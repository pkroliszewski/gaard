import sqlite3

import pytest
from sqlalchemy import select

from gaard_api.admin.database import create_session, reset_metadata_store_for_tests
from gaard_api.admin.models import DatasourceConnector
from gaard_api.core.settings import settings
from gaard_api.example_database import (
    install_medical_poc_database,
    install_medical_poc_example_database,
    sqlite_database_url,
)
from gaard_api.server_cli import create_parser


def test_server_cli_parses_start_defaults() -> None:
    args = create_parser().parse_args(["start"])

    assert args.command == "start"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.reload is False


def test_server_cli_parses_start_options() -> None:
    args = create_parser().parse_args(
        ["start", "--host", "0.0.0.0", "--port", "9000", "--reload"]
    )

    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.reload is True


def test_server_cli_parses_install_example_database_options() -> None:
    args = create_parser().parse_args(
        [
            "install-example-database",
            "--output",
            "demo.db",
            "--no-overwrite",
        ]
    )

    assert args.command == "install-example-database"
    assert args.output == "demo.db"
    assert args.no_overwrite is True


def test_install_medical_poc_database_creates_sqlite_demo_database(tmp_path) -> None:
    database_path = tmp_path / "demo.db"

    installed_path = install_medical_poc_database(database_path)

    assert installed_path == database_path

    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        active_patients = connection.execute(
            "SELECT COUNT(*) FROM patients WHERE status = 'active'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert tables >= {"patients", "appointments", "doctors"}
    assert active_patients > 0

    with pytest.raises(FileExistsError):
        install_medical_poc_database(database_path, overwrite=False)


def test_install_medical_poc_example_database_registers_active_datasource(
    tmp_path,
    monkeypatch,
) -> None:
    metadata_db = tmp_path / "metadata.db"
    database_path = tmp_path / "demo.db"
    monkeypatch.setattr(settings, "gaard_metadata_database_url", f"sqlite:///{metadata_db}")
    reset_metadata_store_for_tests()

    install_medical_poc_example_database(database_path)

    with create_session() as session:
        connector = session.scalar(
            select(DatasourceConnector).where(
                DatasourceConnector.connector_key == "default"
            )
        )
        metadata_connector = session.scalar(
            select(DatasourceConnector).where(
                DatasourceConnector.connector_key == "metadata-db"
            )
        )

    assert connector is not None
    assert connector.name == "Medical POC SQLite"
    assert connector.database_type == "sqlite"
    assert connector.database_url == sqlite_database_url(database_path)
    assert connector.sql_dialect == "sqlite"
    assert connector.active is True
    assert metadata_connector is not None
    assert metadata_connector.active is False

    reset_metadata_store_for_tests()
