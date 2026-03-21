from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


class _BrokenGraph:
    async def astream(self, *_args, **_kwargs):
        if False:
            yield None
        raise RuntimeError("quota exceeded")

    async def aget_state(self, _config):
        raise AssertionError("aget_state should not be reached after graph error")


class _Session:
    def __init__(self):
        self.graph = _BrokenGraph()
        self.thread_id = "thread-1"
        self.device_cache = {}
        self.progress_sink = {}


def test_stream_chat_surfaces_graph_error_and_stops():
    from src.sse_stream import stream_chat

    async def _collect():
        session = _Session()
        events = []
        async for evt in stream_chat(session, "hello"):
            events.append(evt)
        return events

    events = asyncio.run(_collect())

    assert events[0]["event"] == "status"
    assert events[1] == {
        "event": "error",
        "data": {"message": "quota exceeded", "type": "graph_error"},
    }
    assert events[2] == {
        "event": "status",
        "data": {"text": "Summarizing answer...", "tool_name": None, "args": None},
    }
    assert events[3]["event"] == "analyst_token"
    assert events[-2]["event"] == "analyst_done"
    assert events[-1] == {"event": "done", "data": {}}
