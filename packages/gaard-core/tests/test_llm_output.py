from gaard_core.llm_output import remove_thinking_blocks


def test_remove_thinking_blocks_removes_qwen_think_tag() -> None:
    output = """
<think>
I should reason about the answer here.
</think>

W bazie znajduje się 4 aktywnych pacjentów.
"""

    assert remove_thinking_blocks(output) == "W bazie znajduje się 4 aktywnych pacjentów."