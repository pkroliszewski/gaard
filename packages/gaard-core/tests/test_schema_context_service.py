from gaard_core.schema.cache import SchemaContextCache
from gaard_core.schema.context import SchemaContextService
from gaard_core.schema.models import ColumnInfo, DatabaseSchema, TableInfo


class FakeIntrospector:
    def __init__(self) -> None:
        self.calls = 0

    def introspect(self) -> DatabaseSchema:
        self.calls += 1
        return DatabaseSchema(
            tables=[
                TableInfo(
                    name="patients",
                    columns=[
                        ColumnInfo(name="id", type="INTEGER", primary_key=True),
                    ],
                )
            ]
        )


def test_schema_context_service_uses_cache_after_first_call() -> None:
    introspector = FakeIntrospector()
    cache = SchemaContextCache(ttl_seconds=300)

    service = SchemaContextService(
        introspector=introspector,
        cache=cache,
    )

    first = service.get_schema_context("sqlite:test")
    second = service.get_schema_context("sqlite:test")

    assert first.formatted_schema == second.formatted_schema
    assert introspector.calls == 1
    assert "Table: patients" in first.formatted_schema