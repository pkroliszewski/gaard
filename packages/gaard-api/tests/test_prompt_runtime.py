from decimal import Decimal

from gaard_core.query_pipeline.models import QueryRequest, QueryResult

from gaard_api.admin.models import PromptTemplate
from gaard_api.admin.prompt_runtime import (
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
