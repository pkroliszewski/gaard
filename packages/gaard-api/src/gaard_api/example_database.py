from importlib.resources import files
from pathlib import Path
import sqlite3

from sqlalchemy import select


MEDICAL_POC_RESOURCE_PACKAGE = "gaard_api.example_data.medical_poc"
MEDICAL_POC_CONNECTOR_KEY = "default"
MEDICAL_POC_CONNECTOR_NAME = "Medical POC SQLite"


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


def install_medical_poc_example_database(
    output_path: str | Path = "examples/medical-poc/demo.db",
    *,
    overwrite: bool = True,
    register_metadata: bool = True,
) -> Path:
    database_path = install_medical_poc_database(output_path, overwrite=overwrite)

    if register_metadata:
        register_medical_poc_datasource(database_path)

    return database_path


def sqlite_database_url(path: str | Path) -> str:
    return f"sqlite:///{Path(path).expanduser().resolve().as_posix()}"
