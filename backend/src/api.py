"""
src/api.py
==========
FastAPI application — simplified for core incident flow.

Run with::

    cd backend
    uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload

Endpoints
---------
POST   /api/session          Create a new chat session
DELETE /api/session/{id}     Delete a chat session
POST   /api/chat             Send a message (returns SSE stream)
GET    /api/health           Health check
GET    /api/inventory        Device inventory (legacy)

POST   /api/ops/ingest/syslog           Syslog push ingestion
GET    /api/ops/overview                 Dashboard data
GET    /api/ops/devices                  Device list
GET    /api/ops/incidents                Incident list
GET    /api/ops/incidents/{id}           Incident detail
POST   /api/ops/incidents/{id}/investigate    Refresh AI summary from logs
POST   /api/ops/incidents/{id}/troubleshoot   AI SSH troubleshoot (read-only)
POST   /api/ops/incidents/{id}/troubleshoot/plan     SSE: propose investigation plan
POST   /api/ops/incidents/{id}/troubleshoot/execute  SSE: execute plan with real-time progress
GET    /api/ops/incidents/{id}/remediation-status
GET    /api/ops/approvals                Approval list
POST   /api/ops/approvals/{id}/approve
POST   /api/ops/approvals/{id}/reject
POST   /api/ops/approvals/{id}/execute
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import BackgroundTasks, Body, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from src.session_manager import (
    cleanup_stale,
    create_session,
    delete_session,
    get_session,
    session_count,
)
from src.ops.troubleshoot_session import cleanup_stale_ts_sessions
from src.ops.db import init_db, session_scope
from src.ops.free_run import (
    run_incident_troubleshoot_free_run,
    stream_incident_execute,
    stream_incident_plan,
)
from src.ops.runtime import OpsEmbeddedScheduler, scheduler_enabled
from src.ops.ops_loop import (
    get_loop_status,
    loop_config as get_ops_loop_config,
    on_incident_created,
    retrigger_incident_loop,
    subscribe_loop,
    unsubscribe_loop,
)
from src.ops.service import (
    ApprovalExecutionError,
    assign_incident,
    devices_payload,
    execute_approval,
    get_cluster_detail,
    get_incident_detail,
    get_incident_feedback,
    get_incident_remediation_status,
    ingest_syslog_push,
    list_approvals,
    list_clusters,
    list_incidents,
    overview,
    review_approval,
    run_incident_scan,
    run_incident_chat,
    run_incident_investigation,
    submit_incident_feedback,
    update_incident_status,
)
from src.sse_stream import stream_chat
from src.tools.inventory_tools import list_all_devices

# ---------------------------------------------------------------------------
# Background cleanup task
# ---------------------------------------------------------------------------

_cleanup_task: asyncio.Task | None = None


async def _periodic_cleanup() -> None:
    """Remove stale sessions every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        await cleanup_stale()
        await cleanup_stale_ts_sessions()


def _cors_origins() -> list[str]:
    configured = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cleanup_task
    init_db()
    scheduler: OpsEmbeddedScheduler | None = None
    if scheduler_enabled():
        scheduler = OpsEmbeddedScheduler()
        await scheduler.start()
    app.state.ops_scheduler = scheduler
    _cleanup_task = asyncio.create_task(_periodic_cleanup())
    yield
    _cleanup_task.cancel()
    try:
        await _cleanup_task
    except asyncio.CancelledError:
        pass
    if scheduler is not None:
        await scheduler.stop()


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Network Copilot API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateSessionResponse(BaseModel):
    session_id: str


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(..., min_length=1, max_length=2000)


class HealthResponse(BaseModel):
    status: str
    sessions: int


class InventoryDeviceResponse(BaseModel):
    hostname: str
    ip_address: str
    os_platform: str
    device_role: str
    site: str
    version: str


class OpsActionResponse(BaseModel):
    ok: bool = True
    detail: str
    data: dict = Field(default_factory=dict)


class ReviewApprovalRequest(BaseModel):
    actor: str = Field(default="manager", min_length=1, max_length=120)
    actor_role: Literal["viewer", "operator", "approver", "admin"] = "admin"
    comment: str | None = Field(default=None, max_length=4000)


class InvestigateIncidentRequest(BaseModel):
    requested_by: str = Field(default="manager", min_length=1, max_length=120)
    requested_by_role: Literal["viewer", "operator", "approver", "admin"] = "admin"


class TroubleshootIncidentRequest(BaseModel):
    requested_by: str = Field(default="manager", min_length=1, max_length=120)
    requested_by_role: Literal["viewer", "operator", "approver", "admin"] = "admin"


class TroubleshootExecuteRequest(BaseModel):
    troubleshoot_session_id: str = Field(..., min_length=1, max_length=255)
    user_instruction: str = Field(default="", max_length=2000)
    requested_by: str = Field(default="manager", min_length=1, max_length=120)
    requested_by_role: Literal["viewer", "operator", "approver", "admin"] = "admin"


class SyslogIngestEventRequest(BaseModel):
    source_ip: str = Field(..., min_length=1, max_length=64)
    raw_message: str = Field(..., min_length=1, max_length=12000)
    file_path: str | None = Field(default=None, max_length=512)
    event_uid: str | None = Field(default=None, max_length=255)
    collector_time: datetime | None = None
    metadata: dict = Field(default_factory=dict)


class SyslogIngestRequest(BaseModel):
    collector: str = Field(default="syslog-ng", min_length=1, max_length=120)
    events: list[SyslogIngestEventRequest] = Field(default_factory=list, min_length=1, max_length=500)


class SyslogIngestSingleRequest(BaseModel):
    collector: str = Field(default="syslog-ng", min_length=1, max_length=120)
    source_ip: str = Field(..., min_length=1, max_length=64)
    raw_message: str = Field(..., min_length=1, max_length=12000)
    file_path: str | None = Field(default=None, max_length=512)
    event_uid: str | None = Field(default=None, max_length=255)
    collector_time: datetime | None = None
    metadata: dict = Field(default_factory=dict)


def _status_for_value_error(exc: ValueError) -> int:
    return 404 if "not found" in str(exc).lower() else 400


def _require_syslog_token(provided_token: str | None) -> None:
    expected_token = os.getenv("SYSLOG_INGEST_TOKEN", "").strip()
    if expected_token and provided_token != expected_token:
        raise HTTPException(status_code=403, detail="Invalid syslog ingest token")


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------


@app.post("/api/session", response_model=CreateSessionResponse)
async def create_session_endpoint():
    session = await create_session()
    return CreateSessionResponse(session_id=session.session_id)


@app.delete("/api/session/{session_id}")
async def delete_session_endpoint(session_id: str):
    removed = await delete_session(session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@app.get("/api/session/{session_id}/validate")
async def validate_session_endpoint(session_id: str):
    session = await get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    session = await get_session(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator():
        try:
            async for evt in stream_chat(session, req.message):
                yield {
                    "event": evt["event"],
                    "data": json.dumps(evt.get("data", {}), ensure_ascii=False),
                }
        except Exception as exc:
            yield {
                "event": "error",
                "data": json.dumps({"message": str(exc), "type": "graph_error"}, ensure_ascii=False),
            }
            yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())


@app.get("/api/health", response_model=HealthResponse)
async def health_endpoint():
    return HealthResponse(status="ok", sessions=session_count())


@app.get("/api/inventory", response_model=list[InventoryDeviceResponse])
async def inventory_endpoint():
    rows = json.loads(list_all_devices.invoke({}))
    if isinstance(rows, dict) and rows.get("error"):
        raise HTTPException(status_code=500, detail=rows["error"])
    return rows


# ---------------------------------------------------------------------------
# Ops — Syslog ingestion
# ---------------------------------------------------------------------------


def _background_incident_scan() -> None:
    """Run one analyzer pass after syslog ingest so incidents appear near real time."""
    try:
        with session_scope() as session:
            result = run_incident_scan(session, requested_by="syslog_ingest")
        # Trigger the AI ops loop when a new incident is created
        if result.get("incidents_created", 0) > 0 and result.get("touched_incident_id"):
            incident_id = result["touched_incident_id"]
            try:
                loop = asyncio.get_event_loop()
                asyncio.run_coroutine_threadsafe(on_incident_created(incident_id), loop)
            except RuntimeError:
                logger.warning("Could not trigger ops loop for incident %s: no event loop", incident_id)
    except Exception as exc:
        logger.error("Background incident scan failed: %s", exc, exc_info=True)


@app.post("/api/ops/ingest/syslog", response_model=OpsActionResponse)
def ops_ingest_syslog_endpoint(
    background_tasks: BackgroundTasks,
    payload: dict = Body(...),
    x_syslog_token: str | None = Header(default=None, alias="X-Syslog-Token"),
):
    _require_syslog_token(x_syslog_token)
    if "events" in payload:
        req = SyslogIngestRequest.model_validate(payload)
        collector_name = req.collector
        events = [event.model_dump() for event in req.events]
    else:
        req = SyslogIngestSingleRequest.model_validate(payload)
        collector_name = req.collector
        events = [req.model_dump(exclude={"collector"})]
    with session_scope() as session:
        data = ingest_syslog_push(session, collector_name=collector_name, events=events)

    if data.get("raw_logs", 0) or data.get("events", 0):
        background_tasks.add_task(_background_incident_scan)

    return OpsActionResponse(detail="Syslog batch ingested", data=data)


# ---------------------------------------------------------------------------
# Ops — Dashboard
# ---------------------------------------------------------------------------


@app.get("/api/ops/overview")
def ops_overview_endpoint():
    with session_scope() as session:
        return overview(session)


# ---------------------------------------------------------------------------
# Ops — Devices
# ---------------------------------------------------------------------------


@app.get("/api/ops/devices")
def ops_devices_endpoint(
    q: str | None = None,
    site: str | None = None,
    role: str | None = None,
    has_open_incidents: bool = False,
    sort_by: str = "hostname",
    sort_dir: Literal["asc", "desc"] = "asc",
    page: int = 1,
    page_size: int = 25,
):
    with session_scope() as session:
        return devices_payload(
            session, q=q, site=site, role=role,
            has_open_incidents=has_open_incidents,
            sort_by=sort_by, sort_dir=sort_dir,
            page=max(page, 1), page_size=min(max(page_size, 1), 100),
        )


# ---------------------------------------------------------------------------
# Ops — Incidents
# ---------------------------------------------------------------------------


@app.get("/api/ops/incidents")
def ops_incidents_endpoint(
    q: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    site: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
    sort_by: str = "updated_at",
    sort_dir: Literal["asc", "desc"] = "desc",
    page: int = 1,
    page_size: int = 25,
):
    with session_scope() as session:
        return list_incidents(
            session, q=q, status=status, severity=severity, site=site,
            updated_from=updated_from, updated_to=updated_to,
            sort_by=sort_by, sort_dir=sort_dir,
            page=max(page, 1), page_size=min(max(page_size, 1), 100),
        )


@app.get("/api/ops/incidents/{incident_id}")
def ops_incident_detail_endpoint(incident_id: int):
    with session_scope() as session:
        payload = get_incident_detail(session, incident_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return payload


@app.post("/api/ops/incidents/{incident_id}/investigate", response_model=OpsActionResponse)
def ops_investigate_incident_endpoint(incident_id: int, req: InvestigateIncidentRequest):
    with session_scope() as session:
        try:
            data = run_incident_investigation(
                session, incident_id,
                requested_by=req.requested_by, requested_by_role=req.requested_by_role,
            )
        except ValueError as exc:
            raise HTTPException(status_code=_status_for_value_error(exc), detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return OpsActionResponse(detail="Investigation completed", data=data)


@app.post("/api/ops/incidents/{incident_id}/troubleshoot", response_model=OpsActionResponse)
async def ops_troubleshoot_incident_endpoint(incident_id: int, req: TroubleshootIncidentRequest):
    try:
        data = await run_incident_troubleshoot_free_run(
            incident_id,
            requested_by=req.requested_by, requested_by_role=req.requested_by_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=_status_for_value_error(exc), detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return OpsActionResponse(detail="Troubleshooting completed", data=data)


@app.post("/api/ops/incidents/{incident_id}/troubleshoot/plan")
async def ops_troubleshoot_plan_endpoint(incident_id: int, req: TroubleshootIncidentRequest):
    """SSE stream: ask the LLM to propose an investigation plan (no SSH yet)."""
    async def event_generator():
        try:
            async for evt in stream_incident_plan(
                incident_id,
                requested_by=req.requested_by,
                requested_by_role=req.requested_by_role,
            ):
                yield {
                    "event": evt["event"],
                    "data": json.dumps(evt.get("data", {}), ensure_ascii=False),
                }
        except Exception as exc:
            yield {
                "event": "error",
                "data": json.dumps({"message": str(exc), "type": "plan_error"}, ensure_ascii=False),
            }
            yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())


@app.post("/api/ops/incidents/{incident_id}/troubleshoot/execute")
async def ops_troubleshoot_execute_endpoint(incident_id: int, req: TroubleshootExecuteRequest):
    """SSE stream: execute the approved investigation plan (SSH into devices)."""
    async def event_generator():
        try:
            async for evt in stream_incident_execute(
                req.troubleshoot_session_id,
                user_instruction=req.user_instruction,
                requested_by=req.requested_by,
                requested_by_role=req.requested_by_role,
            ):
                yield {
                    "event": evt["event"],
                    "data": json.dumps(evt.get("data", {}), ensure_ascii=False),
                }
        except Exception as exc:
            yield {
                "event": "error",
                "data": json.dumps({"message": str(exc), "type": "execute_error"}, ensure_ascii=False),
            }
            yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())


@app.get("/api/ops/incidents/{incident_id}/remediation-status")
def ops_incident_remediation_status_endpoint(incident_id: int):
    with session_scope() as session:
        payload = get_incident_detail(session, incident_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return get_incident_remediation_status(session, incident_id)


# ---------------------------------------------------------------------------
# Ops — AI Ops Loop
# ---------------------------------------------------------------------------


@app.get("/api/ops/loop/config")
def ops_loop_config_endpoint():
    return get_ops_loop_config()


@app.get("/api/ops/incidents/{incident_id}/loop/status")
def ops_loop_status_endpoint(incident_id: int):
    with session_scope() as session:
        return get_loop_status(session, incident_id)


class RetriggerRequest(BaseModel):
    mode: Literal["full", "investigate_only", "troubleshoot_only"] = "full"
    actor: str = "operator"
    actor_role: str = "admin"


@app.post("/api/ops/incidents/{incident_id}/loop/retrigger", response_model=OpsActionResponse)
async def ops_loop_retrigger_endpoint(incident_id: int, req: RetriggerRequest):
    from src.ops.models import Incident
    with session_scope() as session:
        incident = session.get(Incident, incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        if incident.status == "resolved":
            raise HTTPException(status_code=409, detail="Cannot retrigger a resolved incident")
    try:
        await retrigger_incident_loop(incident_id, mode=req.mode)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return OpsActionResponse(detail=f"Ops loop re-triggered ({req.mode})")


@app.get("/api/ops/incidents/{incident_id}/loop/stream")
async def ops_loop_stream_endpoint(incident_id: int):
    """SSE stream for real-time ops loop events."""
    queue = await subscribe_loop(incident_id)

    async def event_generator():
        try:
            while True:
                event = await queue.get()
                yield {
                    "event": event.get("stage", "message"),
                    "data": json.dumps(event),
                }
        except asyncio.CancelledError:
            pass
        finally:
            await unsubscribe_loop(incident_id, queue)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Ops — Incident Feedback
# ---------------------------------------------------------------------------


class IncidentFeedbackRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    was_false_positive: bool = False
    resolution_effectiveness: Literal["effective", "partial", "ineffective", "unknown"] = "unknown"
    operator_notes: str | None = Field(default=None, max_length=4000)
    created_by: str = Field(default="operator", min_length=1, max_length=120)


@app.post("/api/ops/incidents/{incident_id}/feedback", response_model=OpsActionResponse)
def ops_submit_feedback_endpoint(incident_id: int, req: IncidentFeedbackRequest):
    with session_scope() as session:
        try:
            data = submit_incident_feedback(
                session, incident_id=incident_id,
                rating=req.rating, was_false_positive=req.was_false_positive,
                resolution_effectiveness=req.resolution_effectiveness,
                operator_notes=req.operator_notes, created_by=req.created_by,
            )
        except ValueError as exc:
            raise HTTPException(status_code=_status_for_value_error(exc), detail=str(exc)) from exc
    return OpsActionResponse(detail="Feedback submitted", data=data)


@app.get("/api/ops/incidents/{incident_id}/feedback")
def ops_get_feedback_endpoint(incident_id: int):
    with session_scope() as session:
        return get_incident_feedback(session, incident_id)


class AssignIncidentRequest(BaseModel):
    assigned_to: str
    actor: str = "operator"
    actor_role: str = "admin"
    comment: str | None = None


@app.post("/api/ops/incidents/{incident_id}/assign", response_model=OpsActionResponse)
def ops_assign_incident_endpoint(incident_id: int, req: AssignIncidentRequest):
    with session_scope() as session:
        try:
            data = assign_incident(
                session,
                incident_id=incident_id,
                assignee=req.assigned_to,
                actor=req.actor,
                actor_role=req.actor_role,
                comment=req.comment,
            )
        except (ValueError, LookupError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return OpsActionResponse(detail=f"Incident assigned to {req.assigned_to}", data=data)


# ---------------------------------------------------------------------------
# Ops — Incident Chat
# ---------------------------------------------------------------------------


class IncidentChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[dict] = Field(default_factory=list)
    requested_by: str = Field(default="manager", min_length=1, max_length=120)
    requested_by_role: Literal["viewer", "operator", "approver", "admin"] = "admin"


@app.post("/api/ops/incidents/{incident_id}/chat")
def ops_incident_chat_endpoint(incident_id: int, req: IncidentChatRequest):
    with session_scope() as session:
        try:
            result = run_incident_chat(
                session, incident_id=incident_id,
                message=req.message, history=req.history,
                requested_by=req.requested_by,
                requested_by_role=req.requested_by_role,
            )
        except ValueError as exc:
            raise HTTPException(status_code=_status_for_value_error(exc), detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result


# ---------------------------------------------------------------------------
# Ops — Clusters
# ---------------------------------------------------------------------------


@app.get("/api/ops/clusters")
def ops_clusters_endpoint(
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
):
    with session_scope() as session:
        return list_clusters(session, status=status, page=max(page, 1), page_size=min(max(page_size, 1), 100))


@app.get("/api/ops/clusters/{cluster_id}")
def ops_cluster_detail_endpoint(cluster_id: int):
    with session_scope() as session:
        payload = get_cluster_detail(session, cluster_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Cluster not found")
        return payload


# ---------------------------------------------------------------------------
# Ops — Dev: Purge All Incidents (temporary, for testing)
# ---------------------------------------------------------------------------


@app.post("/api/ops/dev/purge-incidents")
def ops_dev_purge_incidents():
    """Purge all incident-related data. Keeps devices and syslog_checkpoints."""
    from sqlalchemy import text
    with session_scope() as session:
        session.execute(text("""
            TRUNCATE ai_artifacts, remediation_tasks, incident_feedback,
                     notification_logs, incident_history, incident_event_links,
                     audit_entries, scan_history, llm_analyses CASCADE
        """))
        session.execute(text("UPDATE device_interfaces SET last_event_id = NULL"))
        session.execute(text("TRUNCATE approvals, incidents, normalized_events, jobs CASCADE"))
        session.execute(text("TRUNCATE raw_logs, incident_clusters CASCADE"))
        for tbl in [
            "ai_artifacts", "remediation_tasks", "incident_feedback",
            "notification_logs", "incident_history", "incident_event_links",
            "audit_entries", "scan_history", "llm_analyses", "approvals", "incidents",
            "normalized_events", "jobs", "raw_logs", "incident_clusters",
        ]:
            session.execute(text(f"ALTER SEQUENCE {tbl}_id_seq RESTART WITH 1"))
    return {"status": "ok", "message": "All incidents purged. IDs restart from 1."}


# ---------------------------------------------------------------------------
# Ops — Approvals
# ---------------------------------------------------------------------------


@app.get("/api/ops/approvals")
def ops_approvals_endpoint(
    q: str | None = None,
    status: str | None = None,
    risk_level: str | None = None,
    sort_by: str = "requested_at",
    sort_dir: Literal["asc", "desc"] = "desc",
    page: int = 1,
    page_size: int = 25,
):
    with session_scope() as session:
        return list_approvals(
            session, q=q, status=status, risk_level=risk_level,
            sort_by=sort_by, sort_dir=sort_dir,
            page=max(page, 1), page_size=min(max(page_size, 1), 100),
        )


@app.post("/api/ops/approvals/{approval_id}/approve", response_model=OpsActionResponse)
def ops_approve_endpoint(approval_id: int, req: ReviewApprovalRequest):
    with session_scope() as session:
        try:
            reviewed = review_approval(
                session, approval_id,
                actor=req.actor, actor_role=req.actor_role,
                comment=req.comment, decision="approved",
            )
            if reviewed.get("status") == "approved":
                data = execute_approval(session, approval_id, actor=req.actor, actor_role=req.actor_role)
                detail = "Approval approved and execution started"
            else:
                data = reviewed
                detail = "Approval recorded"
        except ValueError as exc:
            raise HTTPException(status_code=_status_for_value_error(exc), detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ApprovalExecutionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return OpsActionResponse(detail=detail, data=data)


@app.post("/api/ops/approvals/{approval_id}/reject", response_model=OpsActionResponse)
def ops_reject_endpoint(approval_id: int, req: ReviewApprovalRequest):
    with session_scope() as session:
        try:
            data = review_approval(
                session, approval_id,
                actor=req.actor, actor_role=req.actor_role,
                comment=req.comment, decision="rejected",
            )
        except ValueError as exc:
            raise HTTPException(status_code=_status_for_value_error(exc), detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return OpsActionResponse(detail="Approval rejected", data=data)


@app.post("/api/ops/approvals/{approval_id}/execute", response_model=OpsActionResponse)
def ops_execute_approval_endpoint(approval_id: int, req: ReviewApprovalRequest):
    with session_scope() as session:
        try:
            data = execute_approval(session, approval_id, actor=req.actor, actor_role=req.actor_role)
        except ValueError as exc:
            raise HTTPException(status_code=_status_for_value_error(exc), detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ApprovalExecutionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return OpsActionResponse(detail="Approval executed", data=data)


# ---------------------------------------------------------------------------
# Incident status management
# ---------------------------------------------------------------------------


class UpdateIncidentStatusRequest(BaseModel):
    status: str
    actor: str = "operator"
    actor_role: str = "admin"
    comment: str | None = None


@app.patch("/api/ops/incidents/{incident_id}/status", response_model=OpsActionResponse)
def ops_update_incident_status_endpoint(incident_id: int, req: UpdateIncidentStatusRequest):
    with session_scope() as session:
        try:
            data = update_incident_status(
                session, incident_id,
                status=req.status,
                actor=req.actor,
                actor_role=req.actor_role,
                comment=req.comment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=_status_for_value_error(exc), detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return OpsActionResponse(detail=f"Incident status updated to {req.status}", data=data)
