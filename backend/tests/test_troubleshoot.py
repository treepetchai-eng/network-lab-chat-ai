"""
tests/test_troubleshoot.py
===========================
Unit tests for the Hypothesis-Driven Troubleshoot Subgraph.

Tests cover:
  - AgentState schema (new troubleshoot fields)
  - Troubleshoot subgraph builds
  - Conditional edge logic (_check_resolution)
  - execute_cli_structured return format
  - Parent graph includes troubleshoot_agent node
  - Supervisor routes troubleshoot queries correctly
  - ReAct node output (plan-only on first iteration)
  - Executor node output
  - ReAct node output (analyze + plan on subsequent iterations)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

pytestmark = pytest.mark.skip(
    reason=(
        "Legacy troubleshoot-subgraph tests. Current backend keeps the "
        "free-run single-agent design per AGENTS.md."
    )
)

# Bootstrap path so `from src.xxx` works
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(dotenv_path=_ROOT / ".env")


# ═══════════════════════════════════════════════════════════════════════
# Test 1 — AgentState has all troubleshoot fields
# ═══════════════════════════════════════════════════════════════════════


class TestTroubleshootState:
    """Verify AgentState includes the new troubleshoot fields."""

    def test_state_has_hypothesis_field(self):
        from src.graph.state import AgentState

        annotations = AgentState.__annotations__
        assert "hypothesis" in annotations

    def test_state_has_command_history_field(self):
        from src.graph.state import AgentState

        annotations = AgentState.__annotations__
        assert "command_history" in annotations

    def test_state_has_discovered_topology_field(self):
        from src.graph.state import AgentState

        annotations = AgentState.__annotations__
        assert "discovered_topology" in annotations

    def test_state_has_is_resolved_field(self):
        from src.graph.state import AgentState

        annotations = AgentState.__annotations__
        assert "is_resolved" in annotations

    def test_state_has_iteration_count_field(self):
        from src.graph.state import AgentState

        annotations = AgentState.__annotations__
        assert "iteration_count" in annotations

    def test_original_fields_still_exist(self):
        from src.graph.state import AgentState

        annotations = AgentState.__annotations__
        assert "messages" in annotations
        assert "next_agent" in annotations
        assert "device_cache" in annotations


# ═══════════════════════════════════════════════════════════════════════
# Test 2 — Troubleshoot subgraph builds successfully
# ═══════════════════════════════════════════════════════════════════════


class TestTroubleshootSubgraphBuilds:
    """Verify the subgraph compiles without error."""

    def test_subgraph_compiles_with_mock_llm(self):
        from src.graph.troubleshoot import build_troubleshoot_subgraph

        mock_llm = MagicMock()
        subgraph = build_troubleshoot_subgraph(mock_llm)
        assert subgraph is not None

    def test_subgraph_has_expected_nodes(self):
        from src.graph.troubleshoot import build_troubleshoot_subgraph

        mock_llm = MagicMock()
        subgraph = build_troubleshoot_subgraph(mock_llm)
        node_names = set(subgraph.get_graph().nodes.keys())
        # LangGraph adds __start__ and __end__ nodes automatically
        assert "react_node" in node_names
        assert "executor" in node_names


# ═══════════════════════════════════════════════════════════════════════
# Test 3 — Conditional edge logic (_check_resolution)
# ═══════════════════════════════════════════════════════════════════════


class TestCheckResolution:
    """Verify _check_resolution routes correctly."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from src.graph.troubleshoot.builder import (
            MAX_TROUBLESHOOT_ITERATIONS,
            _check_resolution,
        )

        self.check = _check_resolution
        self.max_iterations = MAX_TROUBLESHOOT_ITERATIONS

    def test_execute_when_not_resolved(self):
        state = {"is_resolved": False, "iteration_count": 1, "command_history": ["cmd1"]}
        assert self.check(state) == "execute"

    def test_end_when_resolved(self):
        state = {"is_resolved": True, "iteration_count": 1, "command_history": ["cmd1"]}
        assert self.check(state) == "end"

    def test_end_when_max_iterations(self):
        state = {
            "is_resolved": False,
            "iteration_count": self.max_iterations,
            "command_history": ["cmd1"],
        }
        assert self.check(state) == "end"

    def test_end_when_duplicate_commands(self):
        state = {
            "is_resolved": False,
            "iteration_count": 2,
            "command_history": ["cmd1", "cmd1"],
        }
        assert self.check(state) == "end"

    def test_execute_when_empty_state(self):
        state = {}
        assert self.check(state) == "execute"

    def test_end_at_iteration_boundary(self):
        state = {
            "is_resolved": False,
            "iteration_count": self.max_iterations,
            "command_history": [],
        }
        assert self.check(state) == "end"

    def test_execute_at_iteration_9(self):
        state = {
            "is_resolved": False,
            "iteration_count": self.max_iterations - 1,
            "command_history": ["cmd1"],
        }
        assert self.check(state) == "execute"


# ═══════════════════════════════════════════════════════════════════════
# Test 4 — execute_cli_structured returns correct dict format
# ═══════════════════════════════════════════════════════════════════════


class TestExecuteCliStructured:
    """Verify execute_cli_structured returns dict with required keys."""

    def test_missing_credentials_returns_error_dict(self):
        with patch.dict(os.environ, {"ROUTER_USER": "", "ROUTER_PASS": ""}):
            from importlib import reload
            from src.tools import ssh_executor

            reload(ssh_executor)
            result = ssh_executor.execute_cli_structured(
                "10.255.1.11", "cisco_ios", "show version"
            )
        assert isinstance(result, dict)
        assert "hostname" in result
        assert "ip" in result
        assert "os" in result
        assert "command" in result
        assert "parsed" in result
        assert "data" in result
        assert result["parsed"] is False
        assert "CONFIG ERROR" in result["data"]

    def test_blocked_command_returns_error_dict(self):
        """Commands that violate safety rules should be blocked."""
        from src.tools.ssh_executor import execute_cli_structured

        result = execute_cli_structured(
            "10.255.1.11", "cisco_ios", "configure terminal"
        )
        assert isinstance(result, dict)
        assert result["parsed"] is False
        assert "BLOCKED" in result["data"]


class TestCommandNormalization:
    """Role-aware command repair and fallback should be deterministic."""

    def test_repair_ios_show_bgp_summary(self):
        from src.tools.command_profiles import normalize_command

        resolution = normalize_command(
            proposed_command="show bgp summary",
            user_query="check bgp on HQ-CORE-RT01",
            device_role="core_router",
            os_platform="cisco_ios",
        )
        assert resolution.command == "show ip bgp summary"
        assert resolution.repaired is True

    def test_switch_only_command_falls_back_on_router_role(self):
        from src.tools.command_profiles import normalize_command

        resolution = normalize_command(
            proposed_command="show vlan brief",
            user_query="check vlan on HQ-DIST-GW01",
            device_role="dist_switch",
            os_platform="cisco_ios",
        )
        assert resolution.command == "show vlan brief"
        assert resolution.fallback_used is False

    def test_router_only_command_falls_back_on_access_switch(self):
        from src.tools.command_profiles import normalize_command

        resolution = normalize_command(
            proposed_command="show ip route",
            user_query="check interfaces on BRANCH-A-Switch",
            device_role="access_switch",
            os_platform="cisco_ios",
        )
        assert resolution.command == "show interfaces status"
        assert resolution.fallback_used is True

    def test_traceroute_candidates_require_target(self):
        from src.tools.command_profiles import resolve_command_candidates

        commands = resolve_command_candidates(
            {"device_role": "router", "os_platform": "cisco_ios", "version": "15.6(2)T"},
            "traceroute_test",
            {},
        )
        assert commands == []


    def test_access_switch_route_lookup_prefers_default_gateway(self):
        from src.tools.command_profiles import resolve_command_candidates

        commands = resolve_command_candidates(
            {"device_role": "access_switch", "os_platform": "cisco_ios", "version": "15.2(4.0.55)E"},
            "route_lookup",
            {},
        )
        assert commands[0].command == "show ip default-gateway"

    def test_ping_source_removed_when_user_did_not_request_it(self):
        from src.tools.command_profiles import normalize_command

        resolution = normalize_command(
            proposed_command="ping 10.255.1.11 source GigabitEthernet0/0",
            user_query="find why BRANCH-A-RTR cannot reach 10.255.1.11",
            device_role="router",
            os_platform="cisco_ios",
        )
        assert resolution.command == "ping 10.255.1.11"


# ═══════════════════════════════════════════════════════════════════════
# Test 5 — Parent graph has troubleshoot_agent node
# ═══════════════════════════════════════════════════════════════════════


class TestParentGraphWithTroubleshoot:
    """Verify the parent graph includes the troubleshoot_agent node."""

    def test_graph_has_troubleshoot_agent_node(self):
        from src.graph.builder import build_graph

        graph = build_graph()
        node_names = set(graph.get_graph().nodes.keys())
        assert "troubleshoot_agent" in node_names

    def test_graph_still_has_original_nodes(self):
        from src.graph.builder import build_graph

        graph = build_graph()
        node_names = set(graph.get_graph().nodes.keys())
        assert "supervisor" in node_names
        assert "inventory_agent" in node_names
        assert "ssh_agent" in node_names
        assert "analyst_agent" in node_names

    def test_troubleshoot_agent_has_edges(self):
        """Troubleshoot agent should have an edge back to supervisor."""
        from src.graph.builder import build_graph

        graph = build_graph()
        graph_data = graph.get_graph()
        # Check edges: troubleshoot_agent should have at least one outgoing edge
        edges = graph_data.edges
        troubleshoot_edges = [
            e for e in edges if e.source == "troubleshoot_agent"
        ]
        assert len(troubleshoot_edges) > 0


class TestTroubleshootRecovery:
    """Troubleshoot planning should recover from malformed LLM output."""

    def test_react_node_uses_fallback_plan_on_bad_json(self):
        from src.graph.troubleshoot.react_node import react_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="not-json")
        state = {
            "messages": [HumanMessage(content="check bgp on HQ-CORE-RT01")],
            "device_cache": {
                "HQ-CORE-RT01": {
                    "ip_address": "10.255.1.11",
                    "os_platform": "cisco_ios",
                    "device_role": "core_router",
                    "site": "HQ",
                }
            },
            "hypothesis": "",
            "command_history": [],
            "discovered_topology": {},
            "iteration_count": 0,
        }

        result = react_node(state, mock_llm)
        assert result["is_resolved"] is False
        plan_msgs = [
            m for m in result["messages"]
            if isinstance(m, AIMessage) and "_troubleshoot_plan" in (m.content or "")
        ]
        assert plan_msgs, "Expected fallback troubleshoot plan"
        plan = json.loads(plan_msgs[0].content)["_troubleshoot_plan"]
        assert plan["device"] == "HQ-CORE-RT01"
        assert plan["command"] == "show ip bgp summary"

    @patch("src.graph.troubleshoot.executor.execute_cli_structured")
    def test_executor_repairs_invalid_router_command(self, mock_execute):
        from src.graph.troubleshoot.executor import executor_node

        mock_execute.return_value = {
            "hostname": "HQ-CORE-RT01",
            "ip": "10.255.1.11",
            "os": "cisco_ios",
            "command": "show ip bgp summary",
            "parsed": False,
            "data": "BGP summary output",
        }
        plan = AIMessage(content=json.dumps({
            "_troubleshoot_plan": {
                "device": "HQ-CORE-RT01",
                "command": "show bgp summary",
                "reasoning": "check bgp",
            }
        }))
        state = {
            "messages": [plan],
            "device_cache": {
                "HQ-CORE-RT01": {
                    "ip_address": "10.255.1.11",
                    "os_platform": "cisco_ios",
                    "device_role": "core_router",
                }
            },
            "command_history": [],
            "iteration_count": 0,
        }

        result = executor_node(state)
        mock_execute.assert_called_once_with(
            "10.255.1.11", "cisco_ios", "show ip bgp summary"
        )
        tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert tool_messages
        assert "[COMMAND REPAIR]" in tool_messages[0].content


# ═══════════════════════════════════════════════════════════════════════
# Test 6 — Supervisor routes troubleshoot queries correctly
# ═══════════════════════════════════════════════════════════════════════


class TestSupervisorRoutesTroubleshoot:
    """Verify supervisor_node routes troubleshoot queries correctly."""

    def _make_state(self, query="test", cache=None, messages=None):
        return {
            "messages": messages or [HumanMessage(content=query)],
            "device_cache": cache or {},
            "next_agent": "",
        }

    def _sample_cache(self):
        return {
            "BRANCH-A-RTR": {
                "ip_address": "10.255.10.1",
                "os_platform": "cisco_ios",
                "device_role": "router",
                "site": "Branch-A",
            },
            "HQ-CORE-RT01": {
                "ip_address": "10.255.1.11",
                "os_platform": "cisco_ios",
                "device_role": "core_router",
                "site": "HQ",
            },
        }

    def test_troubleshoot_query_with_cache_routes_to_troubleshoot(self):
        """Troubleshoot query with device cache should go to troubleshoot_agent."""
        from src.graph.supervisor import supervisor_node

        mock_llm = MagicMock()
        # Use only one device reference to avoid triggering multi-device path
        state = self._make_state(
            query="หาสาเหตุว่าทำไม BRANCH-A-RTR ถึง timeout",
            cache=self._sample_cache(),
        )
        result = supervisor_node(state, mock_llm)
        assert result["next_agent"] == "troubleshoot_agent"

    def test_troubleshoot_query_thai_with_cache(self):
        """Thai troubleshoot query without a ping target should ask for clarification."""
        from src.graph.supervisor import supervisor_node

        mock_llm = MagicMock()
        state = self._make_state(
            query="หาสาเหตุว่าทำไม BRANCH-A-RTR ไม่สามารถ ping ได้",
            cache=self._sample_cache(),
        )
        result = supervisor_node(state, mock_llm)
        assert result["next_agent"] == "analyst_agent"

    def test_troubleshoot_query_without_cache_routes_to_inventory(self):
        """Troubleshoot query without device cache should get inventory first."""
        from src.graph.supervisor import supervisor_node

        mock_llm = MagicMock()
        state = self._make_state(
            query="troubleshoot why BRANCH-A-RTR cannot reach 10.255.1.11",
            cache={},  # empty cache
        )
        result = supervisor_node(state, mock_llm)
        assert result["next_agent"] == "inventory_agent"

    def test_root_cause_query_routes_to_troubleshoot(self):
        """Root cause queries should go to troubleshoot_agent."""
        from src.graph.supervisor import supervisor_node

        mock_llm = MagicMock()
        state = self._make_state(
            query="find root cause of IP SLA timeout on BRANCH-A-RTR",
            cache=self._sample_cache(),
        )
        result = supervisor_node(state, mock_llm)
        assert result["next_agent"] == "troubleshoot_agent"

    def test_why_down_query_routes_to_troubleshoot(self):
        """'Why is X down' queries should go to troubleshoot_agent."""
        from src.graph.supervisor import supervisor_node

        mock_llm = MagicMock()
        state = self._make_state(
            query="why is BGP session down on BRANCH-A-RTR",
            cache=self._sample_cache(),
        )
        result = supervisor_node(state, mock_llm)
        assert result["next_agent"] == "troubleshoot_agent"

    def test_simple_show_query_routes_to_ssh_not_troubleshoot(self):
        """Simple CLI queries should still go to ssh_agent, not troubleshoot."""
        from src.graph.supervisor import supervisor_node

        mock_llm = MagicMock()
        state = self._make_state(
            query="show ip route on HQ-CORE-RT01",
            cache=self._sample_cache(),
        )
        result = supervisor_node(state, mock_llm)
        assert result["next_agent"] == "ssh_agent"

    def test_llm_slow_path_troubleshoot_parsing(self):
        """LLM returning 'troubleshoot_agent' should be parsed correctly."""
        from src.graph.supervisor import supervisor_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="troubleshoot_agent")
        # Use a vague query that bypasses ALL heuristic regex patterns.
        # Must not contain CLI keywords, inventory keywords, troubleshoot
        # keywords, general question keywords, knowledge keywords, or
        # hostname/IP patterns.
        state = self._make_state(
            query="I need you to figure out what went wrong",
            cache=self._sample_cache(),
        )
        result = supervisor_node(state, mock_llm)
        assert result["next_agent"] == "troubleshoot_agent"


# ═══════════════════════════════════════════════════════════════════════
# Test 7 — ReAct node output format (plan-only on first iteration)
# ═══════════════════════════════════════════════════════════════════════


class TestReactNodePlanOnly:
    """Verify react_node output format on first iteration (plan only)."""

    def test_react_node_produces_plan_message(self):
        """First iteration: react_node should plan the first command."""
        from src.graph.troubleshoot.react_node import react_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content=json.dumps({
            "analysis": None,
            "hypothesis": "Check routing table on BRANCH-A-RTR",
            "is_resolved": False,
            "next_command": {
                "device": "BRANCH-A-RTR",
                "command": "show ip route",
                "reasoning": "Check routing table",
            },
        }))

        state = {
            "messages": [HumanMessage(content="troubleshoot why BRANCH-A-RTR cannot reach 10.255.1.11")],
            "device_cache": {"BRANCH-A-RTR": {
                "ip_address": "10.255.10.1",
                "os_platform": "cisco_ios",
                "device_role": "router",
                "site": "Branch-A",
            }},
            "hypothesis": "",
            "command_history": [],
            "discovered_topology": {},
            "iteration_count": 0,
        }

        result = react_node(state, mock_llm)
        assert "messages" in result
        assert len(result["messages"]) > 0
        # The plan should be a JSON-encoded AIMessage with _troubleshoot_plan
        msg = result["messages"][0]
        assert isinstance(msg, AIMessage)
        plan = json.loads(msg.content)
        assert "_troubleshoot_plan" in plan
        assert plan["_troubleshoot_plan"]["device"] == "BRANCH-A-RTR"
        assert plan["_troubleshoot_plan"]["command"].startswith("ping 10.255.1.11")

    def test_react_node_stop_when_resolved(self):
        """When LLM says is_resolved=true, react_node should stop."""
        from src.graph.troubleshoot.react_node import react_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content=json.dumps({
            "analysis": None,
            "hypothesis": "Root cause found",
            "is_resolved": True,
            "next_command": None,
        }))

        state = {
            "messages": [HumanMessage(content="troubleshoot BRANCH-A-RTR")],
            "device_cache": {},
            "hypothesis": "Testing",
            "command_history": [],
            "discovered_topology": {},
            "iteration_count": 3,
        }

        result = react_node(state, mock_llm)
        assert result.get("is_resolved") is True

    def test_react_node_json_parse_failure_resolves(self):
        """If LLM returns unparsable JSON, react_node should stop gracefully."""
        from src.graph.troubleshoot.react_node import react_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="not valid json at all")

        state = {
            "messages": [HumanMessage(content="troubleshoot something")],
            "device_cache": {},
            "hypothesis": "",
            "command_history": [],
            "discovered_topology": {},
            "iteration_count": 0,
        }

        result = react_node(state, mock_llm)
        assert result.get("is_resolved") is True


# ═══════════════════════════════════════════════════════════════════════
# Test 8 — Executor node output format
# ═══════════════════════════════════════════════════════════════════════


class TestExecutorNode:
    """Verify executor_node output format."""

    def test_no_plan_found_resolves(self):
        """If no plan is in messages, executor should stop."""
        from src.graph.troubleshoot.executor import executor_node

        state = {
            "messages": [HumanMessage(content="test")],
            "device_cache": {},
            "command_history": [],
            "iteration_count": 0,
        }

        result = executor_node(state)
        assert result.get("is_resolved") is True

    def test_duplicate_command_resolves(self):
        """Duplicate command should trigger is_resolved."""
        from src.graph.troubleshoot.executor import executor_node

        plan_msg = AIMessage(content=json.dumps({
            "_troubleshoot_plan": {
                "device": "BRANCH-A-RTR",
                "command": "show ip route",
                "reasoning": "test",
            }
        }))

        state = {
            "messages": [HumanMessage(content="test"), plan_msg],
            "device_cache": {"BRANCH-A-RTR": {
                "ip_address": "10.255.10.1",
                "os_platform": "cisco_ios",
                "device_role": "router",
                "site": "Branch-A",
            }},
            "command_history": ["BRANCH-A-RTR: show ip route"],  # already ran
            "iteration_count": 1,
        }

        result = executor_node(state)
        assert result.get("is_resolved") is True

    def test_device_not_in_cache_returns_error_message(self):
        """If device is not in cache, executor should return error message."""
        from src.graph.troubleshoot.executor import executor_node

        plan_msg = AIMessage(content=json.dumps({
            "_troubleshoot_plan": {
                "device": "UNKNOWN-DEVICE",
                "command": "show ip route",
                "reasoning": "test",
            }
        }))

        state = {
            "messages": [HumanMessage(content="test"), plan_msg],
            "device_cache": {},
            "command_history": [],
            "iteration_count": 0,
        }

        result = executor_node(state)
        assert "messages" in result
        assert "not found" in result["messages"][0].content.lower()

    def test_executor_increments_iteration_count(self):
        """Executor should always increment iteration_count."""
        from src.graph.troubleshoot.executor import executor_node

        state = {
            "messages": [HumanMessage(content="test")],
            "device_cache": {},
            "command_history": [],
            "iteration_count": 5,
        }

        result = executor_node(state)
        assert result.get("iteration_count") == 6

    def test_executor_case_insensitive_device_lookup(self):
        """Device lookup should be case-insensitive."""
        from src.graph.troubleshoot.executor import executor_node

        plan_msg = AIMessage(content=json.dumps({
            "_troubleshoot_plan": {
                "device": "branch-a-rtr",  # lowercase
                "command": "show version",
                "reasoning": "test",
            }
        }))

        state = {
            "messages": [HumanMessage(content="test"), plan_msg],
            "device_cache": {"BRANCH-A-RTR": {
                "ip_address": "10.255.10.1",
                "os_platform": "cisco_ios",
                "device_role": "router",
                "site": "Branch-A",
            }},
            "command_history": [],
            "iteration_count": 0,
        }

        # This will try to SSH, which will fail in unit tests,
        # but the point is it should NOT return "device not found"
        result = executor_node(state)
        # If it found the device, it would attempt SSH (and fail with error)
        # It should NOT have the "not found" error
        if "messages" in result and result["messages"]:
            for msg in result["messages"]:
                if isinstance(msg, AIMessage) and msg.content:
                    assert "not found" not in msg.content.lower()


# ═══════════════════════════════════════════════════════════════════════
# Test 9 — ReAct node output format (analyze + plan on subsequent iterations)
# ═══════════════════════════════════════════════════════════════════════


class TestReactNodeAnalyzeAndPlan:
    """Verify react_node output format when analyzing output and planning next."""

    def test_react_node_updates_hypothesis(self):
        """React node should update hypothesis from LLM response."""
        from src.graph.troubleshoot.react_node import react_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content=json.dumps({
            "analysis": {
                "summary": "BRANCH-A-RTR has no route to 10.255.1.11",
                "new_findings": {"route_check": "no route to 10.255.1.11"},
                "root_cause": None,
            },
            "hypothesis": "Route to 10.255.1.11 is missing on BRANCH-A-RTR",
            "is_resolved": False,
            "next_command": {
                "device": "HQ-CORE-RT01",
                "command": "show ip route",
                "reasoning": "Check upstream routing",
            },
        }))

        plan_msg = AIMessage(content=json.dumps({
            "_troubleshoot_plan": {
                "device": "BRANCH-A-RTR",
                "command": "show ip route 10.255.1.11",
                "reasoning": "Check if route exists",
            }
        }))
        tool_msg = ToolMessage(
            content="[Device: BRANCH-A-RTR | IP: 10.255.10.1 | OS: cisco_ios]\n% Network not in table",
            tool_call_id="test-id",
            name="run_cli",
        )

        state = {
            "messages": [
                HumanMessage(content="troubleshoot BRANCH-A-RTR"),
                plan_msg,
                tool_msg,
            ],
            "hypothesis": "Initial investigation",
            "command_history": ["BRANCH-A-RTR: show ip route 10.255.1.11"],
            "discovered_topology": {},
            "iteration_count": 1,
        }

        result = react_node(state, mock_llm)
        assert result["hypothesis"] == "Route to 10.255.1.11 is missing on BRANCH-A-RTR"
        assert result["is_resolved"] is False
        assert "route_check" in result["discovered_topology"]

    def test_react_node_resolves_with_root_cause(self):
        """React node should set is_resolved=True when root cause found."""
        from src.graph.troubleshoot.react_node import react_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content=json.dumps({
            "analysis": {
                "summary": "Found root cause: missing static route",
                "new_findings": {},
                "root_cause": "Missing static route on HQ-DIST-GW01",
            },
            "hypothesis": "Root cause confirmed",
            "is_resolved": True,
            "next_command": None,
        }))

        plan_msg = AIMessage(content=json.dumps({
            "_troubleshoot_plan": {
                "device": "HQ-DIST-GW01",
                "command": "show ip route",
                "reasoning": "Check route table",
            }
        }))
        tool_msg = ToolMessage(
            content="some output",
            tool_call_id="test-id",
            name="run_cli",
        )

        state = {
            "messages": [
                HumanMessage(content="troubleshoot BRANCH-A-RTR"),
                plan_msg,
                tool_msg,
            ],
            "hypothesis": "Route might be missing",
            "command_history": ["test: show ip route"],
            "discovered_topology": {"existing": "data"},
            "iteration_count": 2,
        }

        result = react_node(state, mock_llm)
        assert result["is_resolved"] is True
        assert "messages" in result
        # Status message should mention root cause
        has_root_cause_msg = any(
            "Root Cause" in (m.content or "") for m in result["messages"]
        )
        assert has_root_cause_msg

    def test_react_node_merges_findings(self):
        """New findings should merge into discovered_topology."""
        from src.graph.troubleshoot.react_node import react_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content=json.dumps({
            "analysis": {
                "summary": "Found new info",
                "new_findings": {"new_key": "new_value"},
                "root_cause": None,
            },
            "hypothesis": "Continue",
            "is_resolved": False,
            "next_command": {
                "device": "BRANCH-A-RTR",
                "command": "show ip interface brief",
                "reasoning": "Check interfaces",
            },
        }))

        plan_msg = AIMessage(content=json.dumps({
            "_troubleshoot_plan": {
                "device": "BRANCH-A-RTR",
                "command": "show ip route",
                "reasoning": "test",
            }
        }))
        tool_msg = ToolMessage(
            content="output",
            tool_call_id="test-id",
            name="run_cli",
        )

        state = {
            "messages": [
                HumanMessage(content="troubleshoot"),
                plan_msg,
                tool_msg,
            ],
            "hypothesis": "test",
            "command_history": [],
            "discovered_topology": {"existing_key": "existing_value"},
            "iteration_count": 1,
        }

        result = react_node(state, mock_llm)
        topo = result["discovered_topology"]
        assert "existing_key" in topo
        assert "new_key" in topo

    def test_react_node_json_parse_failure_recovers(self):
        """If react_node can't parse JSON, it should stop gracefully."""
        from src.graph.troubleshoot.react_node import react_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="not valid json")

        plan_msg = AIMessage(content=json.dumps({
            "_troubleshoot_plan": {
                "device": "BRANCH-A-RTR",
                "command": "show ip route",
                "reasoning": "test",
            }
        }))
        tool_msg = ToolMessage(
            content="output",
            tool_call_id="test-id",
            name="run_cli",
        )

        state = {
            "messages": [
                HumanMessage(content="troubleshoot"),
                plan_msg,
                tool_msg,
            ],
            "hypothesis": "test",
            "command_history": [],
            "discovered_topology": {},
            "iteration_count": 1,
        }

        result = react_node(state, mock_llm)
        assert result.get("is_resolved") is True
        assert "messages" in result


# ═══════════════════════════════════════════════════════════════════════
# Test 10 — Prompt content checks
# ═══════════════════════════════════════════════════════════════════════


class TestTroubleshootPrompts:
    """Verify troubleshoot prompts contain expected content."""

    def test_planner_prompt_has_required_sections(self):
        from src.prompts.troubleshoot import PLANNER_PROMPT

        assert "YOUR JOB" in PLANNER_PROMPT
        assert "CISCO IOS COMMAND REFERENCE" in PLANNER_PROMPT
        assert "AVAILABLE DEVICES" in PLANNER_PROMPT
        assert "NEVER repeat" in PLANNER_PROMPT
        assert "show ip" in PLANNER_PROMPT
        assert "ip_sla_status" in PLANNER_PROMPT
        assert "routing_protocols" in PLANNER_PROMPT
        assert "track_status" in PLANNER_PROMPT
        assert "show ip sla summary" in PLANNER_PROMPT
        assert "show ip protocols" in PLANNER_PROMPT
        assert "show track" in PLANNER_PROMPT
        assert "{user_query}" in PLANNER_PROMPT
        assert "{hypothesis}" in PLANNER_PROMPT
        assert "{command_history}" in PLANNER_PROMPT

    def test_analyzer_prompt_has_required_sections(self):
        from src.prompts.troubleshoot import ANALYZER_PROMPT, _LATEST_OUTPUT_TEMPLATE

        assert "YOUR JOB" in ANALYZER_PROMPT
        assert "is_resolved" in ANALYZER_PROMPT
        assert "hypothesis" in ANALYZER_PROMPT
        assert "new_findings" in ANALYZER_PROMPT
        assert "root_cause" in ANALYZER_PROMPT
        assert "{user_query}" in ANALYZER_PROMPT
        # device/command/output placeholders are in the output template
        assert "{device}" in _LATEST_OUTPUT_TEMPLATE
        assert "{command}" in _LATEST_OUTPUT_TEMPLATE
        assert "{output}" in _LATEST_OUTPUT_TEMPLATE

    def test_supervisor_prompt_includes_troubleshoot_agent(self):
        from src.prompts.supervisor import SUPERVISOR_PROMPT

        assert "troubleshoot_agent" in SUPERVISOR_PROMPT

    def test_analyst_prompt_includes_scenario_f(self):
        from src.prompts.analyst import ANALYST_PROMPT

        assert "Troubleshoot" in ANALYST_PROMPT or "troubleshoot" in ANALYST_PROMPT


# ═══════════════════════════════════════════════════════════════════════
# Test 11 — SSE stream handles troubleshoot routing
# ═══════════════════════════════════════════════════════════════════════


class TestSSEStreamTroubleshoot:
    """Verify sse_stream.py handles troubleshoot_agent routing."""

    def test_sse_stream_module_importable(self):
        """SSE stream should import without errors."""
        import src.sse_stream  # noqa: F401

    def test_sse_stream_source_handles_troubleshoot(self):
        """The SSE stream source code should reference troubleshoot_agent."""
        import inspect
        import src.sse_stream

        source = inspect.getsource(src.sse_stream)
        assert "troubleshoot_agent" in source
