from pathlib import Path
import sqlite3

from gaard_connectors import create_builtin_connector_registry
from gaard_plugin_api import ExtensionManager

from gaard_example_custom_connector.connector import (
    CONNECTOR_TYPE_KEY,
    to_custom_sqlite_url,
)


def test_custom_connector_is_discovered_and_executes_query(tmp_path: Path) -> None:
    database_path = tmp_path / "custom-example.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE projects (name TEXT NOT NULL)")
        connection.execute("INSERT INTO projects (name) VALUES ('GAARD')")
        connection.commit()
    finally:
        connection.close()

    registry = create_builtin_connector_registry()
    manager = ExtensionManager()
    manager.discover()
    activated = manager.activate("connectors", registry)

    assert any(
        record.manifest is not None and record.manifest.id == "example-custom-connector"
        for record in activated
    )

    definition = registry.get(CONNECTOR_TYPE_KEY)
    database_url = to_custom_sqlite_url(database_path)
    definition.validate_database_url(database_url)
    definition.validate_sql_dialect("sqlite")

    result = definition.executor_factory(database_url, 10).execute("SELECT name FROM projects")

    assert result.rows == [{"name": "GAARD"}]
    assert definition.introspector_factory(database_url).introspect().tables[0].name == "projects"
