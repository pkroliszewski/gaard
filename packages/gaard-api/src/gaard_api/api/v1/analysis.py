from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Iterator, Protocol
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from gaard_core.llm_output import remove_thinking_blocks
from gaard_core.query_pipeline.models import OutputClassification, QueryRequest, QueryResponse
from gaard_llm.openai_compatible.client import OpenAICompatibleClient
from gaard_llm.providers.models import ChatCompletionRequest, ChatMessage

from gaard_api.admin.database import create_session
from gaard_api.admin.models import AnalysisSessionRecord
from gaard_api.admin.services import (
    get_active_business_logic_prompt_safe,
    get_llm_runtime_config_safe,
    get_query_runtime_config_safe,
    json_dumps,
    upsert_analysis_business_logic_suggestion,
)
from gaard_api.api.v1.query import (
    create_llm_client,
    effective_query_request,
    normalize_datasource_contexts,
    ndjson_line,
    run_sql_request,
)
from gaard_api.query_hooks import DatasourceContext, DatasourceContexts

router = APIRouter()


class AnalysisAction(StrEnum):
    ANSWER_FROM_CONTEXT = "answer_from_context"
    ASK_USER = "ask_user"
    ASK_DATABASE = "ask_database"
    RUN_FINAL_QUERY = "run_final_query"
    OUT_OF_SCOPE = "out_of_scope"


class AnalysisBusinessLogicFinding(BaseModel):
    create_suggestion: bool = False
    knowledge_type: str = "finding"
    title: str = ""
    rule_text: str = ""
    terms: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class AnalysisPlannerDecision(BaseModel):
    action: AnalysisAction
    visible_question: str = ""
    visible_reasoning: str = ""
    user_question: str = ""
    database_question: str = ""
    final_question: str = ""
    answer: str = ""
    business_logic: AnalysisBusinessLogicFinding = Field(
        default_factory=AnalysisBusinessLogicFinding
    )


class AnalysisMessageRequest(BaseModel):
    message: str = Field(min_length=1)


class AnalysisPlanner(Protocol):
    def decide(
        self,
        request: QueryRequest,
        datasource_context: DatasourceContext | DatasourceContexts | None,
        context: dict[str, Any],
    ) -> AnalysisPlannerDecision:
        pass


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def json_list(value: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []

    return payload if isinstance(payload, list) else []


def json_object(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}

    return payload if isinstance(payload, dict) else {}


def default_analysis_context(request: QueryRequest) -> dict[str, Any]:
    return {
        "original_question": request.question,
        "datasource_id": request.datasource_id,
        "user_id": request.user_id,
        "messages": [
            {
                "role": "user",
                "content": request.question,
                "occurred_at": utc_iso(),
            }
        ],
        "observations": [],
        "decisions": [],
    }


def create_analysis_session_record(request: QueryRequest) -> AnalysisSessionRecord:
    session_id = uuid4().hex
    record = AnalysisSessionRecord(
        session_id=session_id,
        status="running",
        user_id=request.user_id,
        datasource_id=request.datasource_id,
        question=request.question,
        context_json=json_dumps(default_analysis_context(request)),
        events_json=json_dumps([]),
    )

    with create_session() as session:
        session.add(record)
        session.commit()
        session.refresh(record)

    return record


def load_analysis_session_record(session_id: str) -> AnalysisSessionRecord | None:
    with create_session() as session:
        return session.scalar(
            select(AnalysisSessionRecord).where(
                AnalysisSessionRecord.session_id == session_id
            )
        )


def serialize_analysis_session(record: AnalysisSessionRecord) -> dict[str, Any]:
    return {
        "session_id": record.session_id,
        "status": record.status,
        "user_id": record.user_id,
        "datasource_id": record.datasource_id,
        "question": record.question,
        "context": json_object(record.context_json),
        "events": json_list(record.events_json),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def save_analysis_context(
    session_id: str,
    context: dict[str, Any],
    status_value: str | None = None,
) -> None:
    with create_session() as session:
        record = session.scalar(
            select(AnalysisSessionRecord).where(
                AnalysisSessionRecord.session_id == session_id
            )
        )
        if record is None:
            return
        record.context_json = json_dumps(context)
        if status_value is not None:
            record.status = status_value
        session.commit()


def append_analysis_event(
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    with create_session() as session:
        record = session.scalar(
            select(AnalysisSessionRecord).where(
                AnalysisSessionRecord.session_id == session_id
            )
        )
        if record is None:
            sequence = 1
            event = {
                "event": event_type,
                "session_id": session_id,
                "sequence": sequence,
                "occurred_at": utc_iso(),
                event_type: payload,
            }
            return event

        events = json_list(record.events_json)
        sequence = len(events) + 1
        event = {
            "event": event_type,
            "session_id": session_id,
            "sequence": sequence,
            "occurred_at": utc_iso(),
            event_type: payload,
        }
        events.append(event)
        record.events_json = json_dumps(events)
        session.commit()
        return event


def stream_event(session_id: str, event_type: str, payload: dict[str, Any]) -> str:
    return ndjson_line(append_analysis_event(session_id, event_type, payload))


def analysis_knowledge(
    datasource_context: DatasourceContext | DatasourceContexts | None,
) -> tuple[str, str]:
    datasource_contexts = normalize_datasource_contexts(datasource_context)
    if not datasource_contexts:
        return "", ""

    connector, schema_cache = datasource_contexts[0]
    return (
        schema_cache.formatted_schema or schema_cache.schema_json,
        get_active_business_logic_prompt_safe(connector.id),
    )


def first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text

    return ""


DATABASE_EVIDENCE_TERMS = (
    "distinct",
    "unique",
    "stored in",
    "database",
    "table",
    "column",
    "values",
    "dictionary",
    "specialization",
    "specialisations",
    "specializations",
    "baza",
    "bazie",
    "tabela",
    "tabeli",
    "kolumna",
    "kolumnie",
    "wartość",
    "wartości",
    "wartosci",
    "słownik",
    "slownik",
    "specjalizacja",
    "specjalizacje",
)

DATABASE_ENUM_TERMS = (
    "status",
    "category",
    "categories",
    "type",
    "types",
    "kategoria",
    "kategorie",
)

DATABASE_LOCATION_TERMS = (
    "stored",
    "database",
    "table",
    "column",
    "records",
    "rows",
    "baza",
    "bazie",
    "tabela",
    "tabeli",
    "kolumna",
    "kolumnie",
    "rekord",
    "wiersz",
)


def looks_like_database_evidence_question(value: str) -> bool:
    normalized = value.lower()
    if not normalized:
        return False

    if any(term in normalized for term in DATABASE_EVIDENCE_TERMS):
        return True

    return any(term in normalized for term in DATABASE_ENUM_TERMS) and any(
        term in normalized for term in DATABASE_LOCATION_TERMS
    )


def coerce_database_evidence_decision(
    decision: AnalysisPlannerDecision,
) -> AnalysisPlannerDecision:
    if decision.action != AnalysisAction.ASK_USER:
        return decision

    user_question = first_non_empty(
        decision.user_question,
        decision.database_question,
        decision.visible_question,
    )
    if not looks_like_database_evidence_question(user_question):
        return decision

    return decision.model_copy(
        update={
            "action": AnalysisAction.ASK_DATABASE,
            "user_question": "",
            "database_question": user_question,
            "visible_reasoning": (
                "To pytanie dotyczy danych zapisanych w bazie, więc sprawdzam bazę "
                "zamiast pytać użytkownika."
            ),
        }
    )


def out_of_scope_answer(decision: AnalysisPlannerDecision) -> str:
    return first_non_empty(
        decision.answer,
        (
            "Nie widzę w bieżącym źródle danych ani w zapisanej logice biznesowej "
            "podstaw do odpowiedzi na to pytanie. Wygląda na to, że temat jest poza "
            "zakresem podłączonej bazy, więc nie wykonuję zapytania SQL."
        ),
    )


def answer_from_context(decision: AnalysisPlannerDecision) -> str:
    return first_non_empty(
        decision.answer,
        (
            "Na podstawie dostępnego schematu, logiki biznesowej i kontekstu sesji "
            "nie muszę wykonywać dodatkowego zapytania SQL, ale nie udało mi się "
            "przygotować pełniejszej odpowiedzi."
        ),
    )


SUPPORTING_DATA_MISMATCH_TERMS = (
    "available data only",
    "can't determine",
    "cannot determine",
    "current database only",
    "does not contain",
    "does not include",
    "doesn't contain",
    "doesn't include",
    "not available",
    "not captured",
    "not enough data",
    "not in the current database",
    "not in current database",
    "not present",
    "not recorded",
    "not stored",
    "not tracked",
    "only contains",
    "only includes",
    "schema only contains",
    "unable to determine",
    "brak danych",
    "nie da się ustalić",
    "nie jest dostęp",
    "nie jest zapis",
    "nie ma danych",
    "nie mogę ustalić",
    "nie obejmuje",
    "nie odnotow",
    "nie pozwala",
    "nie zapisano",
    "nie zawiera",
    "poza zakresem",
    "tylko zawiera",
    "zawiera tylko",
)


def supporting_data_suppression_reason(
    decision: AnalysisPlannerDecision,
    answer: str,
) -> str:
    final_text = "\n".join(
        [
            answer,
            decision.visible_question,
            decision.visible_reasoning,
        ]
    ).lower()

    if any(term in final_text for term in SUPPORTING_DATA_MISMATCH_TERMS):
        return "final_answer_says_supporting_data_is_not_applicable"

    return ""


def select_final_supporting_observation(
    decision: AnalysisPlannerDecision,
    context: dict[str, Any],
    answer: str,
) -> tuple[dict[str, Any] | None, str]:
    if decision.action == AnalysisAction.OUT_OF_SCOPE:
        return None, "out_of_scope"

    observation = latest_observation_with_rows(context)
    if observation is None:
        return None, "no_supporting_rows"

    suppression_reason = supporting_data_suppression_reason(decision, answer)
    if suppression_reason:
        return None, suppression_reason

    return observation, ""


def observation_learning_key(observation: dict[str, Any]) -> str:
    metadata = observation.get("metadata") or {}
    audit_id = metadata.get("data_query_audit_id")
    if audit_id is not None:
        return f"audit:{audit_id}"

    return "|".join(
        [
            str(observation.get("question") or ""),
            str(observation.get("sql") or ""),
        ]
    )


def latest_observation(context: dict[str, Any]) -> dict[str, Any] | None:
    observations = context.get("observations") or []
    if not observations:
        return None

    observation = observations[-1]
    return observation if isinstance(observation, dict) else None


def latest_observation_with_rows(context: dict[str, Any]) -> dict[str, Any] | None:
    for observation in reversed(context.get("observations") or []):
        if not isinstance(observation, dict):
            continue
        rows = observation.get("rows")
        if isinstance(rows, list) and rows:
            return observation

    return None


def observed_columns(rows: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for column in row:
            if column not in columns:
                columns.append(column)

    return columns


def observed_values(rows: list[dict[str, Any]], limit: int = 20) -> list[str]:
    values: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for value in row.values():
            if value is None or isinstance(value, (dict, list)):
                continue
            text = str(value).strip()
            if text and text not in values:
                values.append(text)
            if len(values) >= limit:
                return values

    return values


def observation_looks_durable(observation: dict[str, Any]) -> bool:
    question = str(observation.get("question") or "")
    sql = str(observation.get("sql") or "")
    rows = observation.get("rows")

    if not isinstance(rows, list) or not rows:
        return False

    text = f"{question}\n{sql}"
    if looks_like_database_evidence_question(text):
        return True

    normalized_sql = sql.lower()
    return "distinct" in normalized_sql or "group by" in normalized_sql


def observation_terms(observation: dict[str, Any], columns: list[str]) -> list[str]:
    text = f"{observation.get('question') or ''} {observation.get('sql') or ''}"
    terms = columns[:]
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text):
        if token.lower() in {"select", "from", "where", "distinct", "count", "group"}:
            continue
        if token not in terms:
            terms.append(token)
        if len(terms) >= 12:
            break

    return terms


def infer_business_logic_from_latest_observation(
    context: dict[str, Any],
) -> AnalysisBusinessLogicFinding | None:
    observation = latest_observation(context)
    if observation is None or not observation_looks_durable(observation):
        return None

    learning_keys = context.get("business_logic_learning_keys") or []
    key = observation_learning_key(observation)
    if key in learning_keys:
        return None

    rows = observation.get("rows")
    if not isinstance(rows, list):
        return None

    columns = observed_columns(rows)
    values = observed_values(rows)
    if not values:
        return None

    question = str(observation.get("question") or "analysis database question").strip()
    value_text = ", ".join(values)
    if len(values) >= 20:
        value_text = f"{value_text}, ..."

    column_text = ", ".join(columns) if columns else "the queried columns"
    return AnalysisBusinessLogicFinding(
        create_suggestion=True,
        knowledge_type="dictionary_value",
        title=f"Analysis finding: {question[:180]}",
        rule_text=(
            f"For the question '{question}', the datasource returned durable "
            f"dictionary-like values in {column_text}: {value_text}."
        ),
        terms=observation_terms(observation, columns),
        confidence=0.65,
    )


def business_logic_decision_for_current_context(
    decision: AnalysisPlannerDecision,
    context: dict[str, Any],
) -> AnalysisPlannerDecision:
    if decision.business_logic.create_suggestion:
        return decision

    inferred = infer_business_logic_from_latest_observation(context)
    if inferred is None:
        return decision

    return decision.model_copy(update={"business_logic": inferred})


def mark_latest_observation_business_logic_saved(context: dict[str, Any]) -> None:
    observation = latest_observation(context)
    if observation is None:
        return

    key = observation_learning_key(observation)
    learning_keys = context.setdefault("business_logic_learning_keys", [])
    if key not in learning_keys:
        learning_keys.append(key)


class MockAnalysisPlanner:
    def decide(
        self,
        request: QueryRequest,
        datasource_context: DatasourceContext | DatasourceContexts | None,
        context: dict[str, Any],
    ) -> AnalysisPlannerDecision:
        question = request.question.lower()
        observations = context.get("observations") or []
        messages = context.get("messages") or []

        if observations:
            last_observation = observations[-1]
            finding = AnalysisBusinessLogicFinding()
            if any(
                term in question
                for term in (
                    "słownik",
                    "slownik",
                    "wartości",
                    "wartosci",
                    "values",
                    "distinct",
                    "specialization",
                    "specjaliz",
                )
            ):
                finding = AnalysisBusinessLogicFinding(
                    create_suggestion=True,
                    knowledge_type="dictionary_value",
                    title="Analysis dictionary finding",
                    rule_text=(
                        "Analysis verified a dictionary-like value from the datasource; "
                        "reuse this finding when similar terminology appears."
                    ),
                    terms=["analysis", "dictionary"],
                    confidence=0.7,
                )

            return AnalysisPlannerDecision(
                action=AnalysisAction.ANSWER_FROM_CONTEXT,
                visible_question="Czy wynik pomocniczego pytania wystarcza do odpowiedzi?",
                visible_reasoning="Mam już wynik pomocniczego sprawdzenia z bazy.",
                answer=(
                    "Na podstawie sprawdzenia bazy: "
                    f"{last_observation.get('answer') or 'zapytanie zwróciło dane.'}"
                ),
                business_logic=finding,
            )

        if any(term in question for term in ("toyota", "samochód", "samochod")):
            return AnalysisPlannerDecision(
                action=AnalysisAction.ANSWER_FROM_CONTEXT,
                visible_question="Czy schemat bazy dotyczy pytanego obszaru?",
                visible_reasoning=(
                    "Dostępna wiedza o schemacie nie wskazuje na domenę samochodową."
                ),
                answer=(
                    "Nie widzę w bieżącym schemacie danych dotyczących samochodów. "
                    "Ta baza wygląda na inną domenę, więc nie wykonuję SQL."
                ),
            )

        if any(term in question for term in ("doprecyzuj", "dopytaj", "jaki okres")) and len(
            messages
        ) == 1:
            return AnalysisPlannerDecision(
                action=AnalysisAction.ASK_USER,
                visible_question="Czy potrzebuję doprecyzowania od użytkownika?",
                visible_reasoning="Brakuje jednoznacznego zakresu pytania.",
                user_question="Doprecyzuj proszę zakres, który mam przeanalizować.",
            )

        if len(messages) > 1:
            return AnalysisPlannerDecision(
                action=AnalysisAction.RUN_FINAL_QUERY,
                visible_question="Czy po doprecyzowaniu mogę zadać finalne pytanie do bazy?",
                visible_reasoning="Mam już odpowiedź użytkownika i mogę użyć normalnego query.",
                final_question=f"{request.question} ({messages[-1].get('content')})",
            )

        if any(
            term in question
            for term in (
                "słownik",
                "slownik",
                "wartości",
                "wartosci",
                "values",
                "distinct",
                "specialization",
                "specjaliz",
            )
        ):
            return AnalysisPlannerDecision(
                action=AnalysisAction.ASK_DATABASE,
                visible_question="Czy muszę sprawdzić wartość w bazie?",
                visible_reasoning="Pytanie wygląda na wymagające sprawdzenia wartości danych.",
                database_question=request.question,
            )

        return AnalysisPlannerDecision(
            action=AnalysisAction.RUN_FINAL_QUERY,
            visible_question="Czy mogę zadać finalne pytanie do bazy?",
            visible_reasoning="Pytanie wygląda na możliwe do obsłużenia normalnym query.",
            final_question=request.question,
        )


class LlmAnalysisPlanner:
    def __init__(
        self,
        client: OpenAICompatibleClient,
        model: str,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.extra_body = extra_body or {}

    def decide(
        self,
        request: QueryRequest,
        datasource_context: DatasourceContext | DatasourceContexts | None,
        context: dict[str, Any],
    ) -> AnalysisPlannerDecision:
        schema, business_logic = analysis_knowledge(datasource_context)
        response = self.client.create_chat_completion(
            ChatCompletionRequest(
                model=self.model,
                temperature=0.0,
                extra_body=self.extra_body,
                messages=[
                    ChatMessage(role="system", content=self.system_prompt()),
                    ChatMessage(
                        role="user",
                        content=self.user_prompt(
                            request=request,
                            schema=schema,
                            business_logic=business_logic,
                            context=context,
                        ),
                    ),
                ],
            )
        )

        return parse_analysis_decision(response.content)

    @staticmethod
    def system_prompt() -> str:
        return """You are GAARD Analysis Planner.

You orchestrate analysis over an existing database schema, approved business logic, constraints and session history.
You do not generate SQL yourself. When database evidence is needed, write a normal natural-language database question that the GAARD query endpoint can answer.

Allowed actions:
- answer_from_context: answer from schema, business logic, constraints and session context without querying the database.
- ask_user: ask the user one concise clarification question and wait.
- ask_database: ask one natural-language database question to collect missing evidence, then continue analysis.
- run_final_query: ask one natural-language final question through the normal query endpoint with interpretation enabled.
- out_of_scope: explain that the available datasource does not cover the user's topic.

Decision rules:
- Prefer ask_database over ask_user whenever the missing information is a value, list, category, count, sample, distinct value, table content, column content, dictionary value, or anything the schema/data can answer.
- Use ask_user only for ambiguity in the user's intent, missing timeframe/scope, missing business definition, or external context that is not available in schema, business logic, session history, or the database.
- Never ask the user to manually answer a question that explicitly mentions table, column, database, stored values, distinct values, or data contents.
- If action is out_of_scope, answer must contain a concise, user-facing explanation.

Business logic learning:
- If the latest database observation reveals durable knowledge such as dictionary values, terminology meaning, table meaning, column meaning, or stable domain rules, include business_logic.create_suggestion=true.
- Distinct values, enumerations, statuses, categories, and table/column meaning discovered through ask_database should be saved as business logic findings unless they are clearly one-off metrics for the current answer.
- Do not create business logic for one-off answer values that are only useful for the current final answer.

Visibility and safety:
- Do not reveal hidden chain-of-thought.
- visible_question must show the user what question GAARD is asking itself.
- visible_reasoning must be a short user-facing rationale, not raw reasoning.
- Return JSON only. Do not use markdown or code fences.

Return this exact JSON shape:
{
  "action": "answer_from_context",
  "visible_question": "short user-visible self-question",
  "visible_reasoning": "short user-visible rationale",
  "user_question": "",
  "database_question": "",
  "final_question": "",
  "answer": "",
  "business_logic": {
    "create_suggestion": false,
    "knowledge_type": "finding",
    "title": "",
    "rule_text": "",
    "terms": [],
    "confidence": 0.0
  }
}"""

    @staticmethod
    def user_prompt(
        request: QueryRequest,
        schema: str,
        business_logic: str,
        context: dict[str, Any],
    ) -> str:
        payload = {
            "user_question": request.question,
            "datasource_id": request.datasource_id,
            "schema": schema,
            "business_logic": business_logic,
            "session_context": context,
        }
        return (
            "Choose the next analysis action for this session.\n\n"
            f"Input JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}"
        )


def strip_json_fence(value: str) -> str:
    cleaned = remove_thinking_blocks(value).strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()
    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()
    return cleaned


def parse_analysis_decision(value: str) -> AnalysisPlannerDecision:
    try:
        payload = json.loads(strip_json_fence(value))
    except json.JSONDecodeError as exc:
        raise ValueError("LLM returned non-JSON analysis planner output.") from exc

    return AnalysisPlannerDecision.model_validate(payload)


def create_analysis_planner() -> AnalysisPlanner:
    runtime_config = get_query_runtime_config_safe()

    if runtime_config.sql_generation_mode != "llm":
        return MockAnalysisPlanner()

    llm_config = get_llm_runtime_config_safe()
    return LlmAnalysisPlanner(
        client=create_llm_client(llm_config),
        model=llm_config.model,
        extra_body=llm_config.extra_body,
    )


def query_response_observation(response: QueryResponse) -> dict[str, Any]:
    return {
        "question": response.question,
        "answer": response.answer,
        "sql": response.sql,
        "rows": response.rows,
        "metadata": response.metadata,
    }


def latest_observation_audit_id(context: dict[str, Any]) -> int | None:
    for observation in reversed(context.get("observations") or []):
        metadata = observation.get("metadata") or {}
        audit_id = metadata.get("data_query_audit_id")
        if isinstance(audit_id, int):
            return audit_id
    return None


def save_business_logic_finding(
    session_id: str,
    decision: AnalysisPlannerDecision,
    context: dict[str, Any],
    datasource_context: DatasourceContext | DatasourceContexts | None,
) -> dict[str, Any] | None:
    finding = decision.business_logic
    if not finding.create_suggestion or not finding.rule_text.strip():
        return None

    datasource_contexts = normalize_datasource_contexts(datasource_context)
    if not datasource_contexts:
        return {
            "status": "skipped",
            "reason": "No active datasource connector was available.",
        }

    connector, _schema_cache = datasource_contexts[0]
    runtime_config = get_query_runtime_config_safe()
    with create_session() as session:
        suggestion = upsert_analysis_business_logic_suggestion(
            session=session,
            connector_id=connector.id,
            source_audit_id=latest_observation_audit_id(context),
            title=finding.title,
            rule_text=finding.rule_text,
            knowledge_type=finding.knowledge_type,
            terms=finding.terms,
            confidence=finding.confidence,
            auto_enable=runtime_config.analysis_auto_enable_business_logic,
            actor=f"analysis:{session_id}",
        )
        session.commit()
        return {
            "status": "active" if suggestion.enabled else "pending_approval",
            "suggestion_id": suggestion.id,
            "title": suggestion.title,
            "rule_text": suggestion.rule_text,
            "enabled": suggestion.enabled,
            "error_category": suggestion.error_category,
            "confidence": suggestion.confidence,
        }


def metadata_for_analysis(
    session_id: str,
    step: str,
    original_question: str,
) -> dict[str, Any]:
    return {
        "analysis_session_id": session_id,
        "analysis_step": step,
        "analysis_original_question": original_question,
    }


def final_metadata(
    session_id: str,
    status_value: str,
    iterations: int,
) -> dict[str, Any]:
    return {
        "analysis_mode": "analysis",
        "analysis_session_id": session_id,
        "analysis_status": status_value,
        "analysis_iterations": iterations,
    }


def run_analysis_loop(
    session_id: str,
    request: QueryRequest,
    datasource_context: DatasourceContext | DatasourceContexts | None,
) -> Iterator[str]:
    planner = create_analysis_planner()
    runtime_config = get_query_runtime_config_safe()
    max_iterations = runtime_config.analysis_loop_count

    record = load_analysis_session_record(session_id)
    if record is None:
        yield ndjson_line(
            {
                "event": "error",
                "session_id": session_id,
                "error": {"message": "Analysis session not found."},
            }
        )
        return

    context = json_object(record.context_json)
    original_question = str(context.get("original_question") or request.question)

    try:
        for iteration in range(1, max_iterations + 1):
            decision = coerce_database_evidence_decision(
                planner.decide(request, datasource_context, context)
            )
            context.setdefault("decisions", []).append(decision.model_dump(mode="json"))
            save_analysis_context(session_id, context, "running")

            yield stream_event(
                session_id,
                "analysis_step",
                {
                    "iteration": iteration,
                    "visible_question": decision.visible_question,
                    "visible_reasoning": decision.visible_reasoning,
                },
            )
            yield stream_event(
                session_id,
                "decision",
                decision.model_dump(mode="json"),
            )

            business_logic_decision = business_logic_decision_for_current_context(
                decision,
                context,
            )
            business_logic_payload = save_business_logic_finding(
                session_id,
                business_logic_decision,
                context,
                datasource_context,
            )
            if business_logic_payload is not None:
                if business_logic_payload.get("status") != "skipped":
                    mark_latest_observation_business_logic_saved(context)
                    save_analysis_context(session_id, context, "running")
                yield stream_event(
                    session_id,
                    "business_logic_suggestion",
                    business_logic_payload,
                )

            if decision.action == AnalysisAction.ASK_USER:
                user_question = first_non_empty(
                    decision.user_question,
                    decision.visible_question,
                )
                save_analysis_context(session_id, context, "waiting_for_user")
                yield stream_event(
                    session_id,
                    "user_question",
                    {"question": user_question},
                )
                return

            if decision.action == AnalysisAction.ASK_DATABASE:
                database_question = decision.database_question or request.question
                yield stream_event(
                    session_id,
                    "database_question",
                    {"question": database_question, "iteration": iteration},
                )
                analysis_request = request.model_copy(
                    update={"question": database_question, "interpret": True}
                )
                response = run_sql_request(
                    analysis_request,
                    datasource_context,
                    metadata_for_analysis(
                        session_id,
                        "database_question",
                        original_question,
                    ),
                )
                observation = query_response_observation(response)
                context.setdefault("observations", []).append(observation)
                save_analysis_context(session_id, context, "running")
                yield stream_event(
                    session_id,
                    "database_result",
                    {
                        "question": database_question,
                        "answer": response.answer,
                        "sql": response.sql,
                        "rows": response.rows,
                        "metadata": response.metadata,
                    },
                )
                continue

            if decision.action == AnalysisAction.RUN_FINAL_QUERY:
                final_question = decision.final_question or request.question
                yield stream_event(
                    session_id,
                    "database_question",
                    {
                        "question": final_question,
                        "iteration": iteration,
                        "final": True,
                    },
                )
                final_request = request.model_copy(
                    update={"question": final_question, "interpret": True}
                )
                response = run_sql_request(
                    final_request,
                    datasource_context,
                    metadata_for_analysis(session_id, "final_query", original_question),
                )
                response.metadata.update(final_metadata(session_id, "completed", iteration))
                save_analysis_context(session_id, context, "completed")
                yield stream_event(session_id, "final", response.model_dump(mode="json"))
                return

            if decision.action in {
                AnalysisAction.ANSWER_FROM_CONTEXT,
                AnalysisAction.OUT_OF_SCOPE,
            }:
                answer = (
                    out_of_scope_answer(decision)
                    if decision.action == AnalysisAction.OUT_OF_SCOPE
                    else answer_from_context(decision)
                )
                (
                    supporting_observation,
                    supporting_suppression_reason,
                ) = select_final_supporting_observation(
                    decision,
                    context,
                    answer,
                )
                supporting_metadata = (
                    supporting_observation.get("metadata") or {}
                    if supporting_observation
                    else {}
                )
                supporting_sql = (
                    str(supporting_observation.get("sql") or "")
                    if supporting_observation
                    else ""
                )
                supporting_rows = (
                    supporting_observation.get("rows") or []
                    if supporting_observation
                    else []
                )
                response = QueryResponse(
                    question=request.question,
                    answer=answer,
                    sql=supporting_sql,
                    rows=supporting_rows,
                    metadata={
                        **final_metadata(session_id, "completed", iteration),
                        "datasource_id": supporting_metadata.get(
                            "datasource_id",
                            request.datasource_id,
                        ),
                        "output_classification": OutputClassification.UNKNOWN.value,
                        "analysis_supporting_data": bool(supporting_observation),
                        "analysis_supporting_data_suppressed_reason": (
                            supporting_suppression_reason
                        ),
                        "analysis_supporting_question": (
                            supporting_observation.get("question") or ""
                            if supporting_observation
                            else ""
                        ),
                        "analysis_supporting_step": supporting_metadata.get(
                            "analysis_step",
                            "",
                        ),
                    },
                )
                save_analysis_context(session_id, context, "completed")
                yield stream_event(session_id, "final", response.model_dump(mode="json"))
                return

        response = QueryResponse(
            question=request.question,
            answer=(
                "Nie udało mi się zakończyć analizy w skonfigurowanym limicie kroków."
            ),
            sql="",
            rows=[],
            metadata={
                **final_metadata(session_id, "limit_reached", max_iterations),
                "output_classification": OutputClassification.UNKNOWN.value,
            },
        )
        save_analysis_context(session_id, context, "limit_reached")
        yield stream_event(
            session_id,
            "limit_reached",
            {"analysis_loop_count": max_iterations},
        )
        yield stream_event(session_id, "final", response.model_dump(mode="json"))
    except Exception as exc:
        save_analysis_context(session_id, context, "failed")
        yield stream_event(
            session_id,
            "error",
            {
                "message": str(exc),
                "error_type": exc.__class__.__name__,
            },
        )


def start_stream_for_record(
    record: AnalysisSessionRecord,
    request: QueryRequest,
    datasource_context: DatasourceContexts,
    resumed: bool = False,
) -> Iterator[str]:
    event_name = "session_resumed" if resumed else "session_started"
    yield stream_event(record.session_id, event_name, serialize_analysis_session(record))
    yield from run_analysis_loop(record.session_id, request, datasource_context)


@router.post("/analysis/stream")
def analysis_stream(request: QueryRequest) -> StreamingResponse:
    effective_request, datasource_context = effective_query_request(request)
    record = create_analysis_session_record(effective_request)

    return StreamingResponse(
        start_stream_for_record(record, effective_request, datasource_context),
        media_type="application/x-ndjson",
    )


@router.post("/analysis/{session_id}/messages/stream")
def analysis_message_stream(
    session_id: str,
    request: AnalysisMessageRequest,
) -> StreamingResponse:
    record = load_analysis_session_record(session_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis session not found.",
        )

    context = json_object(record.context_json)
    context.setdefault("messages", []).append(
        {
            "role": "user",
            "content": request.message,
            "occurred_at": utc_iso(),
        }
    )
    save_analysis_context(session_id, context, "running")
    query_request = QueryRequest(
        question=record.question,
        datasource_id=record.datasource_id,
        user_id=record.user_id,
    )
    effective_request, datasource_context = effective_query_request(query_request)
    refreshed_record = load_analysis_session_record(session_id) or record

    return StreamingResponse(
        start_stream_for_record(
            refreshed_record,
            effective_request,
            datasource_context,
            resumed=True,
        ),
        media_type="application/x-ndjson",
    )


@router.get("/analysis/{session_id}")
def get_analysis_session(session_id: str) -> dict[str, Any]:
    record = load_analysis_session_record(session_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis session not found.",
        )

    return {"item": serialize_analysis_session(record)}
