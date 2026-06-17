from gaard_core.prompt_compiler.schema_formatter import SchemaPromptFormatter
from gaard_core.schema.models import ColumnInfo, DatabaseSchema, ForeignKeyInfo, TableInfo


def test_schema_prompt_formatter_formats_tables_columns_and_foreign_keys() -> None:
    schema = DatabaseSchema(
        tables=[
            TableInfo(
                name="appointments",
                columns=[
                    ColumnInfo(name="id", type="INTEGER", nullable=True, primary_key=True),
                    ColumnInfo(name="patient_id", type="INTEGER", nullable=False),
                ],
                foreign_keys=[
                    ForeignKeyInfo(
                        constrained_columns=["patient_id"],
                        referred_table="patients",
                        referred_columns=["id"],
                    )
                ],
            )
        ]
    )

    formatted = SchemaPromptFormatter().format(schema)

    assert "Table: appointments" in formatted
    assert "- id: INTEGER (primary key)" in formatted
    assert "- patient_id: INTEGER (not null)" in formatted
    assert "Foreign keys:" in formatted
    assert "- patient_id -> patients.id" in formatted


def test_schema_prompt_formatter_formats_views() -> None:
    schema = DatabaseSchema(
        tables=[
            TableInfo(
                name="active_patients",
                object_type="view",
                columns=[
                    ColumnInfo(name="id", type="INTEGER"),
                    ColumnInfo(name="status", type="TEXT"),
                ],
            )
        ]
    )

    formatted = SchemaPromptFormatter().format(schema)

    assert "View: active_patients" in formatted
    assert "Table: active_patients" not in formatted
    assert "- status: TEXT" in formatted
