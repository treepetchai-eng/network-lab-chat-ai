from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

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


class _CaptureGraph:
    def __init__(self):
        self.inputs = []

    async def astream(self, inputs, *_args, **_kwargs):
        self.inputs.append(inputs)
        yield (("free_run_agent",), "messages", (AIMessage(content="Used recorded evidence."), {"langgraph_node": "free_run_agent"}))

    async def aget_state(self, _config):
        return type("DummyState", (), {"values": {"messages": []}})()


class _SeededSession:
    def __init__(self):
        self.graph = _CaptureGraph()
        self.thread_id = "thread-seeded"
        self.device_cache = {"HQ-CORE-RT01": {"ip_address": "10.255.1.11"}}
        self.preloaded_messages = [
            HumanMessage(content="[System: Existing incident troubleshoot evidence]\nRecorded troubleshoot summary: SSH failed."),
            ToolMessage(
                content="[SSH ERROR] 10.255.1.11 (OS: cisco_ios): timed out",
                tool_call_id="preloaded-1",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "HQ-CORE-RT01", "command": "show ip bgp summary"},
                    "tool_status": "error",
                    "source": "incident_troubleshoot",
                },
            ),
        ]
        self.preloaded_seeded = False
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


def test_stream_chat_seeds_preloaded_messages_only_on_first_turn():
    from src.sse_stream import stream_chat

    async def _collect():
        session = _SeededSession()
        first_events = []
        async for evt in stream_chat(session, "ควรเช็คอะไรต่อดี"):
            first_events.append(evt)
        second_events = []
        async for evt in stream_chat(session, "สรุปอีกที"):
            second_events.append(evt)
        return session, first_events, second_events

    session, first_events, second_events = asyncio.run(_collect())

    assert first_events[0]["event"] == "status"
    assert second_events[0]["event"] == "status"
    assert session.preloaded_seeded is True
    assert len(session.graph.inputs) == 2
    assert len(session.graph.inputs[0]["messages"]) == 3
    assert isinstance(session.graph.inputs[0]["messages"][0], HumanMessage)
    assert isinstance(session.graph.inputs[0]["messages"][1], ToolMessage)
    assert session.graph.inputs[0]["messages"][-1].content == "ควรเช็คอะไรต่อดี"
    assert len(session.graph.inputs[1]["messages"]) == 1
    assert session.graph.inputs[1]["messages"][0].content == "สรุปอีกที"
