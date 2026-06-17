from pathlib import Path
import sqlite3

from gaard_connectors.sqlalchemy.introspector import SQLAlchemySchemaIntrospector


def test_sqlalchemy_introspector_reads_tables_columns_and_foreign_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE patients (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL
            );

            CREATE TABLE appointments (
                id INTEGER PRIMARY KEY,
                patient_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                FOREIGN KEY (patient_id) REFERENCES patients(id)
            );

            CREATE VIEW active_patients AS
            SELECT id, status
            FROM patients
            WHERE status = 'active';
            """
        )
        connection.commit()
    finally:
        connection.close()

    introspector = SQLAlchemySchemaIntrospector(
        database_url=f"sqlite:///{db_path}",
    )

    schema = introspector.introspect()

    table_names = {table.name for table in schema.tables}

    assert "patients" in table_names
    assert "appointments" in table_names
    assert "active_patients" in table_names

    patients = next(table for table in schema.tables if table.name == "patients")
    patient_columns = {column.name for column in patients.columns}

    assert patients.object_type == "table"
    assert "id" in patient_columns
    assert "status" in patient_columns

    appointments = next(table for table in schema.tables if table.name == "appointments")

    assert len(appointments.foreign_keys) == 1
    assert appointments.foreign_keys[0].referred_table == "patients"
    assert appointments.foreign_keys[0].constrained_columns == ["patient_id"]

    active_patients = next(table for table in schema.tables if table.name == "active_patients")
    active_patient_columns = {column.name for column in active_patients.columns}

    assert active_patients.object_type == "view"
    assert active_patient_columns == {"id", "status"}
    assert active_patients.foreign_keys == []
