from gaard_core.prompt_compiler.result_classification_prompt import (
    ResultClassificationPromptCompiler,
)
from gaard_core.query_pipeline.models import QueryRequest


def test_result_classification_prompt_compiler_builds_prompt_from_answer() -> None:
    compiler = ResultClassificationPromptCompiler()

    compiled = compiler.compile(
        request=QueryRequest(question="How many audit logs refer to personal data?"),
        answer="There are 12 audit logs that refer to personal data.",
    )

    assert "personal_data" in compiled.system_prompt
    assert "There are 12 audit logs" in compiled.user_prompt
    assert "rows" not in compiled.user_prompt
