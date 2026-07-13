from importlib.resources import files
from pathlib import Path
import sqlite3

from sqlalchemy import select


MEDICAL_POC_RESOURCE_PACKAGE = "gaard_api.example_data.medical_poc"
MEDICAL_POC_CONNECTOR_KEY = "default"
MEDICAL_POC_CONNECTOR_NAME = "Medical POC SQLite"
MEDICAL_POC_DASHBOARD_ID = "medical_poc_healthcare_operations"
MEDICAL_POC_DASHBOARD_NAME = "Healthcare Operations"
MEDICAL_POC_DASHBOARD_DESCRIPTION = "Central Hospital Operations Overview"
MEDICAL_POC_DASHBOARD_OWNER_USERNAME = "admin"
MEDICAL_POC_LEGACY_METRIC_KEYS = (
    "client_podaj_liczb_pacjent_w_przyjmowanych_u_poszczeg_l",
    "client_podaj_liczb_wszystkich_lekarzy",
    "client_podaj_najpierw_miesi_c_w_formacie_yyyy-mm_p_niej",
    "client_podaj_najpierw_miesi_c_w_formacie_yyyy-mm_p_niej_2",
    "client_podaj_og_ln_liczb_pacjent_w_obs_u_onych_w_tym_ro",
)
MEDICAL_POC_OVERVIEW_WIDGETS = (
    {
        "widget_key": "medical_monthly_visits_by_specialty",
        "label": "Monthly Visits by Specialty",
        "widget_type": "table",
        "datasource_key": MEDICAL_POC_CONNECTOR_KEY,
        "question": (
            "For the current year, return monthly visit counts by medical specialty. "
            "Return month, specialty and visit_count ordered by month and specialty."
        ),
        "sql": (
            "SELECT strftime('%Y-%m', a.appointment_date) AS month, "
            "d.specialization AS specialty, "
            "COUNT(*) AS visit_count "
            "FROM appointments a "
            "JOIN doctors d ON a.doctor_id = d.id "
            "WHERE strftime('%Y', a.appointment_date) = strftime('%Y', 'now') "
            "GROUP BY month, d.specialization "
            "ORDER BY month, d.specialization"
        ),
        "result_mode": "data",
        "position": 100,
        "grid_width": 12,
        "active": False,
    },
    {
        "widget_key": "medical_monthly_patients_by_insurer",
        "label": "Monthly Patients by Insurer",
        "widget_type": "table",
        "datasource_key": MEDICAL_POC_CONNECTOR_KEY,
        "question": (
            "For the current year, return monthly distinct patient counts by insurer. "
            "Return month, insurer and patient_count ordered by month and insurer."
        ),
        "sql": (
            "SELECT strftime('%Y-%m', a.appointment_date) AS month, "
            "p.insurance_provider AS insurer, "
            "COUNT(DISTINCT a.patient_id) AS patient_count "
            "FROM appointments a "
            "JOIN patients p ON a.patient_id = p.id "
            "WHERE strftime('%Y', a.appointment_date) = strftime('%Y', 'now') "
            "GROUP BY month, p.insurance_provider "
            "ORDER BY month, p.insurance_provider "
            "LIMIT 100"
        ),
        "result_mode": "data",
        "position": 110,
        "grid_width": 12,
        "active": False,
    },
    {
        "widget_key": "medical_total_doctors",
        "label": "Total Doctors",
        "widget_type": "scalar",
        "datasource_key": MEDICAL_POC_CONNECTOR_KEY,
        "question": "Return the total number of doctors.",
        "sql": "SELECT COUNT(*) AS total_doctors FROM doctors",
        "result_mode": "data",
        "position": 120,
        "grid_width": 1,
        "active": False,
    },
    {
        "widget_key": "medical_patients_by_specialty",
        "label": "Patients by Specialty",
        "widget_type": "table",
        "datasource_key": MEDICAL_POC_CONNECTOR_KEY,
        "question": (
            "Return distinct patient counts by medical specialty. "
            "Return specialty and patient_count ordered by patient_count descending."
        ),
        "sql": (
            "SELECT d.specialization AS specialty, "
            "COUNT(DISTINCT a.patient_id) AS patient_count "
            "FROM appointments a "
            "JOIN doctors d ON a.doctor_id = d.id "
            "GROUP BY d.specialization "
            "ORDER BY patient_count DESC, d.specialization "
            "LIMIT 100"
        ),
        "result_mode": "data",
        "position": 130,
        "grid_width": 12,
        "active": False,
    },
    {
        "widget_key": "medical_total_patients_this_year",
        "label": "Total Patients Served This Year",
        "widget_type": "scalar",
        "datasource_key": MEDICAL_POC_CONNECTOR_KEY,
        "question": "Return the total number of distinct patients served this year.",
        "sql": (
            "SELECT COUNT(DISTINCT patient_id) AS total_patients "
            "FROM appointments "
            "WHERE strftime('%Y', appointment_date) = strftime('%Y', 'now')"
        ),
        "result_mode": "data",
        "position": 140,
        "grid_width": 1,
        "active": False,
    },
)
MEDICAL_POC_DASHBOARD_WIDGETS = (
    {
        "widget_id": "medical_poc_monthly_visits_widget",
        "metric_widget_key": "medical_monthly_visits_by_specialty",
        "title": "Monthly Visits by Specialty",
        "visualization_type": "stacked_bar",
        "x": 0,
        "y": 0,
        "w": 6,
        "h": 4,
    },
    {
        "widget_id": "medical_poc_monthly_patients_widget",
        "metric_widget_key": "medical_monthly_patients_by_insurer",
        "title": "Monthly Patients by Insurer",
        "visualization_type": "multi_line",
        "x": 6,
        "y": 0,
        "w": 6,
        "h": 4,
    },
    {
        "widget_id": "medical_poc_total_doctors_widget",
        "metric_widget_key": "medical_total_doctors",
        "title": "Total Doctors",
        "visualization_type": "number",
        "x": 0,
        "y": 4,
        "w": 2,
        "h": 2,
    },
    {
        "widget_id": "medical_poc_total_patients_widget",
        "metric_widget_key": "medical_total_patients_this_year",
        "title": "Total Patients Served This Year",
        "visualization_type": "number",
        "x": 0,
        "y": 6,
        "w": 2,
        "h": 2,
    },
    {
        "widget_id": "medical_poc_patients_by_specialty_widget",
        "metric_widget_key": "medical_patients_by_specialty",
        "title": "Patients by Specialty",
        "visualization_type": "pie",
        "x": 2,
        "y": 4,
        "w": 4,
        "h": 4,
    },
)


def read_medical_poc_resource(filename: str) -> str:
    return (
        files(MEDICAL_POC_RESOURCE_PACKAGE)
        .joinpath(filename)
        .read_text(encoding="utf-8")
    )


def install_medical_poc_database(
    output_path: str | Path = "examples/medical-poc/demo.db",
    *,
    overwrite: bool = True,
) -> Path:
    target_path = Path(output_path).expanduser()

    if target_path.exists() and not overwrite:
        raise FileExistsError(f"Example database already exists: {target_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target_path.with_name(f".{target_path.name}.tmp")

    if temporary_path.exists():
        temporary_path.unlink()

    connection = sqlite3.connect(temporary_path)

    try:
        connection.executescript(read_medical_poc_resource("schema.sql"))
        connection.executescript(read_medical_poc_resource("seed.sql"))
        connection.commit()
    except Exception:
        connection.close()
        if temporary_path.exists():
            temporary_path.unlink()
        raise
    else:
        connection.close()

    if target_path.exists():
        target_path.unlink()

    temporary_path.replace(target_path)
    return target_path


def register_medical_poc_datasource(
    database_path: str | Path,
    *,
    connector_key: str = MEDICAL_POC_CONNECTOR_KEY,
    active: bool = True,
    actor: str = "example-installer",
) -> str:
    from gaard_api.admin.database import create_session
    from gaard_api.admin.models import DatasourceConnector, DatasourceSchemaCache

    database_url = sqlite_database_url(database_path)
    session = create_session()

    try:
        connector = session.scalar(
            select(DatasourceConnector).where(
                DatasourceConnector.connector_key == connector_key
            )
        )
        database_url_changed = connector is not None and connector.database_url != database_url

        if connector is None:
            connector = DatasourceConnector(
                connector_key=connector_key,
                name=MEDICAL_POC_CONNECTOR_NAME,
                database_type="sqlite",
                database_url=database_url,
                sql_dialect="sqlite",
                active=False,
                updated_by=actor,
            )
            session.add(connector)
            session.flush()
        else:
            connector.name = MEDICAL_POC_CONNECTOR_NAME
            connector.database_type = "sqlite"
            connector.database_url = database_url
            connector.sql_dialect = "sqlite"
            connector.updated_by = actor

        if database_url_changed:
            schema_cache = session.get(DatasourceSchemaCache, connector.id)

            if schema_cache is not None:
                session.delete(schema_cache)

        if active:
            for item in session.scalars(select(DatasourceConnector)):
                item.active = item.id == connector.id and item.connector_key != "metadata-db"

                if item.id == connector.id:
                    item.updated_by = actor

        session.commit()
        return connector.connector_key
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def seed_medical_poc_dashboard(
    *,
    actor: str = "example-installer",
) -> str:
    from gaard_api.admin.database import create_session
    from gaard_api.admin.models import (
        AdminUser,
        Dashboard,
        DashboardUserState,
        DashboardWidget,
        OverviewWidget,
        UserSavedMetric,
    )

    session = create_session()

    try:
        owner = session.scalar(
            select(AdminUser).where(AdminUser.username == MEDICAL_POC_DASHBOARD_OWNER_USERNAME)
        )
        if owner is None:
            raise RuntimeError("Admin user is required before seeding medical POC dashboard.")

        owner_user_id = str(owner.id)
        owner_username = owner.username
        seed_metric_keys = {str(item["widget_key"]) for item in MEDICAL_POC_OVERVIEW_WIDGETS}

        for item in MEDICAL_POC_OVERVIEW_WIDGETS:
            widget = session.scalar(
                select(OverviewWidget).where(OverviewWidget.widget_key == item["widget_key"])
            )

            values = dict(item)
            values["updated_by"] = actor

            if widget is None:
                session.add(OverviewWidget(**values))
                continue

            for key, value in values.items():
                setattr(widget, key, value)

        for legacy_key in MEDICAL_POC_LEGACY_METRIC_KEYS:
            saved_metric = session.scalar(
                select(UserSavedMetric).where(
                    UserSavedMetric.owner_user_id == owner_user_id,
                    UserSavedMetric.widget_key == legacy_key,
                )
            )
            if saved_metric is not None:
                session.delete(saved_metric)

        dashboard = session.scalar(
            select(Dashboard).where(
                Dashboard.dashboard_id == MEDICAL_POC_DASHBOARD_ID,
                Dashboard.owner_user_id == owner_user_id,
            )
        )
        if dashboard is None:
            dashboard = session.scalar(
                select(Dashboard).where(
                    Dashboard.owner_user_id == owner_user_id,
                    Dashboard.name == MEDICAL_POC_DASHBOARD_NAME,
                )
            )

        if dashboard is None:
            dashboard = Dashboard(
                dashboard_id=MEDICAL_POC_DASHBOARD_ID,
                owner_user_id=owner_user_id,
                owner_username=owner_username,
                name=MEDICAL_POC_DASHBOARD_NAME,
                description=MEDICAL_POC_DASHBOARD_DESCRIPTION,
            )
            session.add(dashboard)
            session.flush()
        else:
            dashboard.owner_username = owner_username
            dashboard.name = MEDICAL_POC_DASHBOARD_NAME
            dashboard.description = MEDICAL_POC_DASHBOARD_DESCRIPTION

        for item in MEDICAL_POC_OVERVIEW_WIDGETS:
            saved_metric = session.scalar(
                select(UserSavedMetric).where(
                    UserSavedMetric.owner_user_id == owner_user_id,
                    UserSavedMetric.widget_key == item["widget_key"],
                )
            )
            if saved_metric is None:
                session.add(
                    UserSavedMetric(
                        owner_user_id=owner_user_id,
                        owner_username=owner_username,
                        widget_key=str(item["widget_key"]),
                    )
                )
            else:
                saved_metric.owner_username = owner_username

        dashboard_widgets = list(
            session.scalars(
                select(DashboardWidget).where(
                    DashboardWidget.dashboard_id == dashboard.dashboard_id,
                    DashboardWidget.owner_user_id == owner_user_id,
                )
            )
        )
        for widget in dashboard_widgets:
            if widget.metric_widget_key not in seed_metric_keys:
                session.delete(widget)

        session.flush()

        for item in MEDICAL_POC_DASHBOARD_WIDGETS:
            widget = session.scalar(
                select(DashboardWidget).where(DashboardWidget.widget_id == item["widget_id"])
            )
            if widget is None:
                widget = session.scalar(
                    select(DashboardWidget).where(
                        DashboardWidget.dashboard_id == dashboard.dashboard_id,
                        DashboardWidget.owner_user_id == owner_user_id,
                        DashboardWidget.metric_widget_key == item["metric_widget_key"],
                    )
                )

            values = dict(item)

            if widget is None:
                session.add(
                    DashboardWidget(
                        dashboard_id=dashboard.dashboard_id,
                        owner_user_id=owner_user_id,
                        owner_username=owner_username,
                        **values,
                    )
                )
                continue

            widget.widget_id = str(values["widget_id"])
            widget.dashboard_id = dashboard.dashboard_id
            widget.owner_user_id = owner_user_id
            widget.owner_username = owner_username
            widget.metric_widget_key = str(values["metric_widget_key"])
            widget.title = str(values["title"])
            widget.visualization_type = str(values["visualization_type"])
            widget.x = int(values["x"])
            widget.y = int(values["y"])
            widget.w = int(values["w"])
            widget.h = int(values["h"])

        state = session.get(DashboardUserState, owner_user_id)
        if state is None:
            session.add(
                DashboardUserState(
                    owner_user_id=owner_user_id,
                    owner_username=owner_username,
                    active_dashboard_id=dashboard.dashboard_id,
                )
            )
        else:
            state.owner_username = owner_username
            state.active_dashboard_id = dashboard.dashboard_id

        session.commit()
        return dashboard.dashboard_id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def install_medical_poc_example_database(
    output_path: str | Path = "examples/medical-poc/demo.db",
    *,
    overwrite: bool = True,
    register_metadata: bool = True,
) -> Path:
    database_path = install_medical_poc_database(output_path, overwrite=overwrite)

    if register_metadata:
        register_medical_poc_datasource(database_path)
        seed_medical_poc_dashboard()

    return database_path


def sqlite_database_url(path: str | Path) -> str:
    return f"sqlite:///{Path(path).expanduser().resolve().as_posix()}"
