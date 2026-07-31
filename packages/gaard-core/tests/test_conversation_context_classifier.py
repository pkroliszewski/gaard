from typing import cast

from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatCompletionResponse

from gaard_core.conversation_context.llm_classifier import (
    LlmConversationContextClassifier,
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
    assert (
        parse_conversation_context_classification('{"is_continuation":false}').decision
        == ConversationContextDecision.NEW_TOPIC
    )
    assert (
        parse_conversation_context_classification('{"is_continuation":true}').decision
        == ConversationContextDecision.FOLLOW_UP
    )


def test_llm_conversation_context_classifier_exposes_prompt_and_standalone_follow_up() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.request: ChatCompletionRequest | None = None

        def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
            self.request = request
            return ChatCompletionResponse(
                content=(
                    '{"is_continuation":true,"decision":"follow_up",'
                    '"current_question_is_standalone":true,"confidence":0.91,'
                    '"standalone_question":"","reason":"Same metric, new period."}'
                )
            )

    client = FakeClient()
    classifier = LlmConversationContextClassifier(
        client=cast(OpenAICompatibleClient, client),
        model="test-model",
    )

    classification = classifier.classify(
        QueryRequest(question="ilu pacjentów przyjęto w tym tygodniu"),
        {
            "turns": [
                {
                    "question": "ilu pacjentów było przyjętych tydzień temu",
                    "answer": "12",
                },
                {
                    "question": "a dwa tygodnie temu?",
                    "standalone_question": "ilu pacjentów było przyjętych dwa tygodnie temu",
                    "answer": "9",
                },
            ]
        },
    )

    assert classification.decision == ConversationContextDecision.FOLLOW_UP
    assert classification.standalone_question == "ilu pacjentów przyjęto w tym tygodniu"
    assert classification.source == "llm"
    assert "turn_t_minus_1" in classification.prompt["user_prompt"]
    assert "logical continuation" in classification.prompt["system_prompt"]


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
