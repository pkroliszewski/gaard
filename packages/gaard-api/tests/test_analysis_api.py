from collections.abc import Iterator
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gaard_core.query_pipeline.models import QueryResponse
from gaard_api.admin.database import create_session, reset_metadata_store_for_tests
from gaard_api.admin.models import AnalysisSessionRecord, DatasourceConnector
from gaard_api.admin.services import list_business_logic_suggestions, set_setting
from gaard_api.api.v1 import analysis as analysis_module
from gaard_api.core.settings import settings
from gaard_api.example_database import install_medical_poc_example_database
from gaard_api.main import app


@pytest.fixture()
def analysis_client(tmp_path: Path, monkeypatch) -> Iterator[TestClient]:
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


def parse_ndjson(text: str) -> list[dict]:
    return [json.loads(line) for line in text.strip().splitlines() if line.strip()]


def create_active_default_datasource() -> DatasourceConnector:
    with create_session() as session:
        connector = session.scalar(
            select(DatasourceConnector).where(DatasourceConnector.connector_key == "default")
        )
        if connector is None:
            connector = DatasourceConnector(
                connector_key="default",
                name="Default DB",
                database_type="sqlite",
                database_url=settings.gaard_datasource_url,
                sql_dialect="sqlite",
                active=True,
            )
            session.add(connector)
        connector.database_url = settings.gaard_datasource_url
        connector.active = True
        session.commit()
        session.refresh(connector)
        return connector


def test_analysis_stream_runs_final_query_and_persists_session(
    analysis_client: TestClient,
) -> None:
    response = analysis_client.post(
        "/api/v1/analysis/stream",
        json={
            "question": "Ilu jest pacjentów?",
            "user_id": "alice",
        },
    )

    assert response.status_code == 200
    events = parse_ndjson(response.text)
    event_names = [event["event"] for event in events]
    assert event_names == [
        "session_started",
        "analysis_step",
        "decision",
        "database_question",
        "final",
    ]
    final = events[-1]["final"]
    session_id = events[0]["session_id"]
    assert final["sql"] == "SELECT COUNT(*) AS patients_count FROM patients"
    assert final["rows"] == [{"patients_count": 140}]
    assert "140" in final["answer"]
    assert final["metadata"]["analysis_mode"] == "analysis"
    assert final["metadata"]["analysis_session_id"] == session_id

    dump_response = analysis_client.get(f"/api/v1/analysis/{session_id}")

    assert dump_response.status_code == 200
    item = dump_response.json()["item"]
    assert item["status"] == "completed"
    assert item["question"] == "Ilu jest pacjentów?"
    assert len(item["events"]) == len(events)

    with create_session() as session:
        record = session.scalar(
            select(AnalysisSessionRecord).where(
                AnalysisSessionRecord.session_id == session_id
            )
        )
        assert record is not None
        assert record.status == "completed"


def test_analysis_stream_can_pause_for_user_and_resume(
    analysis_client: TestClient,
) -> None:
    response = analysis_client.post(
        "/api/v1/analysis/stream",
        json={
            "question": "dopytaj o zakres",
            "user_id": "alice",
        },
    )

    assert response.status_code == 200
    events = parse_ndjson(response.text)
    session_id = events[0]["session_id"]
    assert events[-1]["event"] == "user_question"
    assert "Doprecyzuj" in events[-1]["user_question"]["question"]

    waiting_response = analysis_client.get(f"/api/v1/analysis/{session_id}")
    assert waiting_response.status_code == 200
    assert waiting_response.json()["item"]["status"] == "waiting_for_user"

    resume_response = analysis_client.post(
        f"/api/v1/analysis/{session_id}/messages/stream",
        json={"message": "ostatni miesiąc"},
    )

    assert resume_response.status_code == 200
    resumed_events = parse_ndjson(resume_response.text)
    assert resumed_events[0]["event"] == "session_resumed"
    assert resumed_events[-1]["event"] == "final"
    assert resumed_events[-1]["final"]["metadata"]["analysis_status"] == "completed"

    completed_response = analysis_client.get(f"/api/v1/analysis/{session_id}")
    assert completed_response.status_code == 200
    context = completed_response.json()["item"]["context"]
    assert context["messages"][-1]["content"] == "ostatni miesiąc"


def test_analysis_database_step_can_record_business_logic_suggestion(
    analysis_client: TestClient,
) -> None:
    connector = create_active_default_datasource()
    with create_session() as session:
        set_setting(session, "gaard_analysis_auto_enable_business_logic", "true", "test")
        session.commit()

    response = analysis_client.post(
        "/api/v1/analysis/stream",
        json={
            "question": "sprawdź słownik wartości",
            "user_id": "alice",
        },
    )

    assert response.status_code == 200
    events = parse_ndjson(response.text)
    event_names = [event["event"] for event in events]
    assert event_names == [
        "session_started",
        "analysis_step",
        "decision",
        "database_question",
        "database_result",
        "analysis_step",
        "decision",
        "business_logic_suggestion",
        "final",
    ]
    suggestion_event = next(
        event["business_logic_suggestion"]
        for event in events
        if event["event"] == "business_logic_suggestion"
    )
    assert suggestion_event["status"] == "active"
    assert suggestion_event["enabled"] is True

    with create_session() as session:
        suggestions = list_business_logic_suggestions(session, connector.id)
        assert len(suggestions) == 1
        assert suggestions[0].enabled is True
        assert suggestions[0].status == "active"
        assert suggestions[0].error_category == "analysis.dictionary_value"


def test_analysis_routes_database_evidence_questions_to_database(
    analysis_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = create_active_default_datasource()

    class EvidencePlanner:
        def __init__(self) -> None:
            self.calls = 0

        def decide(self, request, datasource_context, context):
            self.calls += 1
            if self.calls == 1:
                return analysis_module.AnalysisPlannerDecision(
                    action=analysis_module.AnalysisAction.ASK_USER,
                    visible_question="Do I need a table value?",
                    visible_reasoning="Need values from the doctors table.",
                    user_question=(
                        "What distinct specializations are stored in the doctors table?"
                    ),
                )

            return analysis_module.AnalysisPlannerDecision(
                action=analysis_module.AnalysisAction.ANSWER_FROM_CONTEXT,
                visible_question="Do I have enough evidence?",
                visible_reasoning="The database returned durable values.",
                answer="The doctors table contains durable specialization values.",
            )

    def fake_run_sql_request(request, datasource_context, metadata):
        return QueryResponse(
            question=request.question,
            answer="The specializations are cardiology and pediatrics.",
            sql="SELECT DISTINCT specialization FROM doctors",
            rows=[
                {"specialization": "cardiology"},
                {"specialization": "pediatrics"},
            ],
            metadata=metadata,
        )

    monkeypatch.setattr(
        analysis_module,
        "create_analysis_planner",
        lambda: EvidencePlanner(),
    )
    monkeypatch.setattr(analysis_module, "run_sql_request", fake_run_sql_request)

    response = analysis_client.post(
        "/api/v1/analysis/stream",
        json={
            "question": "List doctor specializations",
            "user_id": "alice",
        },
    )

    assert response.status_code == 200
    events = parse_ndjson(response.text)
    event_names = [event["event"] for event in events]
    assert "user_question" not in event_names
    assert "database_question" in event_names
    assert "business_logic_suggestion" in event_names
    final = events[-1]["final"]
    assert final["sql"] == "SELECT DISTINCT specialization FROM doctors"
    assert final["rows"] == [
        {"specialization": "cardiology"},
        {"specialization": "pediatrics"},
    ]
    assert final["metadata"]["analysis_supporting_data"] is True
    database_event = next(
        event["database_question"]
        for event in events
        if event["event"] == "database_question"
    )
    assert database_event["question"] == (
        "What distinct specializations are stored in the doctors table?"
    )

    with create_session() as session:
        suggestions = list_business_logic_suggestions(session, connector.id)
        assert len(suggestions) == 1
        assert suggestions[0].status == "pending"
        assert suggestions[0].error_category == "analysis.dictionary_value"
        assert "cardiology" in suggestions[0].rule_text


def test_analysis_out_of_scope_has_friendly_final_answer(
    analysis_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OutOfScopePlanner:
        def decide(self, request, datasource_context, context):
            return analysis_module.AnalysisPlannerDecision(
                action=analysis_module.AnalysisAction.OUT_OF_SCOPE,
                visible_question="Is this covered by the datasource?",
                visible_reasoning="The topic is outside the connected data.",
                answer="",
            )

    monkeypatch.setattr(
        analysis_module,
        "create_analysis_planner",
        lambda: OutOfScopePlanner(),
    )

    response = analysis_client.post(
        "/api/v1/analysis/stream",
        json={
            "question": "Jaka jest najlepsza Toyota?",
            "user_id": "alice",
        },
    )

    assert response.status_code == 200
    events = parse_ndjson(response.text)
    assert events[-1]["event"] == "final"
    final = events[-1]["final"]
    assert final["sql"] == ""
    assert "poza zakresem" in final["answer"]


def test_analysis_suppresses_supporting_rows_when_final_says_data_is_not_applicable(
    analysis_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HospitalCostPlanner:
        def __init__(self) -> None:
            self.calls = 0

        def decide(self, request, datasource_context, context):
            self.calls += 1
            if self.calls == 1:
                return analysis_module.AnalysisPlannerDecision(
                    action=analysis_module.AnalysisAction.ASK_DATABASE,
                    visible_question=(
                        "What is the base price for a Cardiology consultation?"
                    ),
                    visible_reasoning=(
                        "We need the hospital cost from the medical_procedures table."
                    ),
                    database_question=(
                        "SELECT base_price FROM medical_procedures "
                        "WHERE name = 'Cardiology consultation';"
                    ),
                )

            return analysis_module.AnalysisPlannerDecision(
                action=analysis_module.AnalysisAction.ANSWER_FROM_CONTEXT,
                visible_question=(
                    "What is the cost of a Cardiology consultation for the hospital?"
                ),
                visible_reasoning=(
                    "The database schema only contains the price charged to patients, "
                    "not the hospital's cost."
                ),
                answer=(
                    "I’m sorry, but the available data only includes the price "
                    "charged to patients. The cost of providing a Cardiology "
                    "consultation to the hospital is not recorded in the current "
                    "database."
                ),
            )

    def fake_run_sql_request(request, datasource_context, metadata):
        return QueryResponse(
            question=request.question,
            answer="The base price for a cardiology consultation is $220.0.",
            sql=(
                "SELECT base_price FROM medical_procedures "
                "WHERE name = 'Cardiology consultation';"
            ),
            rows=[{"base_price": 220.0}],
            metadata=metadata,
        )

    monkeypatch.setattr(
        analysis_module,
        "create_analysis_planner",
        lambda: HospitalCostPlanner(),
    )
    monkeypatch.setattr(analysis_module, "run_sql_request", fake_run_sql_request)

    response = analysis_client.post(
        "/api/v1/analysis/stream",
        json={
            "question": (
                "a jaki jest koszt wykonania Cardiology consultation dla szpitala?"
            ),
            "user_id": "alice",
        },
    )

    assert response.status_code == 200
    events = parse_ndjson(response.text)
    assert [event["event"] for event in events] == [
        "session_started",
        "analysis_step",
        "decision",
        "database_question",
        "database_result",
        "analysis_step",
        "decision",
        "final",
    ]
    assert events[4]["database_result"]["rows"] == [{"base_price": 220.0}]
    final = events[-1]["final"]
    assert final["sql"] == ""
    assert final["rows"] == []
    assert final["metadata"]["analysis_supporting_data"] is False
    assert (
        final["metadata"]["analysis_supporting_data_suppressed_reason"]
        == "final_answer_says_supporting_data_is_not_applicable"
    )
