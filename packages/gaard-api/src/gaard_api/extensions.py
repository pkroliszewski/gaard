from functools import lru_cache

from gaard_connectors import ConnectorRegistry, create_builtin_connector_registry
from gaard_plugin_api import ExtensionManager


@lru_cache
def get_extension_manager() -> ExtensionManager:
    manager = ExtensionManager()
    manager.discover()
    return manager


@lru_cache
def get_connector_registry() -> ConnectorRegistry:
    registry = create_builtin_connector_registry()
    get_extension_manager().activate("connectors", registry)
    return registry
