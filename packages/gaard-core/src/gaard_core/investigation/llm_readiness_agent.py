import json
from typing import Any, Protocol

from gaard_core.investigation.models import (
    InvestigationContext,
    InvestigationReadinessDecision,
    InvestigationRoute,
    RequiredAnalysisTask,
)
from gaard_core.llm_output import remove_thinking_blocks
from gaard_core.prompt_compiler.investigation_readiness_prompt import (
    InvestigationReadinessPromptCompiler,
)
from gaard_core.prompt_compiler.models import CompiledPrompt
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage


class InvestigationReadinessPromptCompilerProtocol(Protocol):
    def compile(self, context: InvestigationContext) -> CompiledPrompt:
        pass


class LlmInvestigationReadinessAgent:
    name = "llm_investigation_readiness"

    def __init__(
        self,
        client: OpenAICompatibleClient,
        model: str,
        extra_body: dict[str, Any] | None = None,
        prompt_compiler: InvestigationReadinessPromptCompilerProtocol | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.extra_body = extra_body or {}
        self.prompt_compiler = prompt_compiler or InvestigationReadinessPromptCompiler()

    def assess(self, context: InvestigationContext) -> InvestigationReadinessDecision:
        compiled_prompt = self.prompt_compiler.compile(context=context)

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

        return parse_investigation_readiness_decision(response.content)


def parse_investigation_readiness_decision(value: str) -> InvestigationReadinessDecision:
    cleaned = remove_thinking_blocks(value).strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return InvestigationReadinessDecision(
            ready_for_sql=False,
            route=InvestigationRoute.ANALYSIS,
            confidence=0.0,
            reason="Investigation readiness agent returned invalid JSON.",
            missing_information=["valid readiness JSON"],
            required_analysis=["Retry readiness assessment with a valid JSON response."],
            model_response={"raw": cleaned},
        )

    if not isinstance(payload, dict):
        return InvestigationReadinessDecision(
            ready_for_sql=False,
            route=InvestigationRoute.ANALYSIS,
            confidence=0.0,
            reason="Investigation readiness agent returned a non-object JSON value.",
            missing_information=["valid readiness JSON object"],
            required_analysis=["Retry readiness assessment with a JSON object response."],
            model_response={"raw": payload},
        )

    ready_for_sql = parse_bool(payload.get("ready_for_sql"))
    route = parse_route(payload.get("route"), ready_for_sql)

    if route == InvestigationRoute.SQL and not ready_for_sql:
        route = InvestigationRoute.ANALYSIS

    if route == InvestigationRoute.ANALYSIS:
        ready_for_sql = False

    missing_information = parse_string_list(payload.get("missing_information"))
    required_analysis = parse_string_list(payload.get("required_analysis"))

    return InvestigationReadinessDecision(
        ready_for_sql=ready_for_sql,
        route=route,
        confidence=parse_confidence(payload.get("confidence")),
        reason=str(payload.get("reason") or ""),
        missing_information=missing_information,
        required_analysis=required_analysis,
        required_analysis_tasks=parse_required_analysis_tasks(
            payload.get("required_analysis_tasks"),
            missing_information,
            required_analysis,
        ),
        assumptions=parse_string_list(payload.get("assumptions")),
        model_response=payload,
    )


def parse_route(value: object, ready_for_sql: bool) -> InvestigationRoute:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {"sql", "ready", "ready_for_sql"}:
            return InvestigationRoute.SQL
        if normalized in {"analysis", "analyze", "requires_analysis"}:
            return InvestigationRoute.ANALYSIS

    return InvestigationRoute.SQL if ready_for_sql else InvestigationRoute.ANALYSIS


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "tak", "1"}

    return False


def parse_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0

    return max(0.0, min(1.0, confidence))


def parse_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    items: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            items.append(text)

    return items


def parse_required_analysis_tasks(
    value: object,
    missing_information: list[str],
    required_analysis: list[str],
) -> list[RequiredAnalysisTask]:
    if isinstance(value, list):
        tasks = [
            parse_required_analysis_task(item)
            for item in value
            if isinstance(item, dict)
        ]
        tasks = [task for task in tasks if task.required_analysis]
        if tasks:
            return tasks

    return required_analysis_tasks_from_lists(missing_information, required_analysis)


def parse_required_analysis_task(value: dict[str, object]) -> RequiredAnalysisTask:
    return RequiredAnalysisTask(
        missing_information=str(value.get("missing_information") or "").strip(),
        required_analysis=str(value.get("required_analysis") or "").strip(),
        category=normalize_analysis_category(value.get("category")),
        expected_output=str(value.get("expected_output") or "").strip(),
    )


def required_analysis_tasks_from_lists(
    missing_information: list[str],
    required_analysis: list[str],
) -> list[RequiredAnalysisTask]:
    tasks: list[RequiredAnalysisTask] = []
    for index, analysis_question in enumerate(required_analysis):
        tasks.append(
            RequiredAnalysisTask(
                missing_information=missing_information[index]
                if index < len(missing_information)
                else "",
                required_analysis=analysis_question,
            )
        )

    return tasks


def normalize_analysis_category(value: object) -> str:
    normalized = str(value or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    allowed_categories = {
        "dictionary_value",
        "relationship_logic",
        "filter_logic",
        "aggregation_logic",
        "entity_mapping",
        "unknown",
    }

    return normalized if normalized in allowed_categories else "unknown"
