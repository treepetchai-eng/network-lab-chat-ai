"""In-memory store for interactive troubleshoot sessions.

Each session wraps a reusable LangGraph graph session so that the LLM
retains context across planning and multiple execution rounds.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.session_manager import create_session, delete_session, get_session

TS_TIMEOUT_SECONDS = 1800  # 30 minutes

_sse_helper_imported = False


@dataclass
class TroubleshootRound:
    round_number: int
    plan_text: str = ""
    analysis_text: str = ""
    approval_id: int | None = None
    artifact_id: int | None = None


@dataclass
class TroubleshootSession:
    ts_id: str
    incident_id: int
    graph_session_id: str
    round_number: int = 0
    plan_text: str = ""
    rounds: list[TroubleshootRound] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.last_active = datetime.now(timezone.utc)


_store: dict[str, TroubleshootSession] = {}
_lock = asyncio.Lock()


async def create_ts_session(incident_id: int) -> TroubleshootSession:
    """Create a troubleshoot session backed by a fresh LangGraph graph session."""
    graph_session = await create_session()
    ts = TroubleshootSession(
        ts_id=str(uuid.uuid4()),
        incident_id=incident_id,
        graph_session_id=graph_session.session_id,
    )
    async with _lock:
        _store[ts.ts_id] = ts
    return ts


async def get_ts_session(ts_id: str) -> TroubleshootSession | None:
    async with _lock:
        ts = _store.get(ts_id)
    if ts is not None:
        ts.touch()
    return ts


async def delete_ts_session(ts_id: str) -> bool:
    async with _lock:
        ts = _store.pop(ts_id, None)
    if ts is None:
        return False
    await delete_session(ts.graph_session_id)
    return True


async def cleanup_stale_ts_sessions() -> int:
    now = datetime.now(timezone.utc)
    to_remove: list[TroubleshootSession] = []
    async with _lock:
        for tid, ts in list(_store.items()):
            if (now - ts.last_active).total_seconds() > TS_TIMEOUT_SECONDS:
                to_remove.append(ts)
                del _store[tid]
    for ts in to_remove:
        # best-effort graph session cleanup (already removed from _store)
        try:
            await delete_session(ts.graph_session_id)
        except Exception:
            pass
    return len(to_remove)
