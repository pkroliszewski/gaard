import re
from typing import Any

from gaard_core.query_pipeline.models import (
    ConversationContextClassification,
    ConversationContextDecision,
    QueryRequest,
)


FOLLOW_UP_PREFIXES = (
    "a ",
    "and ",
    "oraz ",
    "to samo",
    "tak samo",
    "dla ",
    "w ",
    "za ",
    "porównaj",
    "porownaj",
    "compare",
    "same",
)

AMBIGUOUS_TERMS = (
    "to",
    "tego",
    "tamto",
    "tamte",
    "them",
    "it",
    "that",
)

RESULT_REFERENCE_TERMS = (
    "ich",
    "nich",
    "tych",
    "te",
    "tej",
    "tego",
    "their",
    "them",
    "these",
    "those",
)

PROJECTION_COMMAND_PREFIXES = (
    "podaj ",
    "pokaż ",
    "pokaz ",
    "wypisz ",
    "wyświetl ",
    "wyswietl ",
    "show ",
    "list ",
    "give ",
    "return ",
)


class MockConversationContextClassifier:
    def classify(
        self,
        request: QueryRequest,
        conversation_context: dict[str, Any],
    ) -> ConversationContextClassification:
        turns = conversation_context.get("turns") or []
        if not turns:
            return ConversationContextClassification(
                decision=ConversationContextDecision.NEW_TOPIC,
                confidence=1.0,
                standalone_question=request.question,
                reason="No previous turns are available.",
            )

        question = request.question.strip()
        normalized = question.lower()
        if looks_like_follow_up(normalized) or looks_like_projection_follow_up(normalized):
            previous = latest_question(turns)
            standalone = combine_follow_up(previous, question)
            return ConversationContextClassification(
                decision=ConversationContextDecision.FOLLOW_UP,
                confidence=0.8,
                standalone_question=standalone,
                reason="The question appears to continue the previous data question.",
            )

        if normalized in AMBIGUOUS_TERMS or re.fullmatch(
            r"(a )?(co|what|why|dlaczego)\??", normalized
        ):
            return ConversationContextClassification(
                decision=ConversationContextDecision.AMBIGUOUS,
                confidence=0.45,
                standalone_question="",
                reason="The question is too short to safely resolve from context.",
            )

        return ConversationContextClassification(
            decision=ConversationContextDecision.NEW_TOPIC,
            confidence=0.9,
            standalone_question=question,
            reason="The question is self-contained.",
        )


def looks_like_follow_up(normalized_question: str) -> bool:
    return any(normalized_question.startswith(prefix) for prefix in FOLLOW_UP_PREFIXES)


def looks_like_projection_follow_up(normalized_question: str) -> bool:
    if not any(normalized_question.startswith(prefix) for prefix in PROJECTION_COMMAND_PREFIXES):
        return False

    tokens = set(re.findall(r"[\wąćęłńóśźż]+", normalized_question))
    return bool(tokens & set(RESULT_REFERENCE_TERMS))


def latest_question(turns: list[Any]) -> str:
    for turn in reversed(turns):
        if not isinstance(turn, dict):
            continue
        question = str(
            turn.get("standalone_question")
            or turn.get("question")
            or turn.get("original_question")
            or ""
        ).strip()
        if question:
            return question

    return ""


def combine_follow_up(previous_question: str, question: str) -> str:
    previous = previous_question.strip().rstrip(".?")
    current = question.strip().rstrip(".?")
    if not previous:
        return question.strip()

    return f"{previous}; kontynuacja: {current}?"
