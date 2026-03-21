"""Incident-scoped free-run helpers built on the main chat graph."""

from __future__ import annotations

import logging
from sqlalchemy import select
from typing import AsyncGenerator, Callable

import json
import re

from src.llm_factory import resolve_llm_config
from src.ops.ai import synthesize_troubleshoot_result_with_llm
from src.ops.audit import record_audit
from src.ops.db import session_scope
from src.ops.models import AIArtifact, Device, Incident, IncidentEventLink, NormalizedEvent
from src.ops.service import create_approval

logger = logging.getLogger(__name__)
from src.ops.troubleshoot_session import (
    TroubleshootRound,
    create_ts_session,
    delete_ts_session,
    get_ts_session,
)
from src.session_manager import create_session, delete_session, get_session
from src.sse_stream import _sse, stream_chat


def _incident_prompt(
    incident: Incident,
    events: list[NormalizedEvent],
    device: Device | None,
    *,
    cluster_siblings: list[Incident] | None = None,
    cluster_events: list[NormalizedEvent] | None = None,
    cluster_devices: list[Device] | None = None,
) -> str:
    timeline_lines = [
        f"- {event.event_time.isoformat() if event.event_time else 'unknown-time'} | "
        f"{event.event_type} | {event.summary}"
        for event in events[-20:]
    ]
    device_block = (
        f"Primary device: {device.hostname} ({device.mgmt_ip}) | role={device.device_role} | "
        f"site={device.site} | os={device.os_platform}"
        if device
        else "Primary device: none linked from inventory."
    )
    base_prompt = (
        "Troubleshoot this network incident using the normal free-run workflow.\n"
        "You may use inventory lookups and read-only CLI checks as needed.\n"
        "Do not make configuration changes and do not execute remediation.\n"
        "Your goal is to identify the likely cause and gather enough evidence to classify the problem "
        "as configuration, physical/link, provider/external, or still uncertain.\n"
        "Answer in concise operator language and include: summary, likely cause, evidence, and recommended next action.\n"
        "Do not wrap the answer in JSON. A separate synthesis pass will convert your evidence-backed answer "
        "into the structured incident workflow format.\n\n"
        f"Incident: #{incident.id} | {incident.title} | status={incident.status} | severity={incident.severity}\n"
        f"Current incident summary from logs:\n{incident.summary}\n\n"
        f"Current AI log summary:\n{incident.ai_summary or 'No AI summary stored yet.'}\n\n"
        f"{device_block}\n\n"
        "Incident timeline from logs:\n"
        f"{chr(10).join(timeline_lines) if timeline_lines else '- No linked log evidence.'}"
    )

    if cluster_siblings:
        cluster_lines = [
            "\n\n--- CROSS-INCIDENT CLUSTER CONTEXT ---",
            "This incident is part of a cluster. Sibling incidents below.\n",
        ]
        for sib in cluster_siblings:
            cluster_lines.append(
                f"- Sibling #{sib.id} | {sib.title} | status={sib.status} | "
                f"severity={sib.severity} | device={sib.primary_source_ip}"
            )
        if cluster_devices:
            cluster_lines.append("\nCluster devices:")
            for d in cluster_devices:
                cluster_lines.append(
                    f"- {d.hostname} ({d.mgmt_ip}) | role={d.device_role} | site={d.site}"
                )
        if cluster_events:
            cluster_lines.append("\nCluster event timeline:")
            for ev in cluster_events[-15:]:
                ts = ev.event_time.isoformat() if ev.event_time else "unknown"
                cluster_lines.append(f"- {ts} | {ev.event_type} | {ev.summary}")
        cluster_lines.append(
            "\nConsider whether these share infrastructure, "
            "a common upstream device, or a cascading failure pattern."
        )
        base_prompt += "\n".join(cluster_lines)

    return base_prompt


def _load_cluster_context(
    session, incident: Incident
) -> tuple[list[Incident], list[NormalizedEvent], list[Device]]:
    """Load sibling incidents, their events, and all unique devices from a cluster."""
    if not incident.incident_cluster_id:
        return [], [], []

    siblings = session.scalars(
        select(Incident).where(
            Incident.incident_cluster_id == incident.incident_cluster_id,
            Incident.id != incident.id,
        ).order_by(Incident.opened_at.asc()).limit(10)
    ).all()

    sib_ids = [s.id for s in siblings]
    sib_events: list[NormalizedEvent] = []
    if sib_ids:
        sib_links = session.scalars(
            select(IncidentEventLink).where(IncidentEventLink.incident_id.in_(sib_ids))
        ).all()
        sib_event_ids = [link.event_id for link in sib_links]
        if sib_event_ids:
            sib_events = session.scalars(
                select(NormalizedEvent)
                .where(NormalizedEvent.id.in_(sib_event_ids))
                .order_by(NormalizedEvent.event_time.asc().nullslast())
                .limit(30)
            ).all()

    device_ids = {s.primary_device_id for s in siblings if s.primary_device_id}
    devices = [session.get(Device, did) for did in device_ids]
    devices = [d for d in devices if d is not None]

    return siblings, sib_events, devices


async def run_incident_troubleshoot_free_run(
    incident_id: int,
    *,
    requested_by: str = "manager",
    requested_by_role: str = "admin",
    on_stream_event: Callable[[str, dict], None] | None = None,
) -> dict:
    with session_scope() as session:
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

        # Load cross-incident cluster context (sibling incidents + devices)
        cluster_siblings, cluster_events, cluster_devices = _load_cluster_context(session, incident)

        prompt = _incident_prompt(
            incident, events, device,
            cluster_siblings=cluster_siblings or None,
            cluster_events=cluster_events or None,
            cluster_devices=cluster_devices or None,
        )

    graph_session = await create_session()
    # Pre-populate device cache so run_cli works immediately
    if device is not None:
        graph_session.device_cache[device.hostname] = {
            "ip_address": device.mgmt_ip,
            "os_platform": device.os_platform,
            "device_role": device.device_role,
            "site": device.site,
        }
    # Pre-populate all cluster devices so AI can SSH to any of them
    for d in cluster_devices:
        if d.hostname not in graph_session.device_cache:
            graph_session.device_cache[d.hostname] = {
                "ip_address": d.mgmt_ip,
                "os_platform": d.os_platform,
                "device_role": d.device_role,
                "site": d.site,
            }
    final_text = ""
    analyst_tokens: list[str] = []
    status_updates: list[str] = []
    tool_steps: list[dict] = []
    graph_error: str | None = None
    try:
        async for evt in stream_chat(graph_session, prompt):
            event_name = str(evt.get("event") or "")
            data = evt.get("data") or {}
            if event_name == "status":
                status_text = str(data.get("text") or "").strip()
                if status_text:
                    status_updates.append(status_text)
            elif event_name == "tool_result":
                tool_steps.append({
                    "step_name": str(data.get("step_name") or ""),
                    "content": str(data.get("content") or ""),
                    "is_error": bool(data.get("is_error", False)),
                })
            elif event_name == "analyst_token":
                token = str(data.get("token") or "")
                if token:
                    analyst_tokens.append(token)
            elif event_name == "analyst_done":
                final_text = str(data.get("full_content") or "").strip()
            elif event_name == "error":
                graph_error = str(data.get("message") or "unknown graph error")
                logger.error("Troubleshoot stream error for incident %s: %s", incident_id, graph_error)
            # Forward to ops loop SSE bus (if callback provided)
            if on_stream_event is not None and event_name in ("status", "tool_result", "analyst_done"):
                try:
                    on_stream_event(event_name, data)
                except Exception:
                    pass  # best-effort — don't break troubleshoot for SSE failures
    finally:
        await delete_session(graph_session.session_id)

    if not final_text:
        final_text = "".join(analyst_tokens).strip()
    if not final_text:
        err_detail = f" Graph error: {graph_error}" if graph_error else ""
        logger.error(
            "Troubleshoot for incident %s returned no usable text. "
            "status_updates=%s, tokens_collected=%d%s",
            incident_id, status_updates, len(analyst_tokens), err_detail,
        )
        raise RuntimeError(
            f"Incident troubleshooting did not return a usable answer.{err_detail}"
        )

    with session_scope() as session:
        incident = session.get(Incident, incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id} was not found")
        shaped = synthesize_troubleshoot_result_with_llm(
            incident=incident,
            final_text=final_text,
            tool_steps=tool_steps,
        )
        incident.recommendation = (
            shaped.get("recommended_next_action")
            or shaped.get("summary")
            or final_text
        )

        config = resolve_llm_config(reasoning=True)
        artifact = AIArtifact(
            artifact_type="incident_troubleshoot_structured",
            title=f"Incident #{incident_id} troubleshoot",
            incident_id=incident_id,
            device_id=incident.primary_device_id,
            provider=shaped.get("provider") or config.provider,
            model=shaped.get("model") or config.model,
            prompt_version=str(shaped.get("prompt_version") or "ops-troubleshoot-v1"),
            summary=str(shaped.get("summary") or "").strip() or None,
            root_cause=str(shaped.get("probable_root_cause") or "").strip() or None,
            confidence_score=75 if shaped.get("proposal") else 60,
            readiness="ready_for_human_review" if shaped.get("proposal") else "informational",
            risk_explanation=(
                "Configuration remediation was proposed from troubleshooting evidence."
                if shaped.get("proposal")
                else "No config proposal was generated from troubleshooting evidence."
            ),
            evidence_refs_json={"refs": shaped.get("evidence_refs") or []},
            proposed_actions_json={
                "items": [shaped.get("recommended_next_action")] if shaped.get("recommended_next_action") else [],
                "proposal": shaped.get("proposal"),
                "diagnosis_type": shaped.get("diagnosis_type"),
            },
            content_json={
                "raw_text": final_text,
                "structured": shaped,
                "steps": tool_steps,
                "status_updates": status_updates[-8:],
            },
        )
        session.add(artifact)
        session.flush()

        approval_id = None
        proposal = shaped.get("proposal") if shaped.get("diagnosis_type") == "config" else None
        if isinstance(proposal, dict):
            try:
                approval = create_approval(
                    session,
                    title=f"Remediation for Incident #{incident_id}",
                    requested_by=requested_by,
                    requested_by_role=requested_by_role,
                    target_host=str(proposal.get("target_host") or "").strip() or incident.primary_source_ip,
                    commands_text="\n".join(proposal.get("commands") or []),
                    rollback_commands_text="\n".join(proposal.get("rollback_commands") or []) or None,
                    verify_commands_text="\n".join(proposal.get("verify_commands") or []) or None,
                    rationale=str(proposal.get("rationale") or "AI proposed remediation"),
                    risk_level=str(proposal.get("risk_level") or "medium"),
                    notes="Auto-generated by AI troubleshooting",
                    incident_id=incident_id,
                    evidence_snapshot={
                        "artifact_id": artifact.id,
                        "diagnosis_type": shaped.get("diagnosis_type"),
                        "evidence_refs": shaped.get("evidence_refs") or [],
                    },
                )
                approval_id = approval.get("id")
            except Exception as exc:
                logger.error("Failed to create approval from structured troubleshoot result: %s", exc, exc_info=True)

        record_audit(
            session,
            actor=requested_by,
            actor_role=requested_by_role,
            action="incident_troubleshoot_completed",
            entity_type="incident",
            entity_id=incident_id,
            status="completed",
            summary=f"Free-run troubleshooting completed for incident #{incident_id}",
            payload={
                "artifact_id": artifact.id,
                "status_updates": status_updates[-8:],
                "approval_id": approval_id,
                "diagnosis_type": shaped.get("diagnosis_type"),
            },
        )
        return {
            "analysis": str(shaped.get("summary") or final_text),
            "artifact_id": artifact.id,
            "status_updates": status_updates[-8:],
            "approval_id": approval_id,
            "diagnosis_type": shaped.get("diagnosis_type"),
            "probable_root_cause": shaped.get("probable_root_cause"),
            "evidence_refs": shaped.get("evidence_refs") or [],
            "recommended_next_action": shaped.get("recommended_next_action"),
            "proposal": proposal,
        }


# ---------------------------------------------------------------------------
# AI Health Check (read-only verification)
# ---------------------------------------------------------------------------


def _health_check_prompt(incident: Incident, events: list[NormalizedEvent], device: Device | None) -> str:
    """Build a read-only health check prompt for the free-run graph."""
    device_block = ""
    if device is not None:
        device_block = (
            f"\nDevice: {device.hostname}\n"
            f"  Management IP: {device.mgmt_ip}\n"
            f"  OS: {device.os_platform}\n"
            f"  Role: {device.device_role}\n"
            f"  Site: {device.site}\n"
        )

    timeline_lines = []
    for ev in events[-20:]:
        ts = ev.event_time.isoformat() if ev.event_time else "?"
        timeline_lines.append(f"  [{ts}] {ev.event_type}: {ev.summary}")

    return (
        f"Health verification for Incident #{incident.id}: {incident.title}\n\n"
        f"Original issue: {incident.summary}\n"
        f"{device_block}\n"
        f"Recent event timeline:\n" + "\n".join(timeline_lines) + "\n\n"
        "Your task: run read-only diagnostic commands on this device to determine "
        "if the original issue has been resolved.\n\n"
        "Check the relevant interfaces, routing neighbors, CPU/memory, and recent logs.\n\n"
        "After your investigation, respond with ONLY this JSON block:\n"
        "```json\n"
        '{"healthy": true, "confidence": 85, "summary": "brief explanation"}\n'
        "```\n\n"
        "Field descriptions:\n"
        "- healthy: true if the device appears to have recovered from the original issue, false otherwise\n"
        "- confidence: 0-100 how confident you are in your assessment\n"
        "- summary: brief explanation of what you found and why you believe the device is or isn't healthy\n\n"
        "IMPORTANT:\n"
        "- Run ONLY show/display commands. Do NOT propose any configuration changes.\n"
        "- If you cannot reach the device via SSH, set healthy=false and explain.\n"
        "- Focus specifically on the original issue described above.\n"
    )


async def run_health_check_free_run(incident_id: int) -> dict:
    """Run a read-only AI health check on a device to verify incident recovery.

    Returns ``{"healthy": bool, "confidence": int, "summary": str, "artifact_id": int}``.
    """
    with session_scope() as session:
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
        prompt = _health_check_prompt(incident, events, device)

    graph_session = await create_session()
    if device is not None:
        graph_session.device_cache[device.hostname] = {
            "ip_address": device.mgmt_ip,
            "os_platform": device.os_platform,
            "device_role": device.device_role,
            "site": device.site,
        }

    final_text = ""
    analyst_tokens: list[str] = []
    tool_steps: list[dict] = []
    try:
        async for evt in stream_chat(graph_session, prompt):
            event_name = str(evt.get("event") or "")
            data = evt.get("data") or {}
            if event_name == "tool_result":
                tool_steps.append({
                    "step_name": str(data.get("step_name") or ""),
                    "content": str(data.get("content") or ""),
                    "is_error": bool(data.get("is_error", False)),
                })
            elif event_name == "analyst_token":
                token = str(data.get("token") or "")
                if token:
                    analyst_tokens.append(token)
            elif event_name == "analyst_done":
                final_text = str(data.get("full_content") or "").strip()
            elif event_name == "error":
                logger.error("Health check stream error for incident %s: %s",
                             incident_id, data.get("message"))
    finally:
        await delete_session(graph_session.session_id)

    if not final_text:
        final_text = "".join(analyst_tokens).strip()

    # Parse the JSON health check result from the LLM output
    result = {"healthy": False, "confidence": 0, "summary": ""}
    json_match = re.search(r"```json\s*(.*?)\s*```", final_text, re.DOTALL | re.IGNORECASE)
    if not json_match:
        # Try bare JSON object
        json_match = re.search(r'\{[^{}]*"healthy"[^{}]*\}', final_text)
    if json_match:
        try:
            parsed = json.loads(json_match.group(1) if json_match.lastindex else json_match.group(0))
            result["healthy"] = bool(parsed.get("healthy", False))
            result["confidence"] = max(0, min(100, int(parsed.get("confidence", 0))))
            result["summary"] = str(parsed.get("summary", ""))
        except Exception:
            logger.warning("Failed to parse health check JSON for incident %s", incident_id)

    # Store as artifact
    with session_scope() as session:
        artifact = store_text_artifact(
            session,
            artifact_type="health_check",
            title=f"Incident #{incident_id} health check",
            incident_id=incident_id,
            raw_text=final_text,
            reasoning=True,
            steps=tool_steps or None,
        )
        result["artifact_id"] = artifact.id

    logger.info(
        "Health check for incident %d: healthy=%s confidence=%d",
        incident_id, result["healthy"], result["confidence"],
    )
    return result


def _load_incident_context(incident_id: int) -> tuple[Incident, list[NormalizedEvent], Device | None]:
    """Load incident, linked events, and primary device from DB.

    Eagerly accesses all attributes so ORM objects remain usable after
    the session closes (attributes cached in __dict__).
    """
    with session_scope() as session:
        incident = session.get(Incident, incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id} was not found")
        links = session.scalars(
            select(IncidentEventLink).where(IncidentEventLink.incident_id == incident_id)
        ).all()
        event_ids = [link.event_id for link in links]
        events: list[NormalizedEvent] = []
        if event_ids:
            events = list(session.scalars(
                select(NormalizedEvent)
                .where(NormalizedEvent.id.in_(event_ids))
                .order_by(NormalizedEvent.event_time.asc().nullslast(), NormalizedEvent.id.asc())
            ).all())
        device = session.get(Device, incident.primary_device_id) if incident.primary_device_id else None
        # Eagerly access all attributes while session is open
        _ = (incident.id, incident.title, incident.status, incident.severity,
             incident.summary, incident.ai_summary, incident.primary_device_id)
        for ev in events:
            _ = (ev.event_time, ev.event_type, ev.summary)
        if device is not None:
            _ = (device.hostname, device.mgmt_ip, device.os_platform,
                 device.device_role, device.site)
    return incident, events, device


def _planning_prompt(incident: Incident, events: list[NormalizedEvent], device: Device | None) -> str:
    """Build a prompt for the planning phase (no tools)."""
    base = _incident_prompt(incident, events, device)
    return (
        "IMPORTANT: This is Phase 1 — Planning only. "
        "Do NOT use any tools. Do NOT SSH into any devices yet.\n\n"
        "Analyze the incident evidence below and propose a numbered investigation plan.\n"
        "For each step, specify:\n"
        "- The device hostname to check\n"
        "- The exact CLI command to run\n"
        "- Why this step is needed\n\n"
        "Respond ONLY with the investigation plan. Do not provide analysis or conclusions yet.\n\n"
        "---\n\n"
        f"{base}"
    )


def _execution_prompt(plan_text: str, user_instruction: str = "") -> str:
    """Build a prompt for the execution phase (tools enabled)."""
    extra = f"\nAdditional instruction from operator: {user_instruction}" if user_instruction.strip() else ""
    return (
        "Phase 2 — Execute. The operator has approved your troubleshoot investigation plan.\n"
        "Proceed with the investigation using the available tools.\n\n"
        "IMPORTANT: Before running CLI commands on a device, make sure the device "
        "is in your inventory cache. If you haven't looked it up yet, use "
        "lookup_device first.\n\n"
        "Run the commands you proposed in your plan.\n\n"
        f"Your approved plan:\n{plan_text}\n"
        f"{extra}\n\n"
        "After gathering evidence, provide your analysis including:\n"
        "- Summary of findings\n"
        "- Likely root cause\n"
        "- Category: configuration / physical-link / provider-external / uncertain\n"
        "- If this is a configuration problem, include a propose_remediation JSON block:\n"
        "```json\n"
        "{\n"
        '  "propose_remediation": true,\n'
        '  "target_host": "hostname",\n'
        '  "commands": ["config command1", "config command2"],\n'
        '  "verify_commands": ["show command to verify the fix worked"],\n'
        '  "rollback_commands": ["commands to undo the change if it fails"],\n'
        '  "risk_level": "low|medium|high|critical",\n'
        '  "rationale": "Why this change is needed"\n'
        "}\n"
        "```\n"
        "Field guidelines:\n"
        "- verify_commands: read-only show/display commands to confirm the fix worked (REQUIRED)\n"
        "- rollback_commands: commands to revert the change if verification fails (REQUIRED)\n"
        "- risk_level: low (cosmetic/debug), medium (single interface/neighbor), "
        "high (routing protocol/ACL changes), critical (multi-device or full device impact)\n\n"
        "Answer in concise operator language."
    )


def _store_results_and_create_approval(
    incident_id: int,
    final_text: str,
    *,
    requested_by: str,
    requested_by_role: str,
    round_number: int = 1,
    steps: list[dict] | None = None,
) -> dict:
    """Store artifact, update incident, parse remediation JSON, create approval if needed."""
    with session_scope() as session:
        incident = session.get(Incident, incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id} was not found")
        artifact = store_text_artifact(
            session,
            artifact_type="incident_troubleshoot",
            title=f"Incident #{incident_id} troubleshoot (round {round_number})",
            incident_id=incident_id,
            device_id=incident.primary_device_id,
            raw_text=final_text,
            reasoning=True,
            steps=steps,
        )
        incident.recommendation = final_text

        approval_id = None
        json_match = re.search(r"```json\s*(.*?)\s*```", final_text, re.DOTALL | re.IGNORECASE)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if isinstance(data, dict) and data.get("propose_remediation") is True:
                    target_host = data.get("target_host")
                    commands = data.get("commands", [])
                    rationale = data.get("rationale")
                    verify_cmds = data.get("verify_commands", [])
                    rollback_cmds = data.get("rollback_commands", [])
                    risk = data.get("risk_level", "medium")
                    if risk not in ("low", "medium", "high", "critical"):
                        risk = "medium"
                    if target_host and commands:
                        commands_text = "\n".join(commands)
                        approval = create_approval(
                            session,
                            title=f"Remediation for Incident #{incident_id}",
                            requested_by=requested_by,
                            requested_by_role="system",
                            target_host=target_host,
                            commands_text=commands_text,
                            rollback_commands_text="\n".join(rollback_cmds) if rollback_cmds else None,
                            verify_commands_text="\n".join(verify_cmds) if verify_cmds else None,
                            rationale=rationale or "AI proposed remediation",
                            risk_level=risk,
                            notes="Auto-generated by AI troubleshooting",
                            incident_id=incident_id,
                        )
                        approval_id = approval.get("id")
            except Exception as e:
                logger.error("Failed to parse or create approval from AI output: %s", e, exc_info=True)

        record_audit(
            session,
            actor=requested_by,
            actor_role=requested_by_role,
            action="incident_troubleshoot_completed",
            entity_type="incident",
            entity_id=incident_id,
            status="completed",
            summary=f"Troubleshoot round {round_number} completed for incident #{incident_id}",
            payload={
                "artifact_id": artifact.id,
                "round_number": round_number,
                "approval_id": approval_id,
            },
        )
        return {
            "artifact_id": artifact.id,
            "approval_id": approval_id,
        }


# ---------------------------------------------------------------------------
# Interactive 2-phase troubleshooting: Plan → Execute → Continue
# ---------------------------------------------------------------------------


async def stream_incident_plan(
    incident_id: int,
    *,
    requested_by: str = "manager",
    requested_by_role: str = "admin",
) -> AsyncGenerator[dict, None]:
    """Phase 1 — Ask the LLM to propose an investigation plan (no SSH).

    Yields SSE events: status, analyst_token, analyst_done, plan_ready, done.
    """
    incident, events, device = _load_incident_context(incident_id)
    prompt = _planning_prompt(incident, events, device)

    ts = await create_ts_session(incident_id)
    graph_session = await get_session(ts.graph_session_id)
    if graph_session is None:
        yield _sse("error", message="Failed to create troubleshoot session", type="session_error")
        yield _sse("done")
        return

    # Pre-populate device cache so run_cli works immediately
    if device is not None:
        graph_session.device_cache[device.hostname] = {
            "ip_address": device.mgmt_ip,
            "os_platform": device.os_platform,
            "device_role": device.device_role,
            "site": device.site,
        }

    plan_text = ""
    plan_tokens: list[str] = []

    try:
        async for evt in stream_chat(graph_session, prompt):
            event_name = str(evt.get("event") or "")
            data = evt.get("data") or {}

            if event_name == "status":
                yield evt
            elif event_name == "analyst_token":
                token = str(data.get("token") or "")
                if token:
                    plan_tokens.append(token)
                yield evt
            elif event_name == "analyst_done":
                plan_text = str(data.get("full_content") or "").strip()
                yield evt
            elif event_name == "error":
                yield evt
            # Skip tool_result — planning phase should not have tools,
            # but pass through just in case the LLM calls one
            elif event_name == "tool_result":
                yield evt

        if not plan_text:
            plan_text = "".join(plan_tokens).strip()

        if not plan_text:
            yield _sse("error", message="Planning did not return a usable plan", type="plan_error")
            await delete_ts_session(ts.ts_id)
            yield _sse("done")
            return

        ts.plan_text = plan_text
        yield _sse("plan_ready", session_id=ts.ts_id, plan_text=plan_text)
        yield _sse("done")

    except Exception as exc:
        yield _sse("error", message=str(exc), type="plan_error")
        await delete_ts_session(ts.ts_id)
        yield _sse("done")


async def stream_incident_execute(
    ts_session_id: str,
    *,
    user_instruction: str = "",
    requested_by: str = "manager",
    requested_by_role: str = "admin",
) -> AsyncGenerator[dict, None]:
    """Phase 2 — Execute the investigation plan (SSH into devices).

    Yields SSE events: status, tool_result, analyst_token, analyst_done, round_done, done.
    Can be called multiple times on the same session for iterative investigation.
    """
    ts = await get_ts_session(ts_session_id)
    if ts is None:
        yield _sse("error", message="Troubleshoot session not found or expired", type="session_error")
        yield _sse("done")
        return

    graph_session = await get_session(ts.graph_session_id)
    if graph_session is None:
        yield _sse("error", message="Graph session expired", type="session_error")
        await delete_ts_session(ts.ts_id)
        yield _sse("done")
        return

    ts.round_number += 1
    round_number = ts.round_number
    prompt = _execution_prompt(ts.plan_text, user_instruction)

    final_text = ""
    analyst_tokens: list[str] = []
    tool_steps: list[dict] = []

    try:
        async for evt in stream_chat(graph_session, prompt):
            event_name = str(evt.get("event") or "")
            data = evt.get("data") or {}

            if event_name == "tool_result":
                tool_steps.append({
                    "step_name": str(data.get("step_name") or ""),
                    "content": str(data.get("content") or ""),
                    "is_error": bool(data.get("is_error", False)),
                })
                yield evt
            elif event_name in ("status", "analyst_token"):
                if event_name == "analyst_token":
                    token = str(data.get("token") or "")
                    if token:
                        analyst_tokens.append(token)
                yield evt
            elif event_name == "analyst_done":
                final_text = str(data.get("full_content") or "").strip()
                yield evt
            elif event_name == "error":
                yield evt

        if not final_text:
            final_text = "".join(analyst_tokens).strip()

        if not final_text:
            yield _sse("error", message="Execution did not return usable results", type="execute_error")
            yield _sse("done")
            return

        # Store artifact + create approval if remediation proposed
        result = _store_results_and_create_approval(
            ts.incident_id,
            final_text,
            requested_by=requested_by,
            requested_by_role=requested_by_role,
            round_number=round_number,
            steps=tool_steps or None,
        )

        # Record this round
        ts_round = TroubleshootRound(
            round_number=round_number,
            plan_text=ts.plan_text,
            analysis_text=final_text,
            approval_id=result.get("approval_id"),
            artifact_id=result.get("artifact_id"),
        )
        ts.rounds.append(ts_round)

        yield _sse(
            "round_done",
            session_id=ts.ts_id,
            round_number=round_number,
            analysis=final_text,
            artifact_id=result.get("artifact_id"),
            approval_id=result.get("approval_id"),
        )
        yield _sse("done")

    except Exception as exc:
        yield _sse("error", message=str(exc), type="execute_error")
        yield _sse("done")
