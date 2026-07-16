import logging
import time
from collections.abc import Callable
from typing import Protocol

from gaard_core.execution.mock_executor import MockQueryExecutor
from gaard_core.query_pipeline.mock_sql_generator import MockSqlGenerator
from gaard_core.errors import LlmProviderError, QueryPipelineStepError
from gaard_core.query_pipeline.models import (
    GeneratedSql,
    OutputClassification,
    QueryRequest,
    QueryResponse,
    QueryResult,
)
from gaard_core.result_classifier.mock_classifier import MockResultClassifier
from gaard_core.result_interpreter.mock_interpreter import MockResultInterpreter
from gaard_core.sql_validator.select_only import SelectOnlySqlValidator


logger = logging.getLogger(__name__)


class SqlGenerator(Protocol):
    def generate(self, request: QueryRequest) -> GeneratedSql:
        pass


class QueryExecutor(Protocol):
    def execute(self, sql: str) -> QueryResult:
        pass


class ResultInterpreter(Protocol):
    def interpret(self, request: QueryRequest, result: QueryResult, sql: str = "") -> str:
        pass


class ResultClassifier(Protocol):
    def classify(self, request: QueryRequest, answer: str) -> OutputClassification:
        pass


class QueryPipeline:
    def __init__(
        self,
        sql_generator: SqlGenerator | None = None,
        sql_validator: SelectOnlySqlValidator | None = None,
        executor: QueryExecutor | None = None,
        interpreter: ResultInterpreter | None = None,
        classifier: ResultClassifier | None = None,
        sql_generation_mode: str = "mock",
        result_interpretation_mode: str = "mock",
        output_classification_mode: str = "mock",
    ) -> None:
        self.sql_generator = sql_generator or MockSqlGenerator()
        self.sql_validator = sql_validator or SelectOnlySqlValidator()
        self.executor = executor or MockQueryExecutor()
        self.interpreter = interpreter or MockResultInterpreter()
        self.classifier = classifier or MockResultClassifier()
        self.sql_generation_mode = sql_generation_mode
        self.result_interpretation_mode = result_interpretation_mode
        self.output_classification_mode = output_classification_mode

    def handle(
        self,
        request: QueryRequest,
        on_stage: Callable[[str], None] | None = None,
    ) -> QueryResponse:
        started_at = time.perf_counter()

        try:
            generated_sql = self.sql_generator.generate(request)
        except LlmProviderError as exc:
            raise QueryPipelineStepError(
                message=exc.message,
                phase="sql_generation",
                error_code=exc.code,
                error_detail=exc.message,
            ) from exc

        logger.info(
            "SQL pipeline generated query: datasource_id=%r datasource_ids=%r sql=%r "
            "validator=%s executor=%s",
            request.datasource_id,
            request.datasource_ids,
            generated_sql.sql,
            type(self.sql_validator).__name__,
            type(self.executor).__name__,
        )
        self.sql_validator.validate(generated_sql.sql)
        logger.info("SQL pipeline validation passed: sql=%r", generated_sql.sql)

        if on_stage is not None:
            on_stage("waiting_on_data_server")
        result = self.executor.execute(generated_sql.sql)
        logger.info(
            "SQL pipeline execution completed: sql=%r columns=%r row_count=%s",
            generated_sql.sql,
            result.columns,
            len(result.rows),
        )
        if not request.interpret:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            metadata = {
                "duration_ms": duration_ms,
                "datasource_id": request.datasource_id,
                "user_id": request.user_id,
                "confidence": generated_sql.confidence,
                "assumptions": generated_sql.assumptions,
                "sql_generation_mode": self.sql_generation_mode,
                "result_interpretation_mode": "none",
                "output_classification_mode": "none",
                "output_classification": OutputClassification.UNKNOWN.value,
                "raw_sql_output": True,
            }
            if generated_sql.prompt_metadata:
                metadata["sql_generation_prompt_metadata"] = generated_sql.prompt_metadata
            return QueryResponse(
                question=request.question,
                answer="",
                sql=generated_sql.sql,
                rows=result.rows,
                metadata=metadata,
            )

        try:
            answer = self.interpreter.interpret(
                request=request,
                result=result,
                sql=generated_sql.sql,
            )
        except LlmProviderError as exc:
            raise QueryPipelineStepError(
                message=exc.message,
                phase="result_interpretation",
                sql=generated_sql.sql,
                error_code=exc.code,
                error_detail=exc.message,
            ) from exc
        output_classification = OutputClassification.UNKNOWN
        classification_error = ""

        try:
            output_classification = self.classifier.classify(
                request=request,
                answer=answer,
            )
        except Exception as exc:
            classification_error = str(exc)

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        metadata = {
            "duration_ms": duration_ms,
            "datasource_id": request.datasource_id,
            "user_id": request.user_id,
            "confidence": generated_sql.confidence,
            "assumptions": generated_sql.assumptions,
            "sql_generation_mode": self.sql_generation_mode,
            "result_interpretation_mode": self.result_interpretation_mode,
            "output_classification_mode": self.output_classification_mode,
            "output_classification": output_classification.value,
        }

        if classification_error:
            metadata["output_classification_error"] = classification_error

        if generated_sql.prompt_metadata:
            metadata["sql_generation_prompt_metadata"] = generated_sql.prompt_metadata

        return QueryResponse(
            question=request.question,
            answer=answer,
            sql=generated_sql.sql,
            rows=result.rows,
            metadata=metadata,
        )
