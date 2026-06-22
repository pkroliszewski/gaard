from gaard_core.schema.cache import SchemaContextCache

from gaard_api.core.settings import settings

schema_context_cache = SchemaContextCache(
    ttl_seconds=settings.gaard_schema_cache_ttl_seconds,
)