from decimal import Decimal

from gaard_core.query_pipeline.models import QueryRequest, QueryResult

from gaard_api.admin.models import PromptTemplate
from gaard_api.admin.prompt_runtime import (
    MetadataAnswerExplanationPromptCompiler,
    MetadataConversationContextPromptCompiler,
    MetadataIntentClassificationPromptCompiler,
    MetadataResultClassificationPromptCompiler,
    MetadataResultInterpretationPromptCompiler,
)


def test_metadata_result_interpretation_prompt_compiler_serializes_decimal_rows() -> None:
    compiler = MetadataResultInterpretationPromptCompiler(
        prompt_template=PromptTemplate(
            prompt_key="result_interpretation",
            name="Result interpretation",
            system_prompt="system",
            user_prompt_template="{payload}\n{rows}",
            version=1,
            active=True,
        )
    )

    compiled = compiler.compile(
        request=QueryRequest(question="How much time was spent?"),
        sql="SELECT SUM(minutes) AS total_minutes FROM worklog",
        result=QueryResult(
            columns=["total_minutes"],
            rows=[{"total_minutes": Decimal("30.5")}],
        ),
    )

    assert '"total_minutes": 30.5' in compiled.user_prompt


def test_metadata_intent_classification_prompt_compiler_serializes_question() -> None:
    compiler = MetadataIntentClassificationPromptCompiler(
        prompt_template=PromptTemplate(
            prompt_key="intent_classification",
            name="Intent classification",
            system_prompt="system",
            user_prompt_template="{payload}\n{question}",
            version=1,
            active=True,
        )
    )

    compiled = compiler.compile(
        request=QueryRequest(
            question="zmodufikuj zlecenia klienta Emix",
            datasource_id="design_db",
            user_id="alice",
        ),
    )

    assert "zmodufikuj zlecenia" in compiled.user_prompt
    assert '"datasource_id": "design_db"' in compiled.user_prompt
    assert compiled.metadata["prompt_key"] == "intent_classification"


def test_metadata_conversation_context_prompt_compiler_serializes_recent_turns() -> None:
    compiler = MetadataConversationContextPromptCompiler(
        prompt_template=PromptTemplate(
            prompt_key="conversation_context_classification",
            name="Conversation context classification",
            system_prompt="system",
            user_prompt_template="{payload}\n{question}",
            version=3,
            active=True,
        )
    )

    compiled = compiler.compile(
        request=QueryRequest(question="ilu pacjentów przyjęto w tym tygodniu"),
        conversation_context={
            "turns": [
                {
                    "question": "ilu pacjentów było przyjętych tydzień temu",
                    "answer": "12",
                },
                {
                    "question": "a dwa tygodnie temu?",
                    "standalone_question": "ilu pacjentów było przyjętych dwa tygodnie temu",
                    "answer": "9",
                },
            ]
        },
    )

    assert '"turn_t_minus_2"' in compiled.user_prompt
    assert '"turn_t_minus_1"' in compiled.user_prompt
    assert '"turn_t"' in compiled.user_prompt
    assert "ilu pacjentów przyjęto w tym tygodniu" in compiled.user_prompt
    assert compiled.metadata["prompt_key"] == "conversation_context_classification"
    assert compiled.metadata["prompt_version"] == 3
    assert compiled.metadata["decision_task"] == "logical_continuation_yes_no"


def test_metadata_result_classification_prompt_compiler_serializes_answer() -> None:
    compiler = MetadataResultClassificationPromptCompiler(
        prompt_template=PromptTemplate(
            prompt_key="result_classification",
            name="Result classification",
            system_prompt="system",
            user_prompt_template="{payload}\n{answer}",
            version=1,
            active=True,
        )
    )

    compiled = compiler.compile(
        request=QueryRequest(question="How many audit logs refer to personal data?"),
        answer="There are 12 audit logs that refer to personal data.",
    )

    assert "There are 12 audit logs" in compiled.user_prompt
    assert compiled.metadata["prompt_key"] == "result_classification"


def test_metadata_answer_explanation_prompt_compiler_serializes_context() -> None:
    compiler = MetadataAnswerExplanationPromptCompiler(
        prompt_template=PromptTemplate(
            prompt_key="answer_explanation",
            name="Answer explanation",
            system_prompt="system",
            user_prompt_template="{payload}\n{question}\n{sql}\n{business_logic}",
            version=4,
            active=True,
        )
    )

    compiled = compiler.compile(
        {
            "question": "Ilu pacjentów przyjęto w tym tygodniu?",
            "sql": "SELECT COUNT(*) AS patient_count FROM appointments",
            "answer": "Przyjęto 12 pacjentów.",
            "result": {
                "columns": ["patient_count"],
                "rows": [{"patient_count": Decimal("12")}],
            },
            "metadata": {"sql_generation_mode": "llm"},
            "inference_metadata": {"intent_decision": "read_only_data_question"},
            "prompt_metadata": {"sql_generation_prompt_metadata": {"prompt_version": 2}},
            "business_logic": "Business logic:\n- Count completed appointments.",
        }
    )

    assert "Ilu pacjentów" in compiled.user_prompt
    assert "SELECT COUNT(*)" in compiled.user_prompt
    assert '"patient_count": 12' in compiled.user_prompt
    assert "Count completed appointments" in compiled.user_prompt
    assert compiled.metadata["prompt_key"] == "answer_explanation"
    assert compiled.metadata["prompt_version"] == 4
    assert compiled.metadata["has_business_logic"] is True
