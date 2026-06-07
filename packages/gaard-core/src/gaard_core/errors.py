from typing import Any


class GaardError(Exception):
    code = "GAARD_ERROR"
    status_code = 500

    def __init__(self, message: str | None = None) -> None:
        self.message = message or "GAARD error."
        super().__init__(self.message)


class ConfigurationError(GaardError):
    code = "CONFIGURATION_ERROR"
    status_code = 500


class SqlGenerationError(GaardError):
    code = "SQL_GENERATION_ERROR"
    status_code = 502


class SqlValidationError(GaardError):
    code = "SQL_VALIDATION_ERROR"
    status_code = 400

    def __init__(
        self,
        message: str | None = None,
        sql: str = "",
        error_detail: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.sql = sql
        self.error_detail = error_detail
        self.metadata = metadata or {}
        super().__init__(message)


class QueryExecutionError(GaardError):
    code = "QUERY_EXECUTION_ERROR"
    status_code = 400

    def __init__(
        self,
        message: str | None = None,
        sql: str = "",
        error_detail: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.sql = sql
        self.error_detail = error_detail
        self.metadata = metadata or {}
        super().__init__(message)


class QueryPipelineStepError(GaardError):
    status_code = 502

    def __init__(
        self,
        message: str | None = None,
        phase: str = "",
        sql: str = "",
        error_code: str = "QUERY_PIPELINE_STEP_ERROR",
        error_detail: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.code = error_code
        self.phase = phase
        self.sql = sql
        self.error_detail = error_detail
        self.metadata = metadata or {}
        super().__init__(message)


class LlmProviderError(GaardError):
    code = "LLM_PROVIDER_ERROR"
    status_code = 502
