from gaard_plugin_api import ExtensionManifest


def extension() -> ExtensionManifest:
    return ExtensionManifest(
        id="example-custom-connector",
        version="2.0.7",
        requires={
            "gaard-connectors": ">=2.0.7,<2.1.0",
            "gaard-plugin-api": ">=2.0.7,<2.1.0",
        },
        contributions={
            "connectors": "gaard_example_custom_connector.connector:register",
        },
    )
