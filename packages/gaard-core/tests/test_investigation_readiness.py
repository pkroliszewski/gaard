from gaard_core.investigation.llm_readiness_agent import (
    parse_investigation_readiness_decision,
)
from gaard_core.investigation.loop import InvestigationLoop
from gaard_core.investigation.mock_readiness_agent import MockInvestigationReadinessAgent
from gaard_core.investigation.models import (
    InvestigationContext,
    InvestigationLoopConfig,
    InvestigationReadinessDecision,
    InvestigationRoute,
    RequiredAnalysisTask,
)
from gaard_core.prompt_compiler.investigation_readiness_prompt import (
    InvestigationReadinessPromptCompiler,
)


def test_parse_readiness_decision_routes_ready_json_to_sql() -> None:
    decision = parse_investigation_readiness_decision(
        """
        {
          "ready_for_sql": true,
          "route": "sql",
          "confidence": 0.94,
          "reason": "Schema and business logic identify the needed columns.",
          "missing_information": [],
          "required_analysis": [],
          "assumptions": []
        }
        """
    )

    assert decision.ready_for_sql is True
    assert decision.route == InvestigationRoute.SQL
    assert decision.confidence == 0.94


def test_parse_readiness_decision_invalid_json_requires_analysis() -> None:
    decision = parse_investigation_readiness_decision("ready")

    assert decision.ready_for_sql is False
    assert decision.route == InvestigationRoute.ANALYSIS
    assert "valid readiness JSON" in decision.missing_information


def test_parse_readiness_decision_requires_consistent_ready_signal() -> None:
    decision = parse_investigation_readiness_decision(
        '{"ready_for_sql": false, "route": "sql", "confidence": 0.99}'
    )

    assert decision.ready_for_sql is False
    assert decision.route == InvestigationRoute.ANALYSIS


def test_parse_readiness_decision_reads_structured_required_analysis_tasks() -> None:
    decision = parse_investigation_readiness_decision(
        """
        {
          "ready_for_sql": false,
          "route": "analysis",
          "confidence": 0.91,
          "missing_information": ["specialty dictionary value"],
          "required_analysis": ["List distinct doctors.specialization values."],
          "required_analysis_tasks": [
            {
              "missing_information": "specialty dictionary value",
              "required_analysis": "List distinct doctors.specialization values.",
              "category": "dictionary-value",
              "expected_output": "specialization values"
            }
          ],
          "assumptions": []
        }
        """
    )

    assert decision.required_analysis_tasks == [
        RequiredAnalysisTask(
            missing_information="specialty dictionary value",
            required_analysis="List distinct doctors.specialization values.",
            category="dictionary_value",
            expected_output="specialization values",
        )
    ]


def test_parse_readiness_decision_builds_tasks_from_legacy_lists() -> None:
    decision = parse_investigation_readiness_decision(
        """
        {
          "ready_for_sql": false,
          "route": "analysis",
          "confidence": 0.91,
          "missing_information": ["specialty dictionary value"],
          "required_analysis": ["List distinct doctors.specialization values."],
          "assumptions": []
        }
        """
    )

    assert decision.required_analysis_tasks == [
        RequiredAnalysisTask(
            missing_information="specialty dictionary value",
            required_analysis="List distinct doctors.specialization values.",
        )
    ]


def test_investigation_loop_requires_confidence_threshold_for_sql() -> None:
    agent = MockInvestigationReadinessAgent(
        InvestigationReadinessDecision(
            ready_for_sql=True,
            route=InvestigationRoute.SQL,
            confidence=0.5,
            reason="Low confidence.",
        )
    )

    result = InvestigationLoop(
        readiness_agent=agent,
        config=InvestigationLoopConfig(
            max_iterations=1,
            readiness_confidence_threshold=0.85,
        ),
    ).run(
        InvestigationContext(
            question="ile jest wizyt",
            formatted_schema="Table: appointments",
        )
    )

    assert result.ready_for_sql is False
    assert result.route == InvestigationRoute.ANALYSIS
    assert result.iterations[0].decision.ready_for_sql is False


def test_readiness_prompt_includes_schema_and_business_logic() -> None:
    compiled = InvestigationReadinessPromptCompiler().compile(
        InvestigationContext(
            question="ile jest aktywnych pacjentów",
            datasource_id="medical",
            user_id="alice",
            formatted_schema="Table: patients",
            business_logic="Active patient means patients.status = 'active'.",
        )
    )

    assert "Assume nothing. Verify continuously." in compiled.system_prompt
    assert "Table: patients" in compiled.user_prompt
    assert "patients.status = 'active'" in compiled.user_prompt
