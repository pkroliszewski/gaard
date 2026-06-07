from gaard_core.query_intent.llm_classifier import (
    LlmQueryIntentClassifier,
    parse_query_intent_classification,
)
from gaard_core.query_pipeline.models import QueryIntentDecision, QueryRequest
from gaard_llm.providers.models import ChatCompletionResponse


class FakeLlmClient:
    def create_chat_completion(self, request):
        return ChatCompletionResponse(
            content=(
                '{"decision":"write_or_mutation_request",'
                '"confidence":0.97,'
                '"reason":"The user asks to update data."}'
            ),
            model="test-model",
            raw={},
        )


class CapturingLlmClient:
    def __init__(self) -> None:
        self.requests = []

    def create_chat_completion(self, request):
        self.requests.append(request)

        return ChatCompletionResponse(
            content='{"decision":"read_only_data_question","confidence":1}',
            model="test-model",
            raw={},
        )


def test_llm_query_intent_classifier_returns_model_decision() -> None:
    classifier = LlmQueryIntentClassifier(
        client=FakeLlmClient(),  # type: ignore[arg-type]
        model="test-model",
    )

    classification = classifier.classify(
        QueryRequest(question="zmodufikuj zlecenia klienta Emix")
    )

    assert classification.decision == QueryIntentDecision.WRITE_OR_MUTATION_REQUEST
    assert classification.confidence == 0.97
    assert "update data" in classification.reason
    assert classification.model_response == {
        "decision": "write_or_mutation_request",
        "confidence": 0.97,
        "reason": "The user asks to update data.",
    }


def test_llm_query_intent_classifier_sends_provider_extra_body() -> None:
    client = CapturingLlmClient()
    classifier = LlmQueryIntentClassifier(
        client=client,  # type: ignore[arg-type]
        model="test-model",
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": False,
            },
        },
    )

    classifier.classify(QueryRequest(question="How many patients are active?"))

    assert client.requests[0].extra_body == {
        "chat_template_kwargs": {
            "enable_thinking": False,
        },
    }


def test_parse_query_intent_classification_handles_aliases_and_invalid_values() -> None:
    assert (
        parse_query_intent_classification("<think>hidden</think>\n\nselect").decision
        == QueryIntentDecision.READ_ONLY_DATA_QUESTION
    )
    assert (
        parse_query_intent_classification('{"decision":"surprising"}').decision
        == QueryIntentDecision.AMBIGUOUS
    )
