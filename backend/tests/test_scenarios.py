"""
Scenario-level regression tests for the active free-run backend.

This suite intentionally reflects the current architecture:
single `free_run_agent`, prompt-first tool choice, and runtime guardrails.
It does not reintroduce the retired multi-agent/supervisor design.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


class TestInventoryLookupByHostname:
    """lookup_device must resolve every hostname in inventory.csv."""

    @pytest.fixture(autouse=True)
    def _import_tool(self):
        from src.tools.inventory_tools import lookup_device

        self.tool = lookup_device

    def _call(self, hostname: str) -> dict:
        raw = self.tool.invoke({"hostname": hostname})
        return json.loads(raw)

    def test_lab_mgmt_br01(self):
        d = self._call("LAB-MGMT-BR01")
        assert d["hostname"] == "LAB-MGMT-BR01"
        assert d["ip_address"] == "10.255.0.1"
        assert d["device_role"] == "router"

    def test_hq_core_rt01(self):
        d = self._call("HQ-CORE-RT01")
        assert d["ip_address"] == "10.255.1.11"
        assert d["device_role"] == "core_router"

    def test_hq_core_rt02(self):
        d = self._call("HQ-CORE-RT02")
        assert d["ip_address"] == "10.255.1.12"
        assert d["device_role"] == "core_router"

    def test_hq_dist_gw01(self):
        d = self._call("HQ-DIST-GW01")
        assert d["ip_address"] == "10.255.2.21"
        assert d["device_role"] == "dist_switch"

    def test_hq_dist_gw02(self):
        d = self._call("HQ-DIST-GW02")
        assert d["ip_address"] == "10.255.2.22"
        assert d["device_role"] == "dist_switch"

    def test_branch_a_rtr(self):
        d = self._call("BRANCH-A-RTR")
        assert d["ip_address"] == "10.255.3.101"
        assert d["device_role"] == "router"

    def test_branch_b_rtr(self):
        d = self._call("BRANCH-B-RTR")
        assert d["ip_address"] == "10.255.3.102"
        assert d["device_role"] == "router"

    def test_branch_a_switch(self):
        d = self._call("BRANCH-A-Switch")
        assert d["ip_address"] == "192.168.99.11"
        assert d["device_role"] == "access_switch"

    def test_branch_b_switch(self):
        d = self._call("BRANCH-B-Switch")
        assert d["ip_address"] == "192.168.199.11"
        assert d["device_role"] == "access_switch"

    def test_case_insensitive_lookup(self):
        d = self._call("hq-core-rt01")
        assert d["hostname"] == "HQ-CORE-RT01"

    def test_unknown_hostname_returns_suggestions(self):
        d = self._call("UNKNOWN-DEVICE")
        assert "error" in d
        assert len(d.get("suggestions", [])) > 0


class TestInventoryLookupByIP:
    """lookup_device must resolve by IPv4 address as well."""

    @pytest.fixture(autouse=True)
    def _import_tool(self):
        from src.tools.inventory_tools import lookup_device

        self.tool = lookup_device

    def _call(self, ip: str) -> dict:
        raw = self.tool.invoke({"hostname": ip})
        return json.loads(raw)

    def test_router_by_ip(self):
        d = self._call("10.255.0.1")
        assert d["hostname"] == "LAB-MGMT-BR01"

    def test_core_router_by_ip(self):
        d = self._call("10.255.1.11")
        assert d["hostname"] == "HQ-CORE-RT01"

    def test_switch_by_ip(self):
        d = self._call("192.168.199.11")
        assert d["hostname"] == "BRANCH-B-Switch"

    def test_unknown_ip_returns_error(self):
        d = self._call("1.2.3.4")
        assert "error" in d


class TestListAllDevices:
    def test_returns_all_inventory_rows(self):
        from src.tools.inventory_tools import list_all_devices

        rows = json.loads(list_all_devices.invoke({}))
        assert isinstance(rows, list)
        assert len(rows) == 9

    def test_required_fields_exist(self):
        from src.tools.inventory_tools import list_all_devices

        rows = json.loads(list_all_devices.invoke({}))
        for row in rows:
            for field in ("hostname", "ip_address", "os_platform", "device_role", "site", "version"):
                assert field in row


class TestPromptCoverage:
    def test_full_prompt_matches_free_run_architecture(self):
        from src.prompts.ssh import SSH_PROMPT

        assert "You are fully LLM-first" in SSH_PROMPT
        assert "list_all_devices(): use only for all-device or broad inventory requests." in SSH_PROMPT
        assert "Inventory tool results are not operational proof." in SSH_PROMPT
        assert "Never infer reachability, uptime, health, or readiness from inventory alone." in SSH_PROMPT
        assert "show ip default-gateway" in SSH_PROMPT
        assert "show ip protocols" in SSH_PROMPT
        assert "show ip sla summary" in SSH_PROMPT
        assert "show ip sla configuration" in SSH_PROMPT
        assert "show track" in SSH_PROMPT
        assert "show interfaces trunk" in SSH_PROMPT
        assert "Start with the simplest direct command" in SSH_PROMPT
        assert "avoid large ASCII topology diagrams" in SSH_PROMPT

    def test_compact_prompt_keeps_high_value_command_guidance(self):
        from src.prompts.ssh_compact import SSH_COMPACT_PROMPT

        assert "show ip default-gateway" in SSH_COMPACT_PROMPT
        assert "show ip protocols" in SSH_COMPACT_PROMPT
        assert "show ip sla summary" in SSH_COMPACT_PROMPT
        assert "show track" in SSH_COMPACT_PROMPT
        assert "show processes memory" in SSH_COMPACT_PROMPT
        assert "do not start route/default-route checks" in SSH_COMPACT_PROMPT

    def test_synthesis_prompt_blocks_inventory_only_health_claims(self):
        from src.prompts.ssh_synthesis import SSH_SYNTHESIS_PROMPT

        assert "Inventory-only evidence is not operational proof." in SSH_SYNTHESIS_PROMPT
        assert "do not claim reachability" in SSH_SYNTHESIS_PROMPT
        assert "live operational status" in SSH_SYNTHESIS_PROMPT
        assert "For follow-up requests" in SSH_SYNTHESIS_PROMPT
        assert "route-table next hops" in SSH_SYNTHESIS_PROMPT
        assert "`show ip protocols` proves protocol presence/configuration" in SSH_SYNTHESIS_PROMPT
        assert "Confirmed physical links" in SSH_SYNTHESIS_PROMPT
        assert "Logical relationships" in SSH_SYNTHESIS_PROMPT


class TestFormatters:
    def test_parse_output_handles_command_repair_prefix(self):
        from src.formatters import parse_output

        raw = (
            "[COMMAND REPAIR] host=BRANCH-A-RTR reason=repaired platform syntax\n"
            "[ORIGINAL COMMAND] show arp\n"
            "[EXECUTED COMMAND] show ip arp\n"
            "[Device: BRANCH-A-RTR | IP: 10.255.3.101 | OS: cisco_ios]\n"
            "Protocol  Address          Age (min)\n"
        )
        host, ip, os_type, body = parse_output(raw)
        assert host == "BRANCH-A-RTR"
        assert ip == "10.255.3.101"
        assert os_type == "cisco_ios"
        assert "[EXECUTED COMMAND] show ip arp" in body

    def test_is_error_detects_prefixed_errors(self):
        from src.formatters import is_error

        assert is_error("[AUTH ERROR] Authentication failed")
        assert is_error("[TIMEOUT ERROR] timed out")
        assert not is_error("BGP router identifier 10.255.1.11")


class TestRunCliTool:
    def test_cache_miss_returns_inventory_error(self):
        from src.tools.cli_tool import create_run_cli_tool

        run_cli = create_run_cli_tool({})
        output = run_cli.invoke({"host": "HQ-CORE-RT01", "command": "show version"})
        assert "not found in inventory cache" in output

    def test_unsafe_command_is_blocked_before_ssh_execution(self):
        from src.tools.cli_tool import create_run_cli_tool

        run_cli = create_run_cli_tool({
            "HQ-CORE-RT01": {
                "ip_address": "10.255.1.11",
                "os_platform": "cisco_ios",
                "device_role": "core_router",
                "site": "HQ",
            }
        })
        output = run_cli.invoke({"host": "HQ-CORE-RT01", "command": "configure terminal"})
        assert output.startswith("[BLOCKED]")

    @patch("src.tools.cli_tool.execute_cli")
    def test_safe_command_calls_executor(self, mock_execute):
        from src.tools.cli_tool import create_run_cli_tool

        mock_execute.return_value = "[Device: HQ-CORE-RT01 | IP: 10.255.1.11 | OS: cisco_ios]\nOK"
        run_cli = create_run_cli_tool({
            "HQ-CORE-RT01": {
                "ip_address": "10.255.1.11",
                "os_platform": "cisco_ios",
                "device_role": "core_router",
                "site": "HQ",
            }
        })

        output = run_cli.invoke({"host": "HQ-CORE-RT01", "command": "show ip bgp summary"})
        mock_execute.assert_called_once_with("10.255.1.11", "cisco_ios", "show ip bgp summary")
        assert output.endswith("\nOK")


class TestSSHExecutorCredentialCheck:
    def test_missing_credentials_returns_config_error(self):
        with patch.dict(os.environ, {"ROUTER_USER": "", "ROUTER_PASS": ""}):
            from importlib import reload
            from src.tools import ssh_executor

            reload(ssh_executor)
            result = ssh_executor.execute_cli("10.255.1.11", "cisco_ios", "show version")
        assert "[CONFIG ERROR]" in result


class TestGraphBuilder:
    def test_build_graph_compiles(self):
        from src.graph import build_graph

        graph = build_graph()
        assert graph is not None

    def test_graph_contains_only_free_run_node(self):
        from src.graph.builder import build_graph

        graph = build_graph()
        node_names = set(graph.get_graph().nodes.keys())
        assert "free_run_agent" in node_names
        assert "supervisor" not in node_names
        assert "ssh_agent" not in node_names

    def test_graph_entry_point_is_free_run_agent(self):
        from src.graph.builder import build_graph

        graph = build_graph()
        edges = [edge for edge in graph.get_graph().edges if edge.source == "__start__"]
        assert any(edge.target == "free_run_agent" for edge in edges)


class TestApiSurface:
    def test_health_endpoint_works(self):
        from src.api import app

        client = TestClient(app)
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "sessions" in data

    def test_inventory_endpoint_returns_active_inventory(self):
        from src.api import app

        client = TestClient(app)
        response = client.get("/api/inventory")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 9
        assert any(item["hostname"] == "HQ-CORE-RT01" for item in data)
