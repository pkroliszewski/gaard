from gaard_plugin_api import ExtensionManifest


def extension() -> ExtensionManifest:
    return ExtensionManifest(
        id="duckdb-excel-connector",
        version="0.2.2",
        requires={
            "gaard-connectors": ">=0.2.2,<0.3.0",
            "gaard-plugin-api": ">=0.2.2,<0.3.0",
        },
        contributions={
            "api": "gaard_duckdb_excel_connector.api:register",
            "connectors": "gaard_duckdb_excel_connector.connector:register",
        },
    )
