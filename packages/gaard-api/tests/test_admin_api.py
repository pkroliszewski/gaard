from collections.abc import Iterator
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gaard_core.errors import QueryPipelineStepError
from gaard_core.query_pipeline.mock_sql_generator import MockSqlGenerator
from gaard_core.query_pipeline.models import (
    GeneratedSql,
    OutputClassification,
    QueryIntentClassification,
    QueryIntentDecision,
    QueryRequest,
)
from gaard_llm.providers.models import ChatCompletionResponse

from gaard_api.admin.database import create_session, reset_metadata_store_for_tests
from gaard_api.admin.models import (
    AdminSetting,
    BusinessKnowledgeClaim,
    DataQueryAuditLog,
    DataQueryAuditType,
    DatasourceConnector,
    DatasourceSchemaCache,
    PromptTemplate,
)
from gaard_api.admin.services import (
    get_active_business_logic_prompt_safe,
    get_governance_policy_for_schema,
    get_llm_runtime_config_safe,
    get_query_runtime_config,
    list_business_logic_suggestions,
    record_candidate_business_knowledge,
    set_setting,
)
from gaard_api.core.settings import settings
from gaard_api.example_database import install_medical_poc_example_database
from gaard_api.main import app


@pytest.fixture()
def admin_client(tmp_path: Path, monkeypatch) -> Iterator[TestClient]:
    demo_db = tmp_path / "demo.db"

    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    monkeypatch.setattr(settings, "gaard_datasource_url", f"sqlite:///{demo_db}")
    monkeypatch.setattr(settings, "gaard_sql_generation_mode", "mock")
    monkeypatch.setattr(settings, "gaard_result_interpretation_mode", "mock")
    monkeypatch.setattr(settings, "gaard_llm_api_key", "change-me")
    reset_metadata_store_for_tests()
    install_medical_poc_example_database(demo_db)

    with TestClient(app) as client:
        yield client

    reset_metadata_store_for_tests()


def login(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/admin/auth/login",
        json={
            "username": "admin",
            "password": "admin",
        },
    )

    assert response.status_code == 200
    return response.json()


def change_password(client: TestClient, token: str) -> None:
    response = client.post(
        "/api/v1/admin/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "admin",
            "new_password": "new-admin-password",
        },
    )

    assert response.status_code == 200
    assert response.json()["must_change_password"] is False


def test_system_seeded_mock_runtime_modes_are_migrated_to_current_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    monkeypatch.setattr(settings, "gaard_sql_generation_mode", "mock")
    monkeypatch.setattr(settings, "gaard_result_interpretation_mode", "mock")
    reset_metadata_store_for_tests()

    with create_session() as session:
        query_config = get_query_runtime_config(session)
        assert query_config.sql_generation_mode == "mock"
        assert query_config.result_interpretation_mode == "mock"
        assert query_config.investigation_mode == "llm"

    monkeypatch.setattr(settings, "gaard_sql_generation_mode", "llm")
    monkeypatch.setattr(settings, "gaard_result_interpretation_mode", "llm")
    reset_metadata_store_for_tests()

    with create_session() as session:
        query_config = get_query_runtime_config(session)
        assert query_config.sql_generation_mode == "llm"
        assert query_config.result_interpretation_mode == "llm"
        assert query_config.investigation_mode == "llm"
        assert session.get(AdminSetting, "gaard_sql_generation_mode").updated_by == "system"

    reset_metadata_store_for_tests()


def test_only_investigation_readiness_prompt_is_seeded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    reset_metadata_store_for_tests()

    with create_session() as session:
        investigation_prompts = session.scalars(
            select(PromptTemplate).where(
                PromptTemplate.prompt_key.like("investigation_%")
            )
        ).all()
        assert [prompt.prompt_key for prompt in investigation_prompts] == [
            "investigation_readiness"
        ]
        assert "Assume nothing. Verify continuously." in (
            investigation_prompts[0].system_prompt
        )

    reset_metadata_store_for_tests()


def test_empty_metadata_seed_does_not_create_medical_poc_datasource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    reset_metadata_store_for_tests()

    with create_session() as session:
        connectors = session.scalars(select(DatasourceConnector)).all()

    assert [connector.connector_key for connector in connectors] == ["metadata-db"]
    assert all(connector.active is False for connector in connectors)

    reset_metadata_store_for_tests()


def test_candidate_business_knowledge_can_be_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "gaard_metadata_database_url",
        f"sqlite:///{tmp_path / 'metadata.db'}",
    )
    reset_metadata_store_for_tests()
    install_medical_poc_example_database(tmp_path / "demo.db")

    with create_session() as session:
        connector = session.scalar(
            select(DatasourceConnector).where(
                DatasourceConnector.connector_key == "default"
            )
        )
        assert connector is not None
        connector_id = connector.id

    claim_ids = record_candidate_business_knowledge(
        connector_id,
        [
            {
                "knowledge_type": "term_mapping",
                "claim": "kardiolog maps to doctors.specialization=cardiology",
                "datasource_id": "medical_poc",
                "tables": ["doctors"],
                "columns": ["doctors.specialization"],
                "values": ["cardiology"],
                "evidence": [{"sql": "SELECT specialization FROM doctors"}],
                "confidence": 0.92,
                "request_id": "req_test",
            }
        ],
    )

    with create_session() as session:
        claim = session.get(BusinessKnowledgeClaim, claim_ids[0])
        assert claim is not None
        assert claim.status == "candidate"
        assert claim.knowledge_type == "term_mapping"
        assert claim.confidence == 0.92
        assert "cardiology" in claim.subject_json

    reset_metadata_store_for_tests()


def stub_business_logic_learning_llm(monkeypatch, payload: dict) -> type:
    monkeypatch.setattr(settings, "gaard_llm_api_key", "test-key")
    monkeypatch.setattr(settings, "gaard_llm_model", "lesson-model")
    with create_session() as session:
        set_setting(session, "gaard_llm_api_key", "test-key", "test")
        set_setting(session, "gaard_llm_model", "lesson-model", "test")
        session.commit()

    class FakeOpenAICompatibleClient:
        requests = []

        def __init__(self, *args, **kwargs) -> None:
            pass

        def create_chat_completion(self, request):
            self.__class__.requests.append(request)
            return ChatCompletionResponse(content=json.dumps(payload))

    monkeypatch.setattr(
        "gaard_api.admin.services.OpenAICompatibleClient",
        FakeOpenAICompatibleClient,
    )

    return FakeOpenAICompatibleClient


def test_admin_first_login_requires_password_change(admin_client: TestClient) -> None:
    login_response = login(admin_client)
    token = login_response["token"]

    assert login_response["must_change_password"] is True

    blocked_response = admin_client.get(
        "/api/v1/admin/prompts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert blocked_response.status_code == 403

    change_password(admin_client, token)

    prompts_response = admin_client.get(
        "/api/v1/admin/prompts",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert prompts_response.status_code == 200
    assert len(prompts_response.json()["items"]) >= 2


def test_prompt_update_creates_admin_audit_event(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    prompts_response = admin_client.get(
        "/api/v1/admin/prompts",
        headers={"Authorization": f"Bearer {token}"},
    )
    prompt = prompts_response.json()["items"][0]

    update_response = admin_client.put(
        f"/api/v1/admin/prompts/{prompt['prompt_key']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": prompt["name"],
            "description": "Updated from test.",
            "system_prompt": prompt["system_prompt"],
            "user_prompt_template": prompt["user_prompt_template"],
            "active": True,
        },
    )

    assert update_response.status_code == 200
    assert update_response.json()["item"]["version"] == prompt["version"] + 1

    audit_response = admin_client.get(
        "/api/v1/admin/audit/admin-events",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    assert any(
        item["action"] == "prompt.update"
        for item in audit_response.json()["items"]
    )


def test_llm_config_defaults_to_metadata_and_can_be_overridden(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    get_response = admin_client.get(
        "/api/v1/admin/llm-config",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert get_response.status_code == 200
    item = get_response.json()["item"]
    assert item["provider"] == "openai-compatible"
    assert item["base_url"] == "https://api.openai.com/v1"
    assert "api_key" not in item
    assert item["api_key_configured"] is False
    assert item["api_key_preview"] is None
    assert item["model"] == "gpt-4.1-mini"
    assert item["timeout_seconds"] == 60
    assert item["extra_body"] == {}
    assert item["sql_generation_mode"] == "mock"
    assert item["result_interpretation_mode"] == "mock"
    assert item["intent_classification_mode"] == "auto"
    assert item["output_classification_mode"] == "auto"
    assert item["investigation_mode"] == "llm"
    assert item["query_max_rows"] == 100
    assert set(item["sources"].values()) == {"metadata"}

    update_response = admin_client.put(
        "/api/v1/admin/llm-config",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "http://metadata-llm.test/v1",
            "api_key": "metadata-secret",
            "model": "metadata-model",
            "timeout_seconds": 45,
            "intent_classification_mode": "llm",
            "sql_generation_mode": "llm",
            "result_interpretation_mode": "llm",
            "output_classification_mode": "llm",
            "investigation_mode": "llm",
            "query_max_rows": 250,
            "query_timeout_seconds": 20,
            "extra_body": {"temperature": 0, "chat_template_kwargs": {"enable_thinking": True}},
        },
    )

    assert update_response.status_code == 200
    updated_item = update_response.json()["item"]
    assert updated_item["base_url"] == "http://metadata-llm.test/v1"
    assert "api_key" not in updated_item
    assert updated_item["api_key_configured"] is True
    assert updated_item["api_key_preview"] == "****cret"
    assert updated_item["model"] == "metadata-model"
    assert updated_item["timeout_seconds"] == 45
    assert updated_item["intent_classification_mode"] == "llm"
    assert updated_item["sql_generation_mode"] == "llm"
    assert updated_item["result_interpretation_mode"] == "llm"
    assert updated_item["output_classification_mode"] == "llm"
    assert updated_item["investigation_mode"] == "llm"
    assert updated_item["query_max_rows"] == 250
    assert updated_item["query_timeout_seconds"] == 20
    assert updated_item["extra_body"] == {
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    assert set(updated_item["sources"].values()) == {"metadata"}

    runtime_config = get_llm_runtime_config_safe()
    assert runtime_config.base_url == "http://metadata-llm.test/v1"
    assert runtime_config.api_key == "metadata-secret"
    assert runtime_config.model == "metadata-model"
    assert runtime_config.timeout_seconds == 45
    assert runtime_config.extra_body == {
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": True},
    }

    audit_response = admin_client.get(
        "/api/v1/admin/audit/admin-events",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    audit_item = next(
        item for item in audit_response.json()["items"] if item["action"] == "llm_config.update"
    )
    assert audit_item["details"]["model"] == "metadata-model"
    assert "api_key" not in audit_item["details"]

    preserve_response = admin_client.put(
        "/api/v1/admin/llm-config",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "http://metadata-llm-2.test/v1",
            "api_key": "",
            "model": "metadata-model-2",
            "timeout_seconds": 30,
            "extra_body": {},
        },
    )

    assert preserve_response.status_code == 200
    preserved_item = preserve_response.json()["item"]
    assert "api_key" not in preserved_item
    assert preserved_item["api_key_configured"] is True
    assert preserved_item["api_key_preview"] == "****cret"

    runtime_config = get_llm_runtime_config_safe()
    assert runtime_config.base_url == "http://metadata-llm-2.test/v1"
    assert runtime_config.api_key == "metadata-secret"
    assert runtime_config.model == "metadata-model-2"

    clear_response = admin_client.put(
        "/api/v1/admin/llm-config",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "openai-compatible",
            "base_url": "http://metadata-llm-3.test/v1",
            "clear_api_key": True,
            "model": "metadata-model-3",
            "timeout_seconds": 30,
            "extra_body": {},
        },
    )

    assert clear_response.status_code == 200
    cleared_item = clear_response.json()["item"]
    assert "api_key" not in cleared_item
    assert cleared_item["api_key_configured"] is False
    assert cleared_item["api_key_preview"] is None

    runtime_config = get_llm_runtime_config_safe()
    assert runtime_config.base_url == "http://metadata-llm-3.test/v1"
    assert runtime_config.api_key == "change-me"
    assert runtime_config.model == "metadata-model-3"


def test_governance_policy_defaults_to_metadata_and_can_be_overridden(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    get_response = admin_client.get(
        "/api/v1/admin/governance-policy",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert get_response.status_code == 200
    item = get_response.json()["item"]
    assert item["final_answer"] == {
        "record_level_pii_allowed": False,
        "prefer_aggregates_for_sensitive_domains": True,
    }
    assert item["sql"] == {
        "read_only": True,
        "select_star_allowed": False,
        "tenant_filter_required": False,
        "tenant_column": None,
    }
    assert item["privacy"] == {
        "forbidden_columns": {},
        "record_level_forbidden": False,
    }
    assert item["pii_column_names"]["identity"] == [
        "first_name",
        "last_name",
        "full_name",
    ]
    assert item["sources"]["governance_policy"] == "metadata"

    update_response = admin_client.put(
        "/api/v1/admin/governance-policy",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "final_answer": {
                "record_level_pii_allowed": True,
                "prefer_aggregates_for_sensitive_domains": False,
            },
            "sql": {
                "read_only": True,
                "select_star_allowed": True,
                "tenant_filter_required": True,
                "tenant_column": "tenant_id",
            },
            "privacy": {
                "forbidden_columns": {"employees": ["salary"]},
                "record_level_forbidden": True,
            },
            "pii_column_names": {"custom": ["employee_code"]},
        },
    )

    assert update_response.status_code == 200
    updated_item = update_response.json()["item"]
    assert updated_item["final_answer"]["record_level_pii_allowed"] is True
    assert updated_item["final_answer"]["prefer_aggregates_for_sensitive_domains"] is False
    assert updated_item["sql"]["select_star_allowed"] is True
    assert updated_item["sql"]["tenant_filter_required"] is True
    assert updated_item["sql"]["tenant_column"] == "tenant_id"
    assert updated_item["privacy"]["record_level_forbidden"] is True
    assert updated_item["privacy"]["forbidden_columns"] == {"employees": ["salary"]}
    assert updated_item["pii_column_names"] == {"custom": ["employee_code"]}

    audit_response = admin_client.get(
        "/api/v1/admin/audit/admin-events",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    audit_item = next(
        item
        for item in audit_response.json()["items"]
        if item["action"] == "governance_policy.update"
    )
    assert audit_item["details"]["pii_column_names"] == {"custom": ["employee_code"]}


def test_governance_policy_infers_pii_columns_from_metadata_dictionary(
    admin_client: TestClient,
) -> None:
    login_response = login(admin_client)
    change_password(admin_client, login_response["token"])
    schema_summary = {
        "tables": {
            "employees": {
                "columns": {
                    "full_name": {"type": "TEXT"},
                    "employee_code": {"type": "TEXT"},
                    "department": {"type": "TEXT"},
                }
            }
        }
    }

    with create_session() as session:
        default_policy = get_governance_policy_for_schema(session, schema_summary)
        assert default_policy["privacy"]["forbidden_columns"] == {
            "employees": ["full_name"]
        }

    update_response = admin_client.put(
        "/api/v1/admin/governance-policy",
        headers={"Authorization": f"Bearer {login_response['token']}"},
        json={
            "final_answer": {
                "record_level_pii_allowed": False,
                "prefer_aggregates_for_sensitive_domains": True,
            },
            "sql": {
                "read_only": True,
                "select_star_allowed": False,
                "tenant_filter_required": False,
                "tenant_column": None,
            },
            "privacy": {
                "forbidden_columns": {"employees": ["department"]},
                "record_level_forbidden": False,
            },
            "pii_column_names": {"internal": ["employee_code"]},
        },
    )
    assert update_response.status_code == 200

    with create_session() as session:
        updated_policy = get_governance_policy_for_schema(session, schema_summary)

    assert updated_policy["privacy"]["forbidden_columns"] == {
        "employees": ["department", "employee_code"]
    }


def test_overview_returns_metadata_backed_widgets(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    response = admin_client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()

    assert any(
        item["connector_key"] == "metadata-db"
        for item in body["datasources"]
    )
    assert [item["widget_key"] for item in body["info_widgets"]] == [
        "prompts_count",
        "audit_retention",
        "schema_cache_ttl",
        "license_edition",
    ]
    assert body["runtime_widget"] is None
    assert [item["widget_key"] for item in body["table_widgets"]] == [
        "prompt_templates_table",
    ]
    assert body["info_widgets"][0]["grid_width"] == 1
    assert body["table_widgets"][0]["grid_width"] == 4
    assert body["table_widgets"][0]["result"]["status"] == "ok"
    assert body["table_widgets"][0]["result"]["columns"] == [
        "prompt_key",
        "name",
        "version",
        "active",
    ]
    assert len(body["table_widgets"][0]["result"]["rows"]) >= 1
    assert body["info_widgets"][0]["result"]["status"] == "ok"
    assert body["info_widgets"][0]["sql"] == "SELECT COUNT(*) AS value FROM prompt_templates"

    widgets_response = admin_client.get(
        "/api/v1/admin/overview/widgets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert widgets_response.status_code == 200
    widgets = widgets_response.json()["items"]
    runtime_widget = next(
        item for item in widgets if item["widget_key"] == "runtime_daily_queries"
    )
    assert runtime_widget["active"] is False
    assert runtime_widget["grid_width"] == 4

    second_response = admin_client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second_response.status_code == 200

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert audit_response.status_code == 200
    assert audit_response.json()["items"] == []


def test_overview_widget_can_be_updated(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    update_response = admin_client.put(
        "/api/v1/admin/overview/widgets/prompts_count",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "label": "Prompt templates",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "Return one value for the prompt template count.",
        },
    )

    assert update_response.status_code == 200
    assert update_response.json()["item"]["label"] == "Prompt templates"
    assert update_response.json()["item"]["sql"] == "SELECT 1 AS value"

    overview_response = admin_client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert overview_response.status_code == 200
    overview = overview_response.json()
    widget = next(
        item
        for item in overview["info_widgets"]
        if item["widget_key"] == "prompts_count"
    )
    assert widget["label"] == "Prompt templates"
    assert widget["question"] == "Return one value for the prompt template count."

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    audit_items = audit_response.json()["items"]
    assert len(audit_items) == 1
    assert audit_items[0]["audit_type"] == "info"
    assert audit_items[0]["output_classification"] == "unknown"
    assert audit_items[0]["user_id"] == "overview-widget-config:prompts_count"
    assert audit_items[0]["datasource_id"] == "metadata-db"
    assert audit_items[0]["question"] == "Return one value for the prompt template count."
    assert audit_items[0]["sql"] == "SELECT 1 AS value"
    assert audit_items[0]["metadata"]["operation"] == "overview_widget.update"
    assert audit_items[0]["metadata"]["widget_key"] == "prompts_count"


def test_overview_widget_can_use_table_type(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    update_response = admin_client.put(
        "/api/v1/admin/overview/widgets/prompt_templates_table",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "label": "Prompt template rows",
            "widget_type": "table",
            "datasource_key": "metadata-db",
            "question": "Return rows for prompt templates.",
            "grid_width": 3,
        },
    )

    assert update_response.status_code == 200
    item = update_response.json()["item"]
    assert item["widget_type"] == "table"
    assert item["grid_width"] == 3
    assert item["result"]["status"] == "ok"
    assert item["result"]["columns"] == ["value"]
    assert item["result"]["rows"] == [{"value": 1}]


def test_overview_widget_can_be_saved_from_query_and_deleted(
    admin_client: TestClient,
) -> None:
    create_response = admin_client.post(
        "/api/v1/admin/overview/widgets/from-query",
        json={
            "label": "Prompt count from client",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "How many prompts are configured?",
            "sql": "SELECT COUNT(*) AS value FROM prompt_templates",
        },
    )

    assert create_response.status_code == 200
    created_item = create_response.json()["item"]
    widget_key = created_item["widget_key"]
    assert widget_key.startswith("client_prompt_count_from_client")
    assert created_item["active"] is False
    assert created_item["result_mode"] == "data"

    token = login(admin_client)["token"]
    change_password(admin_client, token)

    overview_response = admin_client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert overview_response.status_code == 200
    assert all(
        item["widget_key"] != widget_key
        for item in overview_response.json()["widgets"]
    )

    widgets_response = admin_client.get(
        "/api/v1/admin/overview/widgets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert widgets_response.status_code == 200
    assert any(
        item["widget_key"] == widget_key
        for item in widgets_response.json()["items"]
    )

    delete_response = admin_client.delete(
        f"/api/v1/admin/overview/widgets/{widget_key}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"

    widgets_after_delete = admin_client.get(
        "/api/v1/admin/overview/widgets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert widgets_after_delete.status_code == 200
    assert all(
        item["widget_key"] != widget_key
        for item in widgets_after_delete.json()["items"]
    )


def test_overview_widget_can_return_interpreted_result(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    update_response = admin_client.put(
        "/api/v1/admin/overview/widgets/prompts_count",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "label": "Prompt templates",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "Return one value for the prompt template count.",
            "result_mode": "interpretation",
        },
    )

    assert update_response.status_code == 200
    item = update_response.json()["item"]
    assert item["result_mode"] == "interpretation"
    assert item["result"]["result_mode"] == "interpretation"
    assert item["result"]["answer"].startswith("Zapytanie zwróciło wynik:")
    assert item["result"]["value"] == item["result"]["answer"]


def test_overview_widget_sql_error_creates_metadata_business_logic_suggestion(
    admin_client: TestClient,
    monkeypatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)
    stub_business_logic_learning_llm(
        monkeypatch,
        {
            "create_suggestion": True,
            "error_category": "schema.missing_table",
            "title": "Use metadata prompt table",
            "rule_text": (
                "When counting configured prompts in the metadata datasource, use "
                "`prompt_templates` rather than nonexistent table `prompts`."
            ),
            "failed_identifier": "prompts",
            "repaired_identifier": "prompt_templates",
            "confidence": 0.88,
            "terms": ["prompts", "prompt_templates"],
            "join_hints": [],
            "skip_reason": "",
        },
    )

    def generate_bad_sql(self, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(
            sql="SELECT COUNT(*) AS value FROM prompts",
            confidence=0.6,
            assumptions=["Intentional missing metadata table for overview learning test."],
        )

    monkeypatch.setattr(MockSqlGenerator, "generate", generate_bad_sql)

    update_response = admin_client.put(
        "/api/v1/admin/overview/widgets/prompts_count",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "label": "Prompt templates",
            "widget_type": "scalar",
            "datasource_key": "metadata-db",
            "question": "How many prompts are configured?",
        },
    )

    assert update_response.status_code == 400

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    audit_item = audit_response.json()["items"][0]
    assert audit_item["output_classification"] == "unknown"
    assert audit_item["user_id"] == "overview-widget-config:prompts_count"
    assert audit_item["datasource_id"] == "metadata-db"
    assert audit_item["metadata"]["error_category"] == "schema.missing_table"
    assert audit_item["metadata"]["failed_identifier"] == "prompts"
    assert audit_item["metadata"]["business_logic_learning"]["suggestion_id"]

    visible_suggestions_response = admin_client.get(
        "/api/v1/admin/business-logic-suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert visible_suggestions_response.status_code == 200
    assert visible_suggestions_response.json()["datasource"]["connector_key"] == "default"
    assert visible_suggestions_response.json()["items"] == []

    with create_session() as session:
        metadata_connector = session.scalar(
            select(DatasourceConnector).where(
                DatasourceConnector.connector_key == "metadata-db"
            )
        )
        assert metadata_connector is not None
        suggestions = list_business_logic_suggestions(session, metadata_connector.id)

    assert len(suggestions) == 1
    assert suggestions[0].failed_identifier == "prompts"
    assert suggestions[0].repaired_identifier == "prompt_templates"


def test_default_datasource_can_be_tested_and_introspected(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    datasources_response = admin_client.get(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert datasources_response.status_code == 200
    items = datasources_response.json()["items"]
    datasource = items[0]
    metadata_datasource = next(item for item in items if item["connector_key"] == "metadata-db")
    assert datasource["connector_key"] == "default"
    assert datasource["database_type"] == "sqlite"
    assert metadata_datasource["system_managed"] is True
    assert metadata_datasource["active"] is False

    test_response = admin_client.post(
        f"/api/v1/admin/datasources/{datasource['id']}/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert test_response.status_code == 200
    assert test_response.json()["status"] == "ok"

    introspect_response = admin_client.post(
        f"/api/v1/admin/datasources/{datasource['id']}/introspect",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert introspect_response.status_code == 200
    schema = introspect_response.json()["item"]["raw_schema"]
    assert {table["name"] for table in schema["tables"]} >= {
        "patients",
        "appointments",
        "doctors",
    }


def test_datasource_connector_accepts_postgres_sql_dialect(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "pg_db",
            "name": "Postgres DB",
            "database_type": "postgresql",
            "database_url": "postgresql://user:pass@example.test/app",
            "sql_dialect": "postgres",
            "active": False,
        },
    )

    assert create_response.status_code == 200
    item = create_response.json()["item"]
    assert item["database_type"] == "postgresql"
    assert item["sql_dialect"] == "postgres"


def test_sql_generation_prompt_uses_active_datasource_dialect(
    admin_client: TestClient,
) -> None:
    with create_session() as session:
        for connector in session.scalars(select(DatasourceConnector)):
            connector.active = False

        connector = DatasourceConnector(
            connector_key="mysql_leads",
            name="MySQL leads",
            database_type="mysql",
            database_url="mysql://user:pass@example.test/leads",
            sql_dialect="mysql",
            active=True,
        )
        session.add(connector)
        session.flush()
        session.add(
            DatasourceSchemaCache(
                connector_id=connector.id,
                schema_json='{"tables":[]}',
                table_settings_json="{}",
                formatted_schema="Table: lead\nColumns:\n- id: INTEGER",
            )
        )
        session.commit()

    response = admin_client.post(
        "/api/v1/prompts/sql-generation",
        json={"question": "ile jest wpisów w tabeli lead"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["dialect"] == "mysql"
    assert "Table: lead" in body["user_prompt"]


def test_datasource_schema_table_settings_are_saved(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    datasource = admin_client.get(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["items"][0]

    admin_client.post(
        f"/api/v1/admin/datasources/{datasource['id']}/introspect",
        headers={"Authorization": f"Bearer {token}"},
    )

    update_response = admin_client.put(
        f"/api/v1/admin/datasources/{datasource['id']}/schema/tables",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "tables": {
                "patients": {
                    "selected": True,
                    "description": "People receiving care.",
                    "primary_key_prompt": "Use patients.id as the stable row identifier.",
                    "foreign_key_prompt": "",
                    "join_logic": "Join appointments.patient_id to patients.id.",
                },
                "appointments": {
                    "selected": False,
                    "description": "",
                    "primary_key_prompt": "",
                    "foreign_key_prompt": "",
                    "join_logic": "",
                },
                "doctors": {
                    "selected": True,
                    "description": "",
                    "primary_key_prompt": "",
                    "foreign_key_prompt": "",
                    "join_logic": "",
                },
            }
        },
    )

    assert update_response.status_code == 200
    item = update_response.json()["item"]
    assert "People receiving care." in item["formatted_schema"]
    assert "Join appointments.patient_id to patients.id." in item["formatted_schema"]
    assert "Table: appointments" not in item["formatted_schema"]


def test_query_endpoint_writes_data_query_audit(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    query_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 200

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert items[0]["audit_type"] == "info"
    assert items[0]["output_classification"] == "neutral_data"
    assert "audit_type" not in items[0]["metadata"]
    assert "output_classification" not in items[0]["metadata"]
    assert items[0]["user_id"] == "alice"
    assert items[0]["question"] == "How many active patients are there?"

    classification_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?output_classification=neutral_data",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert classification_response.status_code == 200
    assert len(classification_response.json()["items"]) == 1

    sql_match_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?sql_contains=COUNT",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sql_match_response.status_code == 200
    assert len(sql_match_response.json()["items"]) == 1

    sql_miss_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?sql_contains=missing_fragment",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sql_miss_response.status_code == 200
    assert sql_miss_response.json()["items"] == []

    invalid_classification_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?output_classification=surprising",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert invalid_classification_response.status_code == 400

    with create_session() as session:
        audit_log = session.scalar(select(DataQueryAuditLog))

    assert audit_log is not None
    assert audit_log.type == DataQueryAuditType.INFO
    assert audit_log.output_classification == OutputClassification.NEUTRAL_DATA


def test_query_endpoint_investigation_runs_normal_sql_when_ready(
    admin_client: TestClient,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    with create_session() as session:
        set_setting(session, "gaard_investigation_mode", "mock", "test")
        session.commit()

    query_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "pokaż wartość kontrolną",
            "user_id": "alice",
            "mode": "investigation",
        },
    )

    assert query_response.status_code == 200
    body = query_response.json()
    assert body["answer"] == "Zapytanie zwróciło wynik: {'value': 1}."
    assert body["sql"] == "SELECT 1 AS value"
    assert body["rows"] == [{"value": 1}]
    assert body["metadata"]["query_mode"] == "investigation"
    assert (
        body["metadata"]["investigation_backend_status"]
        == "readiness_gate_active"
    )
    assert body["metadata"]["investigation_route"] == "sql"
    assert body["metadata"]["investigation_steps"][0]["ready_for_sql"] is True

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert items[0]["question"] == "pokaż wartość kontrolną"
    assert items[0]["metadata"]["query_mode"] == "investigation"
    assert (
        items[0]["metadata"]["investigation_backend_status"]
        == "readiness_gate_active"
    )
    assert items[0]["metadata"]["investigation_route"] == "sql"


def test_query_endpoint_investigation_stops_at_analysis_when_not_ready(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    class NotReadyAgent:
        name = "test_not_ready_agent"

        def assess(self, context):
            from gaard_core.investigation.models import (
                InvestigationReadinessDecision,
                InvestigationRoute,
                RequiredAnalysisTask,
            )

            return InvestigationReadinessDecision(
                ready_for_sql=False,
                route=InvestigationRoute.ANALYSIS,
                confidence=0.92,
                reason="Dictionary value must be verified first.",
                missing_information=["specialization dictionary value"],
                required_analysis=["Inspect distinct doctors.specialization values."],
                required_analysis_tasks=[
                    RequiredAnalysisTask(
                        missing_information="specialization dictionary value",
                        required_analysis=(
                            "Inspect distinct doctors.specialization values."
                        ),
                        category="dictionary_value",
                        expected_output="Known specialization values.",
                    )
                ],
            )

    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_investigation_readiness_agent",
        lambda runtime_config: NotReadyAgent(),
    )

    query_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "który kardiolog leczy pacjentów",
            "user_id": "alice",
            "mode": "investigation",
        },
    )

    assert query_response.status_code == 200
    body = query_response.json()
    assert "Ścieżka Analysis nie jest jeszcze zaimplementowana" in body["answer"]
    assert body["sql"] == ""
    assert body["rows"] == []
    assert body["metadata"]["query_mode"] == "investigation"
    assert body["metadata"]["investigation_route"] == "analysis"
    assert body["metadata"]["analysis_mode_status"] == "not_implemented"
    assert body["metadata"]["analysis_tasks_count"] == 1
    assert body["metadata"]["investigation_steps"][0]["ready_for_sql"] is False
    assert body["metadata"]["investigation_steps"][0]["missing_information"] == [
        "specialization dictionary value"
    ]
    assert body["metadata"]["analysis_results"][0]["sql"] == "SELECT 1 AS value"
    assert (
        body["metadata"]["analysis_results"][0]["business_logic_learning"]["status"]
        == "created"
    )

    with create_session() as session:
        connector = session.scalar(
            select(DatasourceConnector).where(
                DatasourceConnector.connector_key == "default"
            )
        )
        assert connector is not None
        suggestions = list_business_logic_suggestions(session, connector.id)
        assert len(suggestions) == 1
        suggestion = suggestions[0]
        assert suggestion.status == "pending"
        assert suggestion.enabled is False
        assert suggestion.error_category == "investigation.analysis.dictionary_value"
        assert (
            suggestion.failed_identifier
            == "specialization dictionary value"
        )
        assert suggestion.repaired_identifier
        assert (
            "[dictionary_value] specialization dictionary value =>"
            in suggestion.rule_text
        )

        audit_logs = session.scalars(
            select(DataQueryAuditLog).order_by(DataQueryAuditLog.id)
        ).all()
        audit_steps = [
            json.loads(log.metadata_json).get("investigation_step")
            for log in audit_logs
        ]
        assert audit_steps == [
            "readiness",
            "analysis_sql",
            "analysis_business_logic",
            "final",
        ]

    second_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "który kardiolog leczy pacjentów",
            "user_id": "alice",
            "mode": "investigation",
        },
    )

    assert second_response.status_code == 200
    second_body = second_response.json()
    assert (
        second_body["metadata"]["analysis_results"][0]["business_logic_learning"][
            "status"
        ]
        == "existing"
    )

    with create_session() as session:
        connector = session.scalar(
            select(DatasourceConnector).where(
                DatasourceConnector.connector_key == "default"
            )
        )
        assert connector is not None
        suggestions = list_business_logic_suggestions(session, connector.id)
        assert len(suggestions) == 1


def test_query_stream_investigation_reports_analysis_steps(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NotReadyAgent:
        name = "test_not_ready_agent"

        def assess(self, context):
            from gaard_core.investigation.models import (
                InvestigationReadinessDecision,
                InvestigationRoute,
                RequiredAnalysisTask,
            )

            return InvestigationReadinessDecision(
                ready_for_sql=False,
                route=InvestigationRoute.ANALYSIS,
                confidence=0.92,
                reason="Dictionary value must be verified first.",
                required_analysis_tasks=[
                    RequiredAnalysisTask(
                        missing_information="specialization dictionary value",
                        required_analysis=(
                            "Inspect distinct doctors.specialization values."
                        ),
                        category="dictionary_value",
                    )
                ],
            )

    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_investigation_readiness_agent",
        lambda runtime_config: NotReadyAgent(),
    )

    response = admin_client.post(
        "/api/v1/query/stream",
        json={
            "question": "który kardiolog leczy pacjentów",
            "user_id": "alice",
            "mode": "investigation",
        },
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.strip().splitlines()]
    progress_steps = [
        event["progress"]["step"]
        for event in events
        if "progress" in event
    ]
    assert progress_steps == [
        "readiness",
        "readiness_complete",
        "analysis_sql",
        "analysis_sql_complete",
    ]
    assert events[-1]["final"]["metadata"]["analysis_mode_status"] == "not_implemented"


def test_query_blocks_write_intent_before_llm_and_writes_access_audit(
    admin_client: TestClient,
    monkeypatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)
    with create_session() as session:
        set_setting(session, "gaard_sql_generation_mode", "llm", "test")
        session.commit()

    class DenyIntentClassifier:
        def classify(self, request: QueryRequest) -> QueryIntentClassification:
            return QueryIntentClassification(
                decision=QueryIntentDecision.WRITE_OR_MUTATION_REQUEST,
                confidence=0.98,
                reason="The request asks to mutate data.",
            )

    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_intent_classifier",
        lambda llm_config=None: DenyIntentClassifier(),
    )

    questions = [
        "skasuj wszystkie zlecenia",
        "zmodufikuj zlecenia klienta Emix, tak, żeby były warte 100",
        "wyzeruj bazę danych",
    ]

    for question in questions:
        query_response = admin_client.post(
            "/api/v1/query",
            json={
                "question": question,
                "user_id": "alice",
            },
        )

        assert query_response.status_code == 200
        body = query_response.json()
        assert body["answer"].startswith("Nie mogę obsłużyć")
        assert body["sql"] == ""
        assert body["rows"] == []
        assert body["metadata"]["blocked"] is True
        assert body["metadata"]["blocked_reason"] == "access.intent_classification"
        assert body["metadata"]["intent_decision"] == "write_or_mutation_request"
        assert body["metadata"]["intent_confidence"] == 0.98
        assert body["metadata"]["intent_reason"] == "The request asks to mutate data."
        assert body["metadata"]["intent_model_response"] == {
            "decision": "write_or_mutation_request",
            "confidence": 0.98,
            "reason": "The request asks to mutate data.",
        }
        assert body["metadata"]["output_classification"] == "unknown"

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=access_error",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert len(items) == 3
    assert {item["question"] for item in items} == set(questions)
    for item in items:
        assert item["audit_type"] == "access_error"
        assert item["output_classification"] == "unknown"
        assert item["user_id"] == "alice"
        assert item["answer"].startswith("Nie mogę obsłużyć")
        assert item["metadata"]["error_category"] == "access.intent_classification"
        assert item["metadata"]["intent_decision"] == "write_or_mutation_request"
        assert item["metadata"]["intent_confidence"] == 0.98
        assert item["metadata"]["intent_reason"] == "The request asks to mutate data."
        assert item["metadata"]["intent_model_response"] == {
            "decision": "write_or_mutation_request",
            "confidence": 0.98,
            "reason": "The request asks to mutate data.",
        }


def test_query_writes_access_audit_for_generated_non_select_sql(
    admin_client: TestClient,
    monkeypatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    def generate_update_sql(self, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(
            sql="UPDATE patients SET status = 'inactive'",
            confidence=0.6,
            assumptions=["Intentional non-read-only SQL for validation audit test."],
        )

    monkeypatch.setattr(MockSqlGenerator, "generate", generate_update_sql)

    query_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "How many patients are there?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 200
    body = query_response.json()
    assert body["answer"].startswith("Nie mogę tego zrobić.")
    assert body["sql"] == "UPDATE patients SET status = 'inactive'"
    assert body["rows"] == []
    assert body["metadata"]["blocked"] is True
    assert body["metadata"]["blocked_reason"] == "access.sql_validation"
    assert body["metadata"]["intent_model_response"]["decision"] == "read_only_data_question"

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=access_error",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert len(items) == 1
    assert items[0]["audit_type"] == "access_error"
    assert items[0]["sql"] == "UPDATE patients SET status = 'inactive'"
    assert items[0]["metadata"]["error_category"] == "access.sql_validation"
    assert items[0]["metadata"]["error_code"] == "SQL_VALIDATION_ERROR"
    assert items[0]["metadata"]["intent_model_response"]["decision"] == "read_only_data_question"


def test_query_writes_audit_for_llm_provider_error_during_sql_generation(
    admin_client: TestClient,
    monkeypatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    class AllowIntentClassifier:
        def classify(self, request: QueryRequest) -> QueryIntentClassification:
            return QueryIntentClassification(
                decision=QueryIntentDecision.READ_ONLY_DATA_QUESTION,
                confidence=0.99,
                reason="Read-only question.",
            )

    class FailingPipeline:
        def handle(self, request: QueryRequest):
            raise QueryPipelineStepError(
                message="LLM provider returned HTTP 400. context length exceeded",
                phase="sql_generation",
                sql="",
                error_code="LLM_PROVIDER_ERROR",
                error_detail="context length exceeded",
            )

    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_intent_classifier",
        lambda llm_config=None: AllowIntentClassifier(),
    )
    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_pipeline",
        lambda datasource_context=None: FailingPipeline(),
    )

    query_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "jakie zlecenia były ostatnio modyfikowane?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 502
    assert query_response.json()["error"]["code"] == "LLM_PROVIDER_ERROR"

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert len(items) == 1
    assert items[0]["audit_type"] == "sql_error"
    assert items[0]["sql"] == ""
    assert items[0]["answer"] == "LLM provider returned HTTP 400. context length exceeded"
    assert items[0]["metadata"]["error_category"] == "llm.provider_error"
    assert items[0]["metadata"]["error_code"] == "LLM_PROVIDER_ERROR"
    assert items[0]["metadata"]["pipeline_phase"] == "sql_generation"
    assert items[0]["metadata"]["intent_decision"] == "read_only_data_question"


def test_query_writes_generated_sql_for_llm_provider_error_after_sql_generation(
    admin_client: TestClient,
    monkeypatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    class AllowIntentClassifier:
        def classify(self, request: QueryRequest) -> QueryIntentClassification:
            return QueryIntentClassification(
                decision=QueryIntentDecision.READ_ONLY_DATA_QUESTION,
                confidence=0.99,
                reason="Read-only question.",
            )

    class FailingPipeline:
        def handle(self, request: QueryRequest):
            raise QueryPipelineStepError(
                message="LLM provider request failed.",
                phase="result_interpretation",
                sql="SELECT id, temat FROM design_zlecenie ORDER BY updated_at DESC",
                error_code="LLM_PROVIDER_ERROR",
                error_detail="provider timeout",
            )

    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_intent_classifier",
        lambda llm_config=None: AllowIntentClassifier(),
    )
    monkeypatch.setattr(
        "gaard_api.api.v1.query.create_pipeline",
        lambda datasource_context=None: FailingPipeline(),
    )

    query_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "jakie zlecenia były ostatnio modyfikowane?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 502

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    item = audit_response.json()["items"][0]
    assert item["metadata"]["error_category"] == "llm.provider_error"
    assert item["metadata"]["pipeline_phase"] == "result_interpretation"
    assert item["sql"] == "SELECT id, temat FROM design_zlecenie ORDER BY updated_at DESC"


def test_query_audit_uses_active_datasource_connector_key(admin_client: TestClient) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "con_db",
            "name": "Connected DB",
            "database_type": "sqlite",
            "database_url": settings.gaard_datasource_url,
            "sql_dialect": "sqlite",
            "active": True,
        },
    )

    assert create_response.status_code == 200

    query_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 200
    assert query_response.json()["metadata"]["datasource_id"] == "con_db"

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    assert audit_response.json()["items"][0]["datasource_id"] == "con_db"


def test_query_endpoint_writes_sql_error_data_query_audit(
    admin_client: TestClient,
    tmp_path: Path,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)
    empty_db = tmp_path / "empty.db"

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "broken_db",
            "name": "Broken DB",
            "database_type": "sqlite",
            "database_url": f"sqlite:///{empty_db}",
            "sql_dialect": "sqlite",
            "active": True,
        },
    )

    assert create_response.status_code == 200

    query_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "How many active patients are there?",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 400
    assert query_response.json()["error"]["code"] == "QUERY_EXECUTION_ERROR"

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert audit_response.status_code == 200
    items = audit_response.json()["items"]
    assert len(items) == 1
    assert items[0]["audit_type"] == "sql_error"
    assert items[0]["output_classification"] == "unknown"
    assert items[0]["datasource_id"] == "broken_db"
    assert items[0]["user_id"] == "alice"
    assert "audit_type" not in items[0]["metadata"]
    assert items[0]["metadata"]["error_code"] == "QUERY_EXECUTION_ERROR"
    assert items[0]["metadata"]["business_logic_learning"]["status"] == "skipped"
    assert (
        items[0]["metadata"]["business_logic_learning"]["reason"]
        == "LLM API key is not configured for business logic learning."
    )
    assert "SELECT COUNT(*)" in items[0]["sql"]

    with create_session() as session:
        audit_log = session.scalar(select(DataQueryAuditLog))

    assert audit_log is not None
    assert audit_log.type == DataQueryAuditType.SQL_ERROR

    info_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=info",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert info_response.status_code == 200
    assert info_response.json()["items"] == []


def test_sql_error_creates_datasource_scoped_business_logic_suggestion(
    admin_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)
    learning_llm = stub_business_logic_learning_llm(
        monkeypatch,
        {
            "create_suggestion": True,
            "error_category": "schema.missing_table",
            "title": "Map project orders table",
            "rule_text": (
                "When the user asks about zlecenia or projects, use table "
                "`zlecenia_zlecenie` rather than nonexistent table `zlecenia`."
            ),
            "failed_identifier": "zlecenia",
            "repaired_identifier": "zlecenia_zlecenie",
            "confidence": 0.84,
            "terms": ["zlecenia", "projekty", "zlecenia_zlecenie"],
            "join_hints": [],
            "skip_reason": "",
        },
    )
    db_path = tmp_path / "projects.db"
    connection = sqlite3.connect(db_path)

    try:
        connection.execute("CREATE TABLE zlecenia_zlecenie (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE zlecenia_osoby (id INTEGER PRIMARY KEY)")
        connection.execute(
            """
            CREATE TABLE zlecenia_projektanci (
                zlecenie_id INTEGER NOT NULL,
                osoby_id INTEGER NOT NULL,
                FOREIGN KEY(zlecenie_id) REFERENCES zlecenia_zlecenie(id),
                FOREIGN KEY(osoby_id) REFERENCES zlecenia_osoby(id)
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    def generate_bad_sql(self, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(
            sql="SELECT COUNT(*) AS liczba_projektow FROM zlecenia",
            confidence=0.6,
            assumptions=["Intentional missing table for repair learning test."],
        )

    monkeypatch.setattr(MockSqlGenerator, "generate", generate_bad_sql)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "con_db",
            "name": "Connected DB",
            "database_type": "sqlite",
            "database_url": f"sqlite:///{db_path}",
            "sql_dialect": "sqlite",
            "active": True,
        },
    )

    assert create_response.status_code == 200
    connector_id = create_response.json()["item"]["id"]

    query_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "Który pracownik zrealizował najwięcej projektów",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 400

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers={"Authorization": f"Bearer {token}"},
    )
    audit_item = audit_response.json()["items"][0]

    assert audit_item["metadata"]["error_category"] == "schema.missing_table"
    assert audit_item["metadata"]["failed_identifier"] == "zlecenia"
    assert audit_item["metadata"]["business_logic_learning"]["suggestion_id"]
    assert audit_item["metadata"]["business_logic_learning"]["admin_section"] == "business-logic"
    assert len(learning_llm.requests) == 1
    learning_prompt = "\n".join(
        message.content for message in learning_llm.requests[0].messages
    )
    assert "Który pracownik zrealizował najwięcej projektów" in learning_prompt
    assert "SELECT COUNT(*) AS liczba_projektow FROM zlecenia" in learning_prompt
    assert "no such table: zlecenia" in learning_prompt

    suggestions_response = admin_client.get(
        "/api/v1/admin/business-logic-suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert suggestions_response.status_code == 200
    assert suggestions_response.json()["datasource"]["connector_key"] == "con_db"
    suggestions = suggestions_response.json()["items"]
    assert len(suggestions) == 1
    assert suggestions[0]["status"] == "pending"
    assert suggestions[0]["enabled"] is False
    assert suggestions[0]["failed_identifier"] == "zlecenia"
    assert suggestions[0]["repaired_identifier"] == "zlecenia_zlecenie"
    assert "zlecenia_zlecenie" in suggestions[0]["rule_text"]

    enable_response = admin_client.put(
        f"/api/v1/admin/business-logic-suggestions/{suggestions[0]['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True},
    )

    assert enable_response.status_code == 200
    assert enable_response.json()["item"]["status"] == "active"
    assert enable_response.json()["item"]["enabled"] is True
    assert "zlecenia_zlecenie" in get_active_business_logic_prompt_safe(connector_id)

    edit_response = admin_client.put(
        f"/api/v1/admin/business-logic-suggestions/{suggestions[0]['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Updated business logic",
            "rule_text": "Use `zlecenia_zlecenie` for customer project orders.",
        },
    )

    assert edit_response.status_code == 200
    edited = edit_response.json()["item"]
    assert edited["title"] == "Updated business logic"
    assert edited["rule_text"] == "Use `zlecenia_zlecenie` for customer project orders."
    assert edited["status"] == "active"
    assert edited["enabled"] is True
    assert "customer project orders" in get_active_business_logic_prompt_safe(connector_id)

    empty_edit_response = admin_client.put(
        f"/api/v1/admin/business-logic-suggestions/{suggestions[0]['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"rule_text": "   "},
    )

    assert empty_edit_response.status_code == 400

    delete_response = admin_client.delete(
        f"/api/v1/admin/business-logic-suggestions/{suggestions[0]['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"


def test_missing_column_sql_error_creates_business_logic_suggestion(
    admin_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)
    stub_business_logic_learning_llm(
        monkeypatch,
        {
            "create_suggestion": True,
            "error_category": "schema.missing_column",
            "title": "Avoid missing address column",
            "rule_text": (
                "Do not select `adres` from `zlecenia_osoby`; this table does not "
                "store a full address column. Use only listed columns."
            ),
            "failed_identifier": "zlecenia_osoby.adres",
            "repaired_identifier": "",
            "confidence": 0.72,
            "terms": ["adres", "zlecenia_osoby"],
            "join_hints": [],
            "skip_reason": "",
        },
    )
    db_path = tmp_path / "people.db"
    connection = sqlite3.connect(db_path)

    try:
        connection.execute(
            """
            CREATE TABLE zlecenia_osoby (
                id INTEGER PRIMARY KEY,
                imie TEXT,
                nazwisko TEXT,
                email TEXT,
                telefon TEXT,
                kod TEXT,
                miasto TEXT,
                dzial_id INTEGER,
                koncern_id INTEGER
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    def generate_bad_sql(self, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(
            sql=(
                "SELECT imie, nazwisko, email, telefon, adres, kod, miasto, "
                "dzial_id, koncern_id FROM zlecenia_osoby WHERE id = 42"
            ),
            confidence=0.6,
            assumptions=["Intentional missing column for repair learning test."],
        )

    monkeypatch.setattr(MockSqlGenerator, "generate", generate_bad_sql)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "con_db",
            "name": "Connected DB",
            "database_type": "sqlite",
            "database_url": f"sqlite:///{db_path}",
            "sql_dialect": "sqlite",
            "active": True,
        },
    )

    assert create_response.status_code == 200
    connector_id = create_response.json()["item"]["id"]

    query_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "Podaj dane osobowe pracownika nr 42",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 400

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers={"Authorization": f"Bearer {token}"},
    )
    audit_item = audit_response.json()["items"][0]

    assert audit_item["metadata"]["error_category"] == "schema.missing_column"
    assert audit_item["metadata"]["failed_identifier"] == "zlecenia_osoby.adres"
    assert audit_item["metadata"]["business_logic_learning"]["suggestion_id"]

    suggestions_response = admin_client.get(
        "/api/v1/admin/business-logic-suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert suggestions_response.status_code == 200
    suggestions = suggestions_response.json()["items"]
    assert len(suggestions) == 1
    assert suggestions[0]["status"] == "pending"
    assert suggestions[0]["enabled"] is False
    assert suggestions[0]["error_category"] == "schema.missing_column"
    assert suggestions[0]["failed_identifier"] == "zlecenia_osoby.adres"
    assert suggestions[0]["repaired_identifier"] == ""
    assert "Do not select `adres` from `zlecenia_osoby`" in suggestions[0]["rule_text"]

    enable_response = admin_client.put(
        f"/api/v1/admin/business-logic-suggestions/{suggestions[0]['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True},
    )

    assert enable_response.status_code == 200
    assert "Do not select `adres`" in get_active_business_logic_prompt_safe(connector_id)


def test_join_missing_column_sql_error_is_learned_by_llm(
    admin_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    token = login(admin_client)["token"]
    change_password(admin_client, token)
    learning_llm = stub_business_logic_learning_llm(
        monkeypatch,
        {
            "create_suggestion": True,
            "error_category": "schema.missing_column",
            "title": "Use client name column with aliases",
            "rule_text": (
                "When grouping orders by client name, join `zlecenia_klienci` with an alias "
                "such as `zk` and select `zk.nazwa AS klient_nazwa`; do not reference "
                "unqualified column `klient_nazwa` as if it existed in the schema."
            ),
            "failed_identifier": "klient_nazwa",
            "repaired_identifier": "zlecenia_klienci.nazwa",
            "confidence": 0.9,
            "terms": ["klient", "klient_nazwa", "zlecenia_klienci", "nazwa"],
            "join_hints": ["zlecenia_osoby.klient -> zlecenia_klienci.id"],
            "skip_reason": "",
        },
    )
    db_path = tmp_path / "clients.db"
    connection = sqlite3.connect(db_path)

    try:
        connection.execute(
            """
            CREATE TABLE zlecenia_osoby (
                id INTEGER PRIMARY KEY,
                klient INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE zlecenia_klienci (
                id INTEGER PRIMARY KEY,
                nazwa TEXT NOT NULL
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    bad_sql = (
        "SELECT klient_nazwa, COUNT(*) AS ilosc FROM zlecenia_osoby "
        "JOIN zlecenia_klienci ON zlecenia_osoby.klient = zlecenia_klienci.id "
        "GROUP BY klient_nazwa ORDER BY ilosc DESC LIMIT 10"
    )

    def generate_bad_sql(self, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(
            sql=bad_sql,
            confidence=0.6,
            assumptions=["Intentional missing unqualified join column."],
        )

    monkeypatch.setattr(MockSqlGenerator, "generate", generate_bad_sql)

    create_response = admin_client.post(
        "/api/v1/admin/datasources",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_key": "con_db",
            "name": "Connected DB",
            "database_type": "sqlite",
            "database_url": f"sqlite:///{db_path}",
            "sql_dialect": "sqlite",
            "active": True,
        },
    )

    assert create_response.status_code == 200

    query_response = admin_client.post(
        "/api/v1/query",
        json={
            "question": "Pokaż klientów z największą liczbą zleceń",
            "user_id": "alice",
        },
    )

    assert query_response.status_code == 400

    audit_response = admin_client.get(
        "/api/v1/admin/audit/data-queries?audit_type=sql_error",
        headers={"Authorization": f"Bearer {token}"},
    )
    audit_item = audit_response.json()["items"][0]

    assert audit_item["metadata"]["business_logic_learning"]["status"] == "pending_approval"
    assert audit_item["metadata"]["business_logic_learning"]["suggestion_id"]
    assert len(learning_llm.requests) == 1
    learning_prompt = "\n".join(
        message.content for message in learning_llm.requests[0].messages
    )
    assert "Pokaż klientów z największą liczbą zleceń" in learning_prompt
    assert bad_sql in learning_prompt
    assert "klient_nazwa" in learning_prompt

    suggestions_response = admin_client.get(
        "/api/v1/admin/business-logic-suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )

    suggestions = suggestions_response.json()["items"]
    assert len(suggestions) == 1
    assert suggestions[0]["error_category"] == "schema.missing_column"
    assert suggestions[0]["failed_identifier"] == "klient_nazwa"
    assert suggestions[0]["repaired_identifier"] == "zlecenia_klienci.nazwa"
    assert "zk.nazwa AS klient_nazwa" in suggestions[0]["rule_text"]


def test_admin_app_is_served(admin_client: TestClient) -> None:
    response = admin_client.get("/admin")

    assert response.status_code == 200
    assert "GAARD Admin Console" in response.text
