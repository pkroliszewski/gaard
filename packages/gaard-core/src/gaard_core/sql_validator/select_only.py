import sqlglot
from sqlglot import expressions as exp

from gaard_core.errors import SqlValidationError


class SelectOnlySqlValidator:
    def __init__(self, dialect: str | None = None) -> None:
        self.dialect = dialect

    def validate(self, sql: str) -> None:
        try:
            statements = sqlglot.parse(sql, read=self.dialect)
        except Exception as exc:
            raise SqlValidationError(f"Invalid SQL syntax. {sql}") from exc

        if len(statements) != 1:
            raise SqlValidationError(f"Only single-statement SQL queries are allowed. SQL: {sql}")

        statement = statements[0]

        if not isinstance(statement, (exp.Select, exp.SetOperation)):
            raise SqlValidationError(f"Only SELECT queries are allowed. {sql}")

        forbidden_expressions = (
            exp.Delete,
            exp.Update,
            exp.Insert,
            exp.Drop,
            exp.Create,
            exp.Alter,
            exp.Command,
        )

        for node in statement.walk():
            if isinstance(node, forbidden_expressions):
                raise SqlValidationError(f"DDL and DML statements are not allowed. {sql}")
            if isinstance(node, (exp.Placeholder, exp.Parameter)):
                raise SqlValidationError(
                    f"SQL bind parameters are not allowed. SQL: {sql}",
                    sql=sql,
                    metadata={
                        "primary_error_category": "sql.validation.bind_parameter",
                        "error_categories": ["sql.validation.bind_parameter"],
                    },
                )
