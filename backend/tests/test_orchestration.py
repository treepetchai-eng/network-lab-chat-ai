"""Unit tests for the explicit troubleshoot orchestration layer."""

from __future__ import annotations

import sys
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest

pytestmark = pytest.mark.skip(
    reason=(
        "Legacy orchestration tests for modules removed by the current "
        "LLM-first free_run architecture."
    )
)

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


def test_dist_switch_capabilities_are_hybrid():
    from src.tools.command_profiles import infer_device_capabilities

    caps = infer_device_capabilities("dist_switch", "cisco_ios", "15.6(2)T")
    assert caps.role_family == "hybrid"
    assert "route_lookup" in caps.allowed_check_types
    assert "vlan_summary" in caps.allowed_check_types


def test_access_switch_prefers_vlan_summary_command():
    from src.tools.command_profiles import resolve_command_candidates

    commands = resolve_command_candidates(
        {"device_role": "access_switch", "os_platform": "cisco_ios", "version": "15.2(4.0.55)E"},
        "vlan_summary",
        {},
    )
    assert commands[0].command == "show vlan brief"


def test_command_validator_fails_closed_for_config_mode():
    from src.tools.command_validator import validate_candidate_command

    result = validate_candidate_command(
        proposed_command="configure terminal",
        user_query="please configure the router",
        device_info={"device_role": "router", "os_platform": "cisco_ios", "version": "15.6(2)T"},
    )
    assert result["allowed"] is False


def test_initialize_investigation_state_extracts_entities():
    from src.graph.troubleshoot.engine import initialize_investigation_state

    state = initialize_investigation_state({
        "messages": [HumanMessage(content="troubleshoot why BRANCH-A-RTR cannot reach 10.255.1.11")],
        "device_cache": {
            "BRANCH-A-RTR": {
                "ip_address": "10.255.3.101",
                "os_platform": "cisco_ios",
                "device_role": "router",
                "site": "BRANCH-A",
                "version": "15.6(2)T",
            },
            "HQ-CORE-RT01": {
                "ip_address": "10.255.1.11",
                "os_platform": "cisco_ios",
                "device_role": "core_router",
                "site": "HQ",
                "version": "15.6(2)T",
            },
        },
    })
    assert state["user_query"].startswith("troubleshoot")
    assert state["extracted_entities"]["target_ip"] == "10.255.1.11"
    assert state["candidate_devices"][0]["hostname"] == "BRANCH-A-RTR"


def test_select_candidate_devices_by_site_and_role():
    from src.graph.troubleshoot.engine import extract_entities, normalize_intent, select_candidate_devices

    device_cache = {
        "HQ-CORE-RT01": {
            "ip_address": "10.255.1.11",
            "os_platform": "cisco_ios",
            "device_role": "core_router",
            "site": "HQ",
            "version": "15.6(2)T",
        },
        "HQ-CORE-RT02": {
            "ip_address": "10.255.1.12",
            "os_platform": "cisco_ios",
            "device_role": "core_router",
            "site": "HQ",
            "version": "15.6(2)T",
        },
        "BRANCH-A-RTR": {
            "ip_address": "10.255.3.101",
            "os_platform": "cisco_ios",
            "device_role": "router",
            "site": "BRANCH-A",
            "version": "15.6(2)T",
        },
    }
    query = "compare BGP on HQ core routers"
    entities = extract_entities(query, device_cache)
    intent = normalize_intent(query, entities)
    candidates = select_candidate_devices(device_cache, entities, intent)
    assert [item["hostname"] for item in candidates] == ["HQ-CORE-RT01", "HQ-CORE-RT02"]


def test_analyze_cli_result_detects_missing_route():
    from src.graph.troubleshoot.engine import analyze_cli_result

    result = analyze_cli_result(
        device="BRANCH-A-RTR",
        check_type="route_lookup",
        command="show ip route 10.255.1.11",
        raw_output="[Device: BRANCH-A-RTR | IP: 10.255.3.101 | OS: cisco_ios]\n% Network not in table",
        state={
            "observations": {},
            "errors": [],
            "unresolved_gaps": [],
            "current_hypothesis": "",
            "hypothesis": "",
            "planned_checks": [],
            "device_cache": {},
            "extracted_entities": {"target_ip": "10.255.1.11"},
        },
    )
    assert result["observations"]["obs_1"]["data"]["route_missing"] is True
    assert "missing a route" in result["summary"]


def test_analyze_route_records_next_hop_and_outgoing_interface():
    from src.graph.troubleshoot.engine import analyze_cli_result

    result = analyze_cli_result(
        device="BRANCH-A-RTR",
        check_type="route_lookup",
        command="show ip route 10.255.1.11",
        raw_output=(
            "[Device: BRANCH-A-RTR | IP: 10.255.3.101 | OS: cisco_ios]\n"
            "Routing entry for 10.255.1.11/32\n"
            "  Known via \"eigrp 100\", distance 90, metric 1000\n"
            "  Routing Descriptor Blocks:\n"
            "  * 172.16.10.1, from 10.255.3.1, 00:00:31 ago, via Tunnel10\n"
        ),
        state={
            "observations": {},
            "errors": [],
            "unresolved_gaps": [],
            "current_hypothesis": "",
            "hypothesis": "",
            "planned_checks": [],
            "device_cache": {},
            "extracted_entities": {"target_ip": "10.255.1.11"},
        },
    )
    data = result["observations"]["obs_1"]["data"]
    assert data["next_hop_ip"] == "172.16.10.1"
    assert data["outgoing_interface"] == "Tunnel10"


def test_interface_summary_does_not_stop_on_unrelated_down_interface():
    from src.graph.troubleshoot.engine import analyze_cli_result

    state = {
        "observations": {
            "obs_1": {
                "device": "BRANCH-A-RTR",
                "check_type": "route_lookup",
                "severity": "info",
                "summary": "BRANCH-A-RTR routes toward the target via 172.16.10.1 on Tunnel10",
                "evidence": "via Tunnel10",
                "data": {
                    "next_hop_ip": "172.16.10.1",
                    "outgoing_interface": "Tunnel10",
                },
            }
        },
        "errors": [],
        "unresolved_gaps": [],
        "current_hypothesis": "",
        "hypothesis": "",
        "planned_checks": [],
        "device_cache": {},
        "extracted_entities": {"target_ip": "10.255.1.11", "interfaces": []},
    }
    result = analyze_cli_result(
        device="BRANCH-A-RTR",
        check_type="interface_summary",
        command="show ip interface brief",
        raw_output=(
            "[Device: BRANCH-A-RTR | IP: 10.255.3.101 | OS: cisco_ios]\n"
            "Interface              IP-Address      OK? Method Status                Protocol\n"
            "GigabitEthernet0/0     unassigned      YES unset  administratively down down\n"
            "Tunnel10               172.16.10.2     YES NVRAM  up                    up\n"
        ),
        state=state,
    )
    assert result["final_conclusion"] == ""
    assert result["stop_reason"] == ""
    assert "not yet confirmed" in result["summary"]



def test_interface_detail_stops_when_path_interface_is_down():
    from src.graph.troubleshoot.engine import analyze_cli_result

    state = {
        "observations": {
            "obs_1": {
                "device": "BRANCH-A-RTR",
                "check_type": "route_lookup",
                "severity": "info",
                "summary": "BRANCH-A-RTR routes toward the target via 172.16.10.1 on Tunnel10",
                "evidence": "via Tunnel10",
                "data": {
                    "next_hop_ip": "172.16.10.1",
                    "outgoing_interface": "Tunnel10",
                },
            }
        },
        "errors": [],
        "unresolved_gaps": [],
        "current_hypothesis": "",
        "hypothesis": "",
        "planned_checks": [],
        "device_cache": {},
        "extracted_entities": {"target_ip": "10.255.1.11", "interfaces": []},
    }
    result = analyze_cli_result(
        device="BRANCH-A-RTR",
        check_type="interface_detail",
        command="show interfaces Tunnel10",
        raw_output=(
            "[Device: BRANCH-A-RTR | IP: 10.255.3.101 | OS: cisco_ios]\n"
            "Tunnel10 is administratively down, line protocol is down\n"
        ),
        state=state,
    )
    assert result["stop_reason"] == "path_interface_down"
    assert "Tunnel10" in result["final_conclusion"]


def test_ssh_agent_targeted_resolution_builds_bgp_command():
    from src.graph.agents.ssh_agent import _resolve_targeted_command

    command = _resolve_targeted_command(
        "check BGP summary on HQ-CORE-RT01",
        "HQ-CORE-RT01",
        {
            "HQ-CORE-RT01": {
                "ip_address": "10.255.1.11",
                "os_platform": "cisco_ios",
                "device_role": "core_router",
                "site": "HQ",
                "version": "15.6(2)T",
            }
        },
    )
    assert command == "show ip bgp summary"


def test_inventory_node_caches_version_field():
    from src.graph.agents.inventory_agent import inventory_node

    class DummyLLM:
        def bind_tools(self, tools):
            raise AssertionError("fast path should not call the llm")

    result = inventory_node(
        {"messages": [HumanMessage(content="ขอรายชื่ออุปกรณ์ทั้งหมดหน่อย")], "device_cache": {}},
        DummyLLM(),
    )
    assert result["device_cache"]["HQ-CORE-RT01"]["version"]



def test_filter_devices_by_scope_honors_site_and_routing_focus():
    from src.graph.supervisor import _filter_devices_by_scope

    device_cache = {
        "HQ-CORE-RT01": {"ip_address": "10.255.1.11", "device_role": "core_router", "site": "HQ"},
        "HQ-DIST-GW01": {"ip_address": "10.255.2.21", "device_role": "dist_switch", "site": "HQ"},
        "BRANCH-A-RTR": {"ip_address": "10.255.3.101", "device_role": "router", "site": "BRANCH-A"},
        "BRANCH-A-Switch": {"ip_address": "192.168.99.11", "device_role": "access_switch", "site": "BRANCH-A"},
    }
    devices = list(device_cache.items())
    filtered = _filter_devices_by_scope("find route to 10.255.1.11 on HQ devices", devices, device_cache)
    assert [hostname for hostname, _ in filtered] == ["HQ-CORE-RT01", "HQ-DIST-GW01"]



def test_parse_output_handles_command_repair_prefix():
    from src.formatters import parse_output

    raw = (
        "[COMMAND REPAIR] host=BRANCH-A-RTR reason=repaired platform syntax\n"
        "[ORIGINAL COMMAND] show arp\n"
        "[EXECUTED COMMAND] show ip arp\n"
        "[Device: BRANCH-A-RTR | IP: 10.255.3.101 | OS: cisco_ios]\n"
        "Protocol  Address          Age (min)  Hardware Addr   Type   Interface\n"
    )
    host, ip, os_type, body = parse_output(raw)
    assert host == "BRANCH-A-RTR"
    assert ip == "10.255.3.101"
    assert os_type == "cisco_ios"
    assert "[EXECUTED COMMAND] show ip arp" in body



def test_filter_devices_by_scope_returns_empty_when_scope_is_exhausted():
    from src.graph.supervisor import _filter_devices_by_scope

    device_cache = {
        "HQ-CORE-RT01": {"ip_address": "10.255.1.11", "device_role": "core_router", "site": "HQ"},
        "HQ-DIST-GW01": {"ip_address": "10.255.2.21", "device_role": "dist_switch", "site": "HQ"},
        "BRANCH-A-RTR": {"ip_address": "10.255.3.101", "device_role": "router", "site": "BRANCH-A"},
    }
    devices = [("BRANCH-A-RTR", device_cache["BRANCH-A-RTR"])]
    filtered = _filter_devices_by_scope("find route to 10.255.1.11 on HQ devices", devices, device_cache)
    assert filtered == []



def test_infer_aggregate_conclusion_for_failure_beyond_next_hop():
    from src.graph.troubleshoot.engine import _infer_aggregate_conclusion

    state = {
        "candidate_devices": [{"hostname": "BRANCH-A-RTR"}],
        "active_device": "BRANCH-A-RTR",
        "device_cache": {
            "BRANCH-A-RTR": {"ip_address": "10.255.3.101"},
            "HQ-CORE-RT01": {"ip_address": "10.255.1.11"},
        },
        "extracted_entities": {"target_ip": "10.255.1.11"},
    }
    observations = {
        "obs_1": {
            "device": "BRANCH-A-RTR",
            "check_type": "ping_test",
            "data": {"ping_target": "10.255.1.11", "success_rate": 0},
        },
        "obs_2": {
            "device": "BRANCH-A-RTR",
            "check_type": "route_lookup",
            "data": {
                "route_target": "10.255.1.11",
                "next_hop_ip": "172.16.10.1",
                "outgoing_interface": "Tunnel10",
            },
        },
        "obs_3": {
            "device": "BRANCH-A-RTR",
            "check_type": "ping_test",
            "data": {"ping_target": "172.16.10.1", "success_rate": 100},
        },
        "obs_4": {
            "device": "HQ-CORE-RT01",
            "check_type": "ping_test",
            "data": {"ping_target": "10.255.1.11", "success_rate": 100},
        },
    }
    conclusion, reason = _infer_aggregate_conclusion(state, observations)
    assert reason == "path_beyond_next_hop"
    assert "172.16.10.1" in conclusion
    assert "10.255.1.11" in conclusion


def test_directional_traceroute_is_not_treated_as_multi_device_batch():
    from src.graph.supervisor import _is_multi_device_query

    assert _is_multi_device_query("tarceroute จาก HQ-CORE-RT01 ไป BRANCH-A-RTR ให้หน่อย") is False


def test_resolve_targeted_command_builds_traceroute_to_target_device_ip():
    from src.graph.agents.ssh_agent import _resolve_targeted_command

    device_cache = {
        "HQ-CORE-RT01": {
            "ip_address": "10.255.1.11",
            "os_platform": "cisco_ios",
            "device_role": "core_router",
            "site": "HQ",
            "version": "15.6(2)T",
        },
        "BRANCH-A-RTR": {
            "ip_address": "10.255.3.101",
            "os_platform": "cisco_ios",
            "device_role": "router",
            "site": "BRANCH-A",
            "version": "15.6(2)T",
        },
    }
    query = "tarceroute จาก HQ-CORE-RT01 ไป BRANCH-A-RTR ให้หน่อย"
    command = _resolve_targeted_command(
        query,
        "HQ-CORE-RT01",
        device_cache,
        messages=[HumanMessage(content=query)],
    )
    assert command == "traceroute 10.255.3.101"


def test_follow_up_traceroute_reuses_recent_path_context():
    from src.graph.agents.ssh_agent import _find_target_host, _resolve_targeted_command

    device_cache = {
        "HQ-CORE-RT01": {
            "ip_address": "10.255.1.11",
            "os_platform": "cisco_ios",
            "device_role": "core_router",
            "site": "HQ",
            "version": "15.6(2)T",
        },
        "BRANCH-A-RTR": {
            "ip_address": "10.255.3.101",
            "os_platform": "cisco_ios",
            "device_role": "router",
            "site": "BRANCH-A",
            "version": "15.6(2)T",
        },
    }
    messages = [
        HumanMessage(content="tarceroute จาก HQ-CORE-RT01 ไป BRANCH-A-RTR ให้หน่อย"),
        HumanMessage(content="traceroute ให้หน่อย"),
    ]
    assert _find_target_host(messages, device_cache) == "HQ-CORE-RT01"
    command = _resolve_targeted_command(
        "traceroute ให้หน่อย",
        "HQ-CORE-RT01",
        device_cache,
        messages=messages,
    )
    assert command == "traceroute 10.255.3.101"


def test_follow_up_cpu_reuses_recent_single_device_context():
    from src.graph.agents.ssh_agent import _find_target_host, _resolve_targeted_command

    device_cache = {
        "BRANCH-B-RTR": {
            "ip_address": "10.255.3.102",
            "os_platform": "cisco_ios",
            "device_role": "router",
            "site": "BRANCH-B",
            "version": "15.6(2)T",
        },
        "HQ-CORE-RT01": {
            "ip_address": "10.255.1.11",
            "os_platform": "cisco_ios",
            "device_role": "core_router",
            "site": "HQ",
            "version": "15.6(2)T",
        },
    }
    messages = [
        HumanMessage(content="ขอดู ip route ของ BRANCH-B-RTR หน่อย"),
        AIMessage(
            content="",
            tool_calls=[{"name": "run_cli", "args": {"host": "BRANCH-B-RTR", "command": "show ip route"}, "id": "call-1"}],
        ),
        ToolMessage(
            content="[Device: BRANCH-B-RTR | IP: 10.255.3.102 | OS: cisco_ios]\nBRANCH-B-RTR# show ip route",
            tool_call_id="call-1",
            name="run_cli",
        ),
        AIMessage(content="route output summarized"),
        HumanMessage(content="cpu หละเป็นยังไง"),
    ]

    assert _find_target_host(messages, device_cache) == "BRANCH-B-RTR"
    command = _resolve_targeted_command(
        "cpu หละเป็นยังไง",
        "BRANCH-B-RTR",
        device_cache,
        messages=messages,
    )
    assert command == "show processes cpu sorted"



def test_follow_up_cpu_does_not_guess_after_multi_device_context():
    from src.graph.agents.ssh_agent import _find_target_host

    device_cache = {
        "BRANCH-B-RTR": {
            "ip_address": "10.255.3.102",
            "os_platform": "cisco_ios",
            "device_role": "router",
            "site": "BRANCH-B",
            "version": "15.6(2)T",
        },
        "HQ-CORE-RT01": {
            "ip_address": "10.255.1.11",
            "os_platform": "cisco_ios",
            "device_role": "core_router",
            "site": "HQ",
            "version": "15.6(2)T",
        },
    }
    messages = [
        HumanMessage(content="เช็ค route ของ HQ-CORE-RT01 กับ BRANCH-B-RTR"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "run_cli", "args": {"host": "HQ-CORE-RT01", "command": "show ip route"}, "id": "call-1"},
                {"name": "run_cli", "args": {"host": "BRANCH-B-RTR", "command": "show ip route"}, "id": "call-2"},
            ],
        ),
        ToolMessage(content="hq output", tool_call_id="call-1", name="run_cli"),
        ToolMessage(content="branch output", tool_call_id="call-2", name="run_cli"),
        HumanMessage(content="cpu หละเป็นยังไง"),
    ]

    assert _find_target_host(messages, device_cache) is None


def test_ssh_node_returns_pending_question_for_ambiguous_traceroute():
    from src.graph.agents.ssh_agent import ssh_node

    class DummyTool:
        def invoke(self, args):
            raise AssertionError("ambiguous traceroute should not execute CLI")

    result = ssh_node(
        {
            "messages": [HumanMessage(content="traceroute ให้หน่อย")],
            "device_cache": {
                "HQ-CORE-RT01": {
                    "ip_address": "10.255.1.11",
                    "os_platform": "cisco_ios",
                    "device_role": "core_router",
                    "site": "HQ",
                    "version": "15.6(2)T",
                },
                "BRANCH-A-RTR": {
                    "ip_address": "10.255.3.101",
                    "os_platform": "cisco_ios",
                    "device_role": "router",
                    "site": "BRANCH-A",
                    "version": "15.6(2)T",
                },
            },
        },
        llm=object(),
        run_cli_tool=DummyTool(),
    )
    assert result["messages"]
    assert result["pending_questions"]
    assert "[Pending Question]" in result["messages"][0].content
    assert "traceroute" in result["pending_questions"][0].lower()


def test_ssh_node_returns_pending_question_for_ambiguous_follow_up_cpu_without_single_device_context():
    from src.graph.agents.ssh_agent import ssh_node

    class DummyTool:
        def invoke(self, args):
            raise AssertionError("ambiguous cpu follow-up should not execute CLI")

    result = ssh_node(
        {
            "messages": [
                HumanMessage(content="เช็ค route ของ HQ-CORE-RT01 กับ BRANCH-B-RTR"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "run_cli", "args": {"host": "HQ-CORE-RT01", "command": "show ip route"}, "id": "call-1"},
                        {"name": "run_cli", "args": {"host": "BRANCH-B-RTR", "command": "show ip route"}, "id": "call-2"},
                    ],
                ),
                ToolMessage(content="hq output", tool_call_id="call-1", name="run_cli"),
                ToolMessage(content="branch output", tool_call_id="call-2", name="run_cli"),
                HumanMessage(content="cpu หละเป็นยังไง"),
            ],
            "device_cache": {
                "HQ-CORE-RT01": {
                    "ip_address": "10.255.1.11",
                    "os_platform": "cisco_ios",
                    "device_role": "core_router",
                    "site": "HQ",
                    "version": "15.6(2)T",
                },
                "BRANCH-B-RTR": {
                    "ip_address": "10.255.3.102",
                    "os_platform": "cisco_ios",
                    "device_role": "router",
                    "site": "BRANCH-B",
                    "version": "15.6(2)T",
                },
            },
        },
        llm=object(),
        run_cli_tool=DummyTool(),
    )
    assert result["messages"]
    assert result["pending_questions"]
    assert "[Pending Question]" in result["messages"][0].content
    assert "อุปกรณ์" in result["pending_questions"][0]


def test_analyst_fallback_prefers_pending_question():
    from src.graph.agents.analyst_agent import _fallback_from_state

    question = "Which source device and destination should I run the ping/traceroute between?"
    text = _fallback_from_state(
        {
            "pending_questions": [question],
            "final_conclusion": "",
            "observations": {},
            "executed_steps": [],
        },
        [HumanMessage(content="traceroute please")],
    )
    assert text == question


def test_supervisor_routes_ambiguous_traceroute_to_analyst_until_context_exists():
    from src.graph.supervisor import _heuristic_route

    device_cache = {
        "HQ-CORE-RT01": {"ip_address": "10.255.1.11", "device_role": "core_router", "site": "HQ"},
        "BRANCH-A-RTR": {"ip_address": "10.255.3.101", "device_role": "router", "site": "BRANCH-A"},
    }
    messages = [HumanMessage(content="traceroute ให้หน่อย")]
    assert _heuristic_route("traceroute ให้หน่อย", device_cache, messages) == "analyst_agent"

    messages = [
        HumanMessage(content="traceroute จาก HQ-CORE-RT01 ไป BRANCH-A-RTR"),
        HumanMessage(content="traceroute ให้หน่อย"),
    ]
    assert _heuristic_route("traceroute ให้หน่อย", device_cache, messages) == "ssh_agent"


def test_supervisor_routes_pending_question_from_ssh_to_analyst():
    from src.graph.supervisor import _heuristic_route

    device_cache = {
        "HQ-CORE-RT01": {"ip_address": "10.255.1.11", "device_role": "core_router", "site": "HQ"},
        "BRANCH-B-RTR": {"ip_address": "10.255.3.102", "device_role": "router", "site": "BRANCH-B"},
    }
    messages = [
        HumanMessage(content="cpu หละเป็นยังไง"),
        AIMessage(content="[Pending Question] ต้องการเช็คบนอุปกรณ์ตัวไหนครับ? เช่น CPU ของ BRANCH-B-RTR หรือ HQ-CORE-RT01"),
    ]

    assert _heuristic_route("cpu หละเป็นยังไง", device_cache, messages) == "analyst_agent"


def test_pair_reachability_query_is_not_treated_as_multi_device_batch():
    from src.graph.supervisor import _is_multi_device_query

    query = "เช็ค routing ระหว่าง HQ-CORE-RT01 กับ BRANCH-B-Switch ให้หน่อยว่า ถึงกันหรือไม่"
    assert _is_multi_device_query(query, [HumanMessage(content=query)]) is False


def test_pair_reachability_uses_ping_for_router_to_access_switch():
    from src.graph.agents.ssh_agent import _resolve_targeted_command

    device_cache = {
        "HQ-CORE-RT01": {
            "ip_address": "10.255.1.11",
            "os_platform": "cisco_ios",
            "device_role": "core_router",
            "site": "HQ",
            "version": "15.6(2)T",
        },
        "BRANCH-B-Switch": {
            "ip_address": "192.168.199.11",
            "os_platform": "cisco_ios",
            "device_role": "access_switch",
            "site": "BRANCH-B",
            "version": "15.2(4.0.55)E",
        },
    }
    query = "เช็ค routing ระหว่าง HQ-CORE-RT01 กับ BRANCH-B-Switch ให้หน่อยว่า ถึงกันหรือไม่"
    command = _resolve_targeted_command(query, "HQ-CORE-RT01", device_cache, messages=[HumanMessage(content=query)])
    assert command == "ping 192.168.199.11 repeat 2 timeout 1"


def test_return_path_query_uses_reverse_ping_on_access_switch():
    from src.graph.agents.ssh_agent import _resolve_targeted_command

    device_cache = {
        "HQ-CORE-RT01": {
            "ip_address": "10.255.1.11",
            "os_platform": "cisco_ios",
            "device_role": "core_router",
            "site": "HQ",
            "version": "15.6(2)T",
        },
        "BRANCH-B-Switch": {
            "ip_address": "192.168.199.11",
            "os_platform": "cisco_ios",
            "device_role": "access_switch",
            "site": "BRANCH-B",
            "version": "15.2(4.0.55)E",
        },
    }
    query = "แล้วขากลับ BRANCH-B-Switch มา HQ-CORE-RT01 หละ"
    command = _resolve_targeted_command(query, "BRANCH-B-Switch", device_cache, messages=[HumanMessage(content=query)])
    assert command == "ping 10.255.1.11 repeat 2 timeout 1"


def test_return_path_query_is_not_treated_as_multi_device_batch():
    from src.graph.supervisor import _is_multi_device_query

    query = "แล้วขากลับ BRANCH-B-Switch มา HQ-CORE-RT01 หละ"
    assert _is_multi_device_query(query, [HumanMessage(content=query)]) is False


def test_infer_check_type_classifies_ip_sla_queries():
    from src.tools.command_profiles import infer_check_type

    assert infer_check_type("ไปเช็ค BRANCH-A-RTR ว่า ip sla ปกติดีหรือไม่") == "ip_sla_status"
    assert infer_check_type("show ip sla configuration on BRANCH-A-RTR") == "ip_sla_config"


def test_normalize_command_falls_back_to_ip_sla_profile_for_ip_sla_queries():
    from src.tools.command_profiles import normalize_command

    resolution = normalize_command(
        proposed_command="show ip interface brief",
        user_query="ไปเช็ค BRANCH-A-RTR ว่า ip sla ปกติดีหรือไม่",
        device_role="router",
        os_platform="cisco_ios",
        version="15.6(2)T",
    )

    assert resolution.command == "show ip sla summary"
    assert resolution.fallback_used is True
    assert resolution.check_type == "ip_sla_status"


def test_ssh_agent_targeted_resolution_builds_ip_sla_command():
    from src.graph.agents.ssh_agent import _resolve_targeted_command

    command = _resolve_targeted_command(
        "ไปเช็ค BRANCH-A-RTR ว่า ip sla ปกติดีหรือไม่",
        "BRANCH-A-RTR",
        {
            "BRANCH-A-RTR": {
                "ip_address": "10.255.3.101",
                "os_platform": "cisco_ios",
                "device_role": "router",
                "site": "BRANCH-A",
                "version": "15.6(2)T",
            }
        },
    )

    assert command == "show ip sla summary"


def test_analyze_ip_sla_status_extracts_target_and_health():
    from src.graph.troubleshoot.engine import analyze_cli_result

    result = analyze_cli_result(
        device="BRANCH-A-RTR",
        check_type="ip_sla_status",
        command="show ip sla summary",
        raw_output=(
            "[Device: BRANCH-A-RTR | IP: 10.255.3.101 | OS: cisco_ios]\n"
            "IPSLAs Latest Operation Summary\n"
            "Codes: * active, ^ inactive, ~ pending\n\n"
            "ID           Type        Destination       Stats       Return      Last\n"
            "                                           (ms)        Code        Run \n"
            "-----------------------------------------------------------------------\n"
            "*10          icmp-echo   172.16.10.1       RTT=3       OK          4 seconds ago\n"
        ),
        state={
            "observations": {},
            "errors": [],
            "unresolved_gaps": [],
            "current_hypothesis": "",
            "hypothesis": "",
            "planned_checks": [],
            "device_cache": {},
            "extracted_entities": {"target_ip": "", "interfaces": []},
        },
    )

    data = result["observations"]["obs_1"]["data"]
    assert data["ip_sla_target"] == "172.16.10.1"
    assert data["ip_sla_return_code"] == "OK"
    assert "healthy" in result["summary"]


def test_build_initial_plan_for_ip_sla_timeout_starts_with_ip_sla_checks():
    from src.graph.troubleshoot.engine import build_initial_plan, initialize_investigation_state

    state = initialize_investigation_state({
        "messages": [HumanMessage(content="find root cause of IP SLA timeout on BRANCH-A-RTR")],
        "device_cache": {
            "BRANCH-A-RTR": {
                "ip_address": "10.255.3.101",
                "os_platform": "cisco_ios",
                "device_role": "router",
                "site": "BRANCH-A",
                "version": "15.6(2)T",
            },
        },
    })

    plan = build_initial_plan(state)

    assert plan[0]["check_type"] == "ip_sla_status"
    assert any(step["check_type"] == "ip_sla_config" for step in plan)


def test_infer_check_type_classifies_routing_protocol_track_and_default_gateway_queries():
    from src.tools.command_profiles import infer_check_type

    assert infer_check_type("เช็ค routing protocol ของ HQ-CORE-RT01") == "routing_protocols"
    assert infer_check_type("เช็ค track ของ BRANCH-A-RTR") == "track_status"
    assert infer_check_type("เช็ค default gateway ของ BRANCH-B-Switch") == "route_lookup"
    assert infer_check_type("show ip default-gateway") == "route_lookup"


def test_ssh_agent_targeted_resolution_builds_routing_protocol_track_and_default_gateway_commands():
    from src.graph.agents.ssh_agent import _resolve_targeted_command

    device_cache = {
        "HQ-CORE-RT01": {
            "ip_address": "10.255.1.11",
            "os_platform": "cisco_ios",
            "device_role": "core_router",
            "site": "HQ",
            "version": "15.6(2)T",
        },
        "BRANCH-A-RTR": {
            "ip_address": "10.255.3.101",
            "os_platform": "cisco_ios",
            "device_role": "router",
            "site": "BRANCH-A",
            "version": "15.6(2)T",
        },
        "BRANCH-B-Switch": {
            "ip_address": "192.168.199.11",
            "os_platform": "cisco_ios",
            "device_role": "access_switch",
            "site": "BRANCH-B",
            "version": "15.2(4.0.55)E",
        },
    }

    assert _resolve_targeted_command(
        "เช็ค routing protocol ของ HQ-CORE-RT01",
        "HQ-CORE-RT01",
        device_cache,
    ) == "show ip protocols"
    assert _resolve_targeted_command(
        "เช็ค track ของ BRANCH-A-RTR",
        "BRANCH-A-RTR",
        device_cache,
    ) == "show track"
    assert _resolve_targeted_command(
        "เช็ค default gateway ของ BRANCH-B-Switch",
        "BRANCH-B-Switch",
        device_cache,
    ) == "show ip default-gateway"


def test_batch_resolution_prefers_deterministic_profiles_before_llm():
    from src.graph.agents.ssh_agent import _resolve_batch_command

    class ExplodingLLM:
        def invoke(self, messages):
            raise AssertionError("deterministic batch resolution should not need the llm")

    command = _resolve_batch_command(
        "เช็ค routing protocol ของ HQ-CORE-RT01 และ BRANCH-A-RTR ว่าใช้อะไรบ้าง",
        {"hostname": "HQ-CORE-RT01", "os": "cisco_ios", "role": "core_router", "version": "15.6(2)T"},
        {},
        ExplodingLLM(),
    )

    assert command == "show ip protocols"


def test_supervisor_routes_inventory_miss_to_analyst_instead_of_ssh():
    from langchain_core.messages import ToolMessage

    from src.graph.supervisor import _heuristic_route

    device_cache = {
        "BRANCH-A-RTR": {"ip_address": "10.255.3.101", "device_role": "router", "site": "BRANCH-A"},
        "BRANCH-B-RTR": {"ip_address": "10.255.3.102", "device_role": "router", "site": "BRANCH-B"},
    }
    query = "show arp on BRANCH-C-RTR"
    messages = [
        HumanMessage(content=query),
        ToolMessage(content='{"error": "Device not found", "suggestions": ["BRANCH-A-RTR", "BRANCH-B-RTR"]}', tool_call_id="inv-1", name="lookup_device"),
    ]

    assert _heuristic_route(query, device_cache, messages) == "analyst_agent"


def test_ssh_node_returns_pending_question_for_unknown_hostname_in_cache_context():
    from src.graph.agents.ssh_agent import ssh_node

    class DummyTool:
        def invoke(self, args):
            raise AssertionError("unknown device should not execute CLI")

    result = ssh_node(
        {
            "messages": [HumanMessage(content="show arp on BRANCH-C-RTR")],
            "device_cache": {
                "BRANCH-A-RTR": {
                    "ip_address": "10.255.3.101",
                    "os_platform": "cisco_ios",
                    "device_role": "router",
                    "site": "BRANCH-A",
                    "version": "15.6(2)T",
                },
                "BRANCH-B-RTR": {
                    "ip_address": "10.255.3.102",
                    "os_platform": "cisco_ios",
                    "device_role": "router",
                    "site": "BRANCH-B",
                    "version": "15.6(2)T",
                },
            },
        },
        llm=object(),
        run_cli_tool=DummyTool(),
    )

    assert result["messages"]
    assert result["pending_questions"]
    assert "[Pending Question]" in result["messages"][0].content
    assert "branch-c-rtr" in result["pending_questions"][0].lower()


def test_build_initial_plan_for_track_focus_starts_with_track_and_ip_sla_checks():
    from src.graph.troubleshoot.engine import build_initial_plan, initialize_investigation_state

    state = initialize_investigation_state({
        "messages": [HumanMessage(content="เช็ค track ของ BRANCH-A-RTR ว่าปกติไหม")],
        "device_cache": {
            "BRANCH-A-RTR": {
                "ip_address": "10.255.3.101",
                "os_platform": "cisco_ios",
                "device_role": "router",
                "site": "BRANCH-A",
                "version": "15.6(2)T",
            },
        },
    })

    plan = build_initial_plan(state)

    assert plan[0]["check_type"] == "track_status"
    assert any(step["check_type"] == "ip_sla_status" for step in plan)


def test_condense_cli_output_extracts_default_gateway_value():
    from src.graph.agents.analyst_agent import _condense_cli_output

    raw = (
        "[Device: BRANCH-B-Switch | IP: 192.168.199.11 | OS: cisco_ios]\n"
        "BRANCH-B-Switch# show ip default-gateway\n"
        "192.168.199.1\n"
    )

    condensed = _condense_cli_output(raw)

    assert "Default Gateway: 192.168.199.1" in condensed


def test_condense_cli_output_extracts_track_summary():
    from src.graph.agents.analyst_agent import _condense_cli_output

    raw = (
        "[Device: BRANCH-A-RTR | IP: 10.255.3.101 | OS: cisco_ios]\n"
        "BRANCH-A-RTR# show track\n"
        "Track 10\n"
        "  IP SLA 10 reachability\n"
        "  Reachability is Up\n"
        "    2 changes, last change 04:47:58\n"
        "  Latest operation return code: OK\n"
        "  Latest RTT (millisecs) 5\n"
        "  Tracked by:\n"
        "    Static IP Routing 0\n"
    )

    condensed = _condense_cli_output(raw)

    assert "Track ID: 10" in condensed
    assert "Reachability: UP" in condensed
    assert "Latest Return Code: OK" in condensed


def test_condense_cli_output_extracts_ip_sla_configuration_summary():
    from src.graph.agents.analyst_agent import _condense_cli_output

    raw = (
        "[Device: BRANCH-A-RTR | IP: 10.255.3.101 | OS: cisco_ios]\n"
        "BRANCH-A-RTR# show ip sla configuration\n"
        "Entry number: 10\n"
        "Type of operation to perform: icmp-echo\n"
        "Target address/Source interface: 172.16.10.1/Tunnel10\n"
        "Schedule:\n"
        "   Operation frequency (seconds): 5  (not considered if randomly scheduled)\n"
        "   Status of entry (SNMP RowStatus): Active\n"
        "Threshold (milliseconds): 5000\n"
    )

    condensed = _condense_cli_output(raw)

    assert "IP SLA Entry: 10" in condensed
    assert "Target: 172.16.10.1" in condensed
    assert "Source Interface: Tunnel10" in condensed


def test_condense_cli_output_extracts_bgp_neighbor_summary():
    from src.graph.agents.analyst_agent import _condense_cli_output

    raw = (
        "[Device: HQ-CORE-RT01 | IP: 10.255.1.11 | OS: cisco_ios]\n"
        "HQ-CORE-RT01# show ip bgp neighbors 100.66.0.2\n"
        "BGP neighbor is 100.66.0.2,  remote AS 64512, external link\n"
        " Description: EBGP-TO-ISP\n"
        "  BGP version 4, remote router ID 203.0.113.1\n"
        "  BGP state = Established, up for 04:48:02\n"
        "    Prefixes Current:               0          2 (Consumes 160 bytes)\n"
        "  Interface associated: GigabitEthernet0/3 (peering address in same link)\n"
    )

    condensed = _condense_cli_output(raw)

    assert "Neighbor: 100.66.0.2" in condensed
    assert "Remote AS: 64512" in condensed
    assert "Received Prefixes: 2" in condensed


def test_deterministic_direct_summary_formats_default_gateway_response():
    from langchain_core.messages import AIMessage, ToolMessage

    from src.graph.agents.analyst_agent import _deterministic_direct_summary

    messages = [
        HumanMessage(content="check default route on BRANCH-B-Switch"),
        AIMessage(content="", tool_calls=[{
            "name": "run_cli",
            "args": {"host": "BRANCH-B-Switch", "command": "show ip default-gateway"},
            "id": "call-1",
        }]),
        ToolMessage(
            content="[Device: BRANCH-B-Switch | IP: 192.168.199.11 | OS: cisco_ios]\n192.168.199.1",
            tool_call_id="call-1",
            name="run_cli",
        ),
    ]

    summary = _deterministic_direct_summary(messages)

    assert summary is not None
    assert "192.168.199.1" in summary
    assert "Not Configured" not in summary


def test_deterministic_direct_summary_formats_ip_sla_configuration_response():
    from langchain_core.messages import AIMessage, ToolMessage

    from src.graph.agents.analyst_agent import _deterministic_direct_summary

    messages = [
        HumanMessage(content="ดู config ของ ip sla บน BRANCH-A-RTR"),
        AIMessage(content="", tool_calls=[{
            "name": "run_cli",
            "args": {"host": "BRANCH-A-RTR", "command": "show ip sla configuration"},
            "id": "call-1",
        }]),
        ToolMessage(
            content=(
                "[Device: BRANCH-A-RTR | IP: 10.255.3.101 | OS: cisco_ios]\n"
                "Entry number: 10\n"
                "Type of operation to perform: icmp-echo\n"
                "Target address/Source interface: 172.16.10.1/Tunnel10\n"
                "Schedule:\n"
                "   Operation frequency (seconds): 5  (not considered if randomly scheduled)\n"
                "   Status of entry (SNMP RowStatus): Active\n"
                "Threshold (milliseconds): 5000\n"
            ),
            tool_call_id="call-1",
            name="run_cli",
        ),
    ]

    summary = _deterministic_direct_summary(messages)

    assert summary is not None
    assert "172.16.10.1" in summary
    assert "Tunnel10" in summary
    assert "IP SLA Configuration" in summary
