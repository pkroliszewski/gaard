from __future__ import annotations

import json
from dataclasses import dataclass
from numbers import Number
from typing import Any
from uuid import uuid4

from gaard_core.query_pipeline.models import (
    ConversationContextClassification,
    ConversationContextDecision,
    OutputClassification,
    QueryRequest,
    QueryResponse,
)
from sqlalchemy import desc, select

from gaard_api.admin.database import create_session
from gaard_api.admin.models import Conversation, ConversationTurn

CONTEXT_TURN_LIMIT = 4
SAFE_ANSWER_CLASSIFICATIONS = {
    OutputClassification.NEUTRAL_DATA.value,
    OutputClassification.TECHNICAL_DATA.value,
}


@dataclass(frozen=True)
class ConversationPrincipal:
    owner_user_id: str
    owner_username: str = ""


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def json_loads(value: str, default: Any) -> Any:
    try:
        payload = json.loads(value or "")
    except json.JSONDecodeError:
        return default

    return payload if payload is not None else default


def iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def create_conversation(
    principal: ConversationPrincipal,
    request: QueryRequest,
) -> Conversation:
    datasource_ids = request.datasource_ids or (
        [request.datasource_id] if request.datasource_id else []
    )
    record = Conversation(
        conversation_id=uuid4().hex,
        owner_user_id=principal.owner_user_id,
        owner_username=principal.owner_username,
        datasource_id=request.datasource_id,
        datasource_ids_json=json_dumps(datasource_ids),
        title=build_title(request.question),
        summary_json=json_dumps(
            {
                "turn_count": 0,
                "latest_question": "",
            }
        ),
    )
    with create_session() as session:
        session.add(record)
        session.commit()
        session.refresh(record)
        return detach_conversation(record)


def load_conversation_for_owner(
    conversation_id: str,
    principal: ConversationPrincipal,
) -> Conversation | None:
    if not conversation_id:
        return None

    with create_session() as session:
        record = session.scalar(
            select(Conversation).where(
                Conversation.conversation_id == conversation_id,
                Conversation.owner_user_id == principal.owner_user_id,
            )
        )
        return detach_conversation(record) if record is not None else None


def ensure_conversation(
    principal: ConversationPrincipal,
    request: QueryRequest,
    *,
    force_new: bool = False,
) -> Conversation:
    if not force_new and request.conversation_id:
        record = load_conversation_for_owner(request.conversation_id, principal)
        if record is not None:
            return record

    return create_conversation(principal, request)


def conversation_exists(conversation_id: str) -> bool:
    if not conversation_id:
        return False

    with create_session() as session:
        return bool(
            session.scalar(
                select(Conversation.id).where(Conversation.conversation_id == conversation_id)
            )
        )


def build_compact_conversation_context(
    conversation_id: str,
    limit: int = CONTEXT_TURN_LIMIT,
) -> dict[str, Any]:
    with create_session() as session:
        turns = list(
            session.scalars(
                select(ConversationTurn)
                .where(
                    ConversationTurn.conversation_id == conversation_id,
                    ConversationTurn.status == "completed",
                )
                .order_by(desc(ConversationTurn.id))
                .limit(limit)
            )
        )

    return {
        "conversation_id": conversation_id,
        "turns": [compact_turn_context(turn) for turn in reversed(turns)],
    }


def list_conversations_for_owner(
    principal: ConversationPrincipal,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 100))
    with create_session() as session:
        rows = list(
            session.scalars(
                select(Conversation)
                .where(Conversation.owner_user_id == principal.owner_user_id)
                .order_by(desc(Conversation.updated_at), desc(Conversation.id))
                .limit(bounded_limit)
            )
        )
    return [serialize_conversation(row) for row in rows]


def list_conversation_turns(
    conversation_id: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 200))
    with create_session() as session:
        rows = list(
            session.scalars(
                select(ConversationTurn)
                .where(ConversationTurn.conversation_id == conversation_id)
                .order_by(desc(ConversationTurn.id))
                .limit(bounded_limit)
            )
        )
    return [serialize_conversation_turn(row) for row in reversed(rows)]


def record_conversation_turn(
    conversation: Conversation,
    *,
    mode: str,
    original_question: str,
    standalone_question: str,
    answer: str,
    sql: str,
    metadata: dict[str, Any],
    context_classification: ConversationContextClassification,
    rows: list[dict[str, Any]] | None = None,
    status: str = "completed",
    data_query_audit_id: int | None = None,
    analysis_session_id: str = "",
) -> ConversationTurn:
    turn = ConversationTurn(
        turn_id=uuid4().hex,
        conversation_id=conversation.conversation_id,
        mode=mode,
        status=status,
        original_question=original_question,
        standalone_question=standalone_question,
        answer=answer,
        sql=sql,
        metadata_json=json_dumps(
            conversation_turn_metadata(
                metadata=metadata,
                context_classification=context_classification,
                original_question=original_question,
                standalone_question=standalone_question,
                sql=sql,
                rows=rows,
            )
        ),
        data_query_audit_id=data_query_audit_id,
        analysis_session_id=analysis_session_id,
        context_decision=context_classification.decision.value,
        context_confidence=context_classification.confidence,
    )

    with create_session() as session:
        session.add(turn)
        record = session.scalar(
            select(Conversation).where(Conversation.conversation_id == conversation.conversation_id)
        )
        if record is not None:
            datasource_id = str(metadata.get("datasource_id") or conversation.datasource_id or "")
            datasource_ids = metadata.get("datasource_ids")
            if not isinstance(datasource_ids, list):
                datasource_ids = [datasource_id] if datasource_id else []
            record.datasource_id = datasource_id
            record.datasource_ids_json = json_dumps(datasource_ids)
            record.title = record.title or build_title(original_question)
            record.summary_json = json_dumps(update_summary(record.summary_json, original_question))
        session.commit()
        session.refresh(turn)
        return detach_turn(turn)


def build_conversation_metadata(
    conversation: Conversation,
    turn: ConversationTurn | None,
    classification: ConversationContextClassification,
) -> dict[str, Any]:
    return {
        "id": conversation.conversation_id,
        "turn_id": turn.turn_id if turn is not None else "",
        "context_decision": classification.decision.value,
        "standalone_question": classification.standalone_question,
        "confidence": classification.confidence,
        "context_reason": classification.reason,
        "context_source": classification.source,
        "context_model_response": classification.model_response,
        "context_prompt": classification.prompt,
    }


def new_topic_classification(
    question: str, confidence: float = 1.0
) -> ConversationContextClassification:
    return ConversationContextClassification(
        decision=ConversationContextDecision.NEW_TOPIC,
        confidence=confidence,
        standalone_question=question,
        reason="Started a new conversation context.",
        source="system",
    )


def ambiguous_context_response(
    request: QueryRequest,
    conversation: Conversation,
    classification: ConversationContextClassification,
) -> QueryResponse:
    turn = record_conversation_turn(
        conversation,
        mode="sql",
        original_question=request.question,
        standalone_question="",
        answer=(
            "Potrzebuję doprecyzowania, czy to pytanie jest kontynuacją poprzedniego "
            "wątku i jak mam je rozumieć."
        ),
        sql="",
        metadata={
            "duration_ms": 0,
            "datasource_id": request.datasource_id,
            "datasource_ids": request.datasource_ids,
            "user_id": request.user_id,
            "output_classification": OutputClassification.UNKNOWN.value,
            "blocked": True,
            "blocked_reason": "conversation.ambiguous_context",
        },
        context_classification=classification,
        status="clarification",
    )
    metadata = {
        "duration_ms": 0,
        "datasource_id": request.datasource_id,
        "datasource_ids": request.datasource_ids,
        "user_id": request.user_id,
        "output_classification": OutputClassification.UNKNOWN.value,
        "blocked": True,
        "blocked_reason": "conversation.ambiguous_context",
        "conversation": build_conversation_metadata(conversation, turn, classification),
    }
    return QueryResponse(
        question=request.question,
        answer=turn.answer,
        sql="",
        rows=[],
        metadata=metadata,
    )


def compact_turn_context(turn: ConversationTurn) -> dict[str, Any]:
    metadata = json_loads(turn.metadata_json, {})
    if not isinstance(metadata, dict):
        metadata = {}
    output_classification = str(metadata.get("output_classification") or "")
    payload = {
        "mode": turn.mode,
        "question": turn.original_question,
        "standalone_question": turn.standalone_question,
        "sql": turn.sql,
        "datasource_id": metadata.get("datasource_id") or "",
        "datasource_ids": metadata.get("datasource_ids") or [],
        "output_classification": output_classification,
        "context_decision": turn.context_decision,
        "context_reason": metadata.get("context_reason") or "",
        "context_model_response": metadata.get("context_model_response") or {},
        "working_context": metadata.get("working_context") or {},
        "result_summary": metadata.get("result_summary") or {},
    }
    if output_classification in SAFE_ANSWER_CLASSIFICATIONS:
        payload["answer"] = turn.answer

    return payload


def compact_response_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "active_datasource_ids",
        "analysis_mode",
        "analysis_session_id",
        "analysis_status",
        "blocked",
        "blocked_reason",
        "data_query_audit_id",
        "datasource_id",
        "datasource_ids",
        "intent_decision",
        "intent_confidence",
        "llm_sql_language",
        "output_classification",
        "sql_generation_mode",
    }
    return {key: metadata[key] for key in allowed_keys if key in metadata}


def conversation_turn_metadata(
    *,
    metadata: dict[str, Any],
    context_classification: ConversationContextClassification,
    original_question: str,
    standalone_question: str,
    sql: str,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    compact = compact_response_metadata(metadata)
    compact["context_reason"] = context_classification.reason
    compact["context_model_response"] = context_classification.model_response
    if context_classification.prompt:
        compact["context_prompt"] = context_classification.prompt
    if context_classification.source:
        compact["context_source"] = context_classification.source
    compact["working_context"] = build_working_context(
        original_question=original_question,
        standalone_question=standalone_question,
        sql=sql,
    )
    compact["result_summary"] = build_result_summary(rows or [])
    return compact


def build_result_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    columns: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for column in row:
            if column not in columns:
                columns.append(column)

    summary: dict[str, Any] = {
        "row_count": len(rows),
        "columns": columns[:20],
    }

    if len(rows) == 1 and isinstance(rows[0], dict) and len(rows[0]) == 1:
        value = next(iter(rows[0].values()))
        if isinstance(value, Number) and not isinstance(value, bool):
            summary["scalar_count"] = value
        elif isinstance(value, str):
            compact = value.strip()
            if compact.isdecimal():
                summary["scalar_count"] = int(compact)

    return summary


def build_working_context(
    *,
    original_question: str,
    standalone_question: str,
    sql: str,
) -> dict[str, Any]:
    text = f"{original_question}\n{standalone_question}\n{sql}".lower()
    return {
        "time_scope": detect_time_scope(text),
        "filters": detect_filters(text),
        "projection": detect_projection(text),
    }


def detect_time_scope(text: str) -> str:
    time_scopes = [
        ("previous_week", ("poprzednim tygodniu", "previous week")),
        ("current_week", ("w tym tygodniu", "bieżącym tygodniu", "yearweek", "curdate")),
        ("previous_month", ("poprzednim miesiącu", "previous month")),
        ("current_month", ("w tym miesiącu", "bieżącym miesiącu")),
        ("yesterday", ("wczoraj", "yesterday")),
        ("today", ("dzisiaj", "today")),
        ("last_7_days", ("interval 7 day", "ostatnich 7", "ostatnie 7")),
    ]
    for label, terms in time_scopes:
        if any(term in text for term in terms):
            return label
    return ""


def detect_filters(text: str) -> list[str]:
    filters: list[str] = []
    if "otwart" in text or "not in (9,20,22,29,67)" in text or "not in (9, 20, 22, 29, 67)" in text:
        filters.append("open_status")
    if "przetworzon" in text or "status_id in (9,20,22,29,67)" in text:
        filters.append("processed_status")
    if "hidden is null" in text:
        filters.append("visible_records")
    return filters


def detect_projection(text: str) -> list[str]:
    projection_terms = [
        ("count", ("count(", "ile ")),
        ("short_description", ("short_description", "krótk", "krotk", "opis")),
        ("status", ("status", "stan")),
        ("name", ("nazwa", "nazwy", "name")),
    ]
    projection: list[str] = []
    for label, terms in projection_terms:
        if any(term in text for term in terms):
            projection.append(label)
    return projection


def update_summary(summary_json: str, latest_question: str) -> dict[str, Any]:
    summary = json_loads(summary_json, {})
    if not isinstance(summary, dict):
        summary = {}

    turn_count = summary.get("turn_count")
    next_turn_count = (
        int(turn_count) + 1
        if isinstance(turn_count, (str, int, float))
        else 1
    )

    summary["turn_count"] = next_turn_count
    summary["latest_question"] = latest_question
    return summary


def build_title(question: str) -> str:
    compact = " ".join(question.split()).strip()
    if not compact:
        return "GAARD conversation"
    return compact[:252] + "..." if len(compact) > 255 else compact


def serialize_conversation(record: Conversation) -> dict[str, Any]:
    summary = json_loads(record.summary_json, {})
    if not isinstance(summary, dict):
        summary = {}
    datasource_ids = json_loads(record.datasource_ids_json, [])
    if not isinstance(datasource_ids, list):
        datasource_ids = []
    return {
        "id": record.conversation_id,
        "title": record.title or "GAARD conversation",
        "status": record.status,
        "datasource_id": record.datasource_id,
        "datasource_ids": datasource_ids,
        "turn_count": int(summary.get("turn_count") or 0),
        "latest_question": str(summary.get("latest_question") or ""),
        "summary": summary,
        "created_at": iso(record.created_at),
        "updated_at": iso(record.updated_at),
    }


def serialize_conversation_turn(record: ConversationTurn) -> dict[str, Any]:
    metadata = json_loads(record.metadata_json, {})
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "id": record.turn_id,
        "conversation_id": record.conversation_id,
        "mode": record.mode,
        "status": record.status,
        "question": record.original_question,
        "standalone_question": record.standalone_question,
        "answer": record.answer,
        "sql": record.sql,
        "metadata": metadata,
        "data_query_audit_id": record.data_query_audit_id,
        "analysis_session_id": record.analysis_session_id,
        "context_decision": record.context_decision,
        "context_confidence": record.context_confidence,
        "created_at": iso(record.created_at),
    }


def detach_conversation(record: Conversation) -> Conversation:
    return Conversation(
        id=record.id,
        conversation_id=record.conversation_id,
        owner_user_id=record.owner_user_id,
        owner_username=record.owner_username,
        status=record.status,
        datasource_id=record.datasource_id,
        datasource_ids_json=record.datasource_ids_json,
        title=record.title,
        summary_json=record.summary_json,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def detach_turn(record: ConversationTurn) -> ConversationTurn:
    return ConversationTurn(
        id=record.id,
        turn_id=record.turn_id,
        conversation_id=record.conversation_id,
        mode=record.mode,
        status=record.status,
        original_question=record.original_question,
        standalone_question=record.standalone_question,
        answer=record.answer,
        sql=record.sql,
        metadata_json=record.metadata_json,
        data_query_audit_id=record.data_query_audit_id,
        analysis_session_id=record.analysis_session_id,
        context_decision=record.context_decision,
        context_confidence=record.context_confidence,
        created_at=record.created_at,
    )
