from gaard_core.schema.cache import SchemaContextCache
from gaard_core.schema.models import DatabaseSchema


def test_schema_context_cache_returns_cached_item() -> None:
    cache = SchemaContextCache(ttl_seconds=300)
    schema = DatabaseSchema(tables=[])

    cache.set(
        key="sqlite:test",
        database_schema=schema,
        formatted_schema="No tables available.",
    )

    cached = cache.get("sqlite:test")

    assert cached is not None
    assert cached.database_schema == schema
    assert cached.formatted_schema == "No tables available."


def test_schema_context_cache_expires_item() -> None:
    cache = SchemaContextCache(ttl_seconds=-1)
    schema = DatabaseSchema(tables=[])

    cache.set(
        key="sqlite:test",
        database_schema=schema,
        formatted_schema="No tables available.",
    )

    assert cache.get("sqlite:test") is None


def test_schema_context_cache_can_invalidate_single_item() -> None:
    cache = SchemaContextCache(ttl_seconds=300)
    schema = DatabaseSchema(tables=[])

    cache.set(
        key="sqlite:test",
        database_schema=schema,
        formatted_schema="No tables available.",
    )

    cache.invalidate("sqlite:test")

    assert cache.get("sqlite:test") is None