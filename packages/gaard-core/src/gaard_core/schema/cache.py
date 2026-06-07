from dataclasses import dataclass
from time import time

from gaard_core.schema.models import DatabaseSchema


@dataclass
class CachedSchemaContext:
    database_schema: DatabaseSchema
    formatted_schema: str
    created_at: float
    expires_at: float

    def is_expired(self) -> bool:
        return time() >= self.expires_at


class SchemaContextCache:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, CachedSchemaContext] = {}

    def get(self, key: str) -> CachedSchemaContext | None:
        item = self._items.get(key)

        if item is None:
            return None

        if item.is_expired():
            self._items.pop(key, None)
            return None

        return item

    def set(
        self,
        key: str,
        database_schema: DatabaseSchema,
        formatted_schema: str,
    ) -> CachedSchemaContext:
        now = time()

        item = CachedSchemaContext(
            database_schema=database_schema,
            formatted_schema=formatted_schema,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )

        self._items[key] = item

        return item

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._items.clear()
            return

        self._items.pop(key, None)