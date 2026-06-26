from fastapi import APIRouter

from gaard_core.schema.context import SchemaContextService
from gaard_core.schema.models import DatabaseSchema

from gaard_api.admin.services import (
    get_datasource_schema_context_safe,
    selected_schema_from_cache,
)
from gaard_api.core.schema_cache import schema_context_cache
from gaard_api.core.settings import settings
from gaard_api.extensions import get_connector_registry

router = APIRouter()


def get_schema_cache_key(database_url: str | None = None, sql_dialect: str | None = None) -> str:
    return (
        f"{sql_dialect or settings.gaard_sql_dialect}:"
        f"{database_url or settings.gaard_datasource_url}"
    )


@router.get("/schema", response_model=DatabaseSchema)
def get_schema() -> DatabaseSchema:
    datasource_context = get_datasource_schema_context_safe()

    if datasource_context is not None:
        _connector, schema_cache = datasource_context
        return selected_schema_from_cache(schema_cache)

    introspector = get_connector_registry().detect_from_database_url(
        settings.gaard_datasource_url
    ).introspector_factory(settings.gaard_datasource_url)

    service = SchemaContextService(
        introspector=introspector,
        cache=schema_context_cache,
    )

    context = service.get_schema_context(get_schema_cache_key())

    return context.database_schema


@router.delete("/schema/cache")
def invalidate_schema_cache() -> dict[str, str]:
    datasource_context = get_datasource_schema_context_safe()

    if datasource_context is not None:
        connector, _schema_cache = datasource_context
        schema_context_cache.invalidate(
            get_schema_cache_key(connector.database_url, connector.sql_dialect)
        )
    else:
        schema_context_cache.invalidate(get_schema_cache_key())

    return {
        "status": "invalidated",
    }
