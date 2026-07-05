from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files
from typing import Any, cast

from gaard_api.api_registry import ApiRegistry
from gaard_plugin_api import ExtensionContext, ExtensionManifest
from sqlalchemy.orm import Session

from gaard_extract.api import create_router
from gaard_extract.db import init_database
from gaard_extract.service import DatasourceHostService, start_job_worker


SessionFactory = Callable[[], Session]


def extension() -> ExtensionManifest:
    return ExtensionManifest(
        id="gaard-extract",
        version="0.2.0",
        requires={
            "gaard-api": ">=0.2.0,<0.3.0",
            "gaard-plugin-api": ">=0.2.0,<0.3.0",
        },
        contributions={
            "api": "gaard_extract.plugin:register_api",
        },
    )


def register_api(context: ExtensionContext) -> None:
    if not isinstance(context.registry, ApiRegistry):
        raise RuntimeError("GAARD Extract requires ApiRegistry for its api contribution.")

    session_factory = cast(SessionFactory, require_service(context.services, "metadata_session_factory"))
    datasource_service = cast(DatasourceHostService, require_service(context.services, "datasources"))

    context.registry.register_initializer(lambda: initialize_runtime(session_factory))
    context.registry.register_router(
        extension_id=context.extension_id,
        router=create_router(session_factory, datasource_service),
    )
    context.registry.register_admin_page(
        extension_id=context.extension_id,
        section_key="extract",
        label="Extract",
        description="Configure unstructured source models from existing GAARD datasources.",
        html_path=files("gaard_extract").joinpath("admin/index.html"),
        order=850,
    )


def require_service(services: dict[str, Any] | Any, name: str) -> Any:
    value = services.get(name)
    if value is None:
        raise RuntimeError(f"GAARD Extract requires host service {name!r}.")
    return value


def initialize_runtime(session_factory: SessionFactory) -> None:
    init_database(session_factory)
    start_job_worker(session_factory)
