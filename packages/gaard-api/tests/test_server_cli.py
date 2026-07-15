import sqlite3

import pytest
from sqlalchemy import select

from gaard_api.admin.database import create_session, reset_metadata_store_for_tests
from gaard_api.admin.models import (
    Dashboard,
    DashboardUserState,
    DashboardWidget,
    DatasourceConnector,
    OverviewWidget,
    OverviewWidgetTag,
)
from gaard_api.core.settings import settings
from gaard_api.example_database import (
    MEDICAL_POC_DASHBOARD_NAME,
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
        appointment_count = connection.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
        specialty_counts = {
            row[0]: row[1]
            for row in connection.execute(
                """
                SELECT d.specialization, COUNT(*)
                FROM appointments a
                JOIN doctors d ON a.doctor_id = d.id
                GROUP BY d.specialization
                """
            )
        }
        cardiology_halves = connection.execute(
            """
            SELECT
                SUM(CASE WHEN CAST(strftime('%m', a.appointment_date) AS INTEGER) <= 6 THEN 1 ELSE 0 END),
                SUM(CASE WHEN CAST(strftime('%m', a.appointment_date) AS INTEGER) > 6 THEN 1 ELSE 0 END)
            FROM appointments a
            JOIN doctors d ON a.doctor_id = d.id
            WHERE d.specialization = 'cardiology'
            """
        ).fetchone()
        orthopedics_seasonality = connection.execute(
            """
            SELECT
                SUM(CASE WHEN CAST(strftime('%m', a.appointment_date) AS INTEGER) IN (6, 7, 8) THEN 1 ELSE 0 END),
                SUM(CASE WHEN CAST(strftime('%m', a.appointment_date) AS INTEGER) IN (1, 2) THEN 1 ELSE 0 END)
            FROM appointments a
            JOIN doctors d ON a.doctor_id = d.id
            WHERE d.specialization = 'orthopedics'
            """
        ).fetchone()
    finally:
        connection.close()

    assert tables >= {"patients", "appointments", "doctors"}
    assert active_patients > 0
    assert appointment_count == 560
    assert set(specialty_counts) == {
        "cardiology",
        "dermatology",
        "neurology",
        "orthopedics",
    }
    assert len(set(specialty_counts.values())) > 1
    assert cardiology_halves[1] > cardiology_halves[0]
    assert orthopedics_seasonality[0] > orthopedics_seasonality[1]

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
        dashboard = session.scalar(
            select(Dashboard).where(Dashboard.name == MEDICAL_POC_DASHBOARD_NAME)
        )
        metrics = session.scalars(
            select(OverviewWidget).where(OverviewWidget.widget_key.like("medical_%"))
        ).all()
        saved_metric_keys = set(
            session.scalars(
                select(OverviewWidget.widget_key)
                .join(OverviewWidgetTag, OverviewWidgetTag.widget_id == OverviewWidget.id)
                .where(OverviewWidgetTag.tag_name == "admin")
            )
        )
        dashboard_widgets = session.scalars(select(DashboardWidget)).all()
        dashboard_state = session.get(DashboardUserState, "1")

    assert connector is not None
    assert connector.name == "Medical POC SQLite"
    assert connector.database_type == "sqlite"
    assert connector.database_url == sqlite_database_url(database_path)
    assert connector.sql_dialect == "sqlite"
    assert connector.active is True
    assert metadata_connector is not None
    assert metadata_connector.active is False
    assert dashboard is not None
    assert dashboard.owner_username == "admin"
    assert dashboard_state is not None
    assert dashboard_state.active_dashboard_id == dashboard.dashboard_id
    assert {metric.widget_key for metric in metrics} == {
        "medical_monthly_patients_by_insurer",
        "medical_monthly_visits_by_specialty",
        "medical_patients_by_specialty",
        "medical_total_doctors",
        "medical_total_patients_this_year",
    }
    assert {metric.label for metric in metrics} == {
        "Monthly Patients by Insurer",
        "Monthly Visits by Specialty",
        "Patients by Specialty",
        "Total Doctors",
        "Total Patients Served This Year",
    }
    assert saved_metric_keys >= {
        metric.widget_key for metric in metrics
    }
    assert {
        (widget.metric_widget_key, widget.title, widget.visualization_type)
        for widget in dashboard_widgets
    } == {
        (
            "medical_monthly_visits_by_specialty",
            "Monthly Visits by Specialty",
            "stacked_bar",
        ),
        (
            "medical_monthly_patients_by_insurer",
            "Monthly Patients by Insurer",
            "multi_line",
        ),
        ("medical_total_doctors", "Total Doctors", "number"),
        (
            "medical_total_patients_this_year",
            "Total Patients Served This Year",
            "number",
        ),
        ("medical_patients_by_specialty", "Patients by Specialty", "pie"),
    }

    reset_metadata_store_for_tests()
