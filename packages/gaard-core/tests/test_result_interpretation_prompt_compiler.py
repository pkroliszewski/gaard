from gaard_core.prompt_compiler.result_interpretation_prompt import (
    ResultInterpretationPromptCompiler,
)
from gaard_core.query_pipeline.models import QueryRequest, QueryResult


def test_result_interpretation_prompt_compiler_builds_prompt_with_question_sql_and_rows() -> None:
    compiler = ResultInterpretationPromptCompiler()

    compiled = compiler.compile(
        request=QueryRequest(question="Ilu jest aktywnych pacjentów?"),
        sql="SELECT COUNT(*) AS active_patients_count FROM patients WHERE status = 'active'",
        result=QueryResult(
            columns=["active_patients_count"],
            rows=[{"active_patients_count": 4}],
        ),
    )

    assert "GAARD Data Result Interpreter" in compiled.system_prompt
    assert "same language as the user's question" in compiled.system_prompt
    assert "Ilu jest aktywnych pacjentów?" in compiled.user_prompt
    assert "active_patients_count" in compiled.user_prompt
    assert "4" in compiled.user_prompt
    assert compiled.metadata["rows_count"] == 1
    assert compiled.metadata["columns_count"] == 1