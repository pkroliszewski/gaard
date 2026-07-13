import pytest

from gaard_core.errors import SqlValidationError
from gaard_core.sql_validator.select_only import SelectOnlySqlValidator


def test_validator_accepts_select() -> None:
    validator = SelectOnlySqlValidator()

    validator.validate("SELECT COUNT(*) FROM patients")


def test_validator_accepts_sqlite_quoted_identifier() -> None:
    validator = SelectOnlySqlValidator(dialect="sqlite")

    validator.validate("SELECT COUNT(*) AS total_leads FROM `lead`")


def test_validator_accepts_mysql_quoted_identifier() -> None:
    validator = SelectOnlySqlValidator(dialect="mysql")

    validator.validate("SELECT COUNT(*) AS total_leads FROM `lead`")


def test_validator_accepts_union_of_literal_selects() -> None:
    validator = SelectOnlySqlValidator()

    validator.validate("SELECT 'form_id' AS column_name UNION ALL SELECT 'status'")


def test_validator_rejects_delete() -> None:
    validator = SelectOnlySqlValidator()

    with pytest.raises(SqlValidationError):
        validator.validate("DELETE FROM patients")


def test_validator_rejects_multiple_statements() -> None:
    validator = SelectOnlySqlValidator()

    with pytest.raises(SqlValidationError):
        validator.validate("SELECT * FROM patients; SELECT * FROM users")


def test_validator_rejects_bind_parameters() -> None:
    validator = SelectOnlySqlValidator(dialect="mysql")

    with pytest.raises(SqlValidationError) as exc_info:
        validator.validate("SELECT * FROM `lead` WHERE source_id = :source_id")

    assert exc_info.value.metadata["primary_error_category"] == ("sql.validation.bind_parameter")
