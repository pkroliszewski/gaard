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


def test_validator_rejects_delete() -> None:
    validator = SelectOnlySqlValidator()

    with pytest.raises(SqlValidationError):
        validator.validate("DELETE FROM patients")


def test_validator_rejects_multiple_statements() -> None:
    validator = SelectOnlySqlValidator()

    with pytest.raises(SqlValidationError):
        validator.validate("SELECT * FROM patients; SELECT * FROM users")
