from gaard_core.prompt_compiler.models import SqlGenerationPromptRequest
from gaard_core.prompt_compiler.sql_generation_prompt import SqlGenerationPromptCompiler
from gaard_core.schema.models import ColumnInfo, DatabaseSchema, TableInfo


def test_sql_generation_prompt_compiler_builds_prompt_with_rules_schema_and_question() -> None:
    schema = DatabaseSchema(
        tables=[
            TableInfo(
                name="patients",
                columns=[
                    ColumnInfo(name="id", type="INTEGER", nullable=True, primary_key=True),
                    ColumnInfo(name="status", type="TEXT", nullable=False),
                ],
            )
        ]
    )

    compiled = SqlGenerationPromptCompiler().compile(
        SqlGenerationPromptRequest(
            question="Ilu jest aktywnych pacjentów?",
            database_schema=schema,
            dialect="sqlite",
            max_rows=100,
        )
    )

    assert "Generate only a SELECT statement" in compiled.system_prompt
    assert "generate SQL for the sqlite dialect" in compiled.system_prompt
    assert "LIMIT 100" in compiled.system_prompt
    assert "every table must have a short, stable alias" in compiled.system_prompt
    assert "every column reference must be qualified" in compiled.system_prompt
    assert "Do not use unqualified column names in joins" in compiled.system_prompt
    assert "Do not use bind parameters" in compiled.system_prompt
    assert "executable without any external parameter binding" in compiled.system_prompt

    assert "Table: patients" in compiled.user_prompt
    assert "- status: TEXT (not null)" in compiled.user_prompt
    assert "Ilu jest aktywnych pacjentów?" in compiled.user_prompt

    assert compiled.metadata["dialect"] == "sqlite"
    assert compiled.metadata["max_rows"] == 100
    assert compiled.metadata["tables_count"] == 1
