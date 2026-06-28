from functools import lru_cache

from gaard_connectors import ConnectorRegistry, create_builtin_connector_registry
from gaard_plugin_api import ExtensionManager

from gaard_api.api_registry import ApiRegistry


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


@lru_cache
def get_api_registry() -> ApiRegistry:
    from fastapi import Depends

    from gaard_api.api.v1.admin import get_current_admin

    registry = ApiRegistry(dependencies=[Depends(get_current_admin)])
    get_extension_manager().activate("api", registry, services=_create_api_extension_services())
    return registry


def _create_api_extension_services() -> dict[str, object]:
    from gaard_api.admin.database import create_session
    from gaard_api.admin.services import (
        get_llm_runtime_config_safe,
        introspect_datasource_connector,
        record_admin_audit,
    )
    from gaard_api.extension_services import DatasourceHostService

    return {
        "metadata_session_factory": create_session,
        "audit": record_admin_audit,
        "connector_registry": get_connector_registry,
        "datasources": DatasourceHostService(create_session),
        "datasource_schema_introspection": introspect_datasource_connector,
        "llm_runtime_config": get_llm_runtime_config_safe,
    }
