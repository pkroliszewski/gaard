import pytest

from gaard_core.errors import LlmProviderError, QueryPipelineStepError
from gaard_core.query_pipeline.models import GeneratedSql, QueryRequest, QueryResult
from gaard_core.query_pipeline.pipeline import QueryPipeline


def test_query_pipeline_returns_mock_patient_count() -> None:
    pipeline = QueryPipeline()

    response = pipeline.handle(
        QueryRequest(
            question="Ilu jest pacjentów?",
            datasource_id="default",
            user_id="test-user",
        )
    )

    assert response.sql == "SELECT COUNT(*) AS patients_count FROM patients"
    assert response.rows == [{"patients_count": 124}]
    assert "124" in response.answer
    assert response.metadata["sql_generation_mode"] == "mock"
    assert response.metadata["result_interpretation_mode"] == "mock"
    assert response.metadata["output_classification_mode"] == "mock"
    assert response.metadata["output_classification"] == "neutral_data"


class FailingSqlGenerator:
    def generate(self, request: QueryRequest) -> GeneratedSql:
        raise LlmProviderError("LLM provider returned HTTP 400.")


class StaticSqlGenerator:
    def generate(self, request: QueryRequest) -> GeneratedSql:
        return GeneratedSql(sql="SELECT 1 AS value")


class StaticExecutor:
    def execute(self, sql: str) -> QueryResult:
        return QueryResult(columns=["value"], rows=[{"value": 1}])


class FailingInterpreter:
    def interpret(self, request: QueryRequest, result: QueryResult, sql: str = "") -> str:
        raise LlmProviderError("LLM provider request failed.")


def test_query_pipeline_wraps_llm_provider_error_during_sql_generation() -> None:
    pipeline = QueryPipeline(sql_generator=FailingSqlGenerator())

    with pytest.raises(QueryPipelineStepError) as exc_info:
        pipeline.handle(QueryRequest(question="What changed recently?"))

    assert exc_info.value.code == "LLM_PROVIDER_ERROR"
    assert exc_info.value.phase == "sql_generation"
    assert exc_info.value.sql == ""


def test_query_pipeline_wraps_llm_provider_error_during_result_interpretation() -> None:
    pipeline = QueryPipeline(
        sql_generator=StaticSqlGenerator(),
        executor=StaticExecutor(),
        interpreter=FailingInterpreter(),
    )

    with pytest.raises(QueryPipelineStepError) as exc_info:
        pipeline.handle(QueryRequest(question="What changed recently?"))

    assert exc_info.value.code == "LLM_PROVIDER_ERROR"
    assert exc_info.value.phase == "result_interpretation"
    assert exc_info.value.sql == "SELECT 1 AS value"
