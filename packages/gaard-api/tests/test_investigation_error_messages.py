from gaard_core.query_pipeline.models import QueryRequest

from gaard_api.admin.services import ACCESS_ERROR_SQL_VALIDATION
from gaard_api.api.v1.query import (
    ALLOWLIST_REFUSAL_ANSWER,
    READ_ONLY_REFUSAL_ANSWER,
    build_access_refusal_response,
)


def test_disallowed_column_validation_error_uses_allowlist_message() -> None:
    response = build_access_refusal_response(
        QueryRequest(question="show a restricted column"),
        ACCESS_ERROR_SQL_VALIDATION,
        metadata={
            "primary_error_category": "sql.validation.disallowed_column",
            "error_categories": ["sql.validation.disallowed_column"],
        },
    )

    assert response.answer == ALLOWLIST_REFUSAL_ANSWER
    assert response.answer != READ_ONLY_REFUSAL_ANSWER


def test_write_operation_validation_error_uses_read_only_message() -> None:
    response = build_access_refusal_response(
        QueryRequest(question="delete rows"),
        ACCESS_ERROR_SQL_VALIDATION,
        metadata={
            "primary_error_category": "sql.validation.write_operation",
            "error_categories": ["sql.validation.write_operation"],
        },
    )

    assert response.answer == READ_ONLY_REFUSAL_ANSWER
