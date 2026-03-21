"""LLM-assisted operational summaries over stored evidence."""

from __future__ import annotations

import json
import re
from typing import Any
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.llm_factory import create_chat_model, resolve_llm_config
from src.ops.models import (
    AIArtifact,
    Approval,
    Device,
    DeviceInterface,
    Incident,
    IncidentEventLink,
    LLMAnalysis,
    NormalizedEvent,
    RawLog,
)

_PROMPT_VERSION = "ops-artifact-v1"
_ANALYZER_PROMPT_VERSION = "ops-log-analyzer-v1"
_TROUBLESHOOT_PROMPT_VERSION = "ops-troubleshoot-v1"
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _incident_evidence(session: Session, incident_id: int) -> tuple[Incident, list[NormalizedEvent], Device | None]:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"Incident {incident_id} was not found")

    links = session.scalars(
        select(IncidentEventLink).where(IncidentEventLink.incident_id == incident_id)
    ).all()
    event_ids = [link.event_id for link in links]
    events: list[NormalizedEvent] = []
    if event_ids:
        events = session.scalars(
            select(NormalizedEvent)
            .where(NormalizedEvent.id.in_(event_ids))
            .order_by(NormalizedEvent.event_time.asc().nullslast(), NormalizedEvent.id.asc())
        ).all()
    device = session.get(Device, incident.primary_device_id) if incident.primary_device_id else None
    return incident, events, device


def _response_text(response) -> str:
    content = getattr(response, "content", str(response))
    if isinstance(content, list):
        return "\n".join(str(item) for item in content).strip()
    return str(content).strip()


def _invoke_llm_text(
    prompt: str,
    *,
    reasoning: bool,
    retry_with_reasoning: bool = False,
) -> tuple[str, bool]:
    llm = create_chat_model(reasoning=reasoning)
    raw_text = _response_text(llm.invoke(prompt)).strip()
    if raw_text or not retry_with_reasoning or reasoning:
        return raw_text, reasoning

    retry_llm = create_chat_model(reasoning=True)
    retry_text = _response_text(retry_llm.invoke(prompt)).strip()
    return retry_text, True


def _parse_structured_json(text: str) -> dict | None:
    stripped = text.strip()
    candidates = [stripped]

    if "```" in stripped:
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
        if fence:
            candidates.append(fence.group(1).strip())

    object_match = _JSON_OBJECT_RE.search(stripped)
    if object_match:
        candidates.append(object_match.group(0).strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_string_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_scope(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]
    return []


def _normalize_confidence(value: Any, *, default: int = 50) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if 0 <= numeric <= 1:
        numeric *= 100
    return max(0, min(100, int(round(numeric))))


def _incident_log_summary_block(parsed: dict) -> str:
    summary = str(parsed.get("summary") or "No summary provided.").strip()
    likely_cause = str(parsed.get("probable_root_cause") or parsed.get("likely_cause") or "Cause is still unclear.").strip()
    affected_scope = _normalize_scope(parsed.get("affected_scope"))
    suggested_actions = _normalize_string_list(parsed.get("suggested_actions"))
    confidence = _normalize_confidence(parsed.get("confidence") or parsed.get("confidence_score"), default=50)

    lines = [
        "Summary:",
        summary,
        "",
        "Probable Root Cause:",
        likely_cause,
        "",
        f"Confidence: {confidence}/100",
        "",
        "Affected Scope:",
    ]
    if affected_scope:
        lines.extend([f"- {item}" for item in affected_scope])
    else:
        lines.append("- Scope is still unclear from the logs.")
    lines.extend(["", "Suggested Actions:"])
    if suggested_actions:
        lines.extend([f"- {item}" for item in suggested_actions])
    else:
        lines.append("- No action proposed yet.")
    return "\n".join(lines).strip()


def _analysis_status(parsed: dict) -> str:
    decision = str(parsed.get("decision") or "no_issue").strip().lower()
    return decision if decision in {"no_issue", "create_incident", "update_incident"} else "no_issue"


def _log_window_prompt(
    raw_logs: list[RawLog],
    open_incidents: list[Incident],
    events_by_log_id: dict[int, NormalizedEvent],
) -> str:
    log_lines = []
    for raw in raw_logs:
        event = events_by_log_id.get(raw.id)
        timestamp = raw.log_time or raw.ingested_at
        details = []
        if event is not None:
            details.extend([
                f"event_type={event.event_type}",
                f"severity={event.severity}",
            ])
            if event.hostname:
                details.append(f"hostname={event.hostname}")
            if event.interface_name:
                details.append(f"interface={event.interface_name}")
            if event.neighbor:
                details.append(f"neighbor={event.neighbor}")
        detail_block = " | ".join(details)
        log_lines.append(
            f"- log#{raw.id} | {timestamp.isoformat() if timestamp else 'unknown-time'} | "
            f"source_ip={raw.source_ip} | {detail_block or 'unparsed'} | {raw.raw_message.strip()}"
        )

    incident_lines = []
    for incident in open_incidents[:15]:
        incident_lines.append(
            f"- incident#{incident.id} | severity={incident.severity} | status={incident.status} | "
            f"title={incident.title} | root_cause={incident.probable_root_cause or 'unknown'} | "
            f"summary={incident.ai_summary or incident.summary or 'n/a'}"
        )

    schema = {
        "decision": "no_issue | create_incident | update_incident",
        "existing_incident_id": 0,
        "incident_title": "short title",
        "severity": "low | medium | high | critical",
        "summary": "short log-based summary",
        "probable_root_cause": "best evidence-backed cause from the logs",
        "affected_scope": ["device or scope"],
        "suggested_actions": ["next human action"],
        "confidence": 0,
        "evidence_log_ids": [101, 102],
        "event_type": "short normalized event family or generic_syslog",
        "primary_source_ip": "source ip linked to the likely root issue",
    }
    return (
        "You are a senior network operations analyst.\n"
        "You will receive raw syslog messages plus currently open incidents.\n"
        "Decide whether the new logs should create a new incident, update an existing incident, or be ignored.\n"
        "Rules:\n"
        "- Use only the evidence present in the logs.\n"
        "- Prefer one shared upstream cause over many downstream symptoms.\n"
        "- Avoid duplicate incidents when the logs fit an existing open incident.\n"
        "- If the logs are not actionable, return decision=no_issue.\n"
        "- Return valid JSON only.\n\n"
        f"Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        "Open incidents:\n"
        f"{chr(10).join(incident_lines) if incident_lines else '- none'}\n\n"
        "New raw syslog window:\n"
        f"{chr(10).join(log_lines) if log_lines else '- none'}"
    )


def _fallback_window_decision(raw_logs: list[RawLog], events_by_log_id: dict[int, NormalizedEvent]) -> dict:
    if not raw_logs:
        return {
            "decision": "no_issue",
            "incident_title": "",
            "severity": "low",
            "summary": "No new logs were available for analysis.",
            "probable_root_cause": "No actionable log evidence in this window.",
            "affected_scope": [],
            "suggested_actions": [],
            "confidence": 100,
            "evidence_log_ids": [],
            "event_type": "generic_syslog",
            "primary_source_ip": None,
        }

    first_raw = raw_logs[0]
    first_event = events_by_log_id.get(first_raw.id)
    summary = first_event.summary if first_event is not None else first_raw.raw_message.strip()[:240]
    source_ip = first_event.source_ip if first_event is not None else first_raw.source_ip
    event_type = first_event.event_type if first_event is not None else "generic_syslog"
    severity = first_event.severity if first_event is not None else "medium"
    return {
        "decision": "create_incident",
        "incident_title": summary[:120] or f"Incident from {source_ip}",
        "severity": severity,
        "summary": summary or "New actionable syslog window detected.",
        "probable_root_cause": "The log batch appears actionable but the structured LLM decision was unavailable.",
        "affected_scope": [first_event.hostname or source_ip] if first_event is not None else [source_ip],
        "suggested_actions": ["Review the linked incident and run AI troubleshoot if needed."],
        "confidence": 40,
        "evidence_log_ids": [first_raw.id],
        "event_type": event_type,
        "primary_source_ip": source_ip,
    }


def _format_summary_block(parsed: dict) -> str:
    summary = str(parsed.get("summary") or "No summary provided.").strip()
    impact = str(parsed.get("impact") or "Impact is not yet clear from the evidence.").strip()
    likely_cause = str(parsed.get("likely_cause") or "Likely cause is still uncertain.").strip()
    next_checks = _normalize_string_list(parsed.get("next_checks"))
    actions = _normalize_string_list(parsed.get("proposed_actions"))

    lines = [
        "Summary:",
        summary,
        "",
        "Impact:",
        impact,
        "",
        "Likely Cause:",
        likely_cause,
        "",
        "Next Checks:",
    ]
    if next_checks:
        lines.extend([f"- {item}" for item in next_checks])
    else:
        lines.append("- No further checks proposed.")
    if actions:
        lines.extend(["", "Proposed Actions:"])
        lines.extend([f"- {item}" for item in actions])
    return "\n".join(lines).strip()


def _artifact_readiness(parsed: dict) -> str:
    confidence = _normalize_confidence(parsed.get("confidence_score"), default=50)
    proposed_actions = _normalize_string_list(parsed.get("proposed_actions"))
    if confidence < 45:
        return "blocked_pending_more_evidence"
    if proposed_actions:
        return "ready_for_human_review"
    return "informational"


def _store_ai_artifact(
    session: Session,
    *,
    artifact_type: str,
    title: str,
    incident_id: int | None = None,
    device_id: int | None = None,
    approval_id: int | None = None,
    job_id: int | None = None,
    parsed: dict,
    raw_text: str,
    reasoning: bool = True,
) -> AIArtifact:
    config = resolve_llm_config(reasoning=reasoning)
    artifact = AIArtifact(
        artifact_type=artifact_type,
        title=title,
        incident_id=incident_id,
        device_id=device_id,
        approval_id=approval_id,
        job_id=job_id,
        provider=config.provider,
        model=config.model,
        prompt_version=_PROMPT_VERSION,
        summary=str(parsed.get("summary") or "").strip() or None,
        root_cause=str(parsed.get("likely_cause") or parsed.get("root_cause") or "").strip() or None,
        confidence_score=_normalize_confidence(parsed.get("confidence_score"), default=50),
        readiness=_artifact_readiness(parsed),
        risk_explanation=str(parsed.get("risk_explanation") or "").strip() or None,
        evidence_refs_json={
            "refs": _normalize_string_list(parsed.get("evidence_refs")),
        },
        proposed_actions_json={
            "items": _normalize_string_list(parsed.get("proposed_actions")),
            "next_checks": _normalize_string_list(parsed.get("next_checks")),
        },
        content_json={
            "parsed": parsed,
            "raw_text": raw_text,
        },
    )
    session.add(artifact)
    session.flush()
    return artifact


def _similar_resolved_incidents(
    session: Session,
    incident: Incident,
    *,
    limit: int = 5,
) -> list[dict]:
    """Find resolved incidents with feedback that match event_type or device."""
    from src.ops.models import IncidentFeedback

    device_or_type = []
    if incident.event_type:
        device_or_type.append(Incident.event_type == incident.event_type)
    if incident.primary_source_ip:
        device_or_type.append(Incident.primary_source_ip == incident.primary_source_ip)
    if not device_or_type:
        return []

    query = (
        select(Incident, IncidentFeedback)
        .outerjoin(IncidentFeedback, IncidentFeedback.incident_id == Incident.id)
        .where(
            Incident.status == "resolved",
            Incident.id != incident.id,
            IncidentFeedback.id.isnot(None),
            or_(*device_or_type),
        )
        .order_by(IncidentFeedback.rating.desc(), Incident.closed_at.desc().nullslast())
        .limit(limit)
    )
    results = []
    for inc, fb in session.execute(query).all():
        results.append({
            "incident_no": inc.incident_no or f"INC-{inc.id:05d}",
            "event_type": inc.event_type,
            "severity": inc.severity,
            "resolution_notes": (inc.resolution_notes or "")[:200],
            "ai_summary": (inc.ai_summary or "")[:200],
            "feedback_rating": fb.rating if fb else None,
            "feedback_effectiveness": fb.resolution_effectiveness if fb else None,
        })
    return results


def _incident_prompt(incident: Incident, events: list[NormalizedEvent], device: Device | None) -> str:
    device_block = "No device inventory record was linked."
    if device is not None:
        device_block = (
            f"hostname={device.hostname}, mgmt_ip={device.mgmt_ip}, site={device.site}, "
            f"role={device.device_role}, os={device.os_platform}, version={device.version}"
        )

    event_lines = []
    for event in events[-20:]:
        when = event.event_time.isoformat() if event.event_time else "unknown-time"
        event_lines.append(
            f"- event#{event.id} | {when} | {event.event_type} | {event.summary} | "
            f"severity={event.severity} | key={event.correlation_key}"
        )

    schema = {
        "summary": "short operator summary",
        "impact": "what is likely affected",
        "likely_cause": "best evidence-backed cause",
        "next_checks": ["short next check"],
        "proposed_actions": ["optional human-review action"],
        "confidence_score": 0,
        "risk_explanation": "why any proposed action is safe or risky",
        "evidence_refs": ["event#123"],
    }
    return (
        "You are a senior network operations analyst. Use only the evidence below.\n"
        "Do not invent facts and do not include chain-of-thought.\n"
        "Return JSON only matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Incident title: {incident.title}\n"
        f"Incident severity: {incident.severity}\n"
        f"Incident status: {incident.status}\n"
        f"Linked device: {device_block}\n"
        "Evidence timeline:\n"
        f"{chr(10).join(event_lines) if event_lines else '- No linked events.'}"
    )


def _fallback_parsed_summary(raw_text: str) -> dict:
    return {
        "summary": raw_text.strip() or "No summary generated.",
        "impact": "Impact could not be structured from the model output.",
        "likely_cause": "Likely cause could not be structured from the model output.",
        "next_checks": [],
        "proposed_actions": [],
        "confidence_score": 45,
        "risk_explanation": "Model output was not strict JSON, so this artifact should be treated as informational.",
        "evidence_refs": [],
    }


def investigate_incident_with_llm(session: Session, incident_id: int) -> dict[str, str | int | dict]:
    """Generate an evidence-only incident investigation summary."""
    incident, events, device = _incident_evidence(session, incident_id)
    llm = create_chat_model(reasoning=True)
    prompt = _incident_prompt(incident, events, device)

    # Inject historical context from similar resolved incidents with feedback
    similar = _similar_resolved_incidents(session, incident, limit=3)
    if similar:
        lines = ["Historical resolutions for similar incidents (use as reference ONLY, do not assume same cause):"]
        for s in similar:
            lines.append(
                f"- {s['incident_no']} ({s['event_type']}, rating={s['feedback_rating']}, "
                f"{s['feedback_effectiveness']}): {s['resolution_notes'] or s['ai_summary'] or 'n/a'}"
            )
        prompt += "\n\n" + "\n".join(lines)

    raw_text = _response_text(llm.invoke(prompt))
    parsed = _parse_structured_json(raw_text) or _fallback_parsed_summary(raw_text)
    analysis_text = _format_summary_block(parsed)

    artifact = _store_ai_artifact(
        session,
        artifact_type="incident_investigation",
        title=f"Incident #{incident.id} investigation",
        incident_id=incident.id,
        device_id=device.id if device else None,
        parsed=parsed,
        raw_text=raw_text,
        reasoning=True,
    )
    incident.ai_summary = analysis_text
    next_checks = _normalize_string_list(parsed.get("next_checks"))
    incident.recommendation = "\n".join(next_checks) if next_checks else analysis_text
    return {
        "analysis": analysis_text,
        "artifact_id": artifact.id,
        "artifact": {
            "summary": artifact.summary,
            "root_cause": artifact.root_cause,
            "confidence_score": artifact.confidence_score,
            "readiness": artifact.readiness,
        },
    }


def analyze_log_window_with_llm(
    session: Session,
    *,
    raw_logs: list[RawLog],
    open_incidents: list[Incident],
    window_start: datetime | None,
    window_end: datetime | None,
) -> dict[str, Any]:
    events = session.scalars(
        select(NormalizedEvent).where(NormalizedEvent.raw_log_id.in_([item.id for item in raw_logs]))
    ).all() if raw_logs else []
    events_by_log_id = {event.raw_log_id: event for event in events}
    prompt = _log_window_prompt(raw_logs, open_incidents, events_by_log_id)
    raw_text, used_reasoning = _invoke_llm_text(prompt, reasoning=False, retry_with_reasoning=True)
    parsed = _parse_structured_json(raw_text) or _fallback_window_decision(raw_logs, events_by_log_id)
    config = resolve_llm_config(reasoning=used_reasoning)
    decision = _analysis_status(parsed)
    evidence_log_ids = [
        log_id
        for log_id in [int(item) for item in _normalize_scope(parsed.get("evidence_log_ids")) if str(item).isdigit()]
        if any(raw.id == log_id for raw in raw_logs)
    ]
    if not evidence_log_ids:
        evidence_log_ids = [raw.id for raw in raw_logs[:5]]

    analysis = LLMAnalysis(
        decision=decision,
        status="completed",
        window_start=window_start,
        window_end=window_end,
        input_log_ids_json=[raw.id for raw in raw_logs],
        open_incident_ids_json=[incident.id for incident in open_incidents],
        provider=config.provider,
        model=config.model,
        prompt_version=_ANALYZER_PROMPT_VERSION,
        raw_text=raw_text,
        output_json={**parsed, "decision": decision, "evidence_log_ids": evidence_log_ids},
    )
    session.add(analysis)
    session.flush()
    return {
        "analysis_id": analysis.id,
        "decision": decision,
        "parsed": analysis.output_json,
        "raw_text": raw_text,
        "provider": config.provider,
        "model": config.model,
        "events_by_log_id": events_by_log_id,
    }


def synthesize_troubleshoot_result_with_llm(
    *,
    incident: Incident,
    final_text: str,
    tool_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    schema = {
        "summary": "clear operator summary",
        "diagnosis_type": "config | physical | provider | unknown",
        "probable_root_cause": "best evidence-backed cause",
        "evidence_refs": ["event#1", "step#2"],
        "recommended_next_action": "what the operator should do next",
        "proposal": {
            "target_host": "hostname",
            "commands": ["config command"],
            "verify_commands": ["show command"],
            "rollback_commands": ["undo command"],
            "risk_level": "low | medium | high | critical",
            "rationale": "why this change should fix the issue",
        },
    }
    step_lines = []
    for index, step in enumerate(tool_steps, start=1):
        step_lines.append(
            f"- step#{index} | {step.get('step_name') or step.get('tool_name') or 'command'} | "
            f"error={bool(step.get('is_error', False))} | {str(step.get('content') or '')[:1200]}"
        )

    prompt = (
        "You are a senior network operations analyst.\n"
        "Convert the troubleshooting evidence below into JSON only.\n"
        "Rules:\n"
        "- diagnosis_type must be one of: config, physical, provider, unknown.\n"
        "- Only include proposal when the issue is configuration-remediable and the fix is supported by evidence.\n"
        "- For physical/provider issues, set proposal to null.\n"
        "- Use concise operator language.\n\n"
        f"Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Incident title: {incident.title}\n"
        f"Current log summary: {incident.ai_summary or incident.summary or 'none'}\n\n"
        "Troubleshooting narrative:\n"
        f"{final_text.strip()}\n\n"
        "Command evidence:\n"
        f"{chr(10).join(step_lines) if step_lines else '- none'}"
    )
    raw_text, used_reasoning = _invoke_llm_text(prompt, reasoning=True, retry_with_reasoning=False)
    parsed = _parse_structured_json(raw_text) or {}
    proposal = parsed.get("proposal")
    if not isinstance(proposal, dict):
        proposal = None
    else:
        commands = _normalize_string_list(proposal.get("commands"))
        verify_commands = _normalize_string_list(proposal.get("verify_commands"))
        rollback_commands = _normalize_string_list(proposal.get("rollback_commands"))
        if not (commands and verify_commands and rollback_commands and proposal.get("target_host")):
            proposal = None
        else:
            risk_level = str(proposal.get("risk_level") or "medium").strip().lower() or "medium"
            if risk_level not in {"low", "medium", "high", "critical"}:
                risk_level = "medium"
            proposal = {
                "target_host": str(proposal.get("target_host")).strip(),
                "commands": commands,
                "verify_commands": verify_commands,
                "rollback_commands": rollback_commands,
                "risk_level": risk_level,
                "rationale": str(proposal.get("rationale") or "").strip() or "AI-proposed remediation",
            }

    diagnosis_type = str(parsed.get("diagnosis_type") or "unknown").strip().lower()
    if diagnosis_type not in {"config", "physical", "provider", "unknown"}:
        diagnosis_type = "unknown"

    config = resolve_llm_config(reasoning=used_reasoning)
    return {
        "summary": str(parsed.get("summary") or final_text).strip(),
        "diagnosis_type": diagnosis_type,
        "probable_root_cause": str(parsed.get("probable_root_cause") or "").strip() or None,
        "evidence_refs": _normalize_string_list(parsed.get("evidence_refs")),
        "recommended_next_action": str(parsed.get("recommended_next_action") or "").strip() or None,
        "proposal": proposal if diagnosis_type == "config" else None,
        "provider": config.provider,
        "model": config.model,
        "prompt_version": _TROUBLESHOOT_PROMPT_VERSION,
        "raw_text": raw_text,
    }


def store_text_artifact(
    session: Session,
    *,
    artifact_type: str,
    title: str,
    raw_text: str,
    incident_id: int | None = None,
    device_id: int | None = None,
    reasoning: bool = True,
    steps: list[dict] | None = None,
) -> AIArtifact:
    """Persist a plain-text LLM result as an AI artifact.

    This is used for free-run troubleshooting flows where we want to keep the
    full answer, but do not require rigid JSON shaping ahead of time.
    """
    summary_text = raw_text.strip()
    parsed = {
        "summary": summary_text,
        "likely_cause": "",
        "next_checks": [],
        "proposed_actions": [],
        "confidence_score": 60,
        "risk_explanation": "",
        "evidence_refs": [],
    }
    artifact = _store_ai_artifact(
        session,
        artifact_type=artifact_type,
        title=title,
        incident_id=incident_id,
        device_id=device_id,
        parsed=parsed,
        raw_text=summary_text,
        reasoning=reasoning,
    )
    if steps:
        artifact.content_json = {**artifact.content_json, "steps": steps}
    return artifact


def summarize_device_with_llm(session: Session, device_id: int) -> dict[str, str | int | dict]:
    """Summarize recent instability for a single device."""
    device = session.get(Device, device_id)
    if device is None:
        raise ValueError(f"Device {device_id} was not found")

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
        .limit(10)
    ).all()
    recent_approvals = session.scalars(
        select(Approval)
        .where(Approval.target_host.in_([device.hostname, device.mgmt_ip]))
        .order_by(Approval.requested_at.desc(), Approval.id.desc())
        .limit(5)
    ).all()

    event_lines = [
        f"- event#{event.id} | {(event.event_time.isoformat() if event.event_time else 'unknown-time')} | "
        f"{event.event_type} | {event.summary}"
        for event in recent_events
    ]
    incident_lines = [
        f"- incident#{incident.id} | {incident.status} | {incident.severity} | {incident.title}"
        for incident in recent_incidents
    ]
    approval_lines = [
        f"- approval#{approval.id} | {approval.status} | {approval.risk_level} | {approval.title}"
        for approval in recent_approvals
    ]
    schema = {
        "summary": "short device health summary",
        "impact": "what this device instability means operationally",
        "likely_cause": "best evidence-backed cause",
        "next_checks": ["short next check"],
        "proposed_actions": ["optional human-review action"],
        "confidence_score": 0,
        "risk_explanation": "why confidence is limited or strong",
        "evidence_refs": ["event#123", "incident#45"],
    }
    prompt = (
        "You are a senior network operations analyst. Use only the evidence below and return JSON only.\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Device: hostname={device.hostname}, mgmt_ip={device.mgmt_ip}, site={device.site}, "
        f"role={device.device_role}, os={device.os_platform}, version={device.version}\n"
        "Recent events:\n"
        f"{chr(10).join(event_lines) if event_lines else '- No recent events.'}\n"
        "Recent incidents:\n"
        f"{chr(10).join(incident_lines) if incident_lines else '- No recent incidents.'}\n"
        "Recent approvals:\n"
        f"{chr(10).join(approval_lines) if approval_lines else '- No recent approvals.'}"
    )
    raw_text, used_reasoning = _invoke_llm_text(
        prompt,
        reasoning=False,
        retry_with_reasoning=True,
    )
    parsed = _parse_structured_json(raw_text) or _fallback_parsed_summary(raw_text)
    analysis_text = _format_summary_block(parsed)

    artifact = _store_ai_artifact(
        session,
        artifact_type="device_summary",
        title=f"Device #{device.id} summary",
        device_id=device.id,
        parsed=parsed,
        raw_text=raw_text,
        reasoning=used_reasoning,
    )
    return {
        "analysis": analysis_text,
        "artifact_id": artifact.id,
        "artifact": {
            "summary": artifact.summary,
            "root_cause": artifact.root_cause,
            "confidence_score": artifact.confidence_score,
            "readiness": artifact.readiness,
        },
    }


def _parse_window(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            parsed = datetime.fromisoformat(raw)
            return parsed.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _focus_analysis_prompt(
    *,
    scope_label: str,
    context_lines: list[str],
) -> str:
    schema = {
        "title": "short analysis title",
        "summary": "operator-ready summary",
        "impact": "operational impact on the network",
        "likely_cause": "best evidence-backed cause",
        "next_checks": ["short runbook step"],
        "proposed_actions": ["optional suggested command or action"],
        "confidence_score": 0,
        "risk_explanation": "why confidence is high or limited",
        "evidence_refs": ["event#123"],
    }
    return (
        "You are a senior network operations analyst. Use only the evidence below.\n"
        "Do not invent facts. Do not mention business impact. Focus on operational scope only.\n"
        "Return JSON only matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Scope: {scope_label}\n"
        "Evidence:\n"
        f"{chr(10).join(context_lines) if context_lines else '- No evidence provided.'}"
    )


def _invoke_structured_analysis(
    session: Session,
    *,
    artifact_type: str,
    title: str,
    prompt: str,
    incident_id: int | None = None,
    device_id: int | None = None,
    reasoning: bool = False,
) -> dict[str, str | int | dict | list[str]]:
    raw_text, used_reasoning = _invoke_llm_text(
        prompt,
        reasoning=reasoning,
        retry_with_reasoning=not reasoning,
    )
    parsed = _parse_structured_json(raw_text) or _fallback_parsed_summary(raw_text)
    analysis_text = _format_summary_block(parsed)
    artifact = _store_ai_artifact(
        session,
        artifact_type=artifact_type,
        title=title,
        incident_id=incident_id,
        device_id=device_id,
        parsed=parsed,
        raw_text=raw_text,
        reasoning=used_reasoning,
    )
    return {
        "title": str(parsed.get("title") or title).strip() or title,
        "analysis": analysis_text,
        "summary": str(parsed.get("summary") or "").strip(),
        "root_cause": str(parsed.get("likely_cause") or parsed.get("root_cause") or "").strip(),
        "operational_impact": str(parsed.get("impact") or "").strip(),
        "runbook_steps": _normalize_string_list(parsed.get("next_checks")),
        "suggested_commands": _normalize_string_list(parsed.get("proposed_actions")),
        "confidence_score": _normalize_confidence(parsed.get("confidence_score"), default=50),
        "artifact_id": artifact.id,
        "artifact": {
            "summary": artifact.summary,
            "root_cause": artifact.root_cause,
            "confidence_score": artifact.confidence_score,
            "readiness": artifact.readiness,
        },
    }


def analyze_site_with_llm(
    session: Session,
    *,
    site: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, str | int | dict | list[str]]:
    devices = session.scalars(
        select(Device).where(Device.site == site).order_by(Device.hostname.asc())
    ).all()
    if not devices:
        raise ValueError(f"Site '{site}' was not found")

    start_dt = _parse_window(start_time)
    end_dt = _parse_window(end_time)
    device_ids = [device.id for device in devices]
    event_query = (
        select(NormalizedEvent)
        .where(NormalizedEvent.device_id.in_(device_ids))
        .order_by(NormalizedEvent.event_time.desc().nullslast(), NormalizedEvent.id.desc())
    )
    if start_dt:
        event_query = event_query.where(NormalizedEvent.event_time >= start_dt)
    if end_dt:
        event_query = event_query.where(NormalizedEvent.event_time <= end_dt)
    events = session.scalars(event_query.limit(60)).all()
    incidents = session.scalars(
        select(Incident)
        .where(Incident.primary_device_id.in_(device_ids))
        .order_by(Incident.updated_at.desc(), Incident.id.desc())
        .limit(20)
    ).all()

    context_lines = [
        f"- devices={len(devices)} | incidents={len(incidents)} | recent_events={len(events)}",
        *[
            f"- device#{device.id} | {device.hostname} | {device.device_role} | {device.os_platform} | {device.version}"
            for device in devices[:20]
        ],
        *[
            f"- incident#{incident.id} | {incident.status} | {incident.severity} | {incident.title}"
            for incident in incidents[:12]
        ],
        *[
            f"- event#{event.id} | {(event.event_time.isoformat() if event.event_time else 'unknown-time')} | "
            f"{event.hostname or event.source_ip} | {event.event_type} | {event.summary}"
            for event in events[:30]
        ],
    ]
    prompt = _focus_analysis_prompt(scope_label=f"site={site}", context_lines=context_lines)
    return _invoke_structured_analysis(
        session,
        artifact_type="site_analysis",
        title=f"Site analysis for {site}",
        prompt=prompt,
    )


def analyze_device_focus_with_llm(
    session: Session,
    *,
    device_id: int,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, str | int | dict | list[str]]:
    device = session.get(Device, device_id)
    if device is None:
        raise ValueError(f"Device {device_id} was not found")

    start_dt = _parse_window(start_time)
    end_dt = _parse_window(end_time)
    event_query = (
        select(NormalizedEvent)
        .where(NormalizedEvent.device_id == device_id)
        .order_by(NormalizedEvent.event_time.desc().nullslast(), NormalizedEvent.id.desc())
    )
    if start_dt:
        event_query = event_query.where(NormalizedEvent.event_time >= start_dt)
    if end_dt:
        event_query = event_query.where(NormalizedEvent.event_time <= end_dt)
    events = session.scalars(event_query.limit(40)).all()
    interfaces = session.scalars(
        select(DeviceInterface)
        .where(DeviceInterface.device_id == device_id)
        .order_by(DeviceInterface.last_event_time.desc().nullslast(), DeviceInterface.name.asc())
        .limit(20)
    ).all()
    incidents = session.scalars(
        select(Incident)
        .where(Incident.primary_device_id == device_id)
        .order_by(Incident.updated_at.desc(), Incident.id.desc())
        .limit(12)
    ).all()

    context_lines = [
        f"- device={device.hostname} | site={device.site} | role={device.device_role} | os={device.os_platform} | version={device.version}",
        *[
            f"- iface {iface.name} | protocol={iface.protocol or '-'} | last_state={iface.last_state or '-'} | events={iface.event_count}"
            for iface in interfaces[:8]
        ],
        *[
            f"- incident#{incident.id} | {incident.status} | {incident.severity} | {incident.title}"
            for incident in incidents[:6]
        ],
        *[
            f"- event#{event.id} | {(event.event_time.isoformat() if event.event_time else 'unknown-time')} | "
            f"{event.event_type} | {event.summary}"
            for event in events[:12]
        ],
    ]
    prompt = _focus_analysis_prompt(scope_label=f"device={device.hostname}", context_lines=context_lines)
    return _invoke_structured_analysis(
        session,
        artifact_type="device_focus_analysis",
        title=f"Device analysis for {device.hostname}",
        prompt=prompt,
        device_id=device.id,
    )


def analyze_global_with_llm(
    session: Session,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, str | int | dict | list[str]]:
    start_dt = _parse_window(start_time)
    end_dt = _parse_window(end_time)
    event_query = select(NormalizedEvent).order_by(NormalizedEvent.event_time.desc().nullslast(), NormalizedEvent.id.desc())
    incident_query = select(Incident).order_by(Incident.updated_at.desc(), Incident.id.desc())
    if start_dt:
        event_query = event_query.where(NormalizedEvent.event_time >= start_dt)
        incident_query = incident_query.where(Incident.updated_at >= start_dt)
    if end_dt:
        event_query = event_query.where(NormalizedEvent.event_time <= end_dt)
        incident_query = incident_query.where(Incident.updated_at <= end_dt)

    events = session.scalars(event_query.limit(80)).all()
    incidents = session.scalars(incident_query.limit(30)).all()
    site_counts = session.execute(
        select(Device.site, func.count().label("count"))
        .where(Device.site != "")
        .group_by(Device.site)
        .order_by(func.count().desc(), Device.site.asc())
        .limit(10)
    ).all()

    context_lines = [
        f"- incidents={len(incidents)} | events={len(events)} | sites={len(site_counts)}",
        *[f"- site {site} | devices={count}" for site, count in site_counts],
        *[
            f"- incident#{incident.id} | {incident.status} | {incident.severity} | {incident.title}"
            for incident in incidents[:20]
        ],
        *[
            f"- event#{event.id} | {(event.event_time.isoformat() if event.event_time else 'unknown-time')} | "
            f"{event.hostname or event.source_ip} | {event.event_type} | {event.summary}"
            for event in events[:40]
        ],
    ]
    prompt = _focus_analysis_prompt(scope_label="global network view", context_lines=context_lines)
    return _invoke_structured_analysis(
        session,
        artifact_type="global_analysis",
        title="Global network analysis",
        prompt=prompt,
    )


def chat_incident_with_llm(
    session: Session,
    *,
    incident_id: int,
    message: str,
    history: list[dict] | None = None,
) -> dict[str, str | int | dict]:
    incident, events, device = _incident_evidence(session, incident_id)
    llm = create_chat_model(reasoning=True)
    prior_messages = []
    for item in history or []:
        role = str(item.get("role") or "user").strip()
        content = str(item.get("content") or "").strip()
        if content:
            prior_messages.append(f"{role}: {content}")
    timeline_lines = [
        f"- event#{event.id} | {(event.event_time.isoformat() if event.event_time else 'unknown-time')} | "
        f"{event.event_type} | {event.summary}"
        for event in events[-20:]
    ]
    prompt = (
        "You are an incident-scoped network operations copilot.\n"
        "Answer only from the incident evidence below and the current user question.\n"
        "If evidence is missing, say what else should be checked. Keep the answer concise.\n\n"
        f"Incident: #{incident.id} | {incident.title} | status={incident.status} | severity={incident.severity}\n"
        f"Linked device: {(device.hostname if device else 'none')} | {(device.site if device else 'no-site')}\n"
        "Evidence timeline:\n"
        f"{chr(10).join(timeline_lines) if timeline_lines else '- No linked events.'}\n\n"
        f"Prior conversation:\n{chr(10).join(prior_messages) if prior_messages else '- No previous messages.'}\n\n"
        f"User question: {message}"
    )
    raw_text = _response_text(llm.invoke(prompt))
    parsed = {
        "summary": raw_text.strip(),
        "impact": "",
        "likely_cause": "",
        "next_checks": [],
        "proposed_actions": [],
        "confidence_score": 60,
        "risk_explanation": "",
        "evidence_refs": [f"event#{event.id}" for event in events[-5:]],
    }
    artifact = _store_ai_artifact(
        session,
        artifact_type="incident_chat_reply",
        title=f"Incident #{incident.id} chat",
        incident_id=incident.id,
        device_id=device.id if device else None,
        parsed=parsed,
        raw_text=raw_text,
    )
    return {
        "reply": raw_text.strip(),
        "artifact_id": artifact.id,
        "artifact": {
            "summary": artifact.summary,
            "confidence_score": artifact.confidence_score,
            "readiness": artifact.readiness,
        },
    }
