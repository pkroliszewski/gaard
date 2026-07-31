from gaard_llm.providers.models import ChatCompletionRequest, ChatCompletionResponse

from gaard_core.query_pipeline.models import OutputClassification, QueryRequest
from gaard_core.result_classifier.llm_classifier import (
    LlmResultClassifier,
    parse_output_classification,
)


class FakeLlmClient:
    def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        return ChatCompletionResponse(
            content='{"output_classification": "personal_data"}',
            model="test-model",
            raw={},
        )


class CapturingLlmClient:
    def __init__(self) -> None:
        self.requests: list[ChatCompletionRequest] = []

    def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        self.requests.append(request)

        return ChatCompletionResponse(
            content="technical_data",
            model="test-model",
            raw={},
        )


def test_llm_result_classifier_returns_model_classification() -> None:
    classifier = LlmResultClassifier(
        client=FakeLlmClient(),  # type: ignore[arg-type]
        model="test-model",
    )

    classification = classifier.classify(
        request=QueryRequest(question="How many audit logs refer to personal data?"),
        answer="There are 12 audit logs that refer to personal data.",
    )

    assert classification == OutputClassification.PERSONAL_DATA


def test_llm_result_classifier_sends_provider_extra_body() -> None:
    client = CapturingLlmClient()
    classifier = LlmResultClassifier(
        client=client,  # type: ignore[arg-type]
        model="test-model",
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": False,
            },
        },
    )

    classifier.classify(
        request=QueryRequest(question="What is the schema cache TTL?"),
        answer="The schema cache TTL is 300 seconds.",
    )

    assert client.requests[0].extra_body == {
        "chat_template_kwargs": {
            "enable_thinking": False,
        },
    }


def test_parse_output_classification_handles_thinking_blocks_and_unknown_values() -> None:
    assert (
        parse_output_classification("<think>hidden</think>\n\nsensitive-data")
        == OutputClassification.SENSITIVE_DATA
    )
    assert parse_output_classification("surprising") == OutputClassification.UNKNOWN
