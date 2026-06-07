from typing import Any, Protocol

from gaard_core.llm_output import remove_thinking_blocks
from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_core.prompt_compiler.result_interpretation_prompt import (
    ResultInterpretationPromptCompiler,
)
from gaard_core.query_pipeline.models import QueryRequest, QueryResult
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage


class ResultPromptCompiler(Protocol):
    def compile(
        self,
        request: QueryRequest,
        sql: str,
        result: QueryResult,
    ) -> CompiledPrompt:
        pass


class LlmResultInterpreter:
    def __init__(
        self,
        client: OpenAICompatibleClient,
        model: str,
        extra_body: dict[str, Any] | None = None,
        prompt_compiler: ResultPromptCompiler | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.extra_body = extra_body or {}
        self.prompt_compiler = prompt_compiler or ResultInterpretationPromptCompiler()

    def interpret(
        self,
        request: QueryRequest,
        result: QueryResult,
        sql: str = "",
    ) -> str:
        compiled_prompt = self.prompt_compiler.compile(
            request=request,
            sql=sql,
            result=result,
        )

        response = self.client.create_chat_completion(
            ChatCompletionRequest(
                model=self.model,
                temperature=0.0,
                extra_body=self.extra_body,
                messages=[
                    ChatMessage(
                        role="system",
                        content=compiled_prompt.system_prompt,
                    ),
                    ChatMessage(
                        role="user",
                        content=compiled_prompt.user_prompt,
                    ),
                ],
            )
        )

        return remove_thinking_blocks(response.content)
