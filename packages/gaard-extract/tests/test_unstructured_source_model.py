from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from gaard_api.admin.models import AdminSetting, DatasourceConnector, DatasourceSchemaCache
from gaard_api.admin.services import LlmRuntimeConfig, set_setting
from gaard_api.license import LicenseAccessError
from gaard_core.errors import LlmProviderError
from gaard_plugin_api import ExtensionContext
from gaard_llm.providers.models import ChatCompletionResponse
import httpx2 as httpx
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from gaard_api.api_registry import ApiRegistry
from gaard_extract import service
from gaard_extract import db
from gaard_extract.api import create_router
from gaard_extract.db import init_database
from gaard_extract.plugin import extension, register_api


SCHEMA = {
    "tables": [
        {
            "name": "case_notes",
            "object_type": "table",
            "columns": [
                {"name": "case_id", "type": "TEXT", "nullable": False, "primary_key": False},
                {"name": "note_text", "type": "TEXT", "nullable": True, "primary_key": False},
                {"name": "created_at", "type": "DATETIME", "nullable": True, "primary_key": False},
            ],
            "foreign_keys": [],
        },
        {
            "name": "case_comments",
            "object_type": "table",
            "columns": [
                {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
                {"name": "case_ref", "type": "TEXT", "nullable": False, "primary_key": False},
                {"name": "comment_text", "type": "TEXT", "nullable": True, "primary_key": False},
            ],
            "foreign_keys": [],
        },
    ]
}


class FakeDatasourceService:
    def __init__(self, *, schema: dict[str, Any] | None = SCHEMA) -> None:
        self.schema = schema
        self.datasources = {
            1: {
                "id": 1,
                "connector_key": "notes-db",
                "name": "Notes DB",
                "database_type": "sqlite",
                "sql_dialect": "sqlite",
                "active": True,
                "system_managed": False,
                "has_schema_cache": schema is not None,
                "updated_by": "tester",
                "updated_at": "2026-01-01T00:00:00",
            },
            2: {
                "id": 2,
                "connector_key": "metadata-db",
                "name": "Metadata DB",
                "database_type": "sqlite",
                "sql_dialect": "sqlite",
                "active": False,
                "system_managed": True,
                "has_schema_cache": True,
                "updated_by": "system",
                "updated_at": "2026-01-01T00:00:00",
            },
            3: {
                "id": 3,
                "connector_key": "archive-db",
                "name": "Archive DB",
                "database_type": "sqlite",
                "sql_dialect": "sqlite",
                "active": False,
                "system_managed": False,
                "has_schema_cache": schema is not None,
                "updated_by": "tester",
                "updated_at": "2026-01-01T00:00:00",
            },
        }

    def list_datasources(self, *, include_system: bool = False) -> list[dict[str, Any]]:
        items = list(self.datasources.values())
        return items if include_system else [item for item in items if not item["system_managed"]]

    def get_datasource(self, connector_id: int) -> dict[str, Any] | None:
        return self.datasources.get(connector_id)

    def get_schema(self, connector_id: int) -> dict[str, Any] | None:
        if connector_id not in self.datasources:
            return None
        return self.schema


class FakeLicenseService:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str | None]] = []

    def require_feature(self, feature: str, detail: str | None = None) -> None:
        self.calls.append((feature, detail))
        if not self.allowed:
            raise LicenseAccessError(detail)


def make_session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db.metadata.create_all(engine)
    for table in (
        AdminSetting.__table__,
        DatasourceConnector.__table__,
        DatasourceSchemaCache.__table__,
    ):
        table.create(bind=engine, checkfirst=True)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def make_client(
    datasource_service: FakeDatasourceService | None = None,
    session_factory: sessionmaker | None = None,
    license_service: FakeLicenseService | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_router(
            session_factory or make_session_factory(),
            datasource_service or FakeDatasourceService(),
            license_service or FakeLicenseService(),
        )
    )
    return TestClient(app)


def test_extension_manifest_and_api_registration() -> None:
    manifest = extension()
    assert manifest.id == "gaard-extract"
    assert manifest.contributions == {"api": "gaard_extract.plugin:register_api"}

    registry = ApiRegistry()
    session_factory = make_session_factory()
    context = ExtensionContext(
        extension_id="gaard-extract",
        capability="api",
        registry=registry,
        services={
            "metadata_session_factory": session_factory,
            "datasources": FakeDatasourceService(),
            "license": FakeLicenseService(),
        },
    )

    register_api(context)
    assert [section.section_key for section in registry.list_admin_sections()] == ["extract"]


def test_datasources_and_schema_endpoints_use_host_service() -> None:
    client = make_client()

    datasources = client.get("/datasources").json()["items"]
    assert [item["connector_key"] for item in datasources] == ["notes-db", "archive-db"]

    schema = client.get("/datasources/1/schema").json()["item"]
    assert [table["name"] for table in schema["tables"]] == ["case_notes", "case_comments"]


def test_source_model_can_be_created_and_updated() -> None:
    client = make_client()

    create_response = client.put(
        "/source-models/1",
        json={
            "main_table": "case_notes",
            "table_roles": {
                "case_notes": {
                    "case_id_column": "case_id",
                    "content_column": "note_text",
                },
                "case_comments": {
                    "case_id_column": "case_ref",
                    "content_column": "comment_text",
                },
            },
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["item"]["table_roles"]["case_comments"] == {
        "case_id_column": "case_ref",
        "content_column": "comment_text",
    }

    update_response = client.put(
        "/source-models/1",
        json={
            "main_table": "case_notes",
            "table_roles": {
                "case_notes": {
                    "case_id_column": "case_id",
                    "content_column": "created_at",
                },
            },
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["item"]["table_roles"] == {
        "case_notes": {
            "case_id_column": "case_id",
            "content_column": "created_at",
        }
    }

    list_response = client.get("/source-models")
    assert len(list_response.json()["items"]) == 1


def test_only_one_source_model_can_be_defined() -> None:
    client = make_client()

    first_response = client.put(
        "/source-models/1",
        json={
            "main_table": "case_notes",
            "table_roles": {
                "case_notes": {
                    "case_id_column": "case_id",
                    "content_column": "note_text",
                },
            },
        },
    )
    second_response = client.put(
        "/source-models/3",
        json={
            "main_table": "case_comments",
            "table_roles": {
                "case_comments": {
                    "case_id_column": "case_ref",
                    "content_column": "comment_text",
                },
            },
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert client.get("/source-models/1").json()["item"] is None
    assert client.get("/source-models/3").json()["item"]["datasource_connector_key"] == "archive-db"

    items = client.get("/source-models").json()["items"]
    assert len(items) == 1
    assert items[0]["datasource_connector_id"] == 3


def test_chunking_config_defaults_and_can_be_updated() -> None:
    client = make_client()

    default_response = client.get("/chunking-config")
    assert default_response.status_code == 200
    assert default_response.json()["item"] == {
        "mode": "fixed_size",
        "persisted": False,
    }

    update_response = client.put("/chunking-config", json={"mode": "semantic"})
    assert update_response.status_code == 200
    assert update_response.json()["item"]["mode"] == "semantic"
    assert update_response.json()["item"]["persisted"] is True

    second_update_response = client.put("/chunking-config", json={"mode": "none"})
    assert second_update_response.status_code == 200
    assert second_update_response.json()["item"]["mode"] == "none"

    get_response = client.get("/chunking-config")
    assert get_response.json()["item"]["mode"] == "none"
    assert get_response.json()["item"]["persisted"] is True


def test_chunking_config_rejects_unknown_mode() -> None:
    client = make_client()

    response = client.put("/chunking-config", json={"mode": "magic"})

    assert response.status_code == 400
    assert "Chunking mode must be one of" in response.json()["detail"]


def test_embedding_config_defaults_and_can_be_updated() -> None:
    client = make_client()

    default_response = client.get("/embedding-config")
    assert default_response.status_code == 200
    assert default_response.json()["item"]["enabled"] is False
    assert default_response.json()["item"]["provider"] == "openai-compatible"
    assert default_response.json()["item"]["model"] == "text-embedding-3-small"
    assert default_response.json()["item"]["api_key_configured"] is False

    update_response = client.put(
        "/embedding-config",
        json={
            "enabled": True,
            "provider": "openai-compatible",
            "base_url": "https://embeddings.example/v1",
            "api_key": "secret-1234",
            "model": "embed-small",
            "timeout_seconds": 45,
            "extra_body": {"encoding_format": "float"},
        },
    )
    assert update_response.status_code == 200
    item = update_response.json()["item"]
    assert item["enabled"] is True
    assert item["base_url"] == "https://embeddings.example/v1"
    assert item["model"] == "embed-small"
    assert item["timeout_seconds"] == 45
    assert item["extra_body"] == {"encoding_format": "float"}
    assert item["api_key_configured"] is True
    assert item["api_key_preview"] == "****1234"
    assert "api_key" not in item

    keep_key_response = client.put(
        "/embedding-config",
        json={
            "enabled": False,
            "provider": "openai-compatible",
            "base_url": "https://embeddings.example/v1",
            "api_key": "",
            "model": "embed-small",
            "timeout_seconds": 45,
            "extra_body": {},
        },
    )
    assert keep_key_response.status_code == 200
    assert keep_key_response.json()["item"]["enabled"] is False
    assert keep_key_response.json()["item"]["api_key_configured"] is True


def test_embedding_config_can_clear_api_key() -> None:
    client = make_client()

    client.put(
        "/embedding-config",
        json={
            "enabled": True,
            "provider": "openai-compatible",
            "base_url": "https://embeddings.example/v1",
            "api_key": "secret-1234",
            "model": "embed-small",
            "timeout_seconds": 45,
            "extra_body": {},
        },
    )
    response = client.put(
        "/embedding-config",
        json={
            "enabled": True,
            "provider": "openai-compatible",
            "base_url": "https://embeddings.example/v1",
            "clear_api_key": True,
            "model": "embed-small",
            "timeout_seconds": 45,
            "extra_body": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["item"]["api_key_configured"] is False


def test_embedding_config_rejects_unsupported_provider() -> None:
    client = make_client()

    response = client.put(
        "/embedding-config",
        json={
            "enabled": True,
            "provider": "other",
            "base_url": "https://embeddings.example/v1",
            "model": "embed-small",
            "timeout_seconds": 45,
            "extra_body": {},
        },
    )

    assert response.status_code == 400
    assert "Only openai-compatible" in response.json()["detail"]


def test_embedding_config_test_calls_openai_compatible_embeddings_endpoint(monkeypatch) -> None:
    client = make_client()
    calls: list[dict[str, Any]] = []

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return httpx.Response(
            200,
            json={
                "model": "embed-small",
                "data": [{"embedding": [0.1, 0.2, 0.3]}],
            },
        )

    monkeypatch.setattr("gaard_extract.service.httpx.post", fake_post)

    response = client.post(
        "/embedding-config/test",
        json={
            "enabled": True,
            "provider": "openai-compatible",
            "base_url": "https://embeddings.example/v1/",
            "api_key": "secret-1234",
            "model": "embed-small",
            "timeout_seconds": 45,
            "extra_body": {"encoding_format": "float"},
        },
    )

    assert response.status_code == 200
    assert response.json()["item"] == {
        "ok": True,
        "model": "embed-small",
        "embedding_dimensions": 3,
    }
    assert calls == [
        {
            "url": "https://embeddings.example/v1/embeddings",
            "json": {
                "model": "embed-small",
                "input": ["GAARD Extract embedding connection test."],
                "encoding_format": "float",
            },
            "headers": {
                "Authorization": "Bearer secret-1234",
                "Content-Type": "application/json",
            },
            "timeout": 45,
        }
    ]


def test_embedding_config_test_can_reuse_saved_api_key(monkeypatch) -> None:
    client = make_client()
    calls: list[dict[str, str]] = []

    client.put(
        "/embedding-config",
        json={
            "enabled": True,
            "provider": "openai-compatible",
            "base_url": "https://embeddings.example/v1",
            "api_key": "secret-1234",
            "model": "embed-small",
            "timeout_seconds": 45,
            "extra_body": {},
        },
    )

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        calls.append({"authorization": headers["Authorization"]})
        return httpx.Response(200, json={"data": [{"embedding": [0.1]}]})

    monkeypatch.setattr("gaard_extract.service.httpx.post", fake_post)

    response = client.post(
        "/embedding-config/test",
        json={
            "enabled": True,
            "provider": "openai-compatible",
            "base_url": "https://embeddings.example/v1",
            "api_key": "",
            "model": "embed-small",
            "timeout_seconds": 45,
            "extra_body": {},
        },
    )

    assert response.status_code == 200
    assert calls == [{"authorization": "Bearer secret-1234"}]


def test_embedding_config_test_rejects_invalid_embedding_response(monkeypatch) -> None:
    client = make_client()

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": []}]})

    monkeypatch.setattr("gaard_extract.service.httpx.post", fake_post)

    response = client.post(
        "/embedding-config/test",
        json={
            "enabled": True,
            "provider": "openai-compatible",
            "base_url": "https://embeddings.example/v1",
            "api_key": "secret-1234",
            "model": "embed-small",
            "timeout_seconds": 45,
            "extra_body": {},
        },
    )

    assert response.status_code == 400
    assert "invalid embedding vector" in response.json()["detail"]


def test_llm_extracting_config_defaults_follow_source_and_embeddings() -> None:
    client = make_client()
    configure_source_model(client)
    configure_embeddings(client, enabled=True)

    response = client.get("/llm-extracting-config")

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["persisted"] is False
    assert set(item["extraction_scope"]["content_tables"]) == {"case_notes", "case_comments"}
    assert item["extraction_scope"]["use_embeddings"] is True
    assert item["extraction_scope"]["chunk_selection"] == "embedding_neighbors"
    assert item["json_schema"]["required"] == [
        "entities",
        "events",
        "facts",
        "relations",
        "warnings",
    ]


def test_llm_extracting_config_can_be_saved_as_blueprint() -> None:
    client = make_client()
    configure_source_model(client)

    response = client.put(
        "/llm-extracting-config",
        json={
            "blueprint_key": "claims_notes",
            "name": "Claims Notes",
            "description": "Claims extraction scope",
            "domain_description": "Notatki reklamacyjne klientów.",
            "case_grain_description": "Jeden case to jedna reklamacja klienta.",
            "language": "pl",
            "status": "draft",
            "information_types": [
                {
                    "key": "decision",
                    "kind": "event",
                    "description": "Decyzja w sprawie reklamacji.",
                    "fields": [
                        {
                            "name": "decision_type",
                            "type": "enum",
                            "required": True,
                            "values": ["positive", "negative", "partial"],
                        },
                        {
                            "name": "decision_date",
                            "type": "date",
                            "required": False,
                            "values": [],
                        },
                    ],
                }
            ],
            "global_rules": ["Nie zgaduj.", "Wymagaj evidence_text."],
            "review_policy": {
                "auto_approve_threshold": 0.92,
                "needs_review_threshold": 0.65,
                "reject_below_threshold": 0.25,
            },
            "extraction_scope": {
                "source_mode": "active_source_model",
                "content_tables": ["case_notes"],
                "chunk_selection": "all_chunks",
                "use_embeddings": False,
                "max_neighbor_chunks": 0,
                "min_similarity": 0.7,
                "max_chunks_per_case": 100,
                "include_case_metadata": True,
                "require_evidence_text": True,
            },
        },
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["persisted"] is True
    assert item["blueprint_key"] == "claims_notes"
    assert item["information_types"][0]["fields"][0]["values"] == [
        "positive",
        "negative",
        "partial",
    ]
    assert item["extraction_scope"]["content_tables"] == ["case_notes"]
    event_schema = item["json_schema"]["properties"]["events"]["items"]["oneOf"][0]
    assert event_schema["properties"]["type"] == {"const": "decision"}
    assert event_schema["properties"]["attributes"]["required"] == ["decision_type"]


def test_llm_extracting_blueprint_key_allows_hyphens() -> None:
    client = make_client()
    configure_source_model(client)

    response = client.put(
        "/llm-extracting-config",
        json=llm_payload(blueprint_key="generic-case-notes"),
    )

    assert response.status_code == 200
    assert response.json()["item"]["blueprint_key"] == "generic-case-notes"


def test_llm_extracting_draft_can_save_keys_before_descriptions_are_complete() -> None:
    client = make_client()
    configure_source_model(client)

    response = client.put(
        "/llm-extracting-config",
        json=llm_payload(
            domain_description="",
            case_grain_description="",
            information_types=[
                {
                    "key": "priority",
                    "kind": "fact",
                    "description": "",
                    "fields": [
                        {
                            "name": "value",
                            "type": "string",
                            "required": False,
                            "values": [],
                        }
                    ],
                }
            ],
        ),
    )

    assert response.status_code == 200
    assert response.json()["item"]["information_types"][0]["key"] == "priority"


def test_llm_extracting_approved_blueprint_requires_domain_descriptions() -> None:
    client = make_client()
    configure_source_model(client)

    response = client.put(
        "/llm-extracting-config",
        json=llm_payload(
            status="approved",
            domain_description="",
            case_grain_description="",
        ),
    )

    assert response.status_code == 400
    assert "Domain description is required" in response.json()["detail"]


def test_llm_key_description_suggestion_uses_saved_llm_config(monkeypatch) -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        set_setting(session, "gaard_llm_provider", "openai-compatible", "test")
        set_setting(session, "gaard_llm_base_url", "https://llm.example/v1", "test")
        set_setting(session, "gaard_llm_api_key", "secret-1234", "test")
        set_setting(session, "gaard_llm_model", "chat-test", "test")
        set_setting(session, "gaard_llm_timeout_seconds", "45", "test")
        set_setting(session, "gaard_llm_extra_body", '{"seed": 7}', "test")
        session.commit()

    class FakeOpenAICompatibleClient:
        init_kwargs: dict[str, Any] | None = None
        requests: list[Any] = []

        def __init__(self, **kwargs: Any) -> None:
            self.__class__.init_kwargs = kwargs

        def create_chat_completion(self, request: Any) -> ChatCompletionResponse:
            self.__class__.requests.append(request)
            return ChatCompletionResponse(
                content='{"description":"Wyodrębnij decyzję reklamacyjną tylko wtedy, gdy tekst wskazuje jej wynik."}',
                model=request.model,
            )

    monkeypatch.setattr(
        "gaard_extract.service.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    client = make_client(session_factory=session_factory)
    response = client.post(
        "/llm-extracting-config/key-description-suggestion",
        json={
            "key": "claim_decision",
            "kind": "event",
            "domain_description": "Notatki reklamacyjne klientów.",
            "case_grain_description": "Jeden case to jedna reklamacja klienta.",
            "language": "pl",
        },
    )

    assert response.status_code == 200
    assert response.json()["item"] == {
        "description": "Wyodrębnij decyzję reklamacyjną tylko wtedy, gdy tekst wskazuje jej wynik.",
        "model": "chat-test",
    }
    assert FakeOpenAICompatibleClient.init_kwargs == {
        "base_url": "https://llm.example/v1",
        "api_key": "secret-1234",
        "timeout_seconds": 45,
    }
    request = FakeOpenAICompatibleClient.requests[0]
    assert request.model == "chat-test"
    assert request.extra_body == {"seed": 7}
    assert request.messages[0].role == "system"
    assert request.messages[1].role == "user"
    assert '"key": "claim_decision"' in request.messages[1].content
    assert '"kind": "event"' in request.messages[1].content
    assert "Notatki reklamacyjne klientów" in request.messages[1].content


def test_llm_key_fields_suggestion_uses_key_definition(monkeypatch) -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        set_setting(session, "gaard_llm_provider", "openai-compatible", "test")
        set_setting(session, "gaard_llm_base_url", "https://llm.example/v1", "test")
        set_setting(session, "gaard_llm_api_key", "secret-1234", "test")
        set_setting(session, "gaard_llm_model", "chat-test", "test")
        set_setting(session, "gaard_llm_timeout_seconds", "45", "test")
        set_setting(session, "gaard_llm_extra_body", '{"seed": 7}', "test")
        session.commit()

    class FakeOpenAICompatibleClient:
        requests: list[Any] = []

        def __init__(self, **kwargs: Any) -> None:
            pass

        def create_chat_completion(self, request: Any) -> ChatCompletionResponse:
            self.__class__.requests.append(request)
            return ChatCompletionResponse(
                content=(
                    '{"fields":['
                    '{"name":"decision_type","type":"enum","required":true,'
                    '"enum":["positive","negative","partial"]},'
                    '{"name":"decision_date","type":"date","required":false,"values":[]},'
                    '{"name":"amount","type":"integer","required":false,"values":[]}'
                    "]}"
                ),
                model=request.model,
            )

    monkeypatch.setattr(
        "gaard_extract.service.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    client = make_client(session_factory=session_factory)
    response = client.post(
        "/llm-extracting-config/key-fields-suggestion",
        json={
            "key": "claim_decision",
            "kind": "event",
            "description": "Decyzja w sprawie reklamacji wraz z wynikiem.",
            "domain_description": "Notatki reklamacyjne klientów.",
            "case_grain_description": "Jeden case to jedna reklamacja klienta.",
            "language": "pl",
        },
    )

    assert response.status_code == 200
    assert response.json()["item"] == {
        "fields": [
            {
                "name": "decision_type",
                "type": "enum",
                "required": True,
                "values": ["positive", "negative", "partial"],
            },
            {
                "name": "decision_date",
                "type": "date",
                "required": False,
                "values": [],
            },
            {
                "name": "amount",
                "type": "number",
                "required": False,
                "values": [],
            },
        ],
        "model": "chat-test",
    }
    request = FakeOpenAICompatibleClient.requests[0]
    assert request.model == "chat-test"
    assert request.extra_body == {"seed": 7}
    assert '"key": "claim_decision"' in request.messages[1].content
    assert '"key_description": "Decyzja w sprawie reklamacji wraz z wynikiem."' in request.messages[1].content
    assert "allowed_field_types" in request.messages[1].content


def test_llm_key_description_suggestion_requires_llm_api_key() -> None:
    session_factory = make_session_factory()
    with session_factory() as session:
        set_setting(session, "gaard_llm_api_key", "change-me", "test")
        session.commit()
    client = make_client(session_factory=session_factory)

    response = client.post(
        "/llm-extracting-config/key-description-suggestion",
        json={
            "key": "claim_decision",
            "kind": "event",
            "language": "pl",
        },
    )

    assert response.status_code == 400
    assert "LLM API key is not configured" in response.json()["detail"]


def test_llm_extracting_config_rejects_unknown_content_table() -> None:
    client = make_client()
    configure_source_model(client)

    response = client.put(
        "/llm-extracting-config",
        json=llm_payload(
            extraction_scope={
                "source_mode": "active_source_model",
                "content_tables": ["missing_table"],
                "chunk_selection": "all_chunks",
                "use_embeddings": False,
            }
        ),
    )

    assert response.status_code == 400
    assert "missing_table" in response.json()["detail"]


def test_llm_extracting_config_requires_enabled_embeddings_for_embedding_context() -> None:
    client = make_client()
    configure_source_model(client)

    response = client.put(
        "/llm-extracting-config",
        json=llm_payload(
            extraction_scope={
                "source_mode": "active_source_model",
                "content_tables": ["case_notes"],
                "chunk_selection": "embedding_neighbors",
                "use_embeddings": True,
            }
        ),
    )

    assert response.status_code == 400
    assert "Enable embeddings" in response.json()["detail"]


def test_database_initializer_creates_job_tables() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    init_database(session_factory)

    table_names = set(inspect(engine).get_table_names())
    assert {
        "extract_job_configs",
        "extract_jobs",
        "extract_job_events",
    }.issubset(table_names)


def test_extract_job_requires_active_extract_license(tmp_path: Path) -> None:
    session_factory = make_session_factory()
    license_service = FakeLicenseService(allowed=False)
    client = make_client(session_factory=session_factory, license_service=license_service)
    configure_extract_job_fixture(client, session_factory, tmp_path)

    response = client.post("/jobs")

    assert response.status_code == 403
    assert response.json()["detail"] == "Extract jobs require an active Enterprise license."
    assert license_service.calls == [
        ("extract_jobs", "Extract jobs require an active Enterprise license.")
    ]
    assert client.get("/jobs").json()["items"] == []


def test_extract_job_refresh_requires_active_extract_license(tmp_path: Path) -> None:
    session_factory = make_session_factory()
    license_service = FakeLicenseService()
    client = make_client(session_factory=session_factory, license_service=license_service)
    configure_extract_job_fixture(client, session_factory, tmp_path)
    job_id = client.post("/jobs").json()["item"]["id"]

    license_service.allowed = False
    license_service.calls.clear()
    response = client.post(f"/jobs/{job_id}/refresh")

    assert response.status_code == 403
    assert response.json()["detail"] == "Extract jobs require an active Enterprise license."
    assert license_service.calls == [
        ("extract_jobs", "Extract jobs require an active Enterprise license.")
    ]
    jobs = client.get("/jobs").json()["items"]
    assert [job["id"] for job in jobs] == [job_id]


def test_extract_job_worker_creates_output_database_and_datasource(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    session_factory = make_session_factory()
    client = make_client(session_factory=session_factory)
    configure_extract_job_fixture(client, session_factory, tmp_path)
    monkeypatch.setenv("GAARD_EXTRACT_OUTPUT_DIR", str(tmp_path / "runs"))

    class FakeOpenAICompatibleClient:
        requests: list[Any] = []

        def __init__(self, **kwargs: Any) -> None:
            pass

        def create_chat_completion(self, request: Any) -> ChatCompletionResponse:
            self.__class__.requests.append(request)
            return ChatCompletionResponse(
                content=(
                    '{"entities":[],"events":[],"facts":['
                    '{"type":"case_status","attributes":{"status":"open"},'
                    '"evidence_text":"Status: open","confidence":0.95}'
                    '],"relations":[],"warnings":[]}'
                ),
                model=request.model,
            )

    monkeypatch.setattr(
        "gaard_extract.service.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    create_response = client.post("/jobs")
    assert create_response.status_code == 200
    job_id = create_response.json()["item"]["id"]

    from gaard_extract import service

    assert service.run_next_job(session_factory) is True

    job_response = client.get(f"/jobs/{job_id}")
    assert job_response.status_code == 200
    job = job_response.json()["item"]
    assert job["status"] == "succeeded"
    assert job["cases_total"] == 2
    assert job["items_total"] == 2
    assert job["output_datasource_key"].startswith("extract-claims-notes-")

    output_path = Path(job["output_path"])
    assert output_path.exists()
    with sqlite3.connect(output_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "extract_runs",
            "extract_cases",
            "extract_documents",
            "extract_chunks",
            "extract_items",
            "extract_attributes",
            "extract_errors",
        }.issubset(table_names)
        rows = connection.execute(
            "SELECT case_id, status, needs_review FROM v_extract_case_status ORDER BY case_id"
        ).fetchall()
        assert rows == [("case-1", "open", 0), ("case-2", "open", 0)]

    with session_factory() as session:
        connector = session.get(DatasourceConnector, job["output_datasource_id"])
        assert connector is not None
        assert connector.active is False
        assert connector.connector_key == job["output_datasource_key"]
        assert session.get(DatasourceSchemaCache, connector.id) is not None


def test_extract_job_config_and_refresh_use_historical_snapshot(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    session_factory = make_session_factory()
    client = make_client(session_factory=session_factory)
    configure_extract_job_fixture(client, session_factory, tmp_path)
    monkeypatch.setenv("GAARD_EXTRACT_OUTPUT_DIR", str(tmp_path / "runs"))

    class FakeOpenAICompatibleClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def create_chat_completion(self, request: Any) -> ChatCompletionResponse:
            return ChatCompletionResponse(
                content=(
                    '{"entities":[],"events":[],"facts":['
                    '{"type":"case_status","attributes":{"status":"closed"},'
                    '"evidence_text":"Status: closed","confidence":0.91}'
                    '],"relations":[],"warnings":[]}'
                ),
                model=request.model,
            )

    monkeypatch.setattr(
        "gaard_extract.service.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    job_id = client.post("/jobs").json()["item"]["id"]
    from gaard_extract import service

    assert service.run_next_job(session_factory) is True

    config_response = client.get(f"/jobs/{job_id}/config")
    assert config_response.status_code == 200
    config = config_response.json()["item"]
    assert config["source_model"]["main_table"] == "case_notes"
    assert config["llm_config"]["blueprint_key"] == "claims_notes"

    refresh_response = client.post(f"/jobs/{job_id}/refresh")
    assert refresh_response.status_code == 200
    refresh_job = refresh_response.json()["item"]
    assert refresh_job["id"] != job_id
    assert refresh_job["status"] == "queued"

    refresh_config_response = client.get(f"/jobs/{refresh_job['id']}/config")
    assert refresh_config_response.status_code == 200
    refresh_config = refresh_config_response.json()["item"]
    assert refresh_config["origin"] == "refresh"
    assert refresh_config["source_job_id"] == job_id
    assert refresh_config["llm_config"]["blueprint_key"] == "claims_notes"


def test_extract_job_requires_llm_api_key(tmp_path: Path) -> None:
    session_factory = make_session_factory()
    client = make_client(session_factory=session_factory)
    configure_extract_job_fixture(client, session_factory, tmp_path)
    configure_llm_runtime(session_factory, api_key="change-me")

    response = client.post("/jobs")

    assert response.status_code == 400
    assert "LLM API key is not configured" in response.json()["detail"]


def test_extract_job_bad_llm_json_fails_and_records_error(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    session_factory = make_session_factory()
    client = make_client(session_factory=session_factory)
    configure_extract_job_fixture(client, session_factory, tmp_path)
    monkeypatch.setenv("GAARD_EXTRACT_OUTPUT_DIR", str(tmp_path / "runs"))

    class FakeOpenAICompatibleClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def create_chat_completion(self, request: Any) -> ChatCompletionResponse:
            return ChatCompletionResponse(content="not json", model=request.model)

    monkeypatch.setattr(
        "gaard_extract.service.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    job_id = client.post("/jobs").json()["item"]["id"]
    from gaard_extract import service

    assert service.run_next_job(session_factory) is True

    job = client.get(f"/jobs/{job_id}").json()["item"]
    assert job["status"] == "failed"
    assert "LLM returned non-JSON" in job["error_message"]
    assert any("LLM returned non-JSON" in event["message"] for event in job["events"])

    with sqlite3.connect(job["output_path"]) as connection:
        errors = connection.execute("SELECT case_id, error FROM extract_errors").fetchall()
    assert errors == [("case-1", "LLM returned non-JSON extraction output.")]


def test_extract_parser_tolerates_reserved_fields_inside_attributes() -> None:
    parsed = service.parse_extraction_response(
        (
            '{"entities":[],"events":[],"facts":['
            '{"type":"opis_problemu","attributes":{'
            '"opis":"Klient zgłasza problem z płatnością.",'
            '"confidence":0.82,'
            '"evidence_text":"problem z płatnością",'
            '"source":"email"'
            '}}'
            '],"relations":[],"warnings":[]}'
        ),
        {
            "information_types": [
                {
                    "key": "opis_problemu",
                    "kind": "fact",
                    "description": "",
                    "fields": [
                        {
                            "name": "opis",
                            "type": "string",
                            "required": True,
                            "values": [],
                        }
                    ],
                }
            ]
        },
    )

    assert parsed["items"] == [
        {
            "kind": "fact",
            "type": "opis_problemu",
            "attributes": {"opis": "Klient zgłasza problem z płatnością."},
            "evidence_text": "problem z płatnością",
            "confidence": 0.82,
        }
    ]


def test_extract_parser_ignores_unknown_attributes_with_warning() -> None:
    parsed = service.parse_extraction_response(
        (
            '{"entities":[],"events":[],"facts":['
            '{"type":"opis_problemu","attributes":{'
            '"opis":"Klient zgłasza problem z płatnością.",'
            '"notes":"Model dopisał dodatkową uwagę."'
            '},'
            '"evidence_text":"problem z płatnością","confidence":0.82}'
            '],"relations":[],"warnings":[]}'
        ),
        {
            "information_types": [
                {
                    "key": "opis_problemu",
                    "kind": "fact",
                    "description": "",
                    "fields": [
                        {
                            "name": "opis",
                            "type": "string",
                            "required": True,
                            "values": [],
                        }
                    ],
                }
            ]
        },
    )

    assert parsed["items"][0]["attributes"] == {
        "opis": "Klient zgłasza problem z płatnością."
    }
    assert parsed["warnings"] == [
        "LLM returned unknown attribute 'notes' for 'opis_problemu'; ignored value "
        '"Model dopisał dodatkową uwagę.".'
    ]


def test_extract_parser_enum_error_reports_bad_value_and_allowed_values() -> None:
    with pytest.raises(ValueError) as exc_info:
        service.parse_extraction_response(
            (
                '{"entities":['
                '{"type":"ludzie","attributes":{"name":"Anna","role":"kupujący"},'
                '"evidence_text":"Anna kupujący","confidence":0.8}'
                '],"events":[],"facts":[],"relations":[],"warnings":[]}'
            ),
            {
                "information_types": [
                    {
                        "key": "ludzie",
                        "kind": "entity",
                        "description": "",
                        "fields": [
                            {
                                "name": "name",
                                "type": "string",
                                "required": True,
                                "values": [],
                            },
                            {
                                "name": "role",
                                "type": "enum",
                                "required": True,
                                "values": ["buyer", "seller"],
                            },
                        ],
                    }
                ]
            },
        )

    message = str(exc_info.value)
    assert "Attribute 'role' for 'ludzie' must match enum values." in message
    assert 'Got "kupujący" (str)' in message
    assert 'allowed values: ["buyer", "seller"]' in message


def test_extract_parser_enum_falls_back_to_other_with_warning() -> None:
    parsed = service.parse_extraction_response(
        (
            '{"entities":['
            '{"type":"ludzie","attributes":{"name":"Anna","role":"HR Manager"},'
            '"evidence_text":"Anna HR Manager","confidence":0.8}'
            '],"events":[],"facts":[],"relations":[],"warnings":[]}'
        ),
        {
            "information_types": [
                {
                    "key": "ludzie",
                    "kind": "entity",
                    "description": "",
                    "fields": [
                        {
                            "name": "name",
                            "type": "string",
                            "required": True,
                            "values": [],
                        },
                        {
                            "name": "role",
                            "type": "enum",
                            "required": True,
                            "values": ["customer", "support", "manager", "vendor", "other"],
                        },
                    ],
                }
            ]
        },
    )

    assert parsed["items"][0]["attributes"]["role"] == "other"
    assert parsed["warnings"] == [
        "Enum attribute 'role' for 'ludzie' got unsupported value "
        '"HR Manager"; stored "other". Allowed values: '
        '["customer", "support", "manager", "vendor", "other"].'
    ]


def test_extract_parser_enum_normalizes_case_with_warning() -> None:
    parsed = service.parse_extraction_response(
        (
            '{"entities":['
            '{"type":"ludzie","attributes":{"name":"Anna","role":"Manager"},'
            '"evidence_text":"Anna Manager","confidence":0.8}'
            '],"events":[],"facts":[],"relations":[],"warnings":[]}'
        ),
        {
            "information_types": [
                {
                    "key": "ludzie",
                    "kind": "entity",
                    "description": "",
                    "fields": [
                        {
                            "name": "name",
                            "type": "string",
                            "required": True,
                            "values": [],
                        },
                        {
                            "name": "role",
                            "type": "enum",
                            "required": True,
                            "values": ["customer", "support", "manager", "vendor", "other"],
                        },
                    ],
                }
            ]
        },
    )

    assert parsed["items"][0]["attributes"]["role"] == "manager"
    assert parsed["warnings"] == [
        "Enum attribute 'role' for 'ludzie' was normalized from "
        '"Manager" to "manager".'
    ]


def test_extract_request_reports_llm_provider_failure_cause() -> None:
    class FailingClient:
        requests: list[Any] = []

        def create_chat_completion(self, request: Any) -> ChatCompletionResponse:
            self.__class__.requests.append(request)
            try:
                raise httpx.ReadTimeout("read timed out")
            except httpx.HTTPError as exc:
                raise LlmProviderError("LLM provider request failed.") from exc

    with pytest.raises(ValueError) as exc_info:
        service.request_case_extraction(
            FailingClient(),
            LlmRuntimeConfig(
                provider="openai-compatible",
                base_url="https://llm.example/v1",
                api_key="secret-1234",
                model="chat-test",
                extra_body={},
                timeout_seconds=45,
            ),
            {"information_types": []},
            {"case_id": "case-8"},
            [{"chunk_id": "chunk-1", "source_table": "email", "text": "hello"}],
        )

    message = str(exc_info.value)
    assert "ReadTimeout: read timed out" in message
    assert "Case=case-8" in message
    assert "model=chat-test" in message
    assert "timeout=45s" in message
    assert "chunks_sent=1" in message
    assert "prompt_chars=" in message
    assert "attempt=2/2" in message
    assert len(FailingClient.requests) == 2


def test_extract_request_retries_once_after_timeout() -> None:
    class TimeoutThenSuccessClient:
        requests: list[Any] = []

        def create_chat_completion(self, request: Any) -> ChatCompletionResponse:
            self.__class__.requests.append(request)
            if len(self.__class__.requests) == 1:
                try:
                    raise httpx.ReadTimeout("read timed out")
                except httpx.HTTPError as exc:
                    raise LlmProviderError("LLM provider request failed.") from exc
            return ChatCompletionResponse(
                content='{"entities":[],"events":[],"facts":[],"relations":[],"warnings":[]}',
                model=request.model,
            )

    response = service.request_case_extraction(
        TimeoutThenSuccessClient(),
        LlmRuntimeConfig(
            provider="openai-compatible",
            base_url="https://llm.example/v1",
            api_key="secret-1234",
            model="chat-test",
            extra_body={},
            timeout_seconds=45,
        ),
        {
            "blueprint_key": "claims_notes",
            "domain_description": "Domain",
            "case_grain_description": "Case",
            "language": "pl",
            "information_types": [],
            "global_rules": [],
            "json_schema": {"type": "object"},
        },
        {"case_id": "case-8"},
        [
            {
                "chunk_id": "chunk-1",
                "source_table": "email",
                "text": "x" * 50_000,
            }
        ],
    )

    first_prompt = TimeoutThenSuccessClient.requests[0].messages[1].content
    second_prompt = TimeoutThenSuccessClient.requests[1].messages[1].content
    assert response == '{"entities":[],"events":[],"facts":[],"relations":[],"warnings":[]}'
    assert len(TimeoutThenSuccessClient.requests) == 2
    assert len(second_prompt) < len(first_prompt)
    assert len(second_prompt) <= service.RETRY_EXTRACTION_PROMPT_CHAR_BUDGET


def test_extract_request_limits_prompt_context_before_llm_call() -> None:
    class CapturingClient:
        request: Any | None = None

        def create_chat_completion(self, request: Any) -> ChatCompletionResponse:
            self.__class__.request = request
            return ChatCompletionResponse(
                content='{"entities":[],"events":[],"facts":[],"relations":[],"warnings":[]}',
                model=request.model,
            )

    chunks = [
        {
            "chunk_id": f"chunk-{index}",
            "source_table": "email",
            "text": f"chunk {index} " + ("x" * 20_000),
        }
        for index in range(1, 8)
    ]

    response = service.request_case_extraction(
        CapturingClient(),
        LlmRuntimeConfig(
            provider="openai-compatible",
            base_url="https://llm.example/v1",
            api_key="secret-1234",
            model="chat-test",
            extra_body={},
            timeout_seconds=45,
        ),
        {
            "blueprint_key": "claims_notes",
            "domain_description": "Domain",
            "case_grain_description": "Case",
            "language": "pl",
            "information_types": [
                {
                    "key": "case_status",
                    "kind": "fact",
                    "description": "Status.",
                    "fields": [
                        {
                            "name": "status",
                            "type": "string",
                            "required": False,
                            "values": [],
                        }
                    ],
                }
            ],
            "global_rules": [],
            "json_schema": {"type": "object"},
        },
        {"case_id": "case-17"},
        chunks,
    )

    assert response == '{"entities":[],"events":[],"facts":[],"relations":[],"warnings":[]}'
    assert CapturingClient.request is not None
    prompt = CapturingClient.request.messages[1].content
    assert len(prompt) <= service.DEFAULT_EXTRACTION_PROMPT_CHAR_BUDGET
    assert '"chunk_id": "chunk-7"' not in prompt


def test_extract_source_content_query_uses_datasource_dialect_quoting() -> None:
    quote = mysql.dialect().identifier_preparer.quote

    sql = service.build_source_content_query(
        quote,
        table_name="email",
        case_id_column="lead_id",
        content_column="content",
    )
    reserved_sql = service.build_source_content_query(
        quote,
        table_name="order",
        case_id_column="select",
        content_column="content",
    )

    assert sql == (
        "SELECT lead_id AS case_id, content AS content "
        "FROM email WHERE content IS NOT NULL"
    )
    assert '"email"' not in sql
    assert "FROM `order`" in reserved_sql
    assert "SELECT `select` AS case_id" in reserved_sql


def test_source_model_validation_rejects_missing_schema() -> None:
    client = make_client(FakeDatasourceService(schema=None))

    response = client.put(
        "/source-models/1",
        json={
            "main_table": "case_notes",
            "table_roles": {
                "case_notes": {
                    "case_id_column": "case_id",
                    "content_column": "note_text",
                }
            },
        },
    )

    assert response.status_code == 400
    assert "has no schema cache" in response.json()["detail"]


def test_source_model_validation_rejects_unknown_table_or_column() -> None:
    client = make_client()

    unknown_table = client.put(
        "/source-models/1",
        json={
            "main_table": "missing",
            "table_roles": {
                "case_notes": {
                    "case_id_column": "case_id",
                    "content_column": "note_text",
                }
            },
        },
    )
    unknown_column = client.put(
        "/source-models/1",
        json={
            "main_table": "case_notes",
            "table_roles": {
                "case_notes": {
                    "case_id_column": "missing",
                    "content_column": "note_text",
                }
            },
        },
    )

    assert unknown_table.status_code == 400
    assert "Table 'missing'" in unknown_table.json()["detail"]
    assert unknown_column.status_code == 400
    assert "Column 'missing'" in unknown_column.json()["detail"]


def test_source_model_validation_rejects_same_case_id_and_content_column() -> None:
    client = make_client()

    response = client.put(
        "/source-models/1",
        json={
            "main_table": "case_notes",
            "table_roles": {
                "case_notes": {
                    "case_id_column": "case_id",
                    "content_column": "case_id",
                }
            },
        },
    )

    assert response.status_code == 400
    assert "different columns" in response.json()["detail"]


def test_source_model_validation_rejects_content_without_case_id() -> None:
    client = make_client()

    response = client.put(
        "/source-models/1",
        json={
            "main_table": "case_notes",
            "table_roles": {
                "case_notes": {
                    "case_id_column": "case_id",
                    "content_column": "",
                },
                "case_comments": {
                    "content_column": "comment_text",
                },
            },
        },
    )

    assert response.status_code == 400
    assert "content but no case_id" in response.json()["detail"]


def test_source_model_validation_rejects_system_datasource() -> None:
    client = make_client()

    response = client.get("/datasources/2/schema")

    assert response.status_code == 400
    assert "metadata datasource" in response.json()["detail"]


def test_database_initializer_migrates_legacy_single_table_roles() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE extract_unstructured_source_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                datasource_connector_id INTEGER NOT NULL,
                datasource_connector_key VARCHAR(255) NOT NULL,
                main_table VARCHAR(255) NOT NULL,
                case_id_column VARCHAR(255) NOT NULL,
                content_column VARCHAR(255) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_by VARCHAR(255) NOT NULL DEFAULT 'system',
                CONSTRAINT uq_extract_unstructured_source_models_datasource
                    UNIQUE (datasource_connector_id)
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO extract_unstructured_source_models (
                datasource_connector_id,
                datasource_connector_key,
                main_table,
                case_id_column,
                content_column,
                updated_by
            )
            VALUES (1, 'notes-db', 'case_notes', 'case_id', 'note_text', 'tester')
            """
        )

    init_database(session_factory)

    with session_factory() as session:
        item = client_item_from_session(session)
        assert item["table_roles"] == {
            "case_notes": {
                "case_id_column": "case_id",
                "content_column": "note_text",
            }
        }


def test_database_initializer_migrates_legacy_blueprints_table() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE extract_blueprints (
                id VARCHAR(64) NOT NULL,
                blueprint_key VARCHAR(255) NOT NULL,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                domain_description TEXT,
                case_grain_description TEXT,
                language VARCHAR(20) NOT NULL DEFAULT 'pl',
                status VARCHAR(50) NOT NULL DEFAULT 'draft',
                config_json TEXT NOT NULL,
                json_schema TEXT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE (blueprint_key)
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO extract_blueprints (
                id,
                blueprint_key,
                name,
                domain_description,
                case_grain_description,
                config_json,
                json_schema
            )
            VALUES (
                'bp-1',
                'legacy_notes',
                'Legacy Notes',
                'Legacy domain',
                'Legacy case grain',
                '{"information_types":[],"global_rules":[],"review_policy":{},"extraction_scope":{}}',
                '{"type":"object"}'
            )
            """
        )

    init_database(session_factory)

    column_names = {
        column["name"]
        for column in inspect(engine).get_columns("extract_blueprints")
    }
    assert "json_schema_json" in column_names
    assert "updated_by" in column_names

    app = FastAPI()
    app.include_router(create_router(session_factory, FakeDatasourceService()))
    response = TestClient(app).get("/llm-extracting-config")

    assert response.status_code == 200
    assert response.json()["item"]["json_schema"] == {"type": "object"}
    assert response.json()["item"]["updated_by"] == "system"


def client_item_from_session(session: Any) -> dict[str, Any]:
    from gaard_extract import service

    item = service.get_source_model(session, 1)
    assert item is not None
    return item


def configure_extract_job_fixture(
    client: TestClient,
    session_factory: sessionmaker,
    tmp_path: Path,
) -> None:
    source_db_path = tmp_path / "source.db"
    create_source_sqlite(source_db_path)
    configure_source_datasource(session_factory, source_db_path)
    configure_llm_runtime(session_factory)
    configure_source_model(client)
    response = client.put("/llm-extracting-config", json=llm_payload())
    assert response.status_code == 200


def create_source_sqlite(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE case_notes (
                case_id TEXT NOT NULL,
                note_text TEXT,
                created_at TEXT
            );
            CREATE TABLE case_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_ref TEXT NOT NULL,
                comment_text TEXT
            );
            INSERT INTO case_notes (case_id, note_text, created_at)
            VALUES
                ('case-1', 'Status: open. Customer asked for review.', '2026-01-01'),
                ('case-2', 'Status: open. Waiting for documents.', '2026-01-02');
            INSERT INTO case_comments (case_ref, comment_text)
            VALUES
                ('case-1', 'Additional note for case one.'),
                ('case-2', 'Additional note for case two.');
            """
        )
        connection.commit()
    finally:
        connection.close()


def configure_source_datasource(session_factory: sessionmaker, db_path: Path) -> None:
    with session_factory() as session:
        connector = session.get(DatasourceConnector, 1)
        if connector is None:
            connector = DatasourceConnector(id=1)
            session.add(connector)
        connector.connector_key = "notes-db"
        connector.name = "Notes DB"
        connector.database_type = "sqlite"
        connector.database_url = f"sqlite:///{db_path.as_posix()}"
        connector.sql_dialect = "sqlite"
        connector.active = True
        connector.updated_by = "tester"
        session.commit()


def configure_llm_runtime(
    session_factory: sessionmaker,
    *,
    api_key: str = "secret-1234",
) -> None:
    with session_factory() as session:
        set_setting(session, "gaard_llm_provider", "openai-compatible", "test")
        set_setting(session, "gaard_llm_base_url", "https://llm.example/v1", "test")
        set_setting(session, "gaard_llm_api_key", api_key, "test")
        set_setting(session, "gaard_llm_model", "chat-test", "test")
        set_setting(session, "gaard_llm_timeout_seconds", "45", "test")
        set_setting(session, "gaard_llm_extra_body", "{}", "test")
        session.commit()


def configure_source_model(client: TestClient) -> None:
    response = client.put(
        "/source-models/1",
        json={
            "main_table": "case_notes",
            "table_roles": {
                "case_notes": {
                    "case_id_column": "case_id",
                    "content_column": "note_text",
                },
                "case_comments": {
                    "case_id_column": "case_ref",
                    "content_column": "comment_text",
                },
            },
        },
    )
    assert response.status_code == 200


def configure_embeddings(client: TestClient, *, enabled: bool) -> None:
    response = client.put(
        "/embedding-config",
        json={
            "enabled": enabled,
            "provider": "openai-compatible",
            "base_url": "https://embeddings.example/v1",
            "api_key": "secret-1234",
            "model": "embed-small",
            "timeout_seconds": 45,
            "extra_body": {},
        },
    )
    assert response.status_code == 200


def llm_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "blueprint_key": "claims_notes",
        "name": "Claims Notes",
        "description": "",
        "domain_description": "Notatki reklamacyjne klientów.",
        "case_grain_description": "Jeden case to jedna reklamacja klienta.",
        "language": "pl",
        "status": "draft",
        "information_types": [
            {
                "key": "case_status",
                "kind": "fact",
                "description": "Status sprawy.",
                "fields": [
                    {
                        "name": "status",
                        "type": "string",
                        "required": True,
                        "values": [],
                    }
                ],
            }
        ],
        "global_rules": ["Nie zgaduj."],
        "review_policy": {
            "auto_approve_threshold": 0.9,
            "needs_review_threshold": 0.6,
            "reject_below_threshold": 0.3,
        },
        "extraction_scope": {
            "source_mode": "active_source_model",
            "content_tables": ["case_notes"],
            "chunk_selection": "all_chunks",
            "use_embeddings": False,
        },
    }
    payload.update(overrides)
    return payload
