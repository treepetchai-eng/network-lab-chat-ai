"""
Minimal FastAPI app for the chat-only Network Copilot backend.

Run with:

    cd backend
    uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
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

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from src.aiops.service import AIOpsService
from src.session_manager import cleanup_stale, create_session, delete_session, get_session, session_count
from src.sse_stream import stream_chat
from src.tools.inventory_tools import list_all_devices

load_dotenv()

logger = logging.getLogger(__name__)

_cleanup_task: asyncio.Task | None = None
_aiops_worker_task: asyncio.Task | None = None
_aiops_service = AIOpsService()
_AIOPS_WORKER_INTERVAL_SECONDS = max(1, int(os.getenv("AIOPS_WORKER_INTERVAL_SECONDS", "2")))
_AIOPS_MAX_PARSE_PER_CYCLE = max(1, int(os.getenv("AIOPS_MAX_PARSE_PER_CYCLE", "4")))
_AIOPS_MAX_DECISIONS_PER_CYCLE = max(1, int(os.getenv("AIOPS_MAX_DECISIONS_PER_CYCLE", "3")))


def _is_test_runtime() -> bool:
    return "pytest" in sys.modules or bool(os.getenv("PYTEST_CURRENT_TEST"))


def _ensure_aiops_ready() -> None:
    _aiops_service.bootstrap()


async def _periodic_cleanup() -> None:
    """Remove stale chat sessions every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        await cleanup_stale()


async def _periodic_aiops_worker() -> None:
    while True:
        try:
            await asyncio.to_thread(
                _aiops_service.process_pending_jobs,
                _AIOPS_MAX_PARSE_PER_CYCLE + _AIOPS_MAX_DECISIONS_PER_CYCLE + 1,
                _AIOPS_MAX_PARSE_PER_CYCLE,
                _AIOPS_MAX_DECISIONS_PER_CYCLE,
            )
        except Exception as exc:
            logger.error("AIOps worker loop failed: %s", exc, exc_info=True)
        await asyncio.sleep(_AIOPS_WORKER_INTERVAL_SECONDS)


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
async def lifespan(_app: FastAPI):
    global _cleanup_task, _aiops_worker_task
    try:
        _aiops_service.bootstrap()
    except Exception as exc:
        logger.error("AIOps bootstrap failed: %s", exc, exc_info=True)
    _cleanup_task = asyncio.create_task(_periodic_cleanup())
    if not _is_test_runtime():
        _aiops_worker_task = asyncio.create_task(_periodic_aiops_worker())
    yield
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        _cleanup_task = None
    if _aiops_worker_task is not None:
        _aiops_worker_task.cancel()
        try:
            await _aiops_worker_task
        except asyncio.CancelledError:
            pass
        _aiops_worker_task = None


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


class SyslogIngestRequest(BaseModel):
    source_ip: str
    raw_message: str = Field(..., min_length=1)
    hostname: str | None = None
    event_time: str | None = None


class ProposalActionRequest(BaseModel):
    actor: str = Field(..., min_length=1, max_length=120)


class RecoveryDecisionRequest(BaseModel):
    healed: bool
    note: str = Field(..., min_length=1, max_length=1000)


class AIOpsResetResponse(BaseModel):
    incidents_removed: int
    events_removed: int
    raw_logs_removed: int


@app.post("/api/session", response_model=CreateSessionResponse)
async def create_session_endpoint():
    session = await create_session()
    return CreateSessionResponse(session_id=session.session_id)


def _build_incident_context(detail: dict) -> str:
    """Build a concise context string from incident detail to inject into the LLM system prompt."""
    inc = detail.get("incident", {})
    ts = detail.get("troubleshoot")
    ai_sum = detail.get("ai_summary")
    logs = detail.get("raw_logs", [])

    lines: list[str] = [
        f"Incident:   {inc.get('incident_no', '?')} — {inc.get('severity', '?').upper()} / {inc.get('status', '?')}",
        f"Title:      {inc.get('title', '?')}",
        f"Device:     {inc.get('primary_hostname') or inc.get('primary_source_ip', '?')} "
        f"({inc.get('primary_source_ip', '')}, {inc.get('os_platform', '')}, {inc.get('device_role', '')})",
        f"Family:     {inc.get('event_family', '?')}",
        f"Corr. key:  {inc.get('correlation_key', '?')}",
    ]

    if ai_sum:
        summary_text = (ai_sum.get("summary") or "")[:400]
        if summary_text:
            lines.append(f"\nAI Summary:\n{summary_text}")

    if ts:
        steps = ts.get("steps") or []
        cli_steps = [s for s in steps if s.get("tool_name") == "run_cli"]
        if cli_steps:
            lines.append("\nCLI Evidence Already Gathered:")
            for i, step in enumerate(cli_steps[:4], 1):
                cmd = (step.get("args") or {}).get("command", "?")
                output = (step.get("content") or "").strip()[:600]
                lines.append(f"  [{i}] {cmd}")
                if output:
                    lines.append(f"      {output[:300]}")

    if logs:
        lines.append("\nRecent Syslog (latest first):")
        for log in logs[:6]:
            lines.append(f"  {log.get('raw_message', '')[:140]}")

    return "\n".join(lines)


@app.post("/api/session/incident/{incident_no}", response_model=CreateSessionResponse)
async def create_incident_session_endpoint(incident_no: str):
    """Create a chat session pre-loaded with incident context and device cache.

    The LLM will know which incident/device it is assisting with from the first
    message, avoiding a lookup_device round-trip and improving response quality.
    """
    _ensure_aiops_ready()
    try:
        detail = _aiops_service.get_incident(incident_no)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    incident_context = _build_incident_context(detail)

    # Pre-populate device_cache with the primary device so the LLM can SSH
    # immediately without calling lookup_device first.
    device_cache_prefill: dict = {}
    inc = detail.get("incident", {})
    hostname = inc.get("primary_hostname") or ""
    ip = inc.get("primary_source_ip") or ""
    if hostname and ip:
        device_cache_prefill[hostname] = {
            "ip_address": ip,
            "os_platform": inc.get("os_platform") or "cisco_ios",
            "device_role": inc.get("device_role") or "",
            "site": inc.get("site") or "",
            "version": inc.get("version") or "",
            "tunnel_ips": [],
        }

    session = await create_session(
        incident_context=incident_context,
        device_cache_prefill=device_cache_prefill,
    )
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
            logger.error("chat stream failed: %s", exc, exc_info=True)
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


@app.get("/api/aiops/health")
async def aiops_health_endpoint():
    return {"status": "ok"}


@app.get("/api/aiops/dashboard")
async def aiops_dashboard_endpoint():
    _ensure_aiops_ready()
    return _aiops_service.dashboard()


@app.get("/api/aiops/incidents")
async def aiops_incidents_endpoint():
    _ensure_aiops_ready()
    return _aiops_service.incidents()


@app.post("/api/aiops/incidents/reset", response_model=AIOpsResetResponse)
async def aiops_reset_incidents_endpoint():
    _ensure_aiops_ready()
    return AIOpsResetResponse(**_aiops_service.reset_incident_data())


@app.get("/api/aiops/incidents/{incident_no}")
async def aiops_incident_detail_endpoint(incident_no: str):
    _ensure_aiops_ready()
    try:
        return _aiops_service.get_incident(incident_no)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/aiops/logs")
async def aiops_logs_endpoint(incident_no: str | None = None, limit: int = 200):
    _ensure_aiops_ready()
    return _aiops_service.logs(incident_no=incident_no, limit=max(1, min(limit, 500)))


@app.get("/api/aiops/approvals")
async def aiops_approvals_endpoint():
    _ensure_aiops_ready()
    return _aiops_service.approvals()


@app.get("/api/aiops/devices")
async def aiops_devices_endpoint():
    _ensure_aiops_ready()
    return _aiops_service.devices()


@app.get("/api/aiops/devices/{hostname}")
async def aiops_device_detail_endpoint(hostname: str):
    _ensure_aiops_ready()
    result = _aiops_service.device_detail(hostname)
    if result is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Device not found"})
    return result


@app.get("/api/aiops/history")
async def aiops_history_endpoint():
    _ensure_aiops_ready()
    return _aiops_service.history()


@app.post("/api/aiops/logs/ingest")
async def aiops_ingest_log_endpoint(req: SyslogIngestRequest):
    _ensure_aiops_ready()
    event_time = None
    if req.event_time:
        event_time = datetime.fromisoformat(req.event_time.replace("Z", "+00:00"))
    result = _aiops_service.enqueue_syslog(
        source_ip=req.source_ip,
        raw_message=req.raw_message,
        hostname=req.hostname,
        event_time=event_time,
    )
    if _is_test_runtime() or os.getenv("AIOPS_INLINE_PIPELINE", "").strip() == "1":
        _aiops_service.process_pending_jobs(10)
    return result


@app.post("/api/aiops/incidents/{incident_no}/troubleshoot")
async def aiops_troubleshoot_endpoint(incident_no: str):
    _ensure_aiops_ready()
    try:
        return _aiops_service.run_troubleshoot(incident_no)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/aiops/incidents/{incident_no}/approve")
async def aiops_approve_endpoint(incident_no: str, req: ProposalActionRequest):
    _ensure_aiops_ready()
    try:
        return _aiops_service.approve_proposal(incident_no, actor=req.actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/aiops/incidents/{incident_no}/execute")
async def aiops_execute_endpoint(incident_no: str, req: ProposalActionRequest):
    _ensure_aiops_ready()
    try:
        return _aiops_service.execute_proposal(incident_no, actor=req.actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/aiops/incidents/{incident_no}/verify")
async def aiops_verify_endpoint(incident_no: str, req: RecoveryDecisionRequest):
    _ensure_aiops_ready()
    try:
        return _aiops_service.verify_recovery(incident_no, healed=req.healed, note=req.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
