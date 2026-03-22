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
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from src.session_manager import cleanup_stale, create_session, delete_session, get_session, session_count
from src.sse_stream import stream_chat
from src.tools.inventory_tools import list_all_devices

load_dotenv()

logger = logging.getLogger(__name__)

_cleanup_task: asyncio.Task | None = None


async def _periodic_cleanup() -> None:
    """Remove stale chat sessions every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        await cleanup_stale()


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
    global _cleanup_task
    _cleanup_task = asyncio.create_task(_periodic_cleanup())
    yield
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        _cleanup_task = None


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
