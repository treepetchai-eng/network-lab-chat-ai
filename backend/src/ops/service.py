"""High-level services for the operations platform APIs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, time, timezone
from math import ceil
from threading import Lock
from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from src.ops.action_catalog import get_action_catalog_entry, list_action_catalog
from src.ops.ai import (
    analyze_log_window_with_llm,
    analyze_device_focus_with_llm,
    analyze_global_with_llm,
    analyze_site_with_llm,
    chat_incident_with_llm,
    investigate_incident_with_llm,
    synthesize_troubleshoot_result_with_llm,
    summarize_device_with_llm,
)
from src.ops.audit import record_audit
from src.ops.db import utcnow
from src.ops.inventory import list_devices_with_stats, sync_inventory_from_csv
from src.ops.jobs import complete_job, create_job, fail_job, start_job
from src.ops.models import (
    AIArtifact,
    Approval,
    AuditEntry,
    Device,
    DeviceInterface,
    Incident,
    LLMAnalysis,
    IncidentCluster,
    IncidentEventLink,
    IncidentFeedback,
    IncidentHistory,
    Job,
    NormalizedEvent,
    NotificationLog,
    RawLog,
    RemediationTask,
    ScanHistory,
)
from src.ops.policy import (
    approval_role_for_risk,
    classify_command_set,
    dual_approval_required,
    execution_role_for_risk,
    max_role,
    normalize_role,
    policy_decision_for_proposal,
    require_role,
)
from src.ops.syslog_sync import sync_syslog_from_remote
from src.ops.syslog_ingest import SyslogIngressRecord, ingest_syslog_records
from src.tools.config_executor import execute_config
from src.tools.ssh_executor import execute_cli

_EXECUTION_ERROR_PREFIXES = (
    "[AUTH ERROR]",
    "[TIMEOUT ERROR]",
    "[SSH ERROR]",
    "[CONFIG ERROR]",
    "[BLOCKED]",
)
_CLI_ERROR_MARKERS = (
    "% invalid input detected",
    "% incomplete command",
    "% ambiguous command",
    "% unknown command",
    "command rejected:",
)
_SYNC_LOCKS = {
    "inventory": Lock(),
    "syslog": Lock(),
    "incident_scan": Lock(),
}
_INCIDENT_STATUS_ALIASES = {
    "open": "new",
    "investigating": "in_progress",
}
_OPEN_INCIDENT_STATUSES = ("new", "acknowledged", "in_progress", "monitoring", "open", "investigating")
_PENDING_APPROVAL_STATUSES = ("pending", "awaiting_second_approval")
_INCIDENT_TRANSITIONS = {
    "new": {"acknowledged", "in_progress", "monitoring", "resolved"},
    "acknowledged": {"in_progress", "monitoring", "resolved"},
    "in_progress": {"monitoring", "resolved"},
    "monitoring": {"in_progress", "resolved"},
    "resolved": {"new", "monitoring", "resolved"},
}
_ROLE_DEFAULTS = {
    "requested_by_role": "operator",
    "review_actor_role": "admin",
    "execute_actor_role": "admin",
}


def _normalize_confidence_score(value: Any, *, default: int = 50) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if 0 <= numeric <= 1:
        numeric *= 100
    return max(0, min(100, int(round(numeric))))


class ApprovalExecutionError(RuntimeError):
    """Raised when an approved action reaches the device but cannot complete safely."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        failure_category: str = "failed_command",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.failure_category = failure_category


class ConcurrentJobError(RuntimeError):
    """Raised when a sync job is already running."""


@contextmanager
def _job_lock(name: str):
    lock = _SYNC_LOCKS[name]
    acquired = lock.acquire(blocking=False)
    if not acquired:
        raise ConcurrentJobError(f"{name} sync is already running")
    try:
        yield
    finally:
        lock.release()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _safe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_incident_status(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "new"
    return _INCIDENT_STATUS_ALIASES.get(raw, raw)


def serialize_audit(entry: AuditEntry) -> dict:
    return {
        "id": entry.id,
        "actor": entry.actor,
        "actor_role": entry.actor_role,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "status": entry.status,
        "summary": entry.summary,
        "payload": entry.payload_json,
        "created_at": _iso(entry.created_at),
    }


def serialize_ai_artifact(artifact: AIArtifact) -> dict:
    return {
        "id": artifact.id,
        "artifact_type": artifact.artifact_type,
        "title": artifact.title,
        "incident_id": artifact.incident_id,
        "device_id": artifact.device_id,
        "job_id": artifact.job_id,
        "approval_id": artifact.approval_id,
        "provider": artifact.provider,
        "model": artifact.model,
        "prompt_version": artifact.prompt_version,
        "summary": artifact.summary,
        "root_cause": artifact.root_cause,
        "confidence_score": artifact.confidence_score,
        "readiness": artifact.readiness,
        "risk_explanation": artifact.risk_explanation,
        "evidence_refs": artifact.evidence_refs_json,
        "proposed_actions": artifact.proposed_actions_json,
        "content": artifact.content_json,
        "steps": (artifact.content_json or {}).get("steps", []),
        "created_at": _iso(artifact.created_at),
    }


def serialize_llm_analysis(entry: LLMAnalysis) -> dict:
    return {
        "id": entry.id,
        "incident_id": entry.incident_id,
        "decision": entry.decision,
        "status": entry.status,
        "window_start": _iso(entry.window_start),
        "window_end": _iso(entry.window_end),
        "input_log_ids": entry.input_log_ids_json,
        "open_incident_ids": entry.open_incident_ids_json,
        "provider": entry.provider,
        "model": entry.model,
        "prompt_version": entry.prompt_version,
        "raw_text": entry.raw_text,
        "output": entry.output_json,
        "created_at": _iso(entry.created_at),
    }


def serialize_device_interface(interface: DeviceInterface) -> dict:
    return {
        "id": interface.id,
        "device_id": interface.device_id,
        "name": interface.name,
        "protocol": interface.protocol,
        "description": interface.description,
        "last_state": interface.last_state,
        "last_event_id": interface.last_event_id,
        "last_event_time": _iso(interface.last_event_time),
        "event_count": interface.event_count,
        "metadata": interface.metadata_json,
        "created_at": _iso(interface.created_at),
        "updated_at": _iso(interface.updated_at),
    }


def serialize_incident_history(entry: IncidentHistory) -> dict:
    return {
        "id": entry.id,
        "incident_id": entry.incident_id,
        "action": entry.action,
        "actor": entry.actor,
        "actor_role": entry.actor_role,
        "from_status": entry.from_status,
        "to_status": entry.to_status,
        "summary": entry.summary,
        "comment": entry.comment,
        "payload": entry.payload_json,
        "created_at": _iso(entry.created_at),
    }


def serialize_notification_log(entry: NotificationLog) -> dict:
    return {
        "id": entry.id,
        "incident_id": entry.incident_id,
        "channel": entry.channel,
        "recipient": entry.recipient,
        "message_text": entry.message_text,
        "requested_by": entry.requested_by,
        "requested_by_role": entry.requested_by_role,
        "status": entry.status,
        "response": entry.response_json,
        "created_at": _iso(entry.created_at),
    }


def serialize_remediation_task(task: RemediationTask) -> dict:
    return {
        "id": task.id,
        "approval_id": task.approval_id,
        "incident_id": task.incident_id,
        "phase": task.phase,
        "step_order": task.step_order,
        "command_text": task.command_text,
        "status": task.status,
        "output_text": task.output_text,
        "created_at": _iso(task.created_at),
        "started_at": _iso(task.started_at),
        "completed_at": _iso(task.completed_at),
        "updated_at": _iso(task.updated_at),
    }


def _execution_failure_from_output(output: str) -> tuple[str, int]:
    stripped = output.lstrip()
    if stripped.startswith("[BLOCKED]"):
        return "failed_blocked", 409
    if stripped.startswith("[AUTH ERROR]") or stripped.startswith("[TIMEOUT ERROR]") or stripped.startswith("[SSH ERROR]"):
        return "failed_transport", 502
    if stripped.startswith("[CONFIG ERROR]"):
        return "failed_command", 502
    return "failed_command", 502


def _approval_related_audits(session: Session, approval_id: int) -> list[AuditEntry]:
    return session.scalars(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "approval", AuditEntry.entity_id == approval_id)
        .order_by(AuditEntry.created_at.asc(), AuditEntry.id.asc())
    ).all()


def _approval_audit_approvers(session: Session, approval_id: int) -> list[AuditEntry]:
    return session.scalars(
        select(AuditEntry)
        .where(
            AuditEntry.entity_type == "approval",
            AuditEntry.entity_id == approval_id,
            AuditEntry.action == "approval_approved",
        )
        .order_by(AuditEntry.created_at.asc(), AuditEntry.id.asc())
    ).all()


def _build_incident_evidence_snapshot(session: Session, incident_id: int | None) -> dict:
    if incident_id is None:
        return {}
    incident = session.get(Incident, incident_id)
    if incident is None:
        return {}
    events = list_events(session, incident_id=incident_id, page_size=10)["items"]
    return {
        "incident": {
            "id": incident.id,
            "title": incident.title,
            "status": incident.status,
            "severity": incident.severity,
            "event_count": incident.event_count,
            "summary": incident.summary,
        },
        "events": events[:10],
    }


def _record_incident_history(
    session: Session,
    *,
    incident_id: int,
    action: str,
    actor: str,
    actor_role: str,
    summary: str,
    from_status: str | None = None,
    to_status: str | None = None,
    comment: str | None = None,
    payload: dict | None = None,
) -> IncidentHistory:
    entry = IncidentHistory(
        incident_id=incident_id,
        action=action,
        actor=actor,
        actor_role=normalize_role(actor_role),
        from_status=from_status,
        to_status=to_status,
        summary=summary,
        comment=comment,
        payload_json=payload or {},
    )
    session.add(entry)
    session.flush()
    return entry


def _replace_remediation_tasks(
    session: Session,
    *,
    approval: Approval,
    execute_commands: list[str],
    verify_commands: list[str],
    rollback_commands: list[str],
) -> None:
    existing_tasks = session.scalars(
        select(RemediationTask).where(RemediationTask.approval_id == approval.id)
    ).all()
    for task in existing_tasks:
        session.delete(task)
    session.flush()

    for phase, commands in (
        ("execute", execute_commands),
        ("verify", verify_commands),
        ("rollback", rollback_commands),
    ):
        for step_order, command in enumerate(commands, start=1):
            session.add(
                RemediationTask(
                    approval_id=approval.id,
                    incident_id=approval.incident_id,
                    phase=phase,
                    step_order=step_order,
                    command_text=command,
                    status="pending",
                )
            )


def _set_task_state(
    session: Session,
    *,
    approval_id: int,
    phase: str,
    step_order: int,
    status: str,
    output_text: str | None = None,
) -> None:
    task = session.scalar(
        select(RemediationTask).where(
            RemediationTask.approval_id == approval_id,
            RemediationTask.phase == phase,
            RemediationTask.step_order == step_order,
        )
    )
    if task is None:
        return
    now = utcnow()
    task.status = status
    if output_text is not None:
        task.output_text = output_text
    if status in {"running", "executing", "verify_pending"} and task.started_at is None:
        task.started_at = now
    if status not in {"pending", "running", "executing", "verify_pending"}:
        task.completed_at = now


def _incident_status_transition_allowed(current_status: str, target_status: str) -> bool:
    current = _normalize_incident_status(current_status)
    target = _normalize_incident_status(target_status)
    if current == target:
        return True
    return target in _INCIDENT_TRANSITIONS.get(current, set())


def list_sites(session: Session) -> list[str]:
    return _distinct_non_empty_strings(session, Device.site)


def _backfill_device_interfaces(session: Session, device_id: int) -> None:
    device = session.get(Device, device_id)
    if device is None:
        return
    changed = False
    known_interfaces = {
        interface.name: interface
        for interface in session.scalars(
            select(DeviceInterface).where(DeviceInterface.device_id == device_id)
        ).all()
    }
    events = session.scalars(
        select(NormalizedEvent)
        .where(
            NormalizedEvent.device_id == device_id,
            NormalizedEvent.interface_name.is_not(None),
            NormalizedEvent.interface_name != "",
        )
        .order_by(NormalizedEvent.event_time.asc().nullslast(), NormalizedEvent.id.asc())
    ).all()
    for event in events:
        interface_name = (event.interface_name or "").strip()
        if not interface_name:
            continue
        interface = known_interfaces.get(interface_name)
        if interface is None:
            interface = DeviceInterface(
                device_id=device_id,
                name=interface_name,
                protocol=event.protocol,
                last_state=event.state,
                last_event_id=event.id,
                last_event_time=event.event_time,
                event_count=1,
                metadata_json={"source": "backfill", "source_ip": event.source_ip},
            )
            session.add(interface)
            known_interfaces[interface_name] = interface
            changed = True
            continue
        interface.protocol = event.protocol or interface.protocol
        interface.last_state = event.state or interface.last_state
        interface.last_event_id = event.id
        interface.last_event_time = event.event_time or interface.last_event_time
        interface.event_count += 1
        changed = True
    if changed:
        session.flush()


def overview(session: Session) -> dict:
    """Return dashboard counters for the ops console."""
    open_incident_count = session.scalar(
        select(func.count()).select_from(Incident).where(
            Incident.status.in_(_OPEN_INCIDENT_STATUSES)
        )
    ) or 0
    pending_approvals = session.scalar(
        select(func.count()).select_from(Approval).where(Approval.status.in_(_PENDING_APPROVAL_STATUSES))
    ) or 0
    device_count = session.scalar(
        select(func.count()).select_from(Device)
    ) or 0
    event_count = session.scalar(
        select(func.count()).select_from(NormalizedEvent)
    ) or 0
    pending_approval_items = session.scalars(
        select(Approval)
        .where(Approval.status.in_(_PENDING_APPROVAL_STATUSES))
        .order_by(Approval.requested_at.desc(), Approval.id.desc())
        .limit(6)
    ).all()
    open_incidents = session.scalars(
        select(Incident)
        .where(Incident.status.in_(_OPEN_INCIDENT_STATUSES))
        .order_by(Incident.updated_at.desc(), Incident.id.desc())
        .limit(8)
    ).all()
    recent_execution_reports = session.scalars(
        select(Approval)
        .where(
            Approval.execution_status.in_(
                [
                    "succeeded",
                    "partial_success",
                    "failed_blocked",
                    "failed_transport",
                    "failed_command",
                    "failed_verification",
                ]
            )
        )
        .order_by(Approval.updated_at.desc(), Approval.id.desc())
        .limit(6)
    ).all()
    # Top event types for dashboard chart
    top_event_rows = session.execute(
        select(NormalizedEvent.event_type, func.count().label("cnt"))
        .group_by(NormalizedEvent.event_type)
        .order_by(desc("cnt"))
        .limit(10)
    ).all()

    return {
        "counts": {
            "open_incidents": open_incident_count,
            "pending_approvals": pending_approvals,
            "devices": device_count,
            "events": event_count,
        },
        "open_incidents": [serialize_incident(session, incident) for incident in open_incidents],
        "pending_approvals": [serialize_approval(session, approval) for approval in pending_approval_items],
        "recent_execution_reports": [serialize_approval(session, approval) for approval in recent_execution_reports],
        "top_event_types": [{"event_type": row.event_type, "count": row.cnt} for row in top_event_rows],
    }


def _page_payload(
    *,
    items: list[dict],
    total: int,
    page: int,
    page_size: int,
    sort_by: str,
    sort_dir: str,
    facets: dict[str, list[str]] | None = None,
) -> dict:
    total_pages = max(ceil(total / page_size), 1) if page_size else 1
    payload = {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }
    if facets is not None:
        payload["facets"] = facets
    return payload


def _parse_datetime_filter(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None

    raw = value.strip()
    if not raw:
        return None

    try:
        if len(raw) == 10:
            parsed_date = datetime.fromisoformat(raw).date()
            parsed = datetime.combine(
                parsed_date,
                time.max if end_of_day else time.min,
                tzinfo=timezone.utc,
            )
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
        return parsed
    except ValueError:
        return None


def _incident_summary_from_analysis(parsed: dict) -> str:
    summary = str(parsed.get("summary") or "No summary provided.").strip()
    root_cause = str(parsed.get("probable_root_cause") or "Cause is still unclear.").strip()
    scope = parsed.get("affected_scope") or []
    if not isinstance(scope, list):
        scope = [scope] if scope else []
    actions = parsed.get("suggested_actions") or []
    if not isinstance(actions, list):
        actions = [actions] if actions else []
    confidence = max(0, min(100, int(parsed.get("confidence") or 50)))

    lines = [
        "Summary:",
        summary,
        "",
        "Probable Root Cause:",
        root_cause,
        "",
        f"Confidence: {confidence}/100",
        "",
        "Affected Scope:",
    ]
    if scope:
        lines.extend([f"- {str(item).strip()}" for item in scope if str(item).strip()])
    else:
        lines.append("- Scope is still being determined from the logs.")
    lines.extend(["", "Suggested Actions:"])
    if actions:
        lines.extend([f"- {str(item).strip()}" for item in actions if str(item).strip()])
    else:
        lines.append("- No action suggested yet.")
    return "\n".join(lines).strip()


def _matching_open_incident_from_analysis(
    session: Session,
    *,
    open_incidents: list[Incident],
    parsed: dict,
) -> Incident | None:
    requested_id = parsed.get("existing_incident_id")
    if isinstance(requested_id, int):
        for incident in open_incidents:
            if incident.id == requested_id:
                return incident

    primary_source_ip = str(parsed.get("primary_source_ip") or "").strip()
    event_type = str(parsed.get("event_type") or "").strip()
    if primary_source_ip:
        for incident in open_incidents:
            if incident.primary_source_ip == primary_source_ip and (
                not event_type or incident.event_type == event_type
            ):
                return incident
    return None


def _link_analysis_evidence(
    session: Session,
    *,
    incident_id: int,
    evidence_log_ids: list[int],
    events_by_log_id: dict[int, NormalizedEvent],
) -> int:
    linked = 0
    seen_event_ids: set[int] = set()
    for log_id in evidence_log_ids:
        event = events_by_log_id.get(log_id)
        if event is None or event.id in seen_event_ids:
            continue
        seen_event_ids.add(event.id)
        existing = session.scalar(
            select(IncidentEventLink).where(
                IncidentEventLink.incident_id == incident_id,
                IncidentEventLink.event_id == event.id,
            )
        )
        if existing is None:
            session.add(IncidentEventLink(incident_id=incident_id, event_id=event.id))
            linked += 1
    return linked


def _store_incident_analysis_artifact(
    session: Session,
    *,
    incident: Incident,
    analysis: LLMAnalysis,
    parsed: dict,
) -> AIArtifact:
    artifact = AIArtifact(
        artifact_type="incident_log_summary",
        title=f"Incident #{incident.id} log summary",
        incident_id=incident.id,
        device_id=incident.primary_device_id,
        provider=analysis.provider,
        model=analysis.model,
        prompt_version=analysis.prompt_version,
        summary=str(parsed.get("summary") or "").strip() or None,
        root_cause=str(parsed.get("probable_root_cause") or "").strip() or None,
        confidence_score=_normalize_confidence_score(parsed.get("confidence"), default=50),
        readiness="informational",
        risk_explanation="Created directly from raw syslog window analysis.",
        evidence_refs_json={
            "log_ids": parsed.get("evidence_log_ids") or [],
        },
        proposed_actions_json={
            "items": parsed.get("suggested_actions") or [],
            "affected_scope": parsed.get("affected_scope") or [],
        },
        content_json={
            "analysis_id": analysis.id,
            "decision": analysis.decision,
            "parsed": parsed,
            "raw_text": analysis.raw_text,
        },
    )
    session.add(artifact)
    session.flush()
    return artifact


def _apply_llm_analysis_to_incident(
    session: Session,
    *,
    analysis: LLMAnalysis,
    parsed: dict,
    open_incidents: list[Incident],
    events_by_log_id: dict[int, NormalizedEvent],
    requested_by: str,
) -> tuple[Incident | None, str]:
    decision = str(parsed.get("decision") or "no_issue").strip().lower()
    if decision == "no_issue":
        return None, "no_issue"

    evidence_log_ids = [
        int(item)
        for item in parsed.get("evidence_log_ids", [])
        if str(item).isdigit()
    ] if isinstance(parsed.get("evidence_log_ids"), list) else []
    if not evidence_log_ids:
        evidence_log_ids = list(events_by_log_id.keys())[:5]

    target_incident = _matching_open_incident_from_analysis(
        session,
        open_incidents=open_incidents,
        parsed=parsed,
    )
    primary_source_ip = str(parsed.get("primary_source_ip") or "").strip() or None
    event_type = str(parsed.get("event_type") or "").strip() or "generic_syslog"
    severity = str(parsed.get("severity") or "medium").strip().lower() or "medium"
    summary = str(parsed.get("summary") or "No summary provided.").strip()
    probable_root_cause = str(parsed.get("probable_root_cause") or "").strip() or None
    affected_scope = parsed.get("affected_scope") if isinstance(parsed.get("affected_scope"), list) else []
    suggested_actions = parsed.get("suggested_actions") if isinstance(parsed.get("suggested_actions"), list) else []
    confidence = _normalize_confidence_score(parsed.get("confidence") or parsed.get("confidence_score"), default=50)

    primary_event = next(
        (
            event
            for log_id in evidence_log_ids
            for event in [events_by_log_id.get(log_id)]
            if event is not None
        ),
        None,
    )
    primary_device_id = primary_event.device_id if primary_event is not None else None
    if primary_source_ip is None and primary_event is not None:
        primary_source_ip = primary_event.source_ip

    if target_incident is None and primary_source_ip:
        target_incident = session.scalar(
            select(Incident).where(
                Incident.primary_source_ip == primary_source_ip,
                Incident.event_type == event_type,
                Incident.status.in_(_OPEN_INCIDENT_STATUSES),
            ).order_by(Incident.updated_at.desc(), Incident.id.desc()).limit(1)
        )

    created = False
    if target_incident is None:
        target_incident = Incident(
            title=str(parsed.get("incident_title") or summary or f"Incident from {primary_source_ip or 'syslog'}").strip(),
            status="new",
            severity=severity,
            source="llm_analyzer",
            event_type=event_type,
            correlation_key=f"llm:{primary_source_ip or 'unknown'}:{event_type}",
            primary_device_id=primary_device_id,
            primary_source_ip=primary_source_ip,
            summary=summary,
            event_count=0,
            last_event_time=primary_event.event_time if primary_event is not None else utcnow(),
        )
        session.add(target_incident)
        session.flush()
        target_incident.incident_no = f"INC-{target_incident.id:05d}"
        created = True
    else:
        if _normalize_incident_status(target_incident.status) == "resolved":
            target_incident.status = "new"
            target_incident.closed_at = None

    target_incident.title = str(parsed.get("incident_title") or target_incident.title or summary).strip() or target_incident.title
    target_incident.severity = severity
    target_incident.summary = summary
    target_incident.ai_summary = _incident_summary_from_analysis(parsed)
    target_incident.probable_root_cause = probable_root_cause
    target_incident.affected_scope_json = {"items": [str(item).strip() for item in affected_scope if str(item).strip()]}
    target_incident.confidence_score = confidence
    target_incident.recommendation = "\n".join([str(item).strip() for item in suggested_actions if str(item).strip()]) or target_incident.ai_summary
    target_incident.primary_device_id = primary_device_id or target_incident.primary_device_id
    target_incident.primary_source_ip = primary_source_ip or target_incident.primary_source_ip
    target_incident.last_event_time = primary_event.event_time if primary_event is not None else target_incident.last_event_time
    target_incident.requires_attention = True
    target_incident.last_analysis_id = analysis.id

    linked_count = _link_analysis_evidence(
        session,
        incident_id=target_incident.id,
        evidence_log_ids=evidence_log_ids,
        events_by_log_id=events_by_log_id,
    )
    target_incident.event_count += linked_count
    analysis.incident_id = target_incident.id
    artifact = _store_incident_analysis_artifact(
        session,
        incident=target_incident,
        analysis=analysis,
        parsed={**parsed, "evidence_log_ids": evidence_log_ids},
    )
    _record_incident_history(
        session,
        incident_id=target_incident.id,
        action="incident_created_from_logs" if created else "incident_updated_from_logs",
        actor=requested_by,
        actor_role="system",
        summary=(
            f"Incident auto-created from LLM log analysis #{analysis.id}"
            if created
            else f"Incident refreshed from LLM log analysis #{analysis.id}"
        ),
        payload={
            "analysis_id": analysis.id,
            "artifact_id": artifact.id,
            "decision": analysis.decision,
            "evidence_log_ids": evidence_log_ids,
        },
    )
    record_audit(
        session,
        actor=requested_by,
        actor_role="system",
        action="llm_incident_analysis_applied",
        entity_type="incident",
        entity_id=target_incident.id,
        status="created" if created else "updated",
        summary=(
            f"LLM analyzer created incident #{target_incident.id}"
            if created
            else f"LLM analyzer updated incident #{target_incident.id}"
        ),
        payload={
            "analysis_id": analysis.id,
            "decision": analysis.decision,
            "artifact_id": artifact.id,
        },
    )
    return target_incident, "created" if created else "updated"


def _distinct_non_empty_strings(session: Session, column) -> list[str]:
    return [
        value
        for value in session.scalars(
            select(column).where(column.is_not(None), column != "").distinct().order_by(column.asc())
        ).all()
        if value
    ]


def _commit_job_start(session: Session, job: Job) -> None:
    start_job(job)
    session.commit()
    session.refresh(job)


def _persist_job_failure(
    session: Session,
    *,
    job_id: int,
    error_text: str,
    incident_id: int | None = None,
    incident_status: str | None = None,
) -> None:
    session.rollback()

    failed_job = session.get(Job, job_id)
    if failed_job is not None:
        fail_job(failed_job, error_text)

    if incident_id is not None and incident_status is not None:
        incident = session.get(Incident, incident_id)
        if incident is not None and incident.status != incident_status:
            try:
                update_incident_status(
                    session,
                    incident_id=incident.id,
                    status=incident_status,
                    actor="system",
                    actor_role="system",
                    comment=f"Job failed: {error_text[:200]}",
                )
            except ValueError:
                incident.status = incident_status

    session.commit()


def run_inventory_sync(session: Session, requested_by: str = "manager") -> dict:
    with _job_lock("inventory"):
        job = create_job(
            session,
            job_type="sync_inventory",
            title="Sync inventory from CSV",
            requested_by=requested_by,
            target_type="inventory",
            target_ref="inventory.csv",
        )
        _commit_job_start(session, job)
        try:
            result = sync_inventory_from_csv(session)
            complete_job(job, summary="Inventory sync completed", result_json=result)
            session.commit()
            return {"job_id": job.id, **result}
        except Exception as exc:
            _persist_job_failure(session, job_id=job.id, error_text=str(exc))
            raise


def run_syslog_sync(session: Session, requested_by: str = "manager") -> dict:
    with _job_lock("syslog"):
        job = create_job(
            session,
            job_type="sync_syslog",
            title="Sync syslog from remote collector",
            requested_by=requested_by,
            target_type="syslog",
            target_ref="remote_collector",
        )
        _commit_job_start(session, job)
        try:
            result = sync_syslog_from_remote(session)
            complete_job(job, summary="Syslog sync completed", result_json=result)
            session.commit()
            return {"job_id": job.id, **result}
        except Exception as exc:
            _persist_job_failure(session, job_id=job.id, error_text=str(exc))
            raise


def ingest_syslog_push(
    session: Session,
    *,
    collector_name: str,
    events: list[dict[str, Any]],
) -> dict:
    records = [
        SyslogIngressRecord(
            source_ip=str(event.get("source_ip", "")).strip(),
            raw_message=str(event.get("raw_message", "")),
            file_path=event.get("file_path"),
            collector_name=collector_name,
            ingest_source="http_push",
            event_uid=event.get("event_uid"),
            reference_time=event.get("collector_time"),
            metadata=event.get("metadata") or {},
        )
        for event in events
        if str(event.get("raw_message", "")).strip()
    ]
    return ingest_syslog_records(session, records)


def list_events(
    session: Session,
    *,
    q: str | None = None,
    severity: str | None = None,
    event_type: str | None = None,
    incident_id: int | None = None,
    event_from: str | None = None,
    event_to: str | None = None,
    sort_by: str = "event_time",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> dict:
    query = select(NormalizedEvent)
    if incident_id is not None:
        event_ids = session.scalars(
            select(IncidentEventLink.event_id).where(IncidentEventLink.incident_id == incident_id)
        ).all()
        if not event_ids:
            return _page_payload(items=[], total=0, page=page, page_size=page_size, sort_by=sort_by, sort_dir=sort_dir)
        query = query.where(NormalizedEvent.id.in_(event_ids))

    conditions = []
    if q:
        like_value = f"%{q.strip()}%"
        conditions.append(or_(
            NormalizedEvent.summary.ilike(like_value),
            NormalizedEvent.hostname.ilike(like_value),
            NormalizedEvent.source_ip.ilike(like_value),
            NormalizedEvent.protocol.ilike(like_value),
            NormalizedEvent.interface_name.ilike(like_value),
            NormalizedEvent.neighbor.ilike(like_value),
            NormalizedEvent.event_type.ilike(like_value),
            NormalizedEvent.event_code.ilike(like_value),
            NormalizedEvent.facility.ilike(like_value),
        ))
    if severity:
        conditions.append(NormalizedEvent.severity == severity)
    if event_type:
        conditions.append(NormalizedEvent.event_type == event_type)
    event_from_dt = _parse_datetime_filter(event_from)
    event_to_dt = _parse_datetime_filter(event_to, end_of_day=True)
    if event_from_dt:
        conditions.append(NormalizedEvent.event_time >= event_from_dt)
    if event_to_dt:
        conditions.append(NormalizedEvent.event_time <= event_to_dt)
    if conditions:
        query = query.where(and_(*conditions))

    sort_columns = {
        "event_time": NormalizedEvent.event_time,
        "ingested_at": NormalizedEvent.created_at,
        "severity": NormalizedEvent.severity,
        "hostname": NormalizedEvent.hostname,
        "event_type": NormalizedEvent.event_type,
    }
    order_column = sort_columns.get(sort_by, NormalizedEvent.event_time)
    order_expr = order_column.desc().nullslast() if sort_dir == "desc" else order_column.asc().nullsfirst()
    query = query.order_by(order_expr, NormalizedEvent.id.desc())

    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    offset = max(page - 1, 0) * page_size
    events = session.scalars(query.offset(offset).limit(page_size)).all()
    return _page_payload(
        items=[serialize_event(event) for event in events],
        total=total,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
        facets={
            "severities": _distinct_non_empty_strings(session, NormalizedEvent.severity),
            "event_types": _distinct_non_empty_strings(session, NormalizedEvent.event_type),
        },
    )


def serialize_event(event: NormalizedEvent) -> dict:
    return {
        "id": event.id,
        "event_time": event.event_time.isoformat() if event.event_time else None,
        "ingested_at": event.created_at.isoformat() if event.created_at else None,
        "source_ip": event.source_ip,
        "hostname": event.hostname,
        "severity": event.severity,
        "facility": event.facility,
        "event_code": event.event_code,
        "event_type": event.event_type,
        "protocol": event.protocol,
        "interface_name": event.interface_name,
        "neighbor": event.neighbor,
        "state": event.state,
        "summary": event.summary,
        "details": event.details_json,
    }


def serialize_incident(session: Session, incident: Incident) -> dict:
    device = session.get(Device, incident.primary_device_id) if incident.primary_device_id else None
    return {
        "id": incident.id,
        "incident_no": incident.incident_no or f"INC-{incident.id:05d}",
        "title": incident.title,
        "status": _normalize_incident_status(incident.status),
        "severity": incident.severity,
        "source": incident.source,
        "event_type": incident.event_type,
        "correlation_key": incident.correlation_key,
        "primary_device_id": incident.primary_device_id,
        "primary_source_ip": incident.primary_source_ip,
        "hostname": device.hostname if device else None,
        "site": device.site if device else None,
        "summary": incident.summary,
        "ai_summary": incident.ai_summary,
        "probable_root_cause": incident.probable_root_cause,
        "affected_scope": (incident.affected_scope_json or {}).get("items", []),
        "confidence_score": incident.confidence_score,
        "last_analysis_id": incident.last_analysis_id,
        "recommendation": incident.recommendation,
        "assigned_to": incident.assigned_to,
        "assigned_at": _iso(incident.assigned_at),
        "acknowledged_by": incident.acknowledged_by,
        "acknowledged_at": _iso(incident.acknowledged_at),
        "resolved_by": incident.resolved_by,
        "resolution_notes": incident.resolution_notes,
        "event_count": incident.event_count,
        "requires_attention": incident.requires_attention,
        "opened_at": _iso(incident.opened_at),
        "updated_at": _iso(incident.updated_at),
        "closed_at": _iso(incident.closed_at),
        "last_event_time": _iso(incident.last_event_time),
        "incident_cluster_id": incident.incident_cluster_id,
    }


def list_incidents(
    session: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    site: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
    sort_by: str = "updated_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> dict:
    query = (
        select(Incident)
        .select_from(Incident)
        .outerjoin(Device, Device.id == Incident.primary_device_id)
    )

    conditions = []
    if q:
        like_value = f"%{q.strip()}%"
        conditions.append(or_(
            Incident.title.ilike(like_value),
            Incident.summary.ilike(like_value),
            Incident.ai_summary.ilike(like_value),
            Incident.probable_root_cause.ilike(like_value),
            Incident.event_type.ilike(like_value),
            Incident.primary_source_ip.ilike(like_value),
            Device.hostname.ilike(like_value),
            Device.site.ilike(like_value),
        ))
    if status:
        normalized_status = _normalize_incident_status(status)
        if normalized_status in {"new", "active"}:
            conditions.append(Incident.status.in_(["new", "open"]))
        elif normalized_status == "in_progress":
            conditions.append(Incident.status.in_(["in_progress", "investigating"]))
        elif normalized_status == "open":
            conditions.append(Incident.status.in_(_OPEN_INCIDENT_STATUSES))
        else:
            conditions.append(Incident.status == normalized_status)
    if severity:
        conditions.append(Incident.severity == severity)
    if site:
        conditions.append(Device.site == site)
    updated_from_dt = _parse_datetime_filter(updated_from)
    updated_to_dt = _parse_datetime_filter(updated_to, end_of_day=True)
    if updated_from_dt:
        conditions.append(Incident.updated_at >= updated_from_dt)
    if updated_to_dt:
        conditions.append(Incident.updated_at <= updated_to_dt)
    if conditions:
        query = query.where(and_(*conditions))

    sort_columns = {
        "updated_at": Incident.updated_at,
        "opened_at": Incident.opened_at,
        "closed_at": Incident.closed_at,
        "last_event_time": Incident.last_event_time,
        "severity": Incident.severity,
        "event_count": Incident.event_count,
    }
    order_column = sort_columns.get(sort_by, Incident.updated_at)
    order_expr = order_column.desc().nullslast() if sort_dir == "desc" else order_column.asc().nullsfirst()
    query = query.order_by(order_expr, Incident.id.desc())

    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    offset = max(page - 1, 0) * page_size
    incidents = session.scalars(query.offset(offset).limit(page_size)).all()
    return _page_payload(
        items=[serialize_incident(session, incident) for incident in incidents],
        total=total,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
        facets={
            "severities": _distinct_non_empty_strings(session, Incident.severity),
            "sites": _distinct_non_empty_strings(session, Device.site),
        },
    )


def get_incident_detail(session: Session, incident_id: int) -> dict | None:
    incident = session.get(Incident, incident_id)
    if incident is None:
        return None
    payload = serialize_incident(session, incident)
    payload["events"] = list_events(session, incident_id=incident_id, page_size=200)["items"]
    linked_approvals = session.scalars(
        select(Approval)
        .where(Approval.incident_id == incident_id)
        .order_by(Approval.requested_at.desc(), Approval.id.desc())
    ).all()
    linked_jobs = session.scalars(
        select(Job)
        .where(and_(Job.target_type == "incident", Job.target_ref == str(incident_id)))
        .order_by(Job.created_at.desc(), Job.id.desc())
    ).all()
    incident_audits = session.scalars(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "incident", AuditEntry.entity_id == incident_id)
        .order_by(AuditEntry.created_at.asc(), AuditEntry.id.asc())
    ).all()
    approval_ids = [approval.id for approval in linked_approvals]
    approval_audits = []
    if approval_ids:
        approval_audits = session.scalars(
            select(AuditEntry)
            .where(AuditEntry.entity_type == "approval", AuditEntry.entity_id.in_(approval_ids))
            .order_by(AuditEntry.created_at.asc(), AuditEntry.id.asc())
        ).all()
    artifacts = session.scalars(
        select(AIArtifact)
        .where(AIArtifact.incident_id == incident_id)
        .order_by(AIArtifact.created_at.desc(), AIArtifact.id.desc())
    ).all()
    history = session.scalars(
        select(IncidentHistory)
        .where(IncidentHistory.incident_id == incident_id)
        .order_by(IncidentHistory.created_at.asc(), IncidentHistory.id.asc())
    ).all()
    notifications = session.scalars(
        select(NotificationLog)
        .where(NotificationLog.incident_id == incident_id)
        .order_by(NotificationLog.created_at.desc(), NotificationLog.id.desc())
    ).all()
    payload["approvals"] = [serialize_approval(session, approval) for approval in linked_approvals]
    payload["jobs"] = [serialize_job(job) for job in linked_jobs]
    payload["audits"] = [serialize_audit(entry) for entry in [*incident_audits, *approval_audits]]
    payload["artifacts"] = [serialize_ai_artifact(artifact) for artifact in artifacts]
    payload["history"] = [serialize_incident_history(entry) for entry in history]
    payload["notifications"] = [serialize_notification_log(entry) for entry in notifications]
    payload["remediation_status"] = get_incident_remediation_status(session, incident_id)
    payload["feedback"] = get_incident_feedback(session, incident_id)
    payload["available_actions"] = list_action_catalog()
    payload["latest_analysis"] = serialize_llm_analysis(session.get(LLMAnalysis, incident.last_analysis_id)) if incident.last_analysis_id else None
    payload["latest_log_summary"] = next(
        (serialize_ai_artifact(artifact) for artifact in artifacts if artifact.artifact_type == "incident_log_summary"),
        None,
    )
    payload["latest_troubleshoot"] = next(
        (
            serialize_ai_artifact(artifact)
            for artifact in artifacts
            if artifact.artifact_type in {"incident_troubleshoot_structured", "incident_troubleshoot"}
        ),
        None,
    )
    payload["latest_execution_report"] = next(
        (serialize_ai_artifact(artifact) for artifact in artifacts if artifact.artifact_type == "execution_report"),
        None,
    )
    payload["latest_proposal"] = payload["approvals"][0] if payload["approvals"] else None
    feedback_items = payload["feedback"]
    payload["feedback_summary"] = {
        "count": len(feedback_items),
        "latest": feedback_items[0] if feedback_items else None,
    }
    return payload


def list_device_interfaces(session: Session, device_id: int) -> list[dict]:
    device = session.get(Device, device_id)
    if device is None:
        raise ValueError(f"Device {device_id} was not found")
    _backfill_device_interfaces(session, device_id)
    interfaces = session.scalars(
        select(DeviceInterface)
        .where(DeviceInterface.device_id == device_id)
        .order_by(DeviceInterface.last_event_time.desc().nullslast(), DeviceInterface.name.asc())
    ).all()
    return [serialize_device_interface(interface) for interface in interfaces]


def update_incident_status(
    session: Session,
    incident_id: int,
    *,
    status: str,
    actor: str,
    actor_role: str,
    comment: str | None = None,
) -> dict:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"Incident {incident_id} was not found")

    actor_role = normalize_role(actor_role)
    target_status = _normalize_incident_status(status)
    current_status = _normalize_incident_status(incident.status)
    if target_status not in {"new", "acknowledged", "in_progress", "monitoring", "resolved"}:
        raise ValueError(f"Unsupported incident status '{status}'")
    if not _incident_status_transition_allowed(current_status, target_status):
        raise ValueError(f"Incident cannot move from '{current_status}' to '{target_status}'")

    # Require notes when operator manually resolves (system auto-resolve from ops_loop is exempt)
    if target_status == "resolved" and actor != "ops_loop" and not (comment and comment.strip()):
        raise ValueError("Resolution notes are required when manually resolving an incident")

    now = utcnow()
    incident.status = target_status
    if target_status == "acknowledged":
        incident.acknowledged_by = actor
        incident.acknowledged_at = now
    if target_status == "resolved":
        incident.closed_at = now
        incident.resolved_by = actor
        if comment:
            incident.resolution_notes = comment
        incident.requires_attention = False
    else:
        incident.requires_attention = True
        if target_status != "resolved":
            incident.closed_at = None

    _record_incident_history(
        session,
        incident_id=incident_id,
        action="status_changed",
        actor=actor,
        actor_role=actor_role,
        from_status=current_status,
        to_status=target_status,
        summary=f"Incident moved to {target_status}",
        comment=comment,
        payload={"status": target_status},
    )
    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action="incident_status_changed",
        entity_type="incident",
        entity_id=incident_id,
        status=target_status,
        summary=f"Incident #{incident_id} moved to {target_status}",
        payload={"from_status": current_status, "to_status": target_status, "comment": comment},
    )
    if target_status == "resolved":
        auto_resolve_cluster_if_done(session, incident)
    return get_incident_detail(session, incident_id) or serialize_incident(session, incident)


def assign_incident(
    session: Session,
    incident_id: int,
    *,
    assignee: str,
    actor: str,
    actor_role: str,
    comment: str | None = None,
) -> dict:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"Incident {incident_id} was not found")
    if not assignee.strip():
        raise ValueError("Assignee is required")

    actor_role = normalize_role(actor_role)
    incident.assigned_to = assignee.strip()
    incident.assigned_at = utcnow()
    _record_incident_history(
        session,
        incident_id=incident_id,
        action="assigned",
        actor=actor,
        actor_role=actor_role,
        from_status=_normalize_incident_status(incident.status),
        to_status=_normalize_incident_status(incident.status),
        summary=f"Incident assigned to {incident.assigned_to}",
        comment=comment,
        payload={"assignee": incident.assigned_to},
    )
    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action="incident_assigned",
        entity_type="incident",
        entity_id=incident_id,
        status="assigned",
        summary=f"Incident #{incident_id} assigned to {incident.assigned_to}",
        payload={"assignee": incident.assigned_to, "comment": comment},
    )
    return get_incident_detail(session, incident_id) or serialize_incident(session, incident)


def resolve_incident(
    session: Session,
    incident_id: int,
    *,
    actor: str,
    actor_role: str,
    resolution_notes: str | None = None,
) -> dict:
    return update_incident_status(
        session,
        incident_id,
        status="resolved",
        actor=actor,
        actor_role=actor_role,
        comment=resolution_notes,
    )


def notify_incident(
    session: Session,
    incident_id: int,
    *,
    channel: str,
    actor: str,
    actor_role: str,
    recipient: str | None = None,
    message_text: str | None = None,
) -> dict:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"Incident {incident_id} was not found")

    actor_role = normalize_role(actor_role)
    channel_name = (channel or "").strip().lower()
    if channel_name not in {"email", "slack", "teams", "webhook"}:
        raise ValueError("Unsupported notification channel")

    message = message_text.strip() if message_text and message_text.strip() else (
        f"[{incident.severity.upper()}] {incident.title}\nStatus: {_normalize_incident_status(incident.status)}\nSummary: {incident.summary}"
    )
    log = NotificationLog(
        incident_id=incident_id,
        channel=channel_name,
        recipient=(recipient or "").strip() or None,
        message_text=message,
        requested_by=actor,
        requested_by_role=actor_role,
        status="mock_sent",
        response_json={"mode": "mock", "delivered": True},
    )
    session.add(log)
    session.flush()
    _record_incident_history(
        session,
        incident_id=incident_id,
        action="notified",
        actor=actor,
        actor_role=actor_role,
        from_status=_normalize_incident_status(incident.status),
        to_status=_normalize_incident_status(incident.status),
        summary=f"Incident notification sent via {channel_name}",
        payload={"notification_id": log.id, "channel": channel_name, "recipient": log.recipient},
    )
    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action="incident_notified",
        entity_type="incident",
        entity_id=incident_id,
        status="mock_sent",
        summary=f"Mock notification sent for incident #{incident_id}",
        payload={"notification_id": log.id, "channel": channel_name, "recipient": log.recipient},
    )
    return serialize_notification_log(log)


def get_incident_remediation_status(session: Session, incident_id: int) -> dict:
    approvals = session.scalars(
        select(Approval)
        .where(Approval.incident_id == incident_id)
        .order_by(Approval.requested_at.desc(), Approval.id.desc())
    ).all()
    latest = approvals[0] if approvals else None
    tasks = session.scalars(
        select(RemediationTask)
        .where(RemediationTask.incident_id == incident_id)
        .order_by(RemediationTask.approval_id.desc(), RemediationTask.phase.asc(), RemediationTask.step_order.asc())
    ).all()
    if latest is None:
        return {
            "approval_id": None,
            "status": "not_started",
            "progress": {"total": 0, "completed": 0, "failed": 0},
            "tasks": [],
        }

    relevant_tasks = [task for task in tasks if task.approval_id == latest.id]
    completed = sum(1 for task in relevant_tasks if task.status in {"succeeded", "completed"})
    failed = sum(1 for task in relevant_tasks if task.status.startswith("failed"))
    return {
        "approval_id": latest.id,
        "status": latest.execution_status,
        "progress": {
            "total": len(relevant_tasks),
            "completed": completed,
            "failed": failed,
        },
        "tasks": [serialize_remediation_task(task) for task in relevant_tasks],
    }


def run_incident_investigation(
    session: Session,
    incident_id: int,
    requested_by: str = "manager",
    requested_by_role: str = _ROLE_DEFAULTS["review_actor_role"],
) -> dict:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"Incident {incident_id} was not found")
    job = create_job(
        session,
        job_type="investigate_incident",
        title=f"Investigate incident #{incident_id}",
        requested_by=requested_by,
        target_type="incident",
        target_ref=str(incident_id),
    )
    previous_status = _normalize_incident_status(incident.status)
    if previous_status != "in_progress":
        try:
            update_incident_status(
                session,
                incident_id=incident.id,
                status="in_progress",
                actor=requested_by,
                actor_role=requested_by_role,
                comment="Investigation started",
            )
        except ValueError:
            pass  # Already in a valid state, continue
    _record_incident_history(
        session,
        incident_id=incident_id,
        action="investigation_requested",
        actor=requested_by,
        actor_role=requested_by_role,
        from_status=previous_status,
        to_status="in_progress",
        summary=f"AI investigation requested for incident #{incident_id}",
        payload={"job_id": job.id},
    )
    record_audit(
        session,
        actor=requested_by,
        actor_role=normalize_role(requested_by_role),
        action="incident_investigation_requested",
        entity_type="incident",
        entity_id=incident_id,
        status="requested",
        summary=f"AI investigation requested for incident #{incident_id}",
        payload={"job_id": job.id},
    )
    _commit_job_start(session, job)
    try:
        result = investigate_incident_with_llm(session, incident_id)
        if _normalize_incident_status(incident.status) != "resolved":
            try:
                update_incident_status(
                    session,
                    incident_id=incident.id,
                    status="monitoring",
                    actor=requested_by,
                    actor_role=requested_by_role,
                    comment="Investigation completed — monitoring for changes",
                )
            except ValueError:
                pass
        complete_job(job, summary="Incident investigation completed", result_json=result)
        _record_incident_history(
            session,
            incident_id=incident_id,
            action="investigation_completed",
            actor=requested_by,
            actor_role=requested_by_role,
            from_status="in_progress",
            to_status=_normalize_incident_status(incident.status),
            summary=f"AI investigation completed for incident #{incident_id}",
            payload={"job_id": job.id, "artifact_id": result.get("artifact_id")},
        )
        record_audit(
            session,
            actor=requested_by,
            actor_role=normalize_role(requested_by_role),
            action="incident_investigation_completed",
            entity_type="incident",
            entity_id=incident_id,
            status="completed",
            summary=f"AI investigation completed for incident #{incident_id}",
            payload={"job_id": job.id, "result": result},
        )
        session.commit()
        return {"job_id": job.id, **result}
    except Exception as exc:
        _persist_job_failure(
            session,
            job_id=job.id,
            error_text=str(exc),
            incident_id=incident_id,
            incident_status="new",
        )
        raise


def list_jobs(
    session: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> dict:
    query = select(Job)
    conditions = []
    if q:
        like_value = f"%{q.strip()}%"
        conditions.append(or_(
            Job.title.ilike(like_value),
            Job.summary.ilike(like_value),
            Job.target_ref.ilike(like_value),
            Job.requested_by.ilike(like_value),
            Job.job_type.ilike(like_value),
            Job.error_text.ilike(like_value),
        ))
    if status:
        conditions.append(Job.status == status)
    if job_type:
        conditions.append(Job.job_type == job_type)
    if conditions:
        query = query.where(and_(*conditions))

    sort_columns = {
        "created_at": Job.created_at,
        "started_at": Job.started_at,
        "completed_at": Job.completed_at,
        "status": Job.status,
        "job_type": Job.job_type,
    }
    order_column = sort_columns.get(sort_by, Job.created_at)
    order_expr = order_column.desc().nullslast() if sort_dir == "desc" else order_column.asc().nullsfirst()
    query = query.order_by(order_expr, Job.id.desc())

    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    offset = max(page - 1, 0) * page_size
    jobs = session.scalars(query.offset(offset).limit(page_size)).all()
    return _page_payload(
        items=[serialize_job(job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
        facets={
            "statuses": _distinct_non_empty_strings(session, Job.status),
            "job_types": _distinct_non_empty_strings(session, Job.job_type),
        },
    )


def serialize_job(job: Job) -> dict:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "title": job.title,
        "summary": job.summary,
        "target_type": job.target_type,
        "target_ref": job.target_ref,
        "requested_by": job.requested_by,
        "payload": job.payload_json,
        "result": job.result_json,
        "error_text": job.error_text,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _resolve_action_entry(action_id: str | None, commands: list[str]):
    readonly, _ = classify_command_set(commands)
    resolved = (
        get_action_catalog_entry(action_id)
        if action_id
        else get_action_catalog_entry("generic_readonly" if readonly else "generic_config_change")
    )
    if resolved is None:
        raise ValueError(f"Unknown action catalog entry '{action_id}'")
    return resolved


def create_approval(
    session: Session,
    *,
    title: str,
    requested_by: str,
    requested_by_role: str = _ROLE_DEFAULTS["requested_by_role"],
    target_host: str | None,
    commands_text: str | None,
    rollback_commands_text: str | None,
    verify_commands_text: str | None,
    rationale: str | None,
    risk_level: str,
    notes: str | None,
    incident_id: int | None = None,
    action_id: str | None = None,
    evidence_snapshot: dict | None = None,
) -> dict:
    requested_by_role = normalize_role(requested_by_role)
    config_commands = _split_commands(commands_text)
    verify_commands = _split_commands(verify_commands_text)
    rollback_commands = _split_commands(rollback_commands_text)
    action_entry = _resolve_action_entry(action_id, config_commands)
    policy = policy_decision_for_proposal(
        action=action_entry,
        commands=config_commands,
        verify_commands=verify_commands,
        rollback_commands=rollback_commands,
        actor_role=requested_by_role,
    )
    if not policy.allowed:
        raise PermissionError(policy.reason)

    evidence = evidence_snapshot or _build_incident_evidence_snapshot(session, incident_id)
    required_approval_role = max_role(policy.required_approval_role, approval_role_for_risk(risk_level))
    required_execution_role = max_role(policy.required_execution_role, execution_role_for_risk(risk_level))
    job = create_job(
        session,
        job_type="change_proposal",
        title=title,
        requested_by=requested_by,
        target_type="device" if target_host else "proposal",
        target_ref=target_host,
        payload_json={
            "incident_id": incident_id,
            "action_id": action_entry.action_id,
            "policy": policy.serialize(),
            "evidence_snapshot": evidence,
        },
    )
    job.status = "awaiting_approval"
    approval = Approval(
        job_id=job.id,
        incident_id=incident_id,
        title=title,
        status="pending",
        requested_by=requested_by,
        requested_by_role=requested_by_role,
        target_host=target_host,
        action_id=action_entry.action_id,
        commands_text=commands_text,
        rollback_commands_text=rollback_commands_text,
        verify_commands_text=verify_commands_text,
        diff_text=commands_text,
        rationale=rationale,
        risk_level=risk_level,
        required_approval_role=required_approval_role,
        required_execution_role=required_execution_role,
        readiness=policy.readiness,
        readiness_score=int(round(policy.readiness_score * 100)),
        execution_status="awaiting_approval",
        policy_snapshot_json={
            **policy.serialize(),
            "catalog_entry": action_entry.serialize(),
            "dual_approval_required": dual_approval_required(risk_level),
            "requested_risk_level": risk_level,
            "required_approval_role": required_approval_role,
            "required_execution_role": required_execution_role,
        },
        evidence_snapshot_json=evidence,
        notes=notes,
    )
    session.add(approval)
    session.flush()
    _replace_remediation_tasks(
        session,
        approval=approval,
        execute_commands=config_commands,
        verify_commands=verify_commands,
        rollback_commands=rollback_commands,
    )
    record_audit(
        session,
        actor=requested_by,
        actor_role=requested_by_role,
        action="approval_created",
        entity_type="approval",
        entity_id=approval.id,
        status="created",
        summary=f"Proposal #{approval.id} created for action '{action_entry.action_id}'",
        payload={
            "job_id": job.id,
            "incident_id": incident_id,
            "policy": approval.policy_snapshot_json,
            "evidence_snapshot": approval.evidence_snapshot_json,
        },
    )
    if incident_id is not None:
        _record_incident_history(
            session,
            incident_id=incident_id,
            action="proposal_created",
            actor=requested_by,
            actor_role=requested_by_role,
            from_status=None,
            to_status=None,
            summary=f"Change proposal #{approval.id} created",
            payload={"approval_id": approval.id, "action_id": action_entry.action_id},
        )
    return serialize_approval(session, approval)


def serialize_approval(session: Session, approval: Approval) -> dict:
    incident = session.get(Incident, approval.incident_id) if approval.incident_id else None
    action_entry = get_action_catalog_entry(approval.action_id)
    audits = _approval_related_audits(session, approval.id)
    execution_status = approval.execution_status
    if approval.status == "executed" and execution_status == "awaiting_approval":
        execution_status = "succeeded"
    elif approval.status == "approved" and execution_status == "awaiting_approval":
        execution_status = "approved"
    elif approval.status == "rejected" and execution_status == "awaiting_approval":
        execution_status = "failed_blocked"
    return {
        "id": approval.id,
        "job_id": approval.job_id,
        "incident_id": approval.incident_id,
        "incident_title": incident.title if incident else None,
        "title": approval.title,
        "status": approval.status,
        "execution_status": execution_status,
        "failure_category": approval.failure_category,
        "requested_by": approval.requested_by,
        "requested_by_role": approval.requested_by_role,
        "reviewed_by": approval.reviewed_by,
        "reviewed_by_role": approval.reviewed_by_role,
        "executed_by": approval.executed_by or (approval.reviewed_by if approval.status == "executed" else None),
        "executed_by_role": approval.executed_by_role or (approval.reviewed_by_role if approval.status == "executed" else None),
        "target_host": approval.target_host,
        "action_id": approval.action_id,
        "action": action_entry.serialize() if action_entry else None,
        "commands_text": approval.commands_text,
        "rollback_commands_text": approval.rollback_commands_text,
        "verify_commands_text": approval.verify_commands_text,
        "diff_text": approval.diff_text,
        "rationale": approval.rationale,
        "decision_comment": approval.decision_comment,
        "risk_level": approval.risk_level,
        "required_approval_role": approval.required_approval_role,
        "required_execution_role": approval.required_execution_role,
        "readiness": approval.readiness,
        "readiness_score": approval.readiness_score,
        "policy_snapshot": approval.policy_snapshot_json,
        "evidence_snapshot": approval.evidence_snapshot_json,
        "notes": approval.notes,
        "execution_output": approval.execution_output,
        "requested_at": _iso(approval.requested_at),
        "decided_at": _iso(approval.decided_at),
        "executed_at": _iso(approval.executed_at),
        "audit_entries": [serialize_audit(entry) for entry in audits],
    }


def list_approvals(
    session: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    risk_level: str | None = None,
    sort_by: str = "requested_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> dict:
    query = (
        select(Approval)
        .select_from(Approval)
        .outerjoin(Incident, Incident.id == Approval.incident_id)
    )
    conditions = []
    if q:
        like_value = f"%{q.strip()}%"
        conditions.append(or_(
            Approval.title.ilike(like_value),
            Approval.target_host.ilike(like_value),
            Approval.requested_by.ilike(like_value),
            Approval.reviewed_by.ilike(like_value),
            Approval.rationale.ilike(like_value),
            Approval.commands_text.ilike(like_value),
            Approval.action_id.ilike(like_value),
            Incident.title.ilike(like_value),
        ))
    if status:
        conditions.append(Approval.status == status)
    if risk_level:
        conditions.append(Approval.risk_level == risk_level)
    if conditions:
        query = query.where(and_(*conditions))

    sort_columns = {
        "requested_at": Approval.requested_at,
        "decided_at": Approval.decided_at,
        "executed_at": Approval.executed_at,
        "risk_level": Approval.risk_level,
        "status": Approval.status,
    }
    order_column = sort_columns.get(sort_by, Approval.requested_at)
    order_expr = order_column.desc().nullslast() if sort_dir == "desc" else order_column.asc().nullsfirst()
    query = query.order_by(order_expr, Approval.id.desc())

    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    offset = max(page - 1, 0) * page_size
    approvals = session.scalars(query.offset(offset).limit(page_size)).all()
    return _page_payload(
        items=[serialize_approval(session, approval) for approval in approvals],
        total=total,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
        facets={
            "statuses": _distinct_non_empty_strings(session, Approval.status),
            "risk_levels": _distinct_non_empty_strings(session, Approval.risk_level),
        },
    )

def _split_commands(value: str | None) -> list[str]:
    if not value:
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]


def _ensure_execution_succeeded(output: str, *, command: str) -> None:
    stripped = output.lstrip()
    if stripped.startswith(_EXECUTION_ERROR_PREFIXES):
        failure_category, status_code = _execution_failure_from_output(stripped)
        raise ApprovalExecutionError(
            f"Execution failed for '{command}': {stripped}",
            status_code=status_code,
            failure_category=failure_category,
        )
    lowered = stripped.lower()
    if any(marker in lowered for marker in _CLI_ERROR_MARKERS):
        raise ApprovalExecutionError(
            f"Execution failed for '{command}': CLI returned an error in the device output.",
            status_code=502,
            failure_category="failed_command",
        )


def review_approval(
    session: Session,
    approval_id: int,
    *,
    actor: str,
    decision: str,
    actor_role: str = _ROLE_DEFAULTS["review_actor_role"],
    comment: str | None = None,
) -> dict:
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise ValueError(f"Approval {approval_id} was not found")
    if decision not in {"approved", "rejected"}:
        raise ValueError(f"Unsupported decision: {decision}")
    if approval.status not in {"pending", "awaiting_second_approval"}:
        raise ValueError(f"Approval cannot be reviewed from status '{approval.status}'")

    actor_role = normalize_role(actor_role)
    require_role(actor_role, approval.required_approval_role, action=f"{decision} approval #{approval.id}")

    job = session.get(Job, approval.job_id) if approval.job_id else None
    if decision == "rejected":
        approval.status = "rejected"
        approval.execution_status = "failed_blocked"
        approval.reviewed_by = actor
        approval.reviewed_by_role = actor_role
        approval.decision_comment = comment
        approval.failure_category = "rejected"
        approval.decided_at = utcnow()
        for task in session.scalars(select(RemediationTask).where(RemediationTask.approval_id == approval.id)).all():
            task.status = "failed_blocked"
            task.completed_at = utcnow()
            task.output_text = comment or "Rejected before execution"
        if job is not None:
            job.status = "rejected"
            job.summary = f"Proposal rejected by {actor}"
        record_audit(
            session,
            actor=actor,
            actor_role=actor_role,
            action="approval_rejected",
            entity_type="approval",
            entity_id=approval.id,
            status="rejected",
            summary=f"Proposal #{approval.id} rejected",
            payload={"comment": comment},
        )
        if approval.incident_id:
            _record_incident_history(
                session,
                incident_id=approval.incident_id,
                action="proposal_rejected",
                actor=actor,
                actor_role=actor_role,
                summary=f"Proposal #{approval.id} rejected",
                comment=comment,
                payload={"approval_id": approval.id},
            )
            # Move incident back to in_progress so operator can re-troubleshoot
            incident_obj = session.get(Incident, approval.incident_id)
            if incident_obj is not None and incident_obj.status not in ("resolved", "new"):
                try:
                    update_incident_status(
                        session, approval.incident_id,
                        status="in_progress", actor=actor, actor_role=actor_role,
                        comment=f"Proposal #{approval.id} rejected — moved back to in_progress for re-evaluation.",
                    )
                except (ValueError, Exception):
                    pass
        return serialize_approval(session, approval)

    prior_approvals = _approval_audit_approvers(session, approval.id)
    approval.decided_at = utcnow()
    approval.decision_comment = comment
    if dual_approval_required(approval.risk_level):
        if approval.status == "pending":
            approval.status = "awaiting_second_approval"
            approval.execution_status = "awaiting_second_approval"
            approval.reviewed_by = actor
            approval.reviewed_by_role = actor_role
            if job is not None:
                job.status = "awaiting_approval"
                job.summary = f"First approval captured from {actor}"
            record_audit(
                session,
                actor=actor,
                actor_role=actor_role,
                action="approval_approved",
                entity_type="approval",
                entity_id=approval.id,
                status="first_approval",
                summary=f"First approval recorded for proposal #{approval.id}",
                payload={"comment": comment, "requires_second_approval": True},
            )
            if approval.incident_id:
                _record_incident_history(
                    session,
                    incident_id=approval.incident_id,
                    action="proposal_first_approved",
                    actor=actor,
                    actor_role=actor_role,
                    summary=f"First approval recorded for proposal #{approval.id}",
                    comment=comment,
                    payload={"approval_id": approval.id},
                )
            return serialize_approval(session, approval)

        prior_actors = {entry.actor for entry in prior_approvals}
        if actor in prior_actors:
            raise PermissionError("A second approval must come from a different reviewer.")

    approval.status = "approved"
    approval.execution_status = "approved"
    approval.reviewed_by = actor
    approval.reviewed_by_role = actor_role
    approval.failure_category = None
    if job is not None:
        job.status = "approved"
        job.summary = f"Proposal approved by {actor}"
    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action="approval_approved",
        entity_type="approval",
        entity_id=approval.id,
        status="approved",
        summary=f"Proposal #{approval.id} approved",
        payload={
            "comment": comment,
            "required_execution_role": approval.required_execution_role,
            "dual_approval_required": dual_approval_required(approval.risk_level),
        },
    )
    if approval.incident_id:
        _record_incident_history(
            session,
            incident_id=approval.incident_id,
            action="proposal_approved",
            actor=actor,
            actor_role=actor_role,
            summary=f"Proposal #{approval.id} approved",
            comment=comment,
            payload={"approval_id": approval.id},
        )
    return serialize_approval(session, approval)


def execute_approval(
    session: Session,
    approval_id: int,
    *,
    actor: str,
    actor_role: str = _ROLE_DEFAULTS["execute_actor_role"],
) -> dict:
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise ValueError(f"Approval {approval_id} was not found")
    if approval.status != "approved":
        raise ValueError("Approval must be approved before execution")
    if not approval.target_host:
        raise ValueError("Approval has no target host")

    device = session.scalar(
        select(Device).where(Device.hostname == approval.target_host)
    )
    if device is None:
        device = session.scalar(select(Device).where(Device.mgmt_ip == approval.target_host))
    if device is None:
        raise ValueError(f"Target device '{approval.target_host}' was not found")

    actor_role = normalize_role(actor_role)
    require_role(actor_role, approval.required_execution_role, action=f"execute approval #{approval.id}")

    config_commands = _split_commands(approval.commands_text)
    verify_commands = _split_commands(approval.verify_commands_text)
    if not config_commands:
        raise ValueError("Approval has no commands to execute")

    readonly = all(cmd.lower().startswith(("show", "sh ", "ping", "traceroute", "tracert", "display")) for cmd in config_commands)
    job = session.get(Job, approval.job_id) if approval.job_id else None
    outputs: list[str] = []
    output_steps: list[dict[str, Any]] = []
    current_phase = "execute"
    current_step_order = 1

    approval.execution_status = "executing"
    approval.failure_category = None

    try:
        if readonly:
            for step_order, command in enumerate(config_commands, start=1):
                current_phase = "execute"
                current_step_order = step_order
                _set_task_state(
                    session,
                    approval_id=approval.id,
                    phase="execute",
                    step_order=step_order,
                    status="running",
                )
                output = execute_cli(device.mgmt_ip, device.os_platform, command)
                _ensure_execution_succeeded(output, command=command)
                _set_task_state(
                    session,
                    approval_id=approval.id,
                    phase="execute",
                    step_order=step_order,
                    status="succeeded",
                    output_text=output,
                )
                outputs.append(f"$ {command}\n{output}")
                output_steps.append({"phase": "execute", "command": command, "output": output})
        else:
            for step_order, _command in enumerate(config_commands, start=1):
                current_phase = "execute"
                current_step_order = step_order
                _set_task_state(
                    session,
                    approval_id=approval.id,
                    phase="execute",
                    step_order=step_order,
                    status="running",
                )
            output = execute_config(device.mgmt_ip, device.os_platform, config_commands)
            _ensure_execution_succeeded(output, command="config set")
            for step_order, _command in enumerate(config_commands, start=1):
                _set_task_state(
                    session,
                    approval_id=approval.id,
                    phase="execute",
                    step_order=step_order,
                    status="succeeded",
                    output_text=output,
                )
            outputs.append(output)
            output_steps.append({"phase": "execute", "command": "config set", "output": output})

        if verify_commands:
            approval.execution_status = "verify_pending"
            outputs.append("## Verification")
            for step_order, command in enumerate(verify_commands, start=1):
                current_phase = "verify"
                current_step_order = step_order
                _set_task_state(
                    session,
                    approval_id=approval.id,
                    phase="verify",
                    step_order=step_order,
                    status="running",
                )
                output = execute_cli(device.mgmt_ip, device.os_platform, command)
                _ensure_execution_succeeded(output, command=command)
                _set_task_state(
                    session,
                    approval_id=approval.id,
                    phase="verify",
                    step_order=step_order,
                    status="succeeded",
                    output_text=output,
                )
                outputs.append(f"$ {command}\n{output}")
                output_steps.append({"phase": "verify", "command": command, "output": output})
    except ApprovalExecutionError as exc:
        if not readonly and current_phase == "execute":
            for step_order, _command in enumerate(config_commands, start=1):
                _set_task_state(
                    session,
                    approval_id=approval.id,
                    phase="execute",
                    step_order=step_order,
                    status=exc.failure_category,
                    output_text=str(exc),
                )
        else:
            _set_task_state(
                session,
                approval_id=approval.id,
                phase=current_phase,
                step_order=current_step_order,
                status=exc.failure_category,
                output_text=str(exc),
            )
        approval.status = "failed"
        approval.execution_status = exc.failure_category
        approval.failure_category = exc.failure_category
        approval.execution_output = "\n\n".join(outputs + [str(exc)]).strip()
        if job is not None:
            job.status = "failed"
            job.completed_at = utcnow()
            job.summary = f"Execution failed on {device.hostname}"
            job.error_text = str(exc)
            job.result_json = {
                "readonly": readonly,
                "target_host": device.hostname,
                "commands": config_commands,
                "verify_commands": verify_commands,
                "steps": output_steps,
            }
        record_audit(
            session,
            actor=actor,
            actor_role=actor_role,
            action="approval_execution_failed",
            entity_type="approval",
            entity_id=approval.id,
            status=exc.failure_category,
            summary=f"Execution failed for proposal #{approval.id}",
            payload={
                "target_host": device.hostname,
                "steps": output_steps,
                "error": str(exc),
            },
        )
        if approval.incident_id:
            _record_incident_history(
                session,
                incident_id=approval.incident_id,
                action="execution_failed",
                actor=actor,
                actor_role=actor_role,
                summary=f"Proposal #{approval.id} failed during execution",
                comment=str(exc),
                payload={"approval_id": approval.id, "failure_category": exc.failure_category},
            )
        session.commit()
        raise

    approval.status = "executed"
    approval.executed_by = actor
    approval.executed_by_role = actor_role
    approval.executed_at = utcnow()
    approval.execution_status = "succeeded" if (readonly or verify_commands) else "partial_success"
    approval.execution_output = "\n\n".join(outputs)

    if job is not None:
        job.status = "executed"
        job.completed_at = utcnow()
        job.summary = f"Executed by {actor}"
        job.result_json = {
            "readonly": readonly,
            "target_host": device.hostname,
            "commands": config_commands,
            "verify_commands": verify_commands,
            "steps": output_steps,
        }

    incident = None
    if approval.incident_id:
        incident = session.get(Incident, approval.incident_id)
        if incident is not None and incident.status != "resolved":
            try:
                update_incident_status(
                    session,
                    incident_id=incident.id,
                    status="monitoring",
                    actor=actor,
                    actor_role=actor_role,
                    comment="Approved commands executed — monitoring for verification",
                )
            except ValueError:
                pass
        _record_incident_history(
            session,
            incident_id=approval.incident_id,
            action="executed",
            actor=actor,
            actor_role=actor_role,
            summary=f"Proposal #{approval.id} executed on {device.hostname}",
            payload={"approval_id": approval.id, "execution_status": approval.execution_status},
        )

    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action="approval_executed",
        entity_type="approval",
        entity_id=approval.id,
        status=approval.execution_status,
        summary=f"Proposal #{approval.id} executed on {device.hostname}",
        payload={
            "target_host": device.hostname,
            "readonly": readonly,
            "commands": config_commands,
            "verify_commands": verify_commands,
            "steps": output_steps,
        },
    )
    session.add(
        AIArtifact(
            artifact_type="execution_report",
            title=f"Execution report for proposal #{approval.id}",
            incident_id=approval.incident_id,
            device_id=device.id,
            approval_id=approval.id,
            summary=(
                f"Executed {len(config_commands)} command(s) on {device.hostname}."
                if approval.execution_status in {"succeeded", "partial_success"}
                else f"Execution completed with status {approval.execution_status} on {device.hostname}."
            ),
            root_cause=incident.probable_root_cause if approval.incident_id and incident is not None else None,
            confidence_score=100,
            readiness="informational",
            risk_explanation="Execution report generated from actual command results.",
            evidence_refs_json={
                "approval_id": approval.id,
                "task_count": len(output_steps),
            },
            proposed_actions_json={
                "items": ["Review verification output before closing the incident."],
            },
            content_json={
                "target_host": device.hostname,
                "readonly": readonly,
                "execution_status": approval.execution_status,
                "failure_category": approval.failure_category,
                "steps": output_steps,
                "raw_output": approval.execution_output,
            },
        )
    )
    return serialize_approval(session, approval)


def devices_payload(
    session: Session,
    *,
    q: str | None = None,
    site: str | None = None,
    role: str | None = None,
    has_open_incidents: bool = False,
    sort_by: str = "hostname",
    sort_dir: str = "asc",
    page: int = 1,
    page_size: int = 25,
) -> dict:
    return list_devices_with_stats(
        session,
        q=q,
        site=site,
        role=role,
        has_open_incidents=has_open_incidents,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )


def list_action_catalog_payload() -> list[dict]:
    return list_action_catalog()


def get_device_detail(session: Session, device_id: int) -> dict | None:
    device = session.get(Device, device_id)
    if device is None:
        return None
    _backfill_device_interfaces(session, device_id)

    recent_events = session.scalars(
        select(NormalizedEvent)
        .where(NormalizedEvent.device_id == device_id)
        .order_by(NormalizedEvent.event_time.desc().nullslast(), NormalizedEvent.id.desc())
        .limit(20)
    ).all()
    recent_incidents = session.scalars(
        select(Incident)
        .where(Incident.primary_device_id == device_id)
        .order_by(Incident.updated_at.desc(), Incident.id.desc())
        .limit(12)
    ).all()
    recent_jobs = session.scalars(
        select(Job)
        .where(Job.target_ref.in_([device.hostname, device.mgmt_ip]))
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(12)
    ).all()
    recent_approvals = session.scalars(
        select(Approval)
        .where(Approval.target_host.in_([device.hostname, device.mgmt_ip]))
        .order_by(Approval.requested_at.desc(), Approval.id.desc())
        .limit(12)
    ).all()
    artifacts = session.scalars(
        select(AIArtifact)
        .where(AIArtifact.device_id == device_id)
        .order_by(AIArtifact.created_at.desc(), AIArtifact.id.desc())
        .limit(10)
    ).all()
    interfaces = session.scalars(
        select(DeviceInterface)
        .where(DeviceInterface.device_id == device_id)
        .order_by(DeviceInterface.last_event_time.desc().nullslast(), DeviceInterface.name.asc())
        .limit(40)
    ).all()
    related_devices = session.scalars(
        select(Device)
        .where(Device.site == device.site, Device.id != device.id)
        .order_by(Device.hostname.asc())
        .limit(8)
    ).all()
    open_incident_count = session.scalar(
        select(func.count()).select_from(Incident).where(
            Incident.primary_device_id == device_id,
            Incident.status.in_(_OPEN_INCIDENT_STATUSES),
        )
    ) or 0
    site_incident_count = session.scalar(
        select(func.count())
        .select_from(Incident)
        .join(Device, Device.id == Incident.primary_device_id)
        .where(Device.site == device.site, Incident.status.in_(_OPEN_INCIDENT_STATUSES))
    ) or 0
    site_event_count = session.scalar(
        select(func.count())
        .select_from(NormalizedEvent)
        .join(Device, Device.id == NormalizedEvent.device_id)
        .where(Device.site == device.site)
    ) or 0
    last_event = recent_events[0] if recent_events else None
    last_successful_checks = [
        serialize_approval(session, approval)
        for approval in recent_approvals
        if approval.status == "executed" and approval.execution_status in {"succeeded", "partial_success"}
    ][:5]

    return {
        "id": device.id,
        "hostname": device.hostname,
        "mgmt_ip": device.mgmt_ip,
        "os_platform": device.os_platform,
        "device_role": device.device_role,
        "site": device.site,
        "version": device.version,
        "vendor": device.vendor,
        "enabled": device.enabled,
        "metadata": device.metadata_json,
        "open_incident_count": open_incident_count,
        "last_seen": _iso(last_event.event_time) if last_event else None,
        "last_event_summary": last_event.summary if last_event else None,
        "reachable": None,
        "last_known_config_snapshot": None,
        "recent_events": [serialize_event(event) for event in recent_events],
        "recent_incidents": [serialize_incident(session, incident) for incident in recent_incidents],
        "recent_jobs": [serialize_job(job) for job in recent_jobs],
        "recent_approvals": [serialize_approval(session, approval) for approval in recent_approvals],
        "last_successful_checks": last_successful_checks,
        "artifacts": [serialize_ai_artifact(artifact) for artifact in artifacts],
        "interfaces": [serialize_device_interface(interface) for interface in interfaces],
        "related_devices": [
            {
                "id": related.id,
                "hostname": related.hostname,
                "mgmt_ip": related.mgmt_ip,
                "device_role": related.device_role,
                "site": related.site,
                "os_platform": related.os_platform,
            }
            for related in related_devices
        ],
        "blast_radius": {
            "site": device.site,
            "site_device_count": len(related_devices) + 1,
            "site_open_incidents": int(site_incident_count),
            "site_event_count": int(site_event_count),
            "impacted_segment": f"Site {device.site}",
        },
    }


def run_device_summary(
    session: Session,
    device_id: int,
    *,
    requested_by: str = "manager",
    requested_by_role: str = _ROLE_DEFAULTS["review_actor_role"],
) -> dict:
    device = session.get(Device, device_id)
    if device is None:
        raise ValueError(f"Device {device_id} was not found")
    job = create_job(
        session,
        job_type="summarize_device",
        title=f"Summarize device #{device_id}",
        requested_by=requested_by,
        target_type="device",
        target_ref=str(device_id),
    )
    record_audit(
        session,
        actor=requested_by,
        actor_role=normalize_role(requested_by_role),
        action="device_summary_requested",
        entity_type="device",
        entity_id=device_id,
        status="requested",
        summary=f"AI summary requested for device #{device_id}",
        payload={"job_id": job.id},
    )
    _commit_job_start(session, job)
    try:
        result = summarize_device_with_llm(session, device_id)
        complete_job(job, summary="Device summary completed", result_json=result)
        record_audit(
            session,
            actor=requested_by,
            actor_role=normalize_role(requested_by_role),
            action="device_summary_completed",
            entity_type="device",
            entity_id=device_id,
            status="completed",
            summary=f"AI summary completed for device #{device_id}",
            payload={"job_id": job.id, "result": result},
        )
        session.commit()
        return {"job_id": job.id, **result}
    except Exception as exc:
        session.rollback()
        failed_job = session.get(Job, job.id)
        if failed_job is not None:
            fail_job(failed_job, str(exc))
        session.commit()
        raise


def run_site_analysis(
    session: Session,
    *,
    site: str,
    requested_by: str = "manager",
    requested_by_role: str = _ROLE_DEFAULTS["review_actor_role"],
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    job = create_job(
        session,
        job_type="analyze_site",
        title=f"Analyze site {site}",
        requested_by=requested_by,
        target_type="site",
        target_ref=site,
        payload_json={"site": site, "start_time": start_time, "end_time": end_time},
    )
    _commit_job_start(session, job)
    try:
        result = analyze_site_with_llm(session, site=site, start_time=start_time, end_time=end_time)
        complete_job(job, summary=f"Site analysis completed for {site}", result_json=result)
        record_audit(
            session,
            actor=requested_by,
            actor_role=normalize_role(requested_by_role),
            action="site_analysis_completed",
            entity_type="site",
            entity_id=None,
            status="completed",
            summary=f"Site analysis completed for {site}",
            payload={"site": site, "job_id": job.id, "result": result},
        )
        session.commit()
        return {"job_id": job.id, **result}
    except Exception as exc:
        _persist_job_failure(session, job_id=job.id, error_text=str(exc))
        raise


def run_device_analysis(
    session: Session,
    *,
    device_id: int,
    requested_by: str = "manager",
    requested_by_role: str = _ROLE_DEFAULTS["review_actor_role"],
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    device = session.get(Device, device_id)
    if device is None:
        raise ValueError(f"Device {device_id} was not found")
    job = create_job(
        session,
        job_type="analyze_device",
        title=f"Analyze device {device.hostname}",
        requested_by=requested_by,
        target_type="device",
        target_ref=str(device_id),
        payload_json={"device_id": device_id, "start_time": start_time, "end_time": end_time},
    )
    _commit_job_start(session, job)
    try:
        result = analyze_device_focus_with_llm(session, device_id=device_id, start_time=start_time, end_time=end_time)
        complete_job(job, summary=f"Device analysis completed for {device.hostname}", result_json=result)
        record_audit(
            session,
            actor=requested_by,
            actor_role=normalize_role(requested_by_role),
            action="device_analysis_completed",
            entity_type="device",
            entity_id=device_id,
            status="completed",
            summary=f"Device analysis completed for {device.hostname}",
            payload={"job_id": job.id, "result": result},
        )
        session.commit()
        return {"job_id": job.id, **result}
    except Exception as exc:
        _persist_job_failure(session, job_id=job.id, error_text=str(exc))
        raise


def run_global_analysis(
    session: Session,
    *,
    requested_by: str = "manager",
    requested_by_role: str = _ROLE_DEFAULTS["review_actor_role"],
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    job = create_job(
        session,
        job_type="analyze_global",
        title="Analyze global network state",
        requested_by=requested_by,
        target_type="global",
        target_ref="network",
        payload_json={"start_time": start_time, "end_time": end_time},
    )
    _commit_job_start(session, job)
    try:
        result = analyze_global_with_llm(session, start_time=start_time, end_time=end_time)
        complete_job(job, summary="Global analysis completed", result_json=result)
        record_audit(
            session,
            actor=requested_by,
            actor_role=normalize_role(requested_by_role),
            action="global_analysis_completed",
            entity_type="global",
            entity_id=None,
            status="completed",
            summary="Global analysis completed",
            payload={"job_id": job.id, "result": result},
        )
        session.commit()
        return {"job_id": job.id, **result}
    except Exception as exc:
        _persist_job_failure(session, job_id=job.id, error_text=str(exc))
        raise


def run_incident_chat(
    session: Session,
    *,
    incident_id: int,
    message: str,
    requested_by: str = "manager",
    requested_by_role: str = _ROLE_DEFAULTS["review_actor_role"],
    history: list[dict] | None = None,
) -> dict:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"Incident {incident_id} was not found")
    result = chat_incident_with_llm(session, incident_id=incident_id, message=message, history=history)
    record_audit(
        session,
        actor=requested_by,
        actor_role=normalize_role(requested_by_role),
        action="incident_chat_reply",
        entity_type="incident",
        entity_id=incident_id,
        status="completed",
        summary=f"Incident chat response generated for incident #{incident_id}",
        payload={"message": message, "artifact_id": result.get("artifact_id")},
    )
    return result


def run_incident_scan(session: Session, *, requested_by: str = "manager") -> dict:
    with _job_lock("incident_scan"):
        job = create_job(
            session,
            job_type="scan_incidents",
            title="Analyze raw logs into incidents",
            requested_by=requested_by,
            target_type="incident_scan",
            target_ref="raw_logs",
        )
        _commit_job_start(session, job)
        latest_scan = session.scalar(
            select(ScanHistory)
            .where(ScanHistory.status == "succeeded")
            .order_by(ScanHistory.started_at.desc(), ScanHistory.id.desc())
            .limit(1)
        )
        scan = ScanHistory(
            requested_by=requested_by,
            status="running",
        )
        session.add(scan)
        session.flush()
        try:
            query = select(RawLog).order_by(RawLog.ingested_at.asc(), RawLog.id.asc())
            if latest_scan and latest_scan.last_event_created_at is not None:
                query = query.where(
                    or_(
                        RawLog.ingested_at > latest_scan.last_event_created_at,
                        and_(
                            RawLog.ingested_at == latest_scan.last_event_created_at,
                            RawLog.id > (latest_scan.last_event_id or 0),
                        ),
                    )
                )
            raw_logs = session.scalars(query.limit(250)).all()
            open_incidents = session.scalars(
                select(Incident)
                .where(Incident.status.in_(_OPEN_INCIDENT_STATUSES))
                .order_by(Incident.updated_at.desc(), Incident.id.desc())
                .limit(25)
            ).all()

            last_log = raw_logs[-1] if raw_logs else None
            created = 0
            updated = 0
            no_issue = 0
            analysis_id = None
            touched_incident_id = None
            analysis_decision = "no_issue"

            if raw_logs:
                window_start = min((item.log_time or item.ingested_at) for item in raw_logs)
                window_end = max((item.log_time or item.ingested_at) for item in raw_logs)
                analysis_result = analyze_log_window_with_llm(
                    session,
                    raw_logs=raw_logs,
                    open_incidents=open_incidents,
                    window_start=window_start,
                    window_end=window_end,
                )
                analysis_id = int(analysis_result["analysis_id"])
                analysis_decision = str(analysis_result["decision"])
                analysis = session.get(LLMAnalysis, analysis_id)
                parsed = analysis_result["parsed"]
                events_by_log_id = analysis_result["events_by_log_id"]
                applied_incident, applied_action = _apply_llm_analysis_to_incident(
                    session,
                    analysis=analysis,
                    parsed=parsed,
                    open_incidents=open_incidents,
                    events_by_log_id=events_by_log_id,
                    requested_by=requested_by,
                )
                if applied_action == "created":
                    created = 1
                    touched_incident_id = applied_incident.id if applied_incident else None
                elif applied_action == "updated":
                    updated = 1
                    touched_incident_id = applied_incident.id if applied_incident else None
                else:
                    no_issue = 1
            else:
                no_issue = 1

            scan.status = "succeeded"
            scan.completed_at = utcnow()
            scan.last_event_created_at = last_log.ingested_at if last_log else latest_scan.last_event_created_at if latest_scan else None
            scan.last_event_id = last_log.id if last_log else latest_scan.last_event_id if latest_scan else None
            scan.events_analyzed = len(raw_logs)
            scan.incidents_opened = created
            scan.incidents_resolved = 0
            scan.incidents_touched = created + updated
            scan.result_json = {
                "logs_analyzed": len(raw_logs),
                "incidents_created": created,
                "incidents_updated": updated,
                "no_issue_windows": no_issue,
                "analysis_id": analysis_id,
                "analysis_decision": analysis_decision,
                "touched_incident_id": touched_incident_id,
            }
            complete_job(job, summary="Incident scan completed", result_json=scan.result_json)
            record_audit(
                session,
                actor=requested_by,
                actor_role="system",
                action="incident_scan_completed",
                entity_type="scan_history",
                entity_id=scan.id,
                status="completed",
                summary="Incident scan completed",
                payload=scan.result_json,
            )
            session.commit()
            return {"job_id": job.id, "scan_id": scan.id, **scan.result_json}
        except Exception as exc:
            scan.status = "failed"
            scan.completed_at = utcnow()
            scan.error_text = str(exc)
            _persist_job_failure(session, job_id=job.id, error_text=str(exc))
            raise


def global_search_payload(session: Session, q: str, *, limit: int = 6) -> dict:
    term = q.strip()
    if not term:
        return {"query": "", "devices": [], "incidents": [], "jobs": [], "approvals": []}

    like_value = f"%{term}%"
    devices = session.scalars(
        select(Device)
        .where(or_(
            Device.hostname.ilike(like_value),
            Device.mgmt_ip.ilike(like_value),
            Device.site.ilike(like_value),
            Device.device_role.ilike(like_value),
        ))
        .order_by(Device.hostname.asc())
        .limit(limit)
    ).all()
    incidents = session.scalars(
        select(Incident)
        .where(or_(
            Incident.title.ilike(like_value),
            Incident.summary.ilike(like_value),
            Incident.event_type.ilike(like_value),
            Incident.primary_source_ip.ilike(like_value),
        ))
        .order_by(Incident.updated_at.desc(), Incident.id.desc())
        .limit(limit)
    ).all()
    jobs = session.scalars(
        select(Job)
        .where(or_(
            Job.title.ilike(like_value),
            Job.summary.ilike(like_value),
            Job.target_ref.ilike(like_value),
            Job.job_type.ilike(like_value),
        ))
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(limit)
    ).all()
    approvals = session.scalars(
        select(Approval)
        .where(or_(
            Approval.title.ilike(like_value),
            Approval.target_host.ilike(like_value),
            Approval.rationale.ilike(like_value),
            Approval.action_id.ilike(like_value),
        ))
        .order_by(Approval.requested_at.desc(), Approval.id.desc())
        .limit(limit)
    ).all()
    return {
        "query": term,
        "devices": [
            {
                "id": item.id,
                "title": item.hostname,
                "subtitle": f"{item.site} • {item.device_role} • {item.mgmt_ip}",
                "href": f"/ops/devices/{item.id}",
            }
            for item in devices
        ],
        "incidents": [
            {
                "id": item.id,
                "title": item.title,
                "subtitle": f"{item.status} • {item.severity}",
                "href": f"/ops/incidents/{item.id}",
            }
            for item in incidents
        ],
        "jobs": [
            {
                "id": item.id,
                "title": item.title,
                "subtitle": f"{item.status} • {item.job_type}",
                "href": "/ops/jobs",
            }
            for item in jobs
        ],
        "approvals": [
            {
                "id": item.id,
                "title": item.title,
                "subtitle": f"{item.status} • {item.risk_level} • {item.target_host or '-'}",
                "href": "/ops/approvals",
            }
            for item in approvals
        ],
    }


# ---------------------------------------------------------------------------
# Incident Feedback
# ---------------------------------------------------------------------------

def serialize_incident_feedback(fb: IncidentFeedback) -> dict:
    return {
        "id": fb.id,
        "incident_id": fb.incident_id,
        "rating": fb.rating,
        "was_false_positive": fb.was_false_positive,
        "resolution_effectiveness": fb.resolution_effectiveness,
        "operator_notes": fb.operator_notes,
        "created_by": fb.created_by,
        "created_at": _iso(fb.created_at),
    }


def submit_incident_feedback(
    session: Session,
    *,
    incident_id: int,
    rating: int,
    was_false_positive: bool = False,
    resolution_effectiveness: str = "unknown",
    operator_notes: str | None = None,
    created_by: str = "operator",
) -> dict:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"Incident {incident_id} was not found")
    fb = IncidentFeedback(
        incident_id=incident_id,
        rating=rating,
        was_false_positive=was_false_positive,
        resolution_effectiveness=resolution_effectiveness,
        operator_notes=operator_notes,
        created_by=created_by,
    )
    session.add(fb)
    session.flush()
    record_audit(
        session, actor=created_by, actor_role="operator",
        action="incident_feedback_submitted", entity_type="incident",
        entity_id=incident_id, status="completed",
        summary=f"Feedback submitted for incident #{incident_id}",
        payload={"feedback_id": fb.id, "rating": rating},
    )
    return serialize_incident_feedback(fb)


def get_incident_feedback(session: Session, incident_id: int) -> list[dict]:
    items = session.scalars(
        select(IncidentFeedback)
        .where(IncidentFeedback.incident_id == incident_id)
        .order_by(IncidentFeedback.created_at.desc())
    ).all()
    return [serialize_incident_feedback(fb) for fb in items]


# ---------------------------------------------------------------------------
# Incident Clusters
# ---------------------------------------------------------------------------

def serialize_incident_cluster(session: Session, cluster: IncidentCluster) -> dict:
    member_count = session.scalar(
        select(func.count(Incident.id)).where(Incident.incident_cluster_id == cluster.id)
    ) or 0
    return {
        "id": cluster.id,
        "title": cluster.title,
        "status": cluster.status,
        "root_cause_summary": cluster.root_cause_summary,
        "severity": cluster.severity,
        "member_count": member_count,
        "created_at": _iso(cluster.created_at),
        "updated_at": _iso(cluster.updated_at),
    }


def list_clusters(
    session: Session,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    query = select(IncidentCluster)
    if status:
        query = query.where(IncidentCluster.status == status)
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    items = session.scalars(
        query.order_by(IncidentCluster.created_at.desc(), IncidentCluster.id.desc())
        .offset(max(page - 1, 0) * page_size)
        .limit(page_size)
    ).all()
    return _page_payload(
        items=[serialize_incident_cluster(session, c) for c in items],
        total=total, page=page, page_size=page_size,
        sort_by="created_at", sort_dir="desc",
    )


def get_cluster_detail(session: Session, cluster_id: int) -> dict | None:
    cluster = session.get(IncidentCluster, cluster_id)
    if cluster is None:
        return None
    payload = serialize_incident_cluster(session, cluster)
    members = session.scalars(
        select(Incident)
        .where(Incident.incident_cluster_id == cluster_id)
        .order_by(Incident.opened_at.asc())
    ).all()
    payload["incidents"] = [serialize_incident(session, inc) for inc in members]
    return payload


def auto_resolve_cluster_if_done(session: Session, incident: Incident) -> None:
    """If all incidents in the cluster are resolved, auto-resolve the cluster too."""
    if not incident.incident_cluster_id:
        return
    open_count = session.scalar(
        select(func.count(Incident.id)).where(
            Incident.incident_cluster_id == incident.incident_cluster_id,
            Incident.status.notin_(["resolved"]),
        )
    ) or 0
    if open_count == 0:
        cluster = session.get(IncidentCluster, incident.incident_cluster_id)
        if cluster and cluster.status != "resolved":
            cluster.status = "resolved"
