from gaard_llm.providers.models import ChatCompletionRequest, ChatCompletionResponse

from gaard_core.query_pipeline.llm_sql_generator import LlmSqlGenerator
from gaard_core.query_pipeline.models import QueryRequest


class CapturingClient:
    def __init__(self) -> None:
        self.requests: list[ChatCompletionRequest] = []

    def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        self.requests.append(request)

        return ChatCompletionResponse(
            content="SELECT 1 AS value;",
            model=request.model,
            raw={},
        )


class DummyClient:
    pass


def test_llm_sql_generator_cleans_sql_markdown_fence() -> None:
    generator = LlmSqlGenerator(
        client=DummyClient(),  # type: ignore[arg-type]
        model="test-model",
        formatted_schema="No tables available.",
    )

    cleaned = generator._clean_sql(
        """
        ```sql
        SELECT COUNT(*) AS patients_count FROM patients;
        ```
        """
    )

    assert cleaned == "SELECT COUNT(*) AS patients_count FROM patients;"


def test_llm_sql_generator_uses_empty_extra_body_by_default() -> None:
    client = CapturingClient()
    generator = LlmSqlGenerator(
        client=client,  # type: ignore[arg-type]
        model="test-model",
        formatted_schema="No tables available.",
    )

    generator.generate(QueryRequest(question="Return one value."))

    assert client.requests[0].extra_body == {}


def test_llm_sql_generator_sends_provider_extra_body() -> None:
    client = CapturingClient()
    generator = LlmSqlGenerator(
        client=client,  # type: ignore[arg-type]
        model="test-model",
        formatted_schema="No tables available.",
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": False,
            },
        },
    )

    generator.generate(QueryRequest(question="Return one value."))

    assert client.requests[0].extra_body == {
        "chat_template_kwargs": {
            "enable_thinking": False,
        },
    }
