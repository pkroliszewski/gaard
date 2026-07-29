from gaard_llm.providers.models import ChatCompletionRequest, ChatCompletionResponse

from gaard_core.query_pipeline.models import QueryRequest, QueryResult
from gaard_core.result_interpreter.llm_interpreter import LlmResultInterpreter


class FakeLlmClient:
    def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        return ChatCompletionResponse(
            content="W bazie znajduje się 4 aktywnych pacjentów.",
            model="test-model",
            raw={},
        )


class CapturingLlmClient:
    def __init__(self) -> None:
        self.requests: list[ChatCompletionRequest] = []

    def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        self.requests.append(request)

        return ChatCompletionResponse(
            content="W bazie znajduje się 4 aktywnych pacjentów.",
            model="test-model",
            raw={},
        )


def test_llm_result_interpreter_returns_model_content() -> None:
    interpreter = LlmResultInterpreter(
        client=FakeLlmClient(),  # type: ignore[arg-type]
        model="test-model",
    )

    answer = interpreter.interpret(
        request=QueryRequest(question="Ilu jest aktywnych pacjentów?"),
        sql="SELECT COUNT(*) AS active_patients_count FROM patients WHERE status = 'active'",
        result=QueryResult(
            columns=["active_patients_count"],
            rows=[{"active_patients_count": 4}],
        ),
    )

    assert answer == "W bazie znajduje się 4 aktywnych pacjentów."


def test_llm_result_interpreter_removes_thinking_blocks() -> None:
    class ThinkingFakeLlmClient:
        def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
            return ChatCompletionResponse(
                content=(
                    "<think>\n"
                    "I need to explain the count.\n"
                    "</think>\n\n"
                    "W bazie znajduje się 4 aktywnych pacjentów."
                ),
                model="test-model",
                raw={},
            )

    interpreter = LlmResultInterpreter(
        client=ThinkingFakeLlmClient(),  # type: ignore[arg-type]
        model="test-model",
    )

    answer = interpreter.interpret(
        request=QueryRequest(question="Ilu jest aktywnych pacjentów?"),
        sql="SELECT COUNT(*) FROM patients WHERE status = 'active'",
        result=QueryResult(
            columns=["COUNT(*)"],
            rows=[{"COUNT(*)": 4}],
        ),
    )

    assert answer == "W bazie znajduje się 4 aktywnych pacjentów."


def test_llm_result_interpreter_sends_provider_extra_body() -> None:
    client = CapturingLlmClient()
    interpreter = LlmResultInterpreter(
        client=client,  # type: ignore[arg-type]
        model="test-model",
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": False,
            },
        },
    )

    interpreter.interpret(
        request=QueryRequest(question="Ilu jest aktywnych pacjentów?"),
        sql="SELECT COUNT(*) FROM patients WHERE status = 'active'",
        result=QueryResult(
            columns=["COUNT(*)"],
            rows=[{"COUNT(*)": 4}],
        ),
    )

    assert client.requests[0].extra_body == {
        "chat_template_kwargs": {
            "enable_thinking": False,
        },
    }
