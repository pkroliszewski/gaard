import re


def remove_thinking_blocks(value: str) -> str:
    cleaned = re.sub(
        r"<think>.*?</think>",
        "",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return cleaned.strip()