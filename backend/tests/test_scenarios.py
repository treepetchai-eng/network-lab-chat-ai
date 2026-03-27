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

    def test_transit_interface_ip_resolves_owner_device(self):
        d = self._call("10.255.10.14")
        assert d["hostname"] == "HQ-DIST-GW01"
        assert d["ip_address"] == "10.255.2.21"
        assert d["resolved_via"] == "interface_ip"
        assert d["matched_value"] == "10.255.10.14"
        assert d["matched_interface"]["name"] == "GigabitEthernet0/1"
        assert d["matched_interface"]["network_cidr"] == "10.255.10.12/30"

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


class TestInterfaceInventoryResolvers:
    def test_resolve_ip_context_returns_exact_and_connected_matches(self):
        from src.tools.interface_inventory import resolve_ip_context

        context = resolve_ip_context("192.168.99.11")
        exact_matches = {
            (row["hostname"], row["interface_name"])
            for row in context["exact_matches"]
        }
        network_matches = {
            (row["hostname"], row["interface_name"])
            for row in context["network_matches"]
        }

        assert ("BRANCH-A-Switch", "Vlan99") in exact_matches
        assert ("BRANCH-A-Switch", "Vlan99") in network_matches
        assert ("BRANCH-A-RTR", "GigabitEthernet0/3.99") in network_matches

    def test_resolve_prefix_context_returns_exact_connected_owners(self):
        from src.tools.interface_inventory import resolve_prefix_context

        context = resolve_prefix_context("192.168.99.0/24")
        exact_matches = {
            (row["hostname"], row["interface_name"])
            for row in context["exact_matches"]
        }

        assert context["normalized_prefix"] == "192.168.99.0/24"
        assert ("BRANCH-A-Switch", "Vlan99") in exact_matches
        assert ("BRANCH-A-RTR", "GigabitEthernet0/3.99") in exact_matches

    def test_resolve_link_context_uses_unique_network_pair(self):
        from src.tools.interface_inventory import resolve_link_context

        context = resolve_link_context("HQ-CORE-RT01", "Gi0/0")

        assert context["link_id"] == "HQ-CORE-RT01:GigabitEthernet0/0<->HQ-CORE-RT02:GigabitEthernet0/0"
        assert context["local_interface"] == "GigabitEthernet0/0"
        assert context["remote_hostname"] == "HQ-CORE-RT02"
        assert context["remote_interface"] == "GigabitEthernet0/0"
        assert context["remote_mgmt_ip"] == "10.255.1.12"
        assert context["topology_confidence"] == "high"

    def test_resolve_link_context_falls_back_to_description_hint(self):
        from src.tools.interface_inventory import resolve_link_context

        context = resolve_link_context("BRANCH-A-Switch", "GigabitEthernet0/1")

        assert context["link_id"] == "BRANCH-A-RTR:GigabitEthernet0/3<->BRANCH-A-Switch:GigabitEthernet0/1"
        assert context["remote_hostname"] == "BRANCH-A-RTR"
        assert context["remote_interface"] == "GigabitEthernet0/3"
        assert context["topology_confidence"] == "high"

    def test_resolve_link_context_returns_empty_link_for_ambiguous_topology(self, monkeypatch):
        from src.tools import interface_inventory as inventory_module

        rows = [
            {"hostname": "LAB-A", "interface_name": "GigabitEthernet0/0", "network_cidr": "10.0.0.0/24", "description": "", "mgmt_ip": "10.0.0.11", "ip_address": "10.0.0.1"},
            {"hostname": "LAB-B", "interface_name": "GigabitEthernet0/0", "network_cidr": "10.0.0.0/24", "description": "", "mgmt_ip": "10.0.0.12", "ip_address": "10.0.0.2"},
            {"hostname": "LAB-C", "interface_name": "GigabitEthernet0/0", "network_cidr": "10.0.0.0/24", "description": "", "mgmt_ip": "10.0.0.13", "ip_address": "10.0.0.3"},
        ]
        index = {
            "by_hostname": {
                "lab-a": [rows[0]],
                "lab-b": [rows[1]],
                "lab-c": [rows[2]],
            },
            "by_ip": {},
            "by_network": {"10.0.0.0/24": rows},
            "network_entries": [],
        }
        monkeypatch.setattr(inventory_module, "_load_interface_rows", lambda *, full=False: (rows, index))

        context = inventory_module.resolve_link_context("LAB-A", "GigabitEthernet0/0")

        assert context["link_id"] == ""
        assert context["local_interface"] == "GigabitEthernet0/0"
        assert context["remote_hostname"] == ""


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
        assert "run_diagnostic(host, kind, target, count=2, timeout=1)" in SSH_PROMPT
        assert "Prefer run_diagnostic for `ping` and `traceroute`" in SSH_PROMPT
        assert "Start with the simplest direct command" in SSH_PROMPT
        assert "avoid large ASCII topology diagrams" in SSH_PROMPT

    def test_compact_prompt_keeps_high_value_command_guidance(self):
        from src.prompts.ssh_compact import SSH_COMPACT_PROMPT

        assert "show ip default-gateway" in SSH_COMPACT_PROMPT
        assert "show ip protocols" in SSH_COMPACT_PROMPT
        assert "show ip sla summary" in SSH_COMPACT_PROMPT
        assert "show track" in SSH_COMPACT_PROMPT
        assert "show processes memory" in SSH_COMPACT_PROMPT
        assert "run_diagnostic(host, kind, target, count=2, timeout=1)" in SSH_COMPACT_PROMPT
        assert "Prefer run_diagnostic for ping/traceroute" in SSH_COMPACT_PROMPT
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
        assert "avoid half-translated labels" in SSH_SYNTHESIS_PROMPT
        assert "End with the final destination device/interface" in SSH_SYNTHESIS_PROMPT


class TestFormatters:
    def test_fmt_lookup_renders_interface_ip_resolution(self):
        from src.formatters import fmt_lookup

        raw = json.dumps({
            "hostname": "HQ-DIST-GW01",
            "ip_address": "10.255.2.21",
            "os_platform": "cisco_ios",
            "device_role": "dist_switch",
            "site": "HQ",
            "version": "15.6(2)T",
            "resolved_via": "interface_ip",
            "matched_value": "10.255.10.14",
            "matched_interface": {
                "name": "GigabitEthernet0/1",
                "ip_address": "10.255.10.14",
                "network_cidr": "10.255.10.12/30",
                "description": "TO-HQ-CORE-RT01 Gi0/2",
                "interface_mode": "routed",
            },
        })

        rendered = fmt_lookup(raw)
        assert "Resolved via: interface IP 10.255.10.14" in rendered
        assert "Matched Interface: GigabitEthernet0/1" in rendered
        assert "Interface Network: 10.255.10.12/30" in rendered

    def test_fmt_lookup_renders_role_scope_resolution(self):
        from src.formatters import fmt_lookup

        raw = json.dumps([
            {
                "hostname": "HQ-CORE-RT01",
                "ip_address": "10.255.1.11",
                "os_platform": "cisco_ios",
                "device_role": "core_router",
                "site": "HQ",
                "version": "15.6(2)T",
            },
            {
                "hostname": "HQ-CORE-RT02",
                "ip_address": "10.255.1.12",
                "os_platform": "cisco_ios",
                "device_role": "core_router",
                "site": "HQ",
                "version": "15.6(2)T",
            },
        ])

        rendered = fmt_lookup(raw)
        assert "Role Scope Resolved" in rendered
        assert "HQ-CORE-RT01" in rendered
        assert "HQ-CORE-RT02" in rendered

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

    def test_is_error_detects_cli_syntax_errors(self):
        from src.formatters import is_error

        assert is_error("% Invalid input detected at '^' marker.")
        assert is_error("% Incomplete command.")
        assert is_error("% Ambiguous command:  \"show ip\"")


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


class TestDiagnosticTool:
    @patch("src.tools.diagnostic_tool.execute_cli")
    def test_traceroute_resolves_target_and_renders_safe_command(self, mock_execute):
        from src.tools.diagnostic_tool import create_run_diagnostic_tool

        mock_execute.return_value = (
            "[Device: HQ-CORE-RT01 | IP: 10.255.1.11 | OS: cisco_ios]\n"
            "Type escape sequence to abort.\n"
            "Tracing the route to 192.168.99.11\n"
            "  1 172.16.10.2 1 msec 2 msec 2 msec\n"
            "  2 192.168.99.11 3 msec 3 msec 3 msec\n"
        )
        run_diagnostic = create_run_diagnostic_tool({
            "HQ-CORE-RT01": {
                "ip_address": "10.255.1.11",
                "os_platform": "cisco_ios",
                "device_role": "core_router",
                "site": "HQ",
            }
        })

        output = run_diagnostic.invoke({
            "host": "HQ-CORE-RT01",
            "kind": "traceroute",
            "target": "BRANCH-A-Switch",
        })

        mock_execute.assert_called_once_with("10.255.1.11", "cisco_ios", "traceroute 192.168.99.11")
        assert "[EXECUTED COMMAND] traceroute 192.168.99.11" in output
        assert "[RESOLVED TARGET] BRANCH-A-Switch (192.168.99.11)" in output
        assert "[TRACE TARGET CONTEXT]" in output
        assert "BRANCH-A-Switch Vlan99" in output
        assert "BRANCH-A-RTR GigabitEthernet0/3.99" in output
        assert "[TRACE HOP ANNOTATION]" in output
        assert "hop 1: 172.16.10.2 -> exact=BRANCH-A-RTR Tunnel10" in output

    @patch("src.tools.diagnostic_tool.execute_cli")
    def test_ping_uses_semantic_renderer_for_ios(self, mock_execute):
        from src.tools.diagnostic_tool import create_run_diagnostic_tool

        mock_execute.return_value = "[Device: HQ-CORE-RT01 | IP: 10.255.1.11 | OS: cisco_ios]\nping ok"
        run_diagnostic = create_run_diagnostic_tool({
            "HQ-CORE-RT01": {
                "ip_address": "10.255.1.11",
                "os_platform": "cisco_ios",
                "device_role": "core_router",
                "site": "HQ",
            }
        })

        output = run_diagnostic.invoke({
            "host": "HQ-CORE-RT01",
            "kind": "ping",
            "target": "192.168.99.11",
            "count": 3,
            "timeout": 2,
        })

        mock_execute.assert_called_once_with("10.255.1.11", "cisco_ios", "ping 192.168.99.11 repeat 3 timeout 2")
        assert "[EXECUTED COMMAND] ping 192.168.99.11 repeat 3 timeout 2" in output


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
