from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

from gaard_api.admin.models import DataQueryAuditLog

PENDING_SIEM_EVENTS_KEY = "gaard_pending_siem_events"


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class SiemSink(Protocol):
    def send(self, event_payload: dict[str, Any]) -> None: ...


@dataclass(slots=True)
class SiemSinkRegistry:
    sinks: list[SiemSink]

    def __init__(self) -> None:
        self.sinks = []

    def register_sink(self, sink: SiemSink) -> None:
        self.sinks.append(sink)

    def emit(self, event_payload: dict[str, Any]) -> None:
        for sink in tuple(self.sinks):
            try:
                sink.send(event_payload)
            except Exception:
                continue


def queue_siem_event(session: Session, event_payload: dict[str, Any]) -> None:
    pending = cast(
        list[dict[str, Any]],
        session.info.setdefault(PENDING_SIEM_EVENTS_KEY, []),
    )
    pending.append(event_payload)


def dispatch_siem_event(event_payload: dict[str, Any]) -> None:
    from gaard_api.extensions import get_siem_registry

    get_siem_registry().emit(event_payload)


def build_admin_audit_event(
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _base_event(
        event_kind="admin.audit",
        severity="info",
        action=action,
        outcome="success",
        actor_id=actor,
        resource_type=resource_type,
        resource_id=resource_id,
        message=f"Admin action {action} on {resource_type}.",
        payload={"details": dict(details or {})},
    )


def build_data_query_audit_event(
    request_user_id: str,
    request_datasource_id: str,
    log: DataQueryAuditLog,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    severity = "error" if log.type.value != "info" else "info"
    outcome = "error" if severity == "error" else "success"
    return _base_event(
        event_kind="data_query.audit",
        severity=severity,
        action=log.type.value,
        outcome=outcome,
        actor_id=request_user_id,
        datasource_id=request_datasource_id,
        message=log.answer,
        payload={
            "audit_id": log.id,
            "question": log.question,
            "answer": log.answer,
            "sql": log.sql,
            "audit_type": log.type.value,
            "output_classification": log.output_classification.value,
            "llm_sql_language": log.llm_sql_language,
            "metadata": dict(metadata),
        },
    )


def build_analysis_event(
    *,
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
    user_id: str = "",
    datasource_id: str = "",
) -> dict[str, Any]:
    return _base_event(
        event_kind="analysis.event",
        severity="info",
        action=event_type,
        outcome="success",
        actor_id=user_id,
        datasource_id=datasource_id,
        message=f"Analysis event {event_type}.",
        payload={
            "analysis_session_id": session_id,
            **dict(payload),
        },
    )


def _base_event(
    *,
    event_kind: str,
    severity: str,
    action: str,
    outcome: str,
    actor_id: str = "",
    datasource_id: str = "",
    resource_type: str = "",
    resource_id: str = "",
    message: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "gaard.siem.v1",
        "event_id": uuid4().hex,
        "timestamp": utc_iso(),
        "source": {
            "product": "gaard",
            "component": "api",
        },
        "event": {
            "kind": event_kind,
            "severity": severity,
            "action": action,
            "outcome": outcome,
            "message": message,
        },
        "actor": {
            "id": actor_id,
        },
        "resource": {
            "type": resource_type,
            "id": resource_id,
        },
        "datasource": {
            "id": datasource_id,
        },
        "payload": dict(payload or {}),
    }


@event.listens_for(Session, "after_commit")
def _flush_pending_siem_events(session: Session) -> None:
    pending = cast(
        list[dict[str, Any]],
        session.info.pop(PENDING_SIEM_EVENTS_KEY, []),
    )
    for event_payload in pending:
        dispatch_siem_event(event_payload)


@event.listens_for(Session, "after_rollback")
def _clear_pending_siem_events(session: Session) -> None:
    session.info.pop(PENDING_SIEM_EVENTS_KEY, None)
