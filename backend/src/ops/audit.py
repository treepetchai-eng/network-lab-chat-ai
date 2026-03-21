"""Audit helpers for ops entities."""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.ops.models import AuditEntry


def record_audit(
    session: Session,
    *,
    actor: str,
    actor_role: str,
    action: str,
    entity_type: str,
    entity_id: int | None,
    status: str,
    summary: str,
    payload: dict | None = None,
) -> AuditEntry:
    entry = AuditEntry(
        actor=actor,
        actor_role=actor_role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        status=status,
        summary=summary,
        payload_json=payload or {},
    )
    session.add(entry)
    session.flush()
    return entry
