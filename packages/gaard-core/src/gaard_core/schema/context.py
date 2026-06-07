from typing import Protocol

from gaard_core.prompt_compiler.schema_formatter import SchemaPromptFormatter
from gaard_core.schema.cache import CachedSchemaContext, SchemaContextCache
from gaard_core.schema.models import DatabaseSchema


class SchemaIntrospector(Protocol):
    def introspect(self) -> DatabaseSchema:
        pass


class SchemaContextService:
    def __init__(
        self,
        introspector: SchemaIntrospector,
        cache: SchemaContextCache,
        formatter: SchemaPromptFormatter | None = None,
    ) -> None:
        self.introspector = introspector
        self.cache = cache
        self.formatter = formatter or SchemaPromptFormatter()

    def get_schema_context(self, cache_key: str) -> CachedSchemaContext:
        cached = self.cache.get(cache_key)

        if cached is not None:
            return cached

        database_schema = self.introspector.introspect()
        formatted_schema = self.formatter.format(database_schema)

        return self.cache.set(
            key=cache_key,
            database_schema=database_schema,
            formatted_schema=formatted_schema,
        )

    def invalidate(self, cache_key: str | None = None) -> None:
        self.cache.invalidate(cache_key)