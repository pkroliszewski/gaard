from gaard_core.conversation_context.llm_classifier import (
    parse_conversation_context_classification,
)
from gaard_core.conversation_context.mock_classifier import MockConversationContextClassifier
from gaard_core.query_pipeline.models import ConversationContextDecision, QueryRequest


def test_parse_conversation_context_classification_handles_aliases_and_invalid_values() -> None:
    assert (
        parse_conversation_context_classification(
            '<think>hidden</think>{"decision":"followup","confidence":0.8,'
            '"standalone_question":"How many patients in May?"}'
        ).decision
        == ConversationContextDecision.FOLLOW_UP
    )
    assert (
        parse_conversation_context_classification('{"decision":"surprising"}').decision
        == ConversationContextDecision.AMBIGUOUS
    )


def test_mock_conversation_context_classifier_rewrites_simple_follow_up() -> None:
    classifier = MockConversationContextClassifier()

    classification = classifier.classify(
        QueryRequest(question="a w maju?"),
        {
            "turns": [
                {
                    "question": "Jaka była sprzedaż w czerwcu według regionów?",
                    "standalone_question": "Jaka była sprzedaż w czerwcu według regionów?",
                }
            ]
        },
    )

    assert classification.decision == ConversationContextDecision.FOLLOW_UP
    assert "czerwcu" in classification.standalone_question
    assert "maju" in classification.standalone_question


def test_mock_conversation_context_classifier_rewrites_projection_follow_up() -> None:
    classifier = MockConversationContextClassifier()

    classification = classifier.classify(
        QueryRequest(question="show their names"),
        {
            "turns": [
                {
                    "question": "How many active patients are there?",
                    "standalone_question": "How many active patients are there?",
                }
            ]
        },
    )

    assert classification.decision == ConversationContextDecision.FOLLOW_UP
    assert "active patients" in classification.standalone_question
    assert "show their names" in classification.standalone_question


def test_mock_conversation_context_classifier_marks_short_reference_ambiguous() -> None:
    classifier = MockConversationContextClassifier()

    classification = classifier.classify(
        QueryRequest(question="to"),
        {"turns": [{"question": "How many active patients are there?"}]},
    )

    assert classification.decision == ConversationContextDecision.AMBIGUOUS
