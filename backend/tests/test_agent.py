#!/usr/bin/env python3
"""
tests/test_agent.py
===================
Live end-to-end test for the Hypothesis-Driven Troubleshoot Subgraph.

Runs against the real EVE-NG lab via the FastAPI SSE backend.
Requires: backend running on localhost:8000, Ollama reachable, EVE-NG lab up.

Usage:
    python3 -m pytest tests/test_agent.py -v -s --tb=long
    # or directly:
    python3 tests/test_agent.py

The test performs a multi-turn conversation:
  Turn 1: Load all device inventory
  Turn 2: Send troubleshoot query → expect autonomous investigation
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.skip(
    reason=(
        "Legacy live E2E for the retired multi-agent/troubleshoot flow. "
        "The active backend is a single free_run_agent per AGENTS.md."
    )
)

# ── Configuration ────────────────────────────────────────────────────

BASE_URL = "http://localhost:8000"
TIMEOUT = 300  # 5 minutes per request (troubleshoot can be slow)

INVENTORY_PROMPT = "ขอรายชื่ออุปกรณ์ทั้งหมดหน่อย"
TROUBLESHOOT_PROMPT = (
    "Find the root cause of why BRANCH-A-RTR cannot reach 10.255.1.11"
)

# ── Helpers (from e2e_test_harness.py) ────────────────────────────────


def create_session() -> str:
    """Create a new chat session and return session_id."""
    resp = requests.post(f"{BASE_URL}/api/session", timeout=30)
    resp.raise_for_status()
    return resp.json()["session_id"]


def delete_session(session_id: str):
    """Delete a chat session."""
    try:
        requests.delete(f"{BASE_URL}/api/session/{session_id}", timeout=10)
    except Exception:
        pass


def send_message(session_id: str, message: str) -> dict:
    """Send a message and collect all SSE events until 'done'.

    Returns dict with:
        - events: list of all SSE events
        - routing: list of agents routed to
        - tool_results: list of tool result dicts
        - analyst_content: final analyst text
        - error: error message if any
        - raw_text: full raw SSE text
    """
    resp = requests.post(
        f"{BASE_URL}/api/chat",
        json={"session_id": session_id, "message": message},
        stream=True,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()

    events = []
    routing = []
    tool_results = []
    analyst_content = ""
    error = None
    raw_text = ""
    event_type = ""

    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        raw_text += line + "\n"

        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_str = line[len("data:"):].strip()
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                data = {"raw": data_str}

            event = {"event": event_type, "data": data}
            events.append(event)

            if event_type == "routing":
                routing.append(data.get("agent", ""))
            elif event_type == "tool_result":
                tool_results.append(data)
            elif event_type == "analyst_done":
                analyst_content = data.get("full_content", "")
            elif event_type == "error":
                error = data.get("message", str(data))
            elif event_type == "done":
                break

    return {
        "events": events,
        "routing": routing,
        "tool_results": tool_results,
        "analyst_content": analyst_content,
        "error": error,
        "raw_text": raw_text,
    }


# ── Check backend availability ────────────────────────────────────────


def backend_is_running() -> bool:
    """Check if the FastAPI backend is reachable."""
    try:
        resp = requests.get(f"{BASE_URL}/", timeout=5)
        return resp.status_code < 500
    except Exception:
        return False


skip_if_no_backend = pytest.mark.skipif(
    not backend_is_running(),
    reason="Backend not running on localhost:8000",
)


# ═══════════════════════════════════════════════════════════════════════
# Live E2E tests
# ═══════════════════════════════════════════════════════════════════════


@skip_if_no_backend
class TestTroubleshootE2E:
    """Live E2E test for the troubleshoot subgraph.

    Multi-turn test flow:
        Turn 1: Load inventory → expect inventory_agent + analyst_agent
        Turn 2: Troubleshoot → expect troubleshoot_agent, 2+ SSH commands,
                 analyst summary with investigation results
    """

    @pytest.fixture(autouse=True)
    def _setup_session(self):
        """Create a fresh session before each test, clean up after."""
        self.session_id = create_session()
        yield
        delete_session(self.session_id)

    def test_troubleshoot_investigation(self):
        """Full troubleshoot flow: inventory → troubleshoot → analyst."""

        # ── Turn 1: Load all devices into cache ──────────────────────
        print(f"\n{'='*60}")
        print(f"TURN 1: Loading inventory ...")
        print(f"{'='*60}")

        t1 = send_message(self.session_id, INVENTORY_PROMPT)

        print(f"  Routing: {t1['routing']}")
        print(f"  Tool results: {len(t1['tool_results'])}")
        print(f"  Error: {t1['error']}")
        if t1['analyst_content']:
            print(f"  Analyst (first 200c): {t1['analyst_content'][:200]}")

        # Verify: inventory loaded
        assert t1["error"] is None, f"Turn 1 error: {t1['error']}"
        assert "inventory_agent" in t1["routing"], (
            f"Expected inventory_agent in routing, got: {t1['routing']}"
        )

        # ── Turn 2: Troubleshoot investigation ──────────────────────
        print(f"\n{'='*60}")
        print(f"TURN 2: Troubleshoot query: {TROUBLESHOOT_PROMPT}")
        print(f"{'='*60}")

        t2 = send_message(self.session_id, TROUBLESHOOT_PROMPT)

        print(f"  Routing: {t2['routing']}")
        print(f"  Tool results ({len(t2['tool_results'])}):")
        for tr in t2["tool_results"]:
            step = tr.get("step_name", "?")
            tool = tr.get("tool_name", "?")
            is_err = tr.get("is_error", False)
            flag = " [ERROR]" if is_err else ""
            print(f"    - [{tool}] {step}{flag}")

        print(f"  Error: {t2['error']}")
        if t2['analyst_content']:
            preview = t2['analyst_content'][:500]
            print(f"  Analyst summary (first 500c):\n{preview}")

        # ── Assertions ──────────────────────────────────────────────

        # 1. No graph error
        assert t2["error"] is None, f"Turn 2 error: {t2['error']}"

        # 2. Troubleshoot agent was invoked
        assert "troubleshoot_agent" in t2["routing"], (
            f"Expected troubleshoot_agent in routing, got: {t2['routing']}"
        )

        # 3. At least 1 SSH command was executed (investigation ran)
        # Note: the LLM may resolve after 1 command if the initial test
        # succeeds (e.g. ping from Loopback0 works), or may dig deeper
        # across multiple hops. Both behaviors are correct.
        ssh_results = [
            tr for tr in t2["tool_results"]
            if tr.get("tool_name") in ("run_cli", "run_device_cli")
        ]
        assert len(ssh_results) >= 1, (
            f"Expected >= 1 SSH tool results for investigation, "
            f"got {len(ssh_results)}: "
            f"{[tr.get('step_name') for tr in ssh_results]}"
        )

        # 4. Analyst produced a summary
        assert len(t2["analyst_content"]) > 50, (
            f"Expected analyst summary > 50 chars, got "
            f"{len(t2['analyst_content'])}: {t2['analyst_content'][:100]}"
        )

        # 5. Analyst was the final routing step (always ends with analyst)
        assert "analyst_agent" in t2["routing"], (
            f"Expected analyst_agent in routing (final step), got: {t2['routing']}"
        )

        print(f"\n{'='*60}")
        print(f"TEST PASSED")
        print(f"  - Troubleshoot agent invoked: YES")
        print(f"  - SSH commands executed: {len(ssh_results)}")
        print(f"  - Analyst summary length: {len(t2['analyst_content'])} chars")
        print(f"{'='*60}")


@skip_if_no_backend
class TestTroubleshootWithThaiQuery:
    """Thai-language troubleshoot query against live lab."""

    @pytest.fixture(autouse=True)
    def _setup_session(self):
        self.session_id = create_session()
        yield
        delete_session(self.session_id)

    def test_thai_troubleshoot(self):
        """Thai query: หาสาเหตุว่าทำไม BRANCH-A-RTR ไม่สามารถ ping 10.255.1.11 ได้"""

        # Turn 1: Load inventory
        print(f"\n{'='*60}")
        print(f"TURN 1: Loading inventory ...")
        t1 = send_message(self.session_id, INVENTORY_PROMPT)
        assert t1["error"] is None

        # Turn 2: Thai troubleshoot query
        thai_query = "หาสาเหตุว่าทำไม BRANCH-A-RTR ไม่สามารถ ping 10.255.1.11 ได้"
        print(f"TURN 2: {thai_query}")
        t2 = send_message(self.session_id, thai_query)

        print(f"  Routing: {t2['routing']}")
        print(f"  Tool results: {len(t2['tool_results'])}")
        for tr in t2["tool_results"]:
            print(f"    - [{tr.get('tool_name')}] {tr.get('step_name')}")

        # Verify troubleshoot was invoked
        assert t2["error"] is None, f"Error: {t2['error']}"
        assert "troubleshoot_agent" in t2["routing"], (
            f"Expected troubleshoot_agent, got: {t2['routing']}"
        )
        assert len(t2["analyst_content"]) > 50, "Expected analyst summary"

        print(f"\n  TEST PASSED - Thai troubleshoot query handled correctly")


# ═══════════════════════════════════════════════════════════════════════
# CLI runner
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """Quick CLI runner without pytest."""
    if not backend_is_running():
        print("ERROR: Backend not running on localhost:8000")
        print("Start it: cd backend && python3 -m uvicorn src.api:app --port 8000")
        sys.exit(1)

    print("=" * 60)
    print("LIVE E2E TEST: Troubleshoot Subgraph")
    print("=" * 60)

    session_id = create_session()
    print(f"Session: {session_id}")

    try:
        # Turn 1: Inventory
        print(f"\n--- Turn 1: Loading inventory ---")
        t1 = send_message(session_id, INVENTORY_PROMPT)
        print(f"Routing: {t1['routing']}")
        print(f"Tool results: {len(t1['tool_results'])}")
        if t1["error"]:
            print(f"ERROR: {t1['error']}")
            sys.exit(1)

        # Turn 2: Troubleshoot
        print(f"\n--- Turn 2: Troubleshoot ---")
        print(f"Query: {TROUBLESHOOT_PROMPT}")
        t2 = send_message(session_id, TROUBLESHOOT_PROMPT)

        print(f"\nRouting: {t2['routing']}")
        print(f"Tool results ({len(t2['tool_results'])}):")
        for tr in t2["tool_results"]:
            step = tr.get("step_name", "?")
            tool = tr.get("tool_name", "?")
            content = tr.get("content", "")
            print(f"  [{tool}] {step}")
            # Show first 5 lines of output
            lines = content.split("\n")[:5]
            for line in lines:
                print(f"    {line}")
            if len(content.split("\n")) > 5:
                print(f"    ... ({len(content.split(chr(10)))} total lines)")

        if t2["error"]:
            print(f"\nERROR: {t2['error']}")
            sys.exit(1)

        print(f"\nAnalyst Summary:")
        print("-" * 40)
        print(t2["analyst_content"][:1000] if t2["analyst_content"] else "(empty)")
        print("-" * 40)

        # Assertions
        ok = True
        checks = {
            "troubleshoot_agent routed": "troubleshoot_agent" in t2["routing"],
            "1+ SSH commands": len([
                tr for tr in t2["tool_results"]
                if tr.get("tool_name") in ("run_cli", "run_device_cli")
            ]) >= 1,
            "analyst summary exists": len(t2.get("analyst_content", "")) > 50,
        }

        print(f"\n{'='*60}")
        for check_name, passed in checks.items():
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {check_name}")
            if not passed:
                ok = False

        print(f"{'='*60}")
        if ok:
            print("ALL CHECKS PASSED")
        else:
            print("SOME CHECKS FAILED")
            sys.exit(1)

    finally:
        delete_session(session_id)
