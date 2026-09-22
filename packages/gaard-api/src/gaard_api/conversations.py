from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from gaard_core.query_pipeline.models import (
    ConversationContextClassification,
    ConversationContextDecision,
    OutputClassification,
    QueryRequest,
)
from sqlalchemy import desc, select

from gaard_api.admin.database import create_session
from gaard_api.admin.models import Conversation, ConversationTurn

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


def build_compact_conversation_context(conversation_id: str) -> dict[str, Any]:
    with create_session() as session:
        boundary = session.scalar(
            select(ConversationTurn.id)
            .where(
                ConversationTurn.conversation_id == conversation_id,
                ConversationTurn.context_decision == ConversationContextDecision.NEW_TOPIC.value,
            )
            .order_by(desc(ConversationTurn.id))
            .limit(1)
        )
        query = select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id)
        if boundary is not None:
            query = query.where(ConversationTurn.id >= boundary)
        turns = list(session.scalars(query.order_by(ConversationTurn.id)))
    return {
        "conversation_id": conversation_id,
        "turns": [compact_turn_context(turn) for turn in turns],
    }


def load_conversation_turn(conversation_id: str, turn_id: str) -> ConversationTurn | None:
    with create_session() as session:
        turn = session.scalar(
            select(ConversationTurn).where(
                ConversationTurn.conversation_id == conversation_id,
                ConversationTurn.turn_id == turn_id,
            )
        )
        return detach_turn(turn) if turn is not None else None


def fail_conversation_turn(
    conversation: Conversation | None,
    classification: ConversationContextClassification | None,
    error: Exception,
) -> None:
    if conversation is None or classification is None or not classification.turn_id:
        return
    with create_session() as session:
        turn = session.scalar(
            select(ConversationTurn).where(
                ConversationTurn.conversation_id == conversation.conversation_id,
                ConversationTurn.turn_id == classification.turn_id,
            )
        )
        if turn is not None:
            turn.status = "failed"
            turn.answer = str(error)
            session.commit()


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
    status: str = "completed",
    data_query_audit_id: int | None = None,
    analysis_session_id: str = "",
) -> ConversationTurn:
    with create_session() as session:
        turn = (
            session.scalar(
                select(ConversationTurn).where(
                    ConversationTurn.conversation_id == conversation.conversation_id,
                    ConversationTurn.turn_id == context_classification.turn_id,
                )
            )
            if context_classification.turn_id
            else None
        )
        is_new = turn is None
        if turn is None:
            turn = ConversationTurn(
                turn_id=uuid4().hex,
                conversation_id=conversation.conversation_id,
            )
            session.add(turn)
        turn.mode = mode
        turn.status = status
        turn.original_question = original_question
        turn.standalone_question = standalone_question
        turn.answer = answer
        turn.sql = sql
        turn.metadata_json = json_dumps(
            conversation_turn_metadata(
                metadata=metadata,
                context_classification=context_classification,
            )
        )
        turn.data_query_audit_id = data_query_audit_id
        turn.analysis_session_id = analysis_session_id
        turn.context_decision = context_classification.decision.value
        turn.context_confidence = context_classification.confidence
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
            if is_new:
                record.summary_json = json_dumps(
                    update_summary(record.summary_json, original_question)
                )
        session.commit()
        session.refresh(turn)
        context_classification.turn_id = turn.turn_id
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
) -> dict[str, Any]:
    compact = compact_response_metadata(metadata)
    compact["context_reason"] = context_classification.reason
    compact["context_model_response"] = context_classification.model_response
    compact["context_prompt"] = context_classification.prompt
    compact["context_source"] = context_classification.source
    return compact


def update_summary(summary_json: str, latest_question: str) -> dict[str, Any]:
    summary = json_loads(summary_json, {})
    if not isinstance(summary, dict):
        summary = {}

    turn_count = summary.get("turn_count")
    next_turn_count = int(turn_count) + 1 if isinstance(turn_count, (str, int, float)) else 1

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
