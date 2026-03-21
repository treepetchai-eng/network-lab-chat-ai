"""AI-Driven Ops Loop orchestrator.

Connects existing pieces without changing their logic:
  syslog ingest → on_incident_created() → auto-investigate → auto-troubleshoot
  approval approved → poll_and_execute_approved() → execute → verify_remediation()
  monitoring timeout → auto_close_stale_monitoring() → auto-resolve

Humans only interact at the approval gate. Everything else is automated.

Configuration (env vars):
  OPS_LOOP_AUTO_TROUBLESHOOT      1      auto-run troubleshoot after new incident
  OPS_LOOP_AUTO_EXECUTE           1      auto-execute after approval approved
  OPS_LOOP_AUTO_VERIFY            1      auto-verify after execution
  OPS_LOOP_AUTO_CLOSE             1      auto-transition incident to monitoring after verify
  OPS_LOOP_TROUBLESHOOT_DELAY     5      seconds before starting troubleshoot
  OPS_LOOP_POLL_INTERVAL          10     seconds between polling for approved approvals
  OPS_LOOP_VERIFY_TIMEOUT         120    seconds to wait for syslog recovery before AI verify
  OPS_LOOP_MAX_AUTO_EXECUTE_RISK  medium max risk level for auto-execute (low/medium/high/critical)
  OPS_LOOP_MAX_RETRIES            3      max auto-troubleshoot attempts per incident per hour
  OPS_LOOP_MONITORING_TIMEOUT     600    seconds before auto-resolving monitoring incidents
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, func

from src.ops.db import session_scope, utcnow
from src.ops.models import AIArtifact, Approval, Incident, IncidentEventLink, IncidentHistory, NormalizedEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def loop_config() -> dict:
    """Read ops loop configuration from environment variables."""
    return {
        "auto_troubleshoot_enabled": os.getenv("OPS_LOOP_AUTO_TROUBLESHOOT", "0") != "0",
        "auto_execute_enabled": os.getenv("OPS_LOOP_AUTO_EXECUTE", "0") not in ("", "0"),
        "auto_verify_enabled": os.getenv("OPS_LOOP_AUTO_VERIFY", "0") != "0",
        "auto_close_enabled": os.getenv("OPS_LOOP_AUTO_CLOSE", "0") != "0",
        "troubleshoot_delay_seconds": int(os.getenv("OPS_LOOP_TROUBLESHOOT_DELAY", "5") or "5"),
        "poll_interval_seconds": int(os.getenv("OPS_LOOP_POLL_INTERVAL", "10") or "10"),
        "verify_timeout_seconds": int(os.getenv("OPS_LOOP_VERIFY_TIMEOUT", "120") or "120"),
        "max_auto_execute_risk": os.getenv("OPS_LOOP_MAX_AUTO_EXECUTE_RISK", "medium"),
        "max_retries_per_hour": int(os.getenv("OPS_LOOP_MAX_RETRIES", "3") or "3"),
    }


# ---------------------------------------------------------------------------
# Circuit breaker — prevent runaway auto-troubleshoot
# ---------------------------------------------------------------------------

_troubleshoot_counts: dict[int, list[datetime]] = {}
_troubleshoot_lock = threading.Lock()  # threading.Lock works across asyncio.to_thread() and new event loops


def _can_auto_troubleshoot(incident_id: int, max_per_hour: int) -> bool:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=1)
    with _troubleshoot_lock:
        recent = [ts for ts in _troubleshoot_counts.get(incident_id, []) if ts > cutoff]
        if len(recent) >= max_per_hour:
            logger.warning(
                "Circuit breaker: incident %d has %d auto-troubleshoot attempts in last hour (max %d)",
                incident_id, len(recent), max_per_hour,
            )
            return False
        recent.append(now)
        _troubleshoot_counts[incident_id] = recent
    return True


def _is_flapping(session, incident_id: int) -> bool:
    """Detect if an incident is flapping (resolved+reopened >3 cycles in 30 min)."""
    cutoff = utcnow() - timedelta(minutes=30)
    count = len(session.scalars(
        select(IncidentHistory).where(
            IncidentHistory.incident_id == incident_id,
            IncidentHistory.action.in_(["auto_resolved", "created"]),
            IncidentHistory.created_at >= cutoff,
        )
    ).all())
    return count >= 6


# ---------------------------------------------------------------------------
# Cluster-level troubleshoot dedup — only one troubleshoot per cluster
# ---------------------------------------------------------------------------

_active_cluster_ts: dict[int, float] = {}  # cluster_id -> start timestamp
_cluster_ts_lock = threading.Lock()
_CLUSTER_TS_TTL = 900  # 15 min max lock


def _claim_cluster_troubleshoot(cluster_id: int | None) -> bool:
    """Return True if this caller wins the right to troubleshoot for the cluster."""
    if cluster_id is None:
        return True  # non-clustered, always proceed
    now = time.time()
    with _cluster_ts_lock:
        started = _active_cluster_ts.get(cluster_id)
        if started and (now - started) < _CLUSTER_TS_TTL:
            return False  # another incident already running
        _active_cluster_ts[cluster_id] = now
        return True


def _release_cluster_troubleshoot(cluster_id: int | None) -> None:
    if cluster_id is None:
        return
    with _cluster_ts_lock:
        _active_cluster_ts.pop(cluster_id, None)


# ---------------------------------------------------------------------------
# SSE Event Bus (in-memory, per-incident)
# ---------------------------------------------------------------------------

_loop_queues: dict[int, list[asyncio.Queue]] = defaultdict(list)
_queue_lock = asyncio.Lock()


async def subscribe_loop(incident_id: int) -> asyncio.Queue:
    """Subscribe to real-time loop events for an incident."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    async with _queue_lock:
        _loop_queues[incident_id].append(queue)
    return queue


async def unsubscribe_loop(incident_id: int, queue: asyncio.Queue) -> None:
    async with _queue_lock:
        queues = _loop_queues.get(incident_id, [])
        if queue in queues:
            queues.remove(queue)
        if not queues and incident_id in _loop_queues:
            del _loop_queues[incident_id]


def emit_loop_event(incident_id: int, stage: str, *, persist: bool = True, **data: Any) -> None:
    """Emit a loop event: broadcast to SSE subscribers + optionally persist to IncidentHistory."""
    event = {
        "event": "loop_stage",
        "data": {
            "incident_id": incident_id,
            "stage": stage,
            "timestamp": utcnow().isoformat(),
            **data,
        },
    }
    # Broadcast to SSE subscribers (non-blocking)
    queues = list(_loop_queues.get(incident_id, []))
    for queue in queues:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass  # Drop if consumer is slow

    # Persist to DB for audit trail (fire-and-forget in thread)
    if persist:
        _persist_loop_event_sync(incident_id, stage, data)


def _persist_loop_event_sync(incident_id: int, stage: str, data: dict) -> None:
    try:
        with session_scope() as session:
            session.add(IncidentHistory(
                incident_id=incident_id,
                action=f"ops_loop_{stage}",
                actor="ops_loop",
                actor_role="system",
                summary=f"Ops loop: {stage.replace('_', ' ')}",
                payload_json={"stage": stage, **data},
            ))
    except Exception:
        logger.debug("Failed to persist loop event for incident %d stage %s", incident_id, stage, exc_info=True)


def _set_loop_incident_in_progress(incident_id: int, comment: str) -> None:
    """Move incident back to in_progress after a terminal failure.

    Prevents false auto-resolve on monitoring incidents where the fix didn't work.
    """
    with session_scope() as session:
        from src.ops.service import update_incident_status
        try:
            update_incident_status(
                session, incident_id,
                status="in_progress", actor="ops_loop", actor_role="system",
                comment=comment,
            )
        except Exception:
            pass  # best-effort — incident may already be in a non-recoverable state


def _make_troubleshoot_stream_callback(incident_id: int):
    """Forward stream_chat events to ops loop SSE bus (ephemeral, not persisted)."""
    def callback(event_name: str, data: dict) -> None:
        if event_name == "tool_result":
            emit_loop_event(
                incident_id, "troubleshoot_tool_result", persist=False,
                step_name=data.get("step_name", ""),
                content=data.get("content", ""),
                is_error=data.get("is_error", False),
                tool_name=data.get("tool_name", ""),
            )
        elif event_name == "status":
            text = data.get("text", "")
            if text:
                emit_loop_event(incident_id, "troubleshoot_status", persist=False, text=text)
        elif event_name == "analyst_done":
            emit_loop_event(incident_id, "troubleshoot_analysis_done", persist=False)
    return callback


# ---------------------------------------------------------------------------
# Phase 1: Auto-investigate + auto-troubleshoot
# ---------------------------------------------------------------------------

async def on_incident_created(incident_id: int, config: dict | None = None) -> None:
    """Entry point called after a new incident is created (from BackgroundTask).

    1. Run AI investigation (existing function, creates ai_summary)
    2. If auto_troubleshoot_enabled: run full SSH troubleshoot + maybe create Approval
    """
    config = config or loop_config()
    emit_loop_event(incident_id, "investigation_started")

    # Stage 1: Investigation
    try:
        from src.ops.service import run_incident_investigation
        with session_scope() as session:
            run_incident_investigation(
                session, incident_id,
                requested_by="ops_loop",
                requested_by_role="system",
            )
        emit_loop_event(incident_id, "investigation_completed")
    except Exception as exc:
        logger.error("Auto-investigation failed for incident %d: %s", incident_id, exc)
        emit_loop_event(incident_id, "investigation_failed", error=str(exc))
        return

    # Stage 2: Auto-troubleshoot
    if not config["auto_troubleshoot_enabled"]:
        return

    # Check circuit breaker + flap detection
    if not _can_auto_troubleshoot(incident_id, config["max_retries_per_hour"]):
        emit_loop_event(incident_id, "troubleshoot_skipped", reason="circuit_breaker")
        return

    with session_scope() as session:
        incident = session.get(Incident, incident_id)
        if incident is None or incident.status == "resolved":
            return
        cluster_id = incident.incident_cluster_id
        if _is_flapping(session, incident_id):
            emit_loop_event(incident_id, "troubleshoot_skipped", reason="flapping")
            return

    # Cluster-level dedup: only one troubleshoot per cluster at a time
    if not _claim_cluster_troubleshoot(cluster_id):
        emit_loop_event(incident_id, "troubleshoot_deferred",
                        reason="Cluster sibling is already being analyzed")
        return

    # Short delay to allow event deduplication (multiple syslog events same incident)
    delay = config["troubleshoot_delay_seconds"]
    if delay > 0:
        await asyncio.sleep(delay)

    # Re-check after delay
    with session_scope() as session:
        incident = session.get(Incident, incident_id)
        if incident is None or incident.status == "resolved":
            _release_cluster_troubleshoot(cluster_id)
            return

    emit_loop_event(incident_id, "troubleshoot_started")
    try:
        from src.ops.free_run import run_incident_troubleshoot_free_run
        result = await run_incident_troubleshoot_free_run(
            incident_id,
            requested_by="ops_loop",
            requested_by_role="admin",
            on_stream_event=_make_troubleshoot_stream_callback(incident_id),
        )
        approval_id = result.get("approval_id")
        emit_loop_event(
            incident_id, "troubleshoot_completed",
            approval_id=approval_id,
            artifact_id=result.get("artifact_id"),
        )
        if approval_id:
            emit_loop_event(incident_id, "awaiting_approval", approval_id=approval_id)
        else:
            # Physical issue or uncertain cause — escalate to human
            emit_loop_event(incident_id, "escalation_needed",
                            reason="AI did not propose config remediation — may require physical intervention")
            with session_scope() as session:
                from src.ops.service import update_incident_status
                try:
                    update_incident_status(
                        session, incident_id,
                        status="acknowledged", actor="ops_loop", actor_role="system",
                        comment="AI troubleshoot completed without config remediation. "
                                "May require physical/manual intervention.",
                    )
                except (ValueError, Exception):
                    pass  # incident may already be in compatible state
    except Exception as exc:
        logger.error("Auto-troubleshoot failed for incident %d: %s", incident_id, exc)
        emit_loop_event(incident_id, "troubleshoot_failed", error=str(exc))
    finally:
        _release_cluster_troubleshoot(cluster_id)


# ---------------------------------------------------------------------------
# Re-trigger loop (operator-initiated)
# ---------------------------------------------------------------------------

async def retrigger_incident_loop(
    incident_id: int,
    mode: str = "full",
    config: dict | None = None,
) -> None:
    """Re-run the ops loop for an existing incident.

    Called when an operator manually requests re-investigation or
    re-troubleshoot after a failure, escalation, or inconclusive result.

    Unlike on_incident_created(), this:
    - Bypasses the circuit breaker (operator explicitly requested)
    - Supports partial mode: "full" | "investigate_only" | "troubleshoot_only"
    """
    config = config or loop_config()
    emit_loop_event(incident_id, "retrigger_requested", mode=mode)

    if mode in ("full", "investigate_only"):
        emit_loop_event(incident_id, "investigation_started")
        try:
            from src.ops.service import run_incident_investigation
            with session_scope() as session:
                run_incident_investigation(
                    session, incident_id,
                    requested_by="ops_loop",
                    requested_by_role="system",
                )
            emit_loop_event(incident_id, "investigation_completed")
        except Exception as exc:
            logger.error("Retrigger investigation failed for incident %d: %s", incident_id, exc)
            emit_loop_event(incident_id, "investigation_failed", error=str(exc))
            if mode == "investigate_only":
                return
            # For "full" mode continue to troubleshoot with whatever data is available

    if mode in ("full", "troubleshoot_only"):
        with session_scope() as session:
            incident = session.get(Incident, incident_id)
            if incident is None or incident.status == "resolved":
                return

        emit_loop_event(incident_id, "troubleshoot_started")
        try:
            from src.ops.free_run import run_incident_troubleshoot_free_run
            result = await run_incident_troubleshoot_free_run(
                incident_id,
                requested_by="ops_loop",
                requested_by_role="admin",
                on_stream_event=_make_troubleshoot_stream_callback(incident_id),
            )
            approval_id = result.get("approval_id")
            emit_loop_event(
                incident_id, "troubleshoot_completed",
                approval_id=approval_id,
                artifact_id=result.get("artifact_id"),
            )
            if approval_id:
                emit_loop_event(incident_id, "awaiting_approval", approval_id=approval_id)
            else:
                emit_loop_event(
                    incident_id, "escalation_needed",
                    reason="AI did not propose config remediation — may require physical intervention",
                )
                with session_scope() as session:
                    from src.ops.service import update_incident_status
                    try:
                        update_incident_status(
                            session, incident_id,
                            status="acknowledged", actor="ops_loop", actor_role="system",
                            comment="AI troubleshoot completed without config remediation. "
                                    "May require physical/manual intervention.",
                        )
                    except (ValueError, Exception):
                        pass
        except Exception as exc:
            logger.error("Retrigger troubleshoot failed for incident %d: %s", incident_id, exc)
            emit_loop_event(incident_id, "troubleshoot_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Phase 2: Auto-execute (called periodically by scheduler)
# ---------------------------------------------------------------------------

def poll_and_execute_approved(config: dict | None = None, main_loop: asyncio.AbstractEventLoop | None = None) -> dict:
    """Poll for approved approvals and auto-execute them.

    Called periodically by OpsEmbeddedScheduler.
    Only executes approvals within the configured risk threshold.
    `main_loop` is the main asyncio event loop (passed from the scheduler coroutine)
    so that verification tasks can be submitted cross-thread via run_coroutine_threadsafe.
    """
    config = config or loop_config()
    if not config["auto_execute_enabled"]:
        return {"skipped": True, "reason": "auto_execute disabled"}

    max_risk_level = _RISK_ORDER.get(config["max_auto_execute_risk"], 1)
    executed: list[int] = []
    skipped: list[int] = []

    with session_scope() as session:
        candidates = session.scalars(
            select(Approval).where(
                Approval.status == "approved",
                Approval.execution_status == "approved",
                Approval.incident_id.isnot(None),
            ).order_by(Approval.id.asc())
        ).all()

        for approval in candidates:
            approval_risk = _RISK_ORDER.get(approval.risk_level or "medium", 1)
            if approval_risk > max_risk_level:
                logger.info(
                    "Skipping auto-execute for approval %d: risk '%s' exceeds max '%s'",
                    approval.id, approval.risk_level, config["max_auto_execute_risk"],
                )
                skipped.append(approval.id)
                continue

            incident_id = approval.incident_id
            try:
                from src.ops.service import execute_approval
                execute_approval(session, approval.id, actor="ops_loop", actor_role="admin")
                executed.append(approval.id)
                emit_loop_event(incident_id, "execution_completed", approval_id=approval.id)

                # Kick off async verification cross-thread using the main event loop
                if config["auto_verify_enabled"]:
                    if main_loop is not None and main_loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            verify_remediation(incident_id, approval.id, config),
                            main_loop,
                        )
                    else:
                        logger.debug("No main_loop provided — skipping async verify for approval %d", approval.id)

            except Exception as exc:
                logger.error("Auto-execute failed for approval %d: %s", approval.id, exc)
                emit_loop_event(incident_id, "execution_failed", approval_id=approval.id, error=str(exc))
                _set_loop_incident_in_progress(
                    incident_id,
                    f"Execution failed for approval #{approval.id}. Requires operator attention.",
                )

    return {"executed": executed, "skipped": skipped, "count": len(executed)}


# ---------------------------------------------------------------------------
# Phase 3: Verification
# ---------------------------------------------------------------------------

async def verify_remediation(
    incident_id: int,
    approval_id: int,
    config: dict | None = None,
) -> None:
    """Post-execution verification: check if the fix actually worked.

    Strategy (in order):
    1. Check execution_status from Approval — if failed, bail early
    2. Poll for syslog recovery event (incident.status == "resolved") up to verify_timeout
    3. Fallback: run AI investigation again, check confidence_score ≥ 70
    4. If confident: transition incident to "monitoring"
    """
    config = config or loop_config()
    emit_loop_event(incident_id, "verification_started", approval_id=approval_id)

    # Check 1: execution result
    with session_scope() as session:
        approval = session.get(Approval, approval_id)
        if approval is None:
            return
        if approval.execution_status in ("failed_command", "failed_auth", "failed_timeout", "failed"):
            emit_loop_event(incident_id, "verification_failed", reason="execution_failed",
                            approval_id=approval_id)
            _set_loop_incident_in_progress(
                incident_id,
                f"Execution failed (approval #{approval_id}). Fix did not apply — requires operator attention.",
            )
            return

        incident = session.get(Incident, incident_id)
        if incident is None:
            return
        # Already resolved by syslog recovery
        if incident.status == "resolved":
            emit_loop_event(incident_id, "verification_succeeded", method="syslog_recovery",
                            approval_id=approval_id)
            return

    # Check 2: Wait for syslog recovery event
    verify_timeout = config["verify_timeout_seconds"]
    poll_seconds = 5
    elapsed = 0
    while elapsed < verify_timeout:
        await asyncio.sleep(poll_seconds)
        elapsed += poll_seconds
        with session_scope() as session:
            incident = session.get(Incident, incident_id)
            if incident is not None and incident.status == "resolved":
                emit_loop_event(incident_id, "verification_succeeded", method="syslog_recovery",
                                elapsed_seconds=elapsed, approval_id=approval_id)
                return

    # Check 3: Fallback — AI re-investigation
    emit_loop_event(incident_id, "verification_ai_check", approval_id=approval_id)
    try:
        from src.ops.service import run_incident_investigation
        with session_scope() as session:
            result = run_incident_investigation(
                session, incident_id,
                requested_by="ops_loop",
                requested_by_role="system",
            )
        confidence = result.get("artifact", {}).get("confidence_score", 0) if isinstance(result, dict) else 0

        if confidence >= 70 and config["auto_close_enabled"]:
            from src.ops.service import update_incident_status
            with session_scope() as session:
                incident = session.get(Incident, incident_id)
                # execute_approval() may have already set status="monitoring" directly
                if incident is not None and incident.status in ("monitoring", "resolved"):
                    emit_loop_event(incident_id, "verification_succeeded", method="ai_analysis",
                                    confidence=confidence, approval_id=approval_id)
                else:
                    try:
                        update_incident_status(
                            session, incident_id,
                            status="monitoring",
                            actor="ops_loop",
                            actor_role="system",
                            comment=f"Auto-verified after remediation (approval #{approval_id}). AI confidence: {confidence}%",
                        )
                        emit_loop_event(incident_id, "verification_succeeded", method="ai_analysis",
                                        confidence=confidence, approval_id=approval_id)
                    except Exception:
                        emit_loop_event(incident_id, "verification_inconclusive", confidence=confidence,
                                        approval_id=approval_id)
                        _set_loop_incident_in_progress(
                            incident_id,
                            f"Verification inconclusive — could not transition to monitoring "
                            f"(approval #{approval_id}, confidence {confidence}%). Requires operator review.",
                        )
        else:
            emit_loop_event(incident_id, "verification_inconclusive", confidence=confidence,
                            approval_id=approval_id)
            _set_loop_incident_in_progress(
                incident_id,
                f"Verification inconclusive — AI confidence {confidence}% is below threshold "
                f"(approval #{approval_id}). Requires operator review.",
            )
    except Exception as exc:
        logger.error("Verification AI check failed for incident %d: %s", incident_id, exc)
        emit_loop_event(incident_id, "verification_failed", reason="ai_error",
                        error=str(exc), approval_id=approval_id)
        _set_loop_incident_in_progress(
            incident_id,
            f"Verification AI check failed (approval #{approval_id}): {exc}. Requires operator attention.",
        )


# ---------------------------------------------------------------------------
# Loop status (for API endpoint)
# ---------------------------------------------------------------------------

def get_loop_status(session, incident_id: int) -> dict:
    """Reconstruct current loop state from IncidentHistory + Approval records."""
    incident = session.get(Incident, incident_id)
    if incident is None:
        return {"error": "Incident not found"}

    history = session.scalars(
        select(IncidentHistory).where(
            IncidentHistory.incident_id == incident_id,
            IncidentHistory.action.like("ops_loop_%"),
        ).order_by(IncidentHistory.created_at.asc())
    ).all()

    approvals = session.scalars(
        select(Approval).where(
            Approval.incident_id == incident_id,
        ).order_by(Approval.id.desc())
    ).all()

    latest_approval = approvals[0] if approvals else None

    stages = []
    for entry in history:
        stages.append({
            "stage": entry.action.replace("ops_loop_", ""),
            "timestamp": entry.created_at.isoformat() if entry.created_at else None,
            "summary": entry.summary,
            "payload": entry.payload_json or {},
        })

    # Derive current phase from stages + approval status
    current_phase = "idle"
    if incident.status == "resolved":
        current_phase = "resolved"
    elif stages:
        last_stage = stages[-1]["stage"]
        if latest_approval:
            if latest_approval.execution_status in ("executing",):
                current_phase = "executing"
            elif latest_approval.execution_status in ("succeeded", "partial_success"):
                if last_stage in ("verification_started", "verification_ai_check"):
                    current_phase = "verifying"
                else:
                    current_phase = "monitoring"
            elif latest_approval.status == "pending":
                current_phase = "awaiting_approval"
            elif latest_approval.status == "awaiting_second_approval":
                current_phase = "awaiting_approval"
            elif latest_approval.status == "approved" and latest_approval.execution_status == "approved":
                current_phase = "awaiting_execution"
            elif latest_approval.status == "rejected":
                current_phase = "rejected"
        if last_stage in ("troubleshoot_started",):
            current_phase = "troubleshooting"
        elif last_stage in ("investigation_started",):
            current_phase = "investigating"
        elif last_stage in ("retrigger_requested",):
            current_phase = "investigating"

    # Derive terminal_state and available_actions
    terminal_state: str | None = None
    available_actions: list[str] = []
    escalation_context: dict | None = None

    last_stage_name = stages[-1]["stage"] if stages else None

    if incident.status == "resolved":
        terminal_state = "success"
    elif last_stage_name in (
        "investigation_failed", "troubleshoot_failed",
        "verification_failed", "verification_inconclusive",
        "troubleshoot_skipped",
    ):
        terminal_state = "needs_action"
        available_actions = ["retrigger_full", "retrigger_troubleshoot", "resolve_manual"]
    elif last_stage_name == "escalation_needed":
        terminal_state = "escalated"
        available_actions = ["retrigger_full", "retrigger_troubleshoot", "resolve_manual"]
        escalation_context = _get_escalation_context(session, incident_id)
    elif last_stage_name == "execution_failed":
        terminal_state = "needs_action"
        available_actions = ["retrigger_troubleshoot", "resolve_manual"]
    elif last_stage_name in ("verification_succeeded", "auto_resolved"):
        terminal_state = "success"
    elif current_phase == "rejected":
        terminal_state = "needs_action"
        available_actions = ["retrigger_troubleshoot", "resolve_manual"]
    elif current_phase == "awaiting_approval":
        available_actions = ["approve", "reject"]
    elif current_phase == "awaiting_execution":
        available_actions = ["execute"]

    return {
        "incident_id": incident_id,
        "incident_status": incident.status,
        "current_phase": current_phase,
        "latest_approval_id": latest_approval.id if latest_approval else None,
        "latest_approval_status": latest_approval.status if latest_approval else None,
        "stages": stages,
        "config": loop_config(),
        "terminal_state": terminal_state,
        "available_actions": available_actions,
        "escalation_context": escalation_context,
    }


def _get_escalation_context(session, incident_id: int) -> dict | None:
    """Pull the latest troubleshoot artifact to show AI analysis when escalation_needed."""
    artifact = session.scalars(
        select(AIArtifact).where(
            AIArtifact.incident_id == incident_id,
            AIArtifact.artifact_type == "incident_troubleshoot",
        ).order_by(AIArtifact.id.desc()).limit(1)
    ).first()
    if artifact is None:
        return None
    return {
        "analysis": artifact.summary or "",
        "root_cause": artifact.root_cause or "",
        "confidence_score": artifact.confidence_score or 0,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
    }


# ---------------------------------------------------------------------------
# Auto-close monitoring incidents
# ---------------------------------------------------------------------------

def auto_close_stale_monitoring() -> dict:
    """Auto-resolve incidents stuck in 'monitoring' with no recent events.

    Called periodically by OpsEmbeddedScheduler.
    """
    timeout = int(os.getenv("OPS_LOOP_MONITORING_TIMEOUT", "600") or "600")
    cutoff = utcnow() - timedelta(seconds=timeout)
    from src.ops.service import update_incident_status

    resolved_count = 0
    with session_scope() as session:
        stale = session.scalars(
            select(Incident)
            .where(Incident.status == "monitoring")
            .where(Incident.updated_at < cutoff)
        ).all()

        for incident in stale:
            has_recent = session.execute(
                select(func.count())
                .select_from(IncidentEventLink)
                .join(NormalizedEvent, NormalizedEvent.id == IncidentEventLink.event_id)
                .where(IncidentEventLink.incident_id == incident.id)
                .where(NormalizedEvent.event_time > cutoff)
            ).scalar()
            if not has_recent:
                try:
                    update_incident_status(
                        session, incident.id,
                        status="resolved",
                        actor="ops_loop",
                        actor_role="system",
                        comment=f"Auto-resolved: no new events after {timeout}s monitoring period",
                    )
                    emit_loop_event(incident.id, "auto_resolved")
                    resolved_count += 1
                except Exception as exc:
                    logger.error("Auto-close failed for incident %d: %s", incident.id, exc)

    return {"resolved": resolved_count}


# ---------------------------------------------------------------------------
# AI Recovery Verification (escalated incidents with recovery syslog)
# ---------------------------------------------------------------------------

QUIET_EVENT_TYPES = {"device_traceback", "device_restart", "cpu_hog", "critical_region_fault"}


async def run_ai_recovery_verify() -> dict:
    """Process escalated incidents that received a recovery syslog event.

    Runs an AI SSH health check to confirm the device is truly restored before
    resolving.  Called every 60 s by OpsEmbeddedScheduler.
    """
    from src.ops.free_run import run_health_check_free_run
    from src.ops.service import update_incident_status

    cutoff = utcnow() - timedelta(hours=2)
    verified = 0
    inconclusive = 0

    with session_scope() as session:
        # Incidents that have a recent "recovery_detected" history entry but
        # have NOT yet been followed by a health-check or resolution.
        blocked_subq = (
            select(IncidentHistory.incident_id)
            .where(
                IncidentHistory.action.in_([
                    "ai_health_check_started", "auto_resolved",
                    "ops_loop_verification_started",
                ]),
                IncidentHistory.created_at > cutoff,
            )
        )
        candidate_ids = list(session.scalars(
            select(Incident.id)
            .join(IncidentHistory, IncidentHistory.incident_id == Incident.id)
            .where(
                Incident.status.in_(["new", "acknowledged", "in_progress"]),
                IncidentHistory.action == "recovery_detected",
                IncidentHistory.created_at > cutoff,
                Incident.id.not_in(blocked_subq),
            )
            .distinct()
        ).all())

    for inc_id in candidate_ids:
        emit_loop_event(inc_id, "ai_health_check_started")
        _persist_loop_event_sync(inc_id, "ai_health_check_started", {})
        try:
            result = await run_health_check_free_run(inc_id)
            if result.get("healthy") and result.get("confidence", 0) >= 75:
                with session_scope() as session:
                    update_incident_status(
                        session, inc_id,
                        status="resolved",
                        actor="ops_loop",
                        actor_role="system",
                        comment=(
                            f"AI verified physical recovery. "
                            f"Confidence: {result['confidence']}%\n"
                            f"{result.get('summary', '')}"
                        ),
                    )
                emit_loop_event(
                    inc_id, "auto_resolved",
                    method="ai_verified_physical_recovery",
                    confidence=result.get("confidence", 0),
                )
                verified += 1
            else:
                emit_loop_event(
                    inc_id, "health_check_inconclusive",
                    confidence=result.get("confidence", 0),
                )
                inconclusive += 1
        except Exception as exc:
            logger.error("AI recovery verify failed for incident %d: %s", inc_id, exc, exc_info=True)
            emit_loop_event(inc_id, "health_check_inconclusive", error=str(exc))
            inconclusive += 1

    return {"verified": verified, "inconclusive": inconclusive}


# ---------------------------------------------------------------------------
# AI Health Check for quiet incidents (no syslog recovery event type)
# ---------------------------------------------------------------------------


async def run_ai_health_check_quiet() -> dict:
    """Proactive health check for incidents whose event types have no recovery syslog.

    After a configurable quiet period with no new events, runs an AI SSH health
    check.  If the device appears healthy, transitions the incident to
    ``monitoring`` so that ``auto_close_stale_monitoring`` can finish the
    resolution after a stability window.

    Called every 300 s by OpsEmbeddedScheduler.
    """
    from src.ops.free_run import run_health_check_free_run
    from src.ops.service import update_incident_status

    quiet_threshold = int(os.getenv("OPS_LOOP_QUIET_THRESHOLD_SECONDS", "900") or "900")
    cutoff_quiet = utcnow() - timedelta(seconds=quiet_threshold)
    cutoff_checked = utcnow() - timedelta(minutes=30)

    checked = 0
    passed = 0

    with session_scope() as session:
        blocked_subq = (
            select(IncidentHistory.incident_id)
            .where(
                IncidentHistory.action == "ai_health_check_started",
                IncidentHistory.created_at > cutoff_checked,
            )
        )
        candidates = list(session.scalars(
            select(Incident)
            .where(
                Incident.event_type.in_(QUIET_EVENT_TYPES),
                Incident.status.in_(["new", "acknowledged"]),
                Incident.last_event_time < cutoff_quiet,
                Incident.primary_device_id.isnot(None),
                Incident.id.not_in(blocked_subq),
            )
        ).all())
        # Materialize attributes before leaving session
        candidate_ids = [inc.id for inc in candidates]

    for inc_id in candidate_ids:
        emit_loop_event(inc_id, "ai_health_check_started")
        _persist_loop_event_sync(inc_id, "ai_health_check_started", {})
        try:
            result = await run_health_check_free_run(inc_id)
            checked += 1
            if result.get("healthy") and result.get("confidence", 0) >= 75:
                monitoring_timeout = int(os.getenv("OPS_LOOP_MONITORING_TIMEOUT", "600") or "600")
                with session_scope() as session:
                    update_incident_status(
                        session, inc_id,
                        status="monitoring",
                        actor="ops_loop",
                        actor_role="system",
                        comment=(
                            f"Device appears healthy (confidence {result['confidence']}%). "
                            f"Monitoring for {monitoring_timeout}s before auto-close.\n"
                            f"{result.get('summary', '')}"
                        ),
                    )
                emit_loop_event(inc_id, "health_check_passed", confidence=result.get("confidence", 0))
                passed += 1
            else:
                emit_loop_event(inc_id, "health_check_inconclusive", confidence=result.get("confidence", 0))
        except Exception as exc:
            logger.error("AI health check failed for incident %d: %s", inc_id, exc, exc_info=True)
            emit_loop_event(inc_id, "health_check_inconclusive", error=str(exc))

    return {"checked": checked, "passed": passed}
