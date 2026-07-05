from gaard_plugin_api import ExtensionManifest


def extension() -> ExtensionManifest:
    return ExtensionManifest(
        id="datasource-access",
        version="0.2.0",
        requires={
            "gaard-api": ">=0.2.0,<0.3.0",
            "gaard-plugin-api": ">=0.2.0,<0.3.0",
        },
        contributions={
            "api": "gaard_multi_datasource_access.api:register",
            "query": "gaard_multi_datasource_access.hooks:register",
        },
    )
