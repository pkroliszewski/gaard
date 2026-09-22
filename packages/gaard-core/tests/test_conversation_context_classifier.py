import json
from typing import cast

import pytest
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatCompletionResponse

from gaard_core.conversation_context.llm_classifier import (
    LlmConversationContextClassifier,
    parse_conversation_context_classification,
)
from gaard_core.conversation_context.mock_classifier import MockConversationContextClassifier
from gaard_core.errors import LlmProviderError
from gaard_core.query_pipeline.models import ConversationContextDecision, QueryRequest


class FakeClient:
    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)
        self.requests: list[ChatCompletionRequest] = []

    def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        self.requests.append(request)
        return ChatCompletionResponse(content=next(self.responses))


@pytest.mark.parametrize(
    ("response", "decision"),
    [
        ('<think>hidden</think>{"decision":"followup"}', "follow_up"),
        ('{"is_continuation":true}', "follow_up"),
        ('{"is_continuation":false}', "new_topic"),
        ('```json\n{"decision":"new_topic"}\n```', "new_topic"),
        ("true", "follow_up"),
        ("no", "new_topic"),
    ],
)
def test_parse_binary_decision(response: str, decision: str) -> None:
    result = parse_conversation_context_classification(response)
    assert result is not None
    assert result.decision.value == decision


@pytest.mark.parametrize(
    "response",
    [
        '{"decision":"ambiguous"}',
        "This continues the previous question.",
        "[]",
        "{}",
        "null",
    ],
)
def test_free_form_response_requires_llm_interpretation(response: str) -> None:
    assert parse_conversation_context_classification(response) is None


def test_classify_then_summarize_full_context_in_separate_deterministic_calls() -> None:
    summary = "How many active patients were admitted in May in Warsaw?"
    client = FakeClient('{"decision":"follow_up","reason":"Same population."}', summary)
    classifier = LlmConversationContextClassifier(
        cast(OpenAICompatibleClient, client), "test", extra_body={"temperature": 0.9}
    )
    request = QueryRequest(question="And in May?")
    context = {
        "turns": [
            {"question": f"question-{index}", "context_decision": "follow_up"} for index in range(8)
        ]
    }
    classification = classifier.classify(request, context)
    assert len(client.requests) == 1
    classification = classifier.summarize(request, context, classification)
    assert classification.decision == ConversationContextDecision.FOLLOW_UP
    assert classification.standalone_question == summary
    assert classification.model_response["summary"] == summary
    assert "summary" in classification.prompt
    for call in client.requests:
        assert call.temperature == 0
        assert call.extra_body["temperature"] == 0
        for index in range(8):
            assert f"question-{index}" in call.messages[1].content
        assert request.question in call.messages[1].content


def test_free_form_answer_is_reinterpreted_by_llm_without_ambiguous_block() -> None:
    client = FakeClient(
        "Yes, this is the same topic, but no rewrite is needed.",
        '{"decision":"follow_up"}',
        "How many patients are there?",
    )
    classifier = LlmConversationContextClassifier(cast(OpenAICompatibleClient, client), "test")
    request = QueryRequest(question="How many patients are there?")
    context = {"turns": [{"question": "How many patients were there yesterday?"}]}
    result = classifier.classify(request, context)
    result = classifier.summarize(request, context, result)
    assert result.standalone_question == request.question
    assert len(client.requests) == 3
    assert "no rewrite is needed" in client.requests[1].messages[1].content


def test_invalid_llm_output_is_provider_error_not_user_clarification() -> None:
    client = FakeClient("nonsense", '{"decision":"ambiguous"}')
    classifier = LlmConversationContextClassifier(cast(OpenAICompatibleClient, client), "test")
    with pytest.raises(LlmProviderError, match="follow_up/new_topic"):
        classifier.classify(QueryRequest(question="Next?"), {"turns": []})
    assert len(client.requests) == 2


def test_new_topic_does_not_use_legacy_rewritten_question() -> None:
    client = FakeClient(json.dumps({"decision": "new_topic", "standalone_question": "old rewrite"}))
    classifier = LlmConversationContextClassifier(cast(OpenAICompatibleClient, client), "test")
    result = classifier.classify(QueryRequest(question="New question"), {"turns": []})
    assert result.standalone_question == "New question"
    assert len(client.requests) == 1


def test_mock_mode_does_not_apply_keyword_rules() -> None:
    classifier = MockConversationContextClassifier()
    for question in ("to", "show their names", "a w maju?"):
        result = classifier.classify(
            QueryRequest(question=question), {"turns": [{"question": "Old"}]}
        )
        assert result.decision == ConversationContextDecision.NEW_TOPIC
        assert result.source == "mock"
