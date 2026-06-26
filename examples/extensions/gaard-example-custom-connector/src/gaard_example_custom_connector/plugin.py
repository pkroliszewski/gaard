from gaard_plugin_api import ExtensionManifest


def extension() -> ExtensionManifest:
    return ExtensionManifest(
        id="example-custom-connector",
        version="0.1.0",
        requires={
            "gaard-connectors": ">=0.1.0,<0.2.0",
            "gaard-plugin-api": ">=0.1.0,<0.2.0",
        },
        contributions={
            "connectors": "gaard_example_custom_connector.connector:register",
        },
    )
