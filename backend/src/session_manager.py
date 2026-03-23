"""In-memory session store for the backend chat API."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.graph import build_graph

# ---------------------------------------------------------------------------
# Session data
# ---------------------------------------------------------------------------

SESSION_TIMEOUT_SECONDS = 3600  # 1 hour


@dataclass
class SessionData:
    session_id: str
    thread_id: str
    device_cache: dict
    graph: object  # CompiledStateGraph
    incident_context: str = ""  # Non-empty for incident-scoped chat sessions
    progress_sink: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        """Update last_active timestamp."""
        self.last_active = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------

_sessions: dict[str, SessionData] = {}
_lock = asyncio.Lock()


async def create_session(
    *,
    incident_context: str = "",
    device_cache_prefill: dict | None = None,
) -> SessionData:
    """Create a new session with a fresh graph and empty grounded cache.

    Args:
        incident_context: Pre-formatted incident context string injected into
            the LLM system prompt for incident-scoped chat sessions.
        device_cache_prefill: Optional device entries to pre-populate the cache
            so the LLM doesn't need to call lookup_device for known devices.
    """
    session_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    device_cache: dict = dict(device_cache_prefill or {})
    progress_sink: dict = {}
    graph = build_graph(device_cache, progress_sink)

    session = SessionData(
        session_id=session_id,
        thread_id=thread_id,
        device_cache=device_cache,
        graph=graph,
        incident_context=incident_context,
        progress_sink=progress_sink,
    )

    async with _lock:
        _sessions[session_id] = session

    return session


async def get_session(session_id: str) -> SessionData | None:
    """Look up a session by ID.  Returns ``None`` if not found."""
    async with _lock:
        session = _sessions.get(session_id)
    if session is not None:
        session.touch()
    return session


async def delete_session(session_id: str) -> bool:
    """Remove a session.  Returns ``True`` if it existed."""
    async with _lock:
        return _sessions.pop(session_id, None) is not None


async def cleanup_stale() -> int:
    """Remove sessions that have been idle longer than *SESSION_TIMEOUT_SECONDS*.

    Returns the number of sessions removed.
    """
    now = datetime.now(timezone.utc)
    to_remove: list[str] = []

    async with _lock:
        for sid, sess in _sessions.items():
            age = (now - sess.last_active).total_seconds()
            if age > SESSION_TIMEOUT_SECONDS:
                to_remove.append(sid)
        for sid in to_remove:
            del _sessions[sid]

    return len(to_remove)


def session_count() -> int:
    """Return the current number of active sessions."""
    return len(_sessions)
