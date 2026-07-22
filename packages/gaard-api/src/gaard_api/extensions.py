from functools import lru_cache
from typing import TYPE_CHECKING

from fastapi import Request
from gaard_connectors import ConnectorRegistry, create_builtin_connector_registry
from gaard_plugin_api import ExtensionManager

from gaard_api.api_registry import ApiRegistry

if TYPE_CHECKING:
    from gaard_api.auth_hooks import AuthProviderRegistry
    from gaard_api.query_hooks import QueryHookRegistry
    from gaard_api.siem import SiemSinkRegistry

EXTRACT_JOBS_FEATURE = "extract_jobs"
EXTRACT_JOBS_LICENSE_MESSAGE = "Extract jobs require an active Enterprise license."
EXTRACT_JOBS_API_PREFIX = "/api/v1/extensions/gaard-extract/jobs"


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

    from gaard_api.api.v1.admin import get_current_enterprise_admin

    registry = ApiRegistry(
        dependencies=[
            Depends(get_current_enterprise_admin),
            Depends(enforce_extension_license_entitlements),
        ]
    )
    get_extension_manager().activate("api", registry, services=_create_api_extension_services())
    return registry


@lru_cache
def get_query_hook_registry() -> "QueryHookRegistry":
    from gaard_api.query_hooks import QueryHookRegistry

    registry = QueryHookRegistry()
    get_extension_manager().activate(
        "query",
        registry,
        services=_create_query_extension_services(),
    )
    return registry


@lru_cache
def get_auth_provider_registry() -> "AuthProviderRegistry":
    from gaard_api.auth_hooks import AuthProviderRegistry

    registry = AuthProviderRegistry()
    get_extension_manager().activate(
        "auth",
        registry,
        services=_create_auth_extension_services(),
    )
    return registry


@lru_cache
def get_siem_registry() -> "SiemSinkRegistry":
    from gaard_api.siem import SiemSinkRegistry

    registry = SiemSinkRegistry()
    get_extension_manager().activate(
        "siem",
        registry,
        services=_create_siem_extension_services(),
    )
    return registry


def enforce_extension_license_entitlements(request: Request) -> None:
    if not is_extract_job_mutation(request.method, request.url.path):
        return

    from gaard_api.license import license_service

    license_service.require_feature(EXTRACT_JOBS_FEATURE, EXTRACT_JOBS_LICENSE_MESSAGE)


def is_extract_job_mutation(method: str, path: str) -> bool:
    if method.upper() != "POST":
        return False

    normalized_path = path.rstrip("/")
    if normalized_path == EXTRACT_JOBS_API_PREFIX:
        return True

    return (
        normalized_path.startswith(f"{EXTRACT_JOBS_API_PREFIX}/")
        and normalized_path.endswith("/refresh")
    )


def _create_api_extension_services() -> dict[str, object]:
    from gaard_api.admin.database import create_session
    from gaard_api.admin.services import (
        get_llm_runtime_config_safe,
        get_setting,
        introspect_datasource_connector,
        json_dumps,
        json_loads,
        record_admin_audit,
        set_setting,
    )
    from gaard_api.extension_services import DatasourceHostService
    from gaard_api.license import license_service

    return {
        "metadata_session_factory": create_session,
        "audit": record_admin_audit,
        "get_setting": get_setting,
        "set_setting": set_setting,
        "json_dumps": json_dumps,
        "json_loads": json_loads,
        "connector_registry": get_connector_registry,
        "datasources": DatasourceHostService(create_session),
        "datasource_schema_introspection": introspect_datasource_connector,
        "llm_runtime_config": get_llm_runtime_config_safe,
        "license": license_service,
    }


def _create_query_extension_services() -> dict[str, object]:
    from gaard_api.admin.database import create_session
    from gaard_api.admin.services import (
        get_active_business_logic_prompt_safe,
        get_datasource_schema_contexts_safe,
        json_loads,
        list_datasource_connectors,
        selected_schema_from_cache,
    )
    from gaard_api.license import license_service

    return {
        "metadata_session_factory": create_session,
        "connector_registry": get_connector_registry,
        "datasource_contexts": get_datasource_schema_contexts_safe,
        "selected_schema_from_cache": selected_schema_from_cache,
        "json_loads": json_loads,
        "active_business_logic_prompt": get_active_business_logic_prompt_safe,
        "list_datasource_connectors": list_datasource_connectors,
        "license": license_service,
    }


def _create_auth_extension_services() -> dict[str, object]:
    from gaard_api.admin.database import create_session
    from gaard_api.admin.services import record_admin_audit

    return {
        "metadata_session_factory": create_session,
        "audit": record_admin_audit,
    }


def _create_siem_extension_services() -> dict[str, object]:
    from gaard_api.admin.database import create_session
    from gaard_api.admin.services import (
        get_setting,
        json_dumps,
        json_loads,
        record_admin_audit,
        set_setting,
    )

    return {
        "metadata_session_factory": create_session,
        "audit": record_admin_audit,
        "get_setting": get_setting,
        "set_setting": set_setting,
        "json_dumps": json_dumps,
        "json_loads": json_loads,
    }
