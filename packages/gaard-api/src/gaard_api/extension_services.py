from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from gaard_api.admin.models import DatasourceConnector
from gaard_api.admin.services import (
    get_datasource_connector,
    get_datasource_schema_cache,
    is_system_datasource_connector,
    list_datasource_connectors,
    selected_schema_from_cache,
)

SessionFactory = Callable[[], Session]


class DatasourceHostService:
    """Read-only datasource/schema facade exposed to trusted extensions."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def list_datasources(self, *, include_system: bool = False) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            items = []
            for connector in list_datasource_connectors(session):
                if not include_system and is_system_datasource_connector(connector):
                    continue
                cache = get_datasource_schema_cache(session, connector.id)
                items.append(
                    serialize_extension_datasource(
                        connector,
                        has_schema_cache=cache is not None,
                    )
                )
            return items

    def get_datasource(self, connector_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            connector = get_datasource_connector(session, connector_id)
            if connector is None:
                return None
            cache = get_datasource_schema_cache(session, connector.id)
            return serialize_extension_datasource(connector, has_schema_cache=cache is not None)

    def get_schema(self, connector_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            connector = get_datasource_connector(session, connector_id)
            if connector is None:
                return None
            cache = get_datasource_schema_cache(session, connector.id)
            if cache is None:
                return None
            return selected_schema_from_cache(cache).model_dump()


def serialize_extension_datasource(
    connector: DatasourceConnector,
    *,
    has_schema_cache: bool,
) -> dict[str, Any]:
    return {
        "id": connector.id,
        "connector_key": connector.connector_key,
        "name": connector.name,
        "database_type": connector.database_type,
        "sql_dialect": connector.sql_dialect,
        "active": connector.active,
        "system_managed": is_system_datasource_connector(connector),
        "has_schema_cache": has_schema_cache,
        "updated_by": connector.updated_by,
        "updated_at": serialize_datetime(connector.updated_at),
    }


def serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
