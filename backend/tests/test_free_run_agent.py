from __future__ import annotations

import sys
import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


def test_sanitize_messages_adds_run_cli_failure_context():
    from src.graph.agents.free_run_agent import _sanitize_messages

    messages = [
        ToolMessage(
            content="[TIMEOUT ERROR] 10.255.2.22 (OS: cisco_ios): Timed out after 30s",
            tool_call_id="call-1",
            name="run_cli",
            additional_kwargs={
                "tool_args": {
                    "host": "HQ-DIST-GW02",
                    "command": "show version",
                },
                "tool_status": "error",
            },
        )
    ]

    clean = _sanitize_messages(messages)

    assert len(clean) == 1
    rendered = clean[0].content
    assert "[Tool Result — run_cli]" in rendered
    assert "status=error" in rendered
    assert "host=HQ-DIST-GW02" in rendered
    assert "command=show version" in rendered
    assert "[TIMEOUT ERROR]" in rendered


def test_sanitize_messages_includes_run_diagnostic_command_context():
    from src.graph.agents.free_run_agent import _sanitize_messages

    messages = [
        ToolMessage(
            content=(
                "[DIAGNOSTIC] kind=traceroute requested_target=BRANCH-A-Switch\n"
                "[EXECUTED COMMAND] traceroute 192.168.99.11\n"
                "[Device: HQ-CORE-RT01 | IP: 10.255.1.11 | OS: cisco_ios]\n"
                "  1 172.16.10.2 1 msec 2 msec 2 msec\n"
                "  2 192.168.99.11 3 msec 3 msec 3 msec\n"
                "[TRACE TARGET CONTEXT]\n"
                "- exact owner(s): BRANCH-A-Switch Vlan99 [svi, role=access_switch, network=192.168.99.0/24, desc=MANAGEMENT]\n"
                "- same-network candidate(s): BRANCH-A-RTR GigabitEthernet0/3.99 [subinterface, role=router, network=192.168.99.0/24, desc=VLAN99 MANAGEMENT-A (SW-MGMT)]\n"
                "[TRACE HOP ANNOTATION]\n"
                "- hop 1: 172.16.10.2 -> exact=BRANCH-A-RTR Tunnel10 [tunnel, role=router, network=172.16.10.0/30, desc=HQ-DIST1 via VLAN101 (PRIMARY)]\n"
            ),
            tool_call_id="call-1",
            name="run_diagnostic",
            additional_kwargs={
                "tool_args": {
                    "host": "HQ-CORE-RT01",
                    "kind": "traceroute",
                    "target": "BRANCH-A-Switch",
                },
                "tool_status": "success",
                "executed_command": "traceroute 192.168.99.11",
            },
        )
    ]

    clean = _sanitize_messages(messages)

    assert len(clean) == 1
    rendered = clean[0].content
    assert "status=success" in rendered
    assert "command=traceroute 192.168.99.11" in rendered
    assert "kind=traceroute" in rendered
    assert "host=HQ-CORE-RT01" in rendered
    assert "BRANCH-A-RTR Tunnel10" in rendered
    assert "BRANCH-A-Switch Vlan99" in rendered


def test_free_run_node_attaches_run_cli_metadata_for_failures():
    from src.graph.agents.free_run_agent import free_run_node

    class DummyLLM:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, _tools):
            return self

        def invoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "run_cli",
                            "args": {
                                "host": "HQ-DIST-GW02",
                                "command": "show version",
                            },
                            "id": "call-1",
                        }
                    ],
                )
            return AIMessage(content="summary", tool_calls=[])

    class DummyRunCliTool:
        def invoke(self, _args):
            return "[TIMEOUT ERROR] 10.255.2.22 (OS: cisco_ios): Timed out after 30s"

    class DummyAnswerLLM:
        def invoke(self, _messages):
            return AIMessage(content="SSH เข้าได้ 0/1 อุปกรณ์ และเข้าไม่ได้ 1/1 คือ HQ-DIST-GW02")

    result = free_run_node(
        {
            "messages": [HumanMessage(content="test ssh เข้าอุปกรณ์ทุกตัว")],
            "device_cache": {
                "HQ-DIST-GW02": {
                    "ip_address": "10.255.2.22",
                    "os_platform": "cisco_ios",
                    "device_role": "dist_switch",
                    "site": "HQ",
                    "version": "15.6(2)T",
                }
            },
        },
        llm=DummyLLM(),
        answer_llm=DummyAnswerLLM(),
        run_cli_tool=DummyRunCliTool(),
    )

    tool_messages = [
        msg
        for msg in result["messages"]
        if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "run_cli"
    ]
    assert len(tool_messages) == 1

    tool_message = tool_messages[0]
    assert tool_message.additional_kwargs["tool_status"] == "error"
    assert tool_message.additional_kwargs["tool_args"]["host"] == "HQ-DIST-GW02"
    assert tool_message.additional_kwargs["tool_args"]["command"] == "show version"
    assert tool_message.content.startswith("[TIMEOUT ERROR]")

    assert isinstance(result["messages"][-1], AIMessage)
    assert "HQ-DIST-GW02" in result["messages"][-1].content
    assert result["messages"][-1].additional_kwargs["phase"] == "final_synthesis"
    assert len([msg for msg in result["messages"] if isinstance(msg, AIMessage) and not msg.tool_calls]) == 1


def test_ssh_prompt_requires_explicit_batch_failure_summary():
    from src.prompts.ssh import SSH_PROMPT

    assert "if some devices succeeded and some failed, say both counts explicitly" in SSH_PROMPT
    assert "name every failed device explicitly and give the failure reason" in SSH_PROMPT
    assert 'answer in this\n     format: reachable X/Y devices, unreachable N devices' in SSH_PROMPT
    assert "do not say all devices are reachable" in SSH_PROMPT
    assert "answer the user's actual question, not the raw CLI output" in SSH_PROMPT
    assert "First decide the answer mode from the user's request" in SSH_PROMPT
    assert "For fleet SSH reachability checks:" in SSH_PROMPT
    assert "sound like a senior network engineer writing an operational assessment" in SSH_PROMPT
    assert "overall status / verdict" in SSH_PROMPT
    assert "If the user's request is in Thai, answer in Thai." in SSH_PROMPT
    assert "If the user asks about device relationships, topology, dependencies" in SSH_PROMPT
    assert "show cdp neighbors detail" in SSH_PROMPT
    assert "show running-config | section" in SSH_PROMPT
    assert 'If the user asks "can you ..." and the requested operation is executable now' in SSH_PROMPT
    assert "Do not ask for scope again if the user already specified one device" in SSH_PROMPT
    assert 'User asks: "คุณสามารถ show run แล้วหาความสัมพันธ์ของอุปกรณ์ทุกตัวได้หรือไม่"' in SSH_PROMPT
    assert "describe the topology as explicit" in SSH_PROMPT
    assert "device-to-device links" in SSH_PROMPT
    assert "`Device-A <-> Device-B`" in SSH_PROMPT
    assert "Logical relationships" in SSH_PROMPT
    assert "physical and logical topology" in SSH_PROMPT
    assert "if the user explicitly asks for logical topology" in SSH_PROMPT
    assert "logical evidence set before" in SSH_PROMPT
    assert "stopping when the user explicitly asked for logical topology" in SSH_PROMPT
    assert "Confirmed physical links" in SSH_PROMPT
    assert "Topology interpretation" in SSH_PROMPT
    assert "if logical evidence exists for only part of the network" in SSH_PROMPT.lower()
    assert "one-link-per-line lists" in SSH_PROMPT
    assert "avoid large ASCII topology diagrams" in SSH_PROMPT
    assert "for `Confirmed physical links`, prefer markdown bullets" in SSH_PROMPT
    assert "for `Topology interpretation`, prefer short bullets grouped by layer" in SSH_PROMPT
    assert "for `Logical relationships`, prefer bullets" in SSH_PROMPT
    assert "for `Limitations`, prefer short bullet points" in SSH_PROMPT
    assert "Inventory tool results are not operational proof." in SSH_PROMPT
    assert "Never infer reachability, uptime, health, or readiness from inventory alone." in SSH_PROMPT
    assert "show ip default-gateway" in SSH_PROMPT
    assert "show ip protocols" in SSH_PROMPT
    assert "show ip sla summary" in SSH_PROMPT
    assert "show ip sla configuration" in SSH_PROMPT
    assert "show track" in SSH_PROMPT
    assert "show interfaces trunk" in SSH_PROMPT
    assert "claiming devices are reachable, healthy, or ready from inventory-only" in SSH_PROMPT
    assert "For follow-up requests in the same session" in SSH_PROMPT
    assert "prefer continuing from prior" in SSH_PROMPT
    assert "prefer `show ip protocols` and protocol neighbor" in SSH_PROMPT
    assert "do not present route-table next hops as confirmed protocol adjacencies" in SSH_PROMPT
    assert "`show ip protocols` proves configured protocols or redistribution" in SSH_PROMPT


def test_ssh_compact_prompt_preserves_topology_scope_guidance():
    from src.prompts.ssh_compact import SSH_COMPACT_PROMPT

    assert '"all devices" / "ทุกตัว" → list_all_devices first' in SSH_COMPACT_PROMPT
    assert "topology / relationships / dependencies across all devices" in SSH_COMPACT_PROMPT
    assert "do not stop after checking only a subset" in SSH_COMPACT_PROMPT
    assert "gather at least one logical" in SSH_COMPACT_PROMPT
    assert "Do not claim a full topology from router-only evidence" in SSH_COMPACT_PROMPT
    assert "show cdp neighbors detail" in SSH_COMPACT_PROMPT
    assert "show ip interface brief" in SSH_COMPACT_PROMPT
    assert "show ip bgp summary" in SSH_COMPACT_PROMPT
    assert "show ip default-gateway" in SSH_COMPACT_PROMPT
    assert "show ip protocols" in SSH_COMPACT_PROMPT
    assert "show ip sla summary" in SSH_COMPACT_PROMPT
    assert "show track" in SSH_COMPACT_PROMPT
    assert "Start with the simplest direct command" in SSH_COMPACT_PROMPT
    assert "reuse already collected evidence" in SSH_COMPACT_PROMPT
    assert "Do not restart the whole topology/protocol sweep" in SSH_COMPACT_PROMPT
    assert "prefer `show ip protocols` and protocol neighbor" in SSH_COMPACT_PROMPT
    assert "`show ip protocols` proves protocol presence/configuration" in SSH_COMPACT_PROMPT


def test_free_run_node_injects_routing_context_and_prefills_candidates():
    from src.graph.agents.free_run_agent import free_run_node

    class DummyLLM:
        def __init__(self):
            self.calls = []

        def bind_tools(self, _tools):
            return self

        def invoke(self, messages):
            self.calls.append(messages)
            system_text = str(messages[0].content)
            assert "ACTIVE ROUTING CONTEXT" in system_text
            assert "HQ-CORE-RT01" in system_text
            assert "BRANCH-A-RTR" in system_text
            assert "show ip route 192.168.99.11" in system_text
            return AIMessage(content="เริ่มเช็ค route จาก HQ-CORE-RT01 ก่อน", tool_calls=[])

    class DummyAnswerLLM:
        def invoke(self, _messages):
            return AIMessage(content="unused")

    class DummyRunCliTool:
        def invoke(self, _args):
            return "unused"

    result = free_run_node(
        {
            "messages": [HumanMessage(content="route ไป 192.168.99.11 จาก HQ-CORE-RT01 ผ่านไหน")],
            "device_cache": {},
        },
        llm=DummyLLM(),
        answer_llm=DummyAnswerLLM(),
        run_cli_tool=DummyRunCliTool(),
    )

    assert "HQ-CORE-RT01" in result["device_cache"]
    assert "BRANCH-A-RTR" in result["device_cache"]
    assert isinstance(result["messages"][-1], AIMessage)
    assert "HQ-CORE-RT01" in result["messages"][-1].content


def test_synthesize_final_answer_uses_evidence_not_raw_dump():
    from src.graph.agents.free_run_agent import _synthesize_final_answer
    from langchain_core.messages import SystemMessage

    class DummyAnswerLLM:
        def __init__(self):
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages)
            return AIMessage(content="SSH เข้าได้ 8/9 ตัว\nเข้าไม่ได้ 1 ตัวคือ HQ-DIST-GW02 เพราะ timeout")

    answer_llm = DummyAnswerLLM()
    result = _synthesize_final_answer(
        answer_llm=answer_llm,
        system_msg=SystemMessage(content="system"),
        device_cache={},
        original_context=[HumanMessage(content="test ssh เข้าทุกตัวครบไหม")],
        session_messages=[HumanMessage(content="test ssh เข้าทุกตัวครบไหม")],
        result_messages=[
            ToolMessage(
                content="[TIMEOUT ERROR] 10.255.2.22 (OS: cisco_ios): Timed out after 30s",
                tool_call_id="call-1",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "HQ-DIST-GW02", "command": "show version"},
                    "tool_status": "error",
                },
            )
        ],
        user_query="test ssh เข้าทุกตัวครบไหม",
    )

    assert result is not None
    assert "HQ-DIST-GW02" in result.content
    assert any(
        "Final answer required" in str(msg.content)
        for call in answer_llm.calls
        for msg in call
    )
    assert any(
        "run_cli_error=1" in str(msg.content)
        for call in answer_llm.calls
        for msg in call
    )


def test_synthesize_final_answer_adds_inventory_only_guardrail():
    from src.graph.agents.free_run_agent import _synthesize_final_answer
    from langchain_core.messages import SystemMessage

    class DummyAnswerLLM:
        def __init__(self):
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages)
            return AIMessage(content="พบอุปกรณ์ทั้งหมด 9 เครื่องตาม inventory")

    answer_llm = DummyAnswerLLM()
    result = _synthesize_final_answer(
        answer_llm=answer_llm,
        system_msg=SystemMessage(content="system"),
        device_cache={"R1": {"ip_address": "10.0.0.1"}},
        original_context=[HumanMessage(content="ขอรายชื่ออุปกรณ์ทั้งหมดหน่อย")],
        session_messages=[HumanMessage(content="ขอรายชื่ออุปกรณ์ทั้งหมดหน่อย")],
        result_messages=[
            ToolMessage(
                content='[{"hostname":"R1","ip_address":"10.0.0.1","os_platform":"cisco_ios","device_role":"router","site":"HQ","version":"15.6(2)T"}]',
                tool_call_id="call-1",
                name="list_all_devices",
                additional_kwargs={"tool_args": {}, "tool_status": "success"},
            )
        ],
        user_query="ขอรายชื่ออุปกรณ์ทั้งหมดหน่อย",
    )

    assert result is not None
    assert any(
        "inventory-only" in str(msg.content)
        and "Do NOT claim devices are reachable" in str(msg.content)
        for call in answer_llm.calls
        for msg in call
    )


def test_build_evidence_digest_counts_run_cli_results():
    from src.graph.agents.free_run_agent import _build_evidence_digest

    digest = _build_evidence_digest(
        [
            ToolMessage(
                content="[Device: LAB-MGMT-BR01 | IP: 10.255.0.1 | OS: cisco_ios]\noutput",
                tool_call_id="call-1",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "LAB-MGMT-BR01", "command": "show version"},
                    "tool_status": "success",
                },
            ),
            ToolMessage(
                content="[Device: HQ-CORE-RT01 | IP: 10.255.1.11 | OS: cisco_ios]\nDevice ID: HQ-DIST-GW01.local.lab\nInterface: GigabitEthernet0/0,  Port ID (outgoing port): GigabitEthernet0/1",
                tool_call_id="call-2",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "HQ-CORE-RT01", "command": "show cdp neighbors detail"},
                    "tool_status": "success",
                },
            ),
            ToolMessage(
                content="[TIMEOUT ERROR] 10.255.2.22 (OS: cisco_ios): Timed out after 30s",
                tool_call_id="call-2",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "HQ-DIST-GW02", "command": "show version"},
                    "tool_status": "error",
                },
            ),
        ]
    )

    assert "run_cli_total=3" in digest
    assert "run_cli_success=2" in digest
    assert "run_cli_error=1" in digest
    assert "host=HQ-DIST-GW02, status=error" in digest
    assert "[Adjacency Digest]" in digest
    assert "HQ-CORE-RT01 <-> HQ-DIST-GW01" in digest


def test_build_evidence_digest_includes_logical_relationships():
    from src.graph.agents.free_run_agent import _build_evidence_digest

    digest = _build_evidence_digest(
        [
            ToolMessage(
                content=(
                    "[Device: HQ-CORE-RT01 | IP: 10.255.1.11 | OS: cisco_ios]\n"
                    "Neighbor        V    AS MsgRcvd MsgSent TblVer InQ OutQ Up/Down  State/PfxRcd\n"
                    "100.66.0.2      4 64512      86      84      3   0    0 01:14:21 2\n"
                ),
                tool_call_id="call-1",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "HQ-CORE-RT01", "command": "show ip bgp summary"},
                    "tool_status": "success",
                },
            ),
            ToolMessage(
                content=(
                    "[Device: BRANCH-B-RTR | IP: 10.255.3.102 | OS: cisco_ios]\n"
                    "H   Address                 Interface              Hold Uptime   SRTT   RTO  Q  Seq\n"
                    "0   10.255.3.1              Gi0/0                   12   00:10:11   12   100  0  4\n"
                ),
                tool_call_id="call-2",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "BRANCH-B-RTR", "command": "show ip eigrp neighbors"},
                    "tool_status": "success",
                },
            ),
        ]
    )

    assert "[Logical Relationship Digest]" in digest
    assert "protocol=bgp, HQ-CORE-RT01 <-> 100.66.0.2" in digest
    assert "protocol=eigrp, BRANCH-B-RTR <-> 10.255.3.1" in digest


def test_build_evidence_digest_includes_traceroute_symmetry_digest():
    from src.graph.agents.free_run_agent import _build_evidence_digest

    digest = _build_evidence_digest(
        [
            ToolMessage(
                content=(
                    "[DIAGNOSTIC] kind=traceroute requested_target=BRANCH-A-Switch\n"
                    "[EXECUTED COMMAND] traceroute 192.168.99.11\n"
                    "[RESOLVED TARGET] BRANCH-A-Switch (192.168.99.11)\n"
                    "[Device: HQ-CORE-RT01 | IP: 10.255.1.11 | OS: cisco_ios]\n"
                    "  1 10.255.10.14 1 msec\n"
                    "  2 172.16.10.2 2 msec\n"
                    "  3 192.168.99.11 3 msec\n"
                    "[TRACE HOP ANNOTATION]\n"
                    "- hop 1: 10.255.10.14 -> exact=HQ-DIST-GW01 GigabitEthernet0/1 [routed]; same_network=HQ-CORE-RT01 GigabitEthernet0/2 [routed]\n"
                    "- hop 2: 172.16.10.2 -> exact=BRANCH-A-RTR Tunnel10 [tunnel]; same_network=HQ-DIST-GW01 Tunnel10 [tunnel]\n"
                    "- hop 3: 192.168.99.11 -> exact=BRANCH-A-Switch Vlan99 [svi]; same_network=BRANCH-A-RTR GigabitEthernet0/3.99 [subinterface]\n"
                ),
                tool_call_id="call-1",
                name="run_diagnostic",
                additional_kwargs={
                    "tool_args": {"host": "HQ-CORE-RT01", "kind": "traceroute", "target": "BRANCH-A-Switch"},
                    "tool_status": "success",
                    "executed_command": "traceroute 192.168.99.11",
                },
            ),
            ToolMessage(
                content=(
                    "[DIAGNOSTIC] kind=traceroute requested_target=10.255.1.11\n"
                    "[EXECUTED COMMAND] traceroute 10.255.1.11\n"
                    "[Device: BRANCH-A-Switch | IP: 192.168.99.11 | OS: cisco_ios]\n"
                    "  1 192.168.99.1 1 msec\n"
                    "  2 172.16.10.1 2 msec\n"
                    "  3 10.255.10.13 3 msec\n"
                    "[TRACE TARGET CONTEXT]\n"
                    "- exact owner(s): HQ-CORE-RT01 Loopback0 [loopback]\n"
                    "[TRACE HOP ANNOTATION]\n"
                    "- hop 1: 192.168.99.1 -> exact=BRANCH-A-RTR GigabitEthernet0/3.99 [subinterface]; same_network=BRANCH-A-Switch Vlan99 [svi]\n"
                    "- hop 2: 172.16.10.1 -> exact=HQ-DIST-GW01 Tunnel10 [tunnel]; same_network=BRANCH-A-RTR Tunnel10 [tunnel]\n"
                    "- hop 3: 10.255.10.13 -> exact=HQ-CORE-RT01 GigabitEthernet0/2 [routed]; same_network=HQ-DIST-GW01 GigabitEthernet0/1 [routed]\n"
                ),
                tool_call_id="call-2",
                name="run_diagnostic",
                additional_kwargs={
                    "tool_args": {"host": "BRANCH-A-Switch", "kind": "traceroute", "target": "10.255.1.11"},
                    "tool_status": "success",
                    "executed_command": "traceroute 10.255.1.11",
                },
            ),
        ]
    )

    assert "[Traceroute Symmetry Digest]" in digest
    assert "forward_path_hosts=HQ-CORE-RT01 -> HQ-DIST-GW01 -> BRANCH-A-RTR -> BRANCH-A-Switch" in digest
    assert "reverse_path_hosts=BRANCH-A-Switch -> BRANCH-A-RTR -> HQ-DIST-GW01 -> HQ-CORE-RT01" in digest
    assert "forward_hops=1:10.255.10.14/HQ-DIST-GW01 ; 2:172.16.10.2/BRANCH-A-RTR ; 3:192.168.99.11/BRANCH-A-Switch" in digest
    assert "reverse_hops=1:192.168.99.1/BRANCH-A-RTR ; 2:172.16.10.1/HQ-DIST-GW01 ; 3:10.255.10.13/HQ-CORE-RT01" in digest
    assert "verdict=likely_symmetric" in digest
    assert "mirrored_hops=" in digest


def test_free_run_node_reminds_llm_when_logical_topology_requested_without_logical_evidence():
    from src.graph.agents.free_run_agent import free_run_node

    class DummyLLM:
        def __init__(self):
            self.calls = []

        def bind_tools(self, _tools):
            return self

        def invoke(self, messages):
            self.calls.append(messages)
            call_no = len(self.calls)
            if call_no == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "list_all_devices", "args": {}, "id": "call-1"},
                        {"name": "run_cli", "args": {"host": "R1", "command": "show cdp neighbors detail"}, "id": "call-2"},
                    ],
                )
            if call_no == 2:
                return AIMessage(content="summary so far", tool_calls=[])
            if call_no == 3:
                reminder_texts = [str(m.content) for m in messages if hasattr(m, "content")]
                assert any("Coverage reminder" in text for text in reminder_texts)
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "run_cli", "args": {"host": "R1", "command": "show ip bgp summary"}, "id": "call-3"},
                    ],
                )
            return AIMessage(content="summary", tool_calls=[])

    class DummyRunCliTool:
        def invoke(self, args):
            if args["command"] == "show cdp neighbors detail":
                return "[Device: R1 | IP: 10.0.0.1 | OS: cisco_ios]\nDevice ID: R2.local.lab"
            return (
                "[Device: R1 | IP: 10.0.0.1 | OS: cisco_ios]\n"
                "Neighbor        V    AS MsgRcvd MsgSent TblVer InQ OutQ Up/Down  State/PfxRcd\n"
                "100.66.0.2      4 64512      86      84      3   0    0 01:14:21 2\n"
            )

    class DummyAnswerLLM:
        def invoke(self, _messages):
            return AIMessage(content="มีทั้ง physical และ logical relationships")

    result = free_run_node(
        {
            "messages": [HumanMessage(content="ช่วยทำ physical and logical topology ของอุปกรณ์ทุกตัวให้หน่อย")],
            "device_cache": {
                "R1": {
                    "ip_address": "10.0.0.1",
                    "os_platform": "cisco_ios",
                    "device_role": "core_router",
                    "site": "HQ",
                    "version": "15.6(2)T",
                }
            },
        },
        llm=DummyLLM(),
        answer_llm=DummyAnswerLLM(),
        run_cli_tool=DummyRunCliTool(),
    )

    tool_messages = [
        msg
        for msg in result["messages"]
        if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "run_cli"
    ]
    assert any(msg.additional_kwargs["tool_args"]["command"] == "show ip bgp summary" for msg in tool_messages)


def test_free_run_node_reminds_llm_when_all_device_topology_scope_is_partially_checked():
    from src.graph.agents.free_run_agent import free_run_node

    class DummyLLM:
        def __init__(self):
            self.calls = []

        def bind_tools(self, _tools):
            return self

        def invoke(self, messages):
            self.calls.append(messages)
            call_no = len(self.calls)
            if call_no == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "run_cli", "args": {"host": "R1", "command": "show cdp neighbors detail"}, "id": "call-1"},
                    ],
                )
            if call_no == 2:
                return AIMessage(content="summary so far", tool_calls=[])
            if call_no == 3:
                reminder_texts = [str(m.content) for m in messages if hasattr(m, "content")]
                assert any("Devices still without direct CLI evidence: R2, R3" in text for text in reminder_texts)
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "run_cli", "args": {"host": "R2", "command": "show cdp neighbors detail"}, "id": "call-2"},
                        {"name": "run_cli", "args": {"host": "R3", "command": "show cdp neighbors detail"}, "id": "call-3"},
                    ],
                )
            return AIMessage(content="summary", tool_calls=[])

    class DummyRunCliTool:
        def invoke(self, args):
            return f"[Device: {args['host']} | IP: 10.0.0.1 | OS: cisco_ios]\nDevice ID: peer"

    class DummyAnswerLLM:
        def invoke(self, _messages):
            return AIMessage(content="มี evidence ครบทุกตัวใน scope แล้ว")

    result = free_run_node(
        {
            "messages": [HumanMessage(content="ช่วยหาความสัมพันธ์ของอุปกรณ์ทุกตัว")],
            "device_cache": {
                "R1": {
                    "ip_address": "10.0.0.1",
                    "os_platform": "cisco_ios",
                    "device_role": "core_router",
                    "site": "HQ",
                    "version": "15.6(2)T",
                },
                "R2": {
                    "ip_address": "10.0.0.2",
                    "os_platform": "cisco_ios",
                    "device_role": "dist_switch",
                    "site": "HQ",
                    "version": "15.6(2)T",
                },
                "R3": {
                    "ip_address": "10.0.0.3",
                    "os_platform": "cisco_ios",
                    "device_role": "access_switch",
                    "site": "BRANCH-A",
                    "version": "15.2(4.0.55)E",
                },
            },
        },
        llm=DummyLLM(),
        answer_llm=DummyAnswerLLM(),
        run_cli_tool=DummyRunCliTool(),
    )

    tool_messages = [
        msg
        for msg in result["messages"]
        if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "run_cli"
    ]
    assert len(tool_messages) == 3
    assert {msg.additional_kwargs["tool_args"]["host"] for msg in tool_messages} == {"R1", "R2", "R3"}


def test_free_run_node_uses_prior_session_cli_evidence_for_topology_coverage():
    from src.graph.agents.free_run_agent import free_run_node

    class DummyLLM:
        def __init__(self):
            self.calls = []

        def bind_tools(self, _tools):
            return self

        def invoke(self, messages):
            self.calls.append(messages)
            call_no = len(self.calls)
            if call_no == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "run_cli", "args": {"host": "R2", "command": "show ip protocols"}, "id": "call-1"},
                    ],
                )
            if call_no == 2:
                return AIMessage(content="summary so far", tool_calls=[])
            if call_no == 3:
                reminder_texts = [str(m.content) for m in messages if hasattr(m, "content")]
                assert any("Devices still without direct CLI evidence: R3" in text for text in reminder_texts)
                assert not any("R1, R3" in text for text in reminder_texts)
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "run_cli", "args": {"host": "R3", "command": "show ip default-gateway"}, "id": "call-2"},
                    ],
                )
            return AIMessage(content="summary", tool_calls=[])

    class DummyRunCliTool:
        def invoke(self, args):
            return f"[Device: {args['host']} | IP: 10.0.0.1 | OS: cisco_ios]\noutput"

    class DummyAnswerLLM:
        def invoke(self, _messages):
            return AIMessage(content="ตอนนี้มี direct CLI evidence ครบทั้ง session แล้ว")

    result = free_run_node(
        {
            "messages": [
                HumanMessage(content="ช่วยหาความสัมพันธ์ของอุปกรณ์ทุกตัว"),
                ToolMessage(
                    content="[Device: R1 | IP: 10.0.0.1 | OS: cisco_ios]\nDevice ID: R2",
                    tool_call_id="prev-call-1",
                    name="run_cli",
                    additional_kwargs={
                        "tool_args": {"host": "R1", "command": "show cdp neighbors detail"},
                        "tool_status": "success",
                    },
                ),
                AIMessage(content="ก่อนหน้านี้เช็ค R1 ไปแล้ว"),
                    HumanMessage(content="ช่วยทำ logical topology ของอุปกรณ์ทุกตัวต่อให้ครบ"),
            ],
            "device_cache": {
                "R1": {
                    "ip_address": "10.0.0.1",
                    "os_platform": "cisco_ios",
                    "device_role": "core_router",
                    "site": "HQ",
                    "version": "15.6(2)T",
                },
                "R2": {
                    "ip_address": "10.0.0.2",
                    "os_platform": "cisco_ios",
                    "device_role": "dist_switch",
                    "site": "HQ",
                    "version": "15.6(2)T",
                },
                "R3": {
                    "ip_address": "10.0.0.3",
                    "os_platform": "cisco_ios",
                    "device_role": "access_switch",
                    "site": "BRANCH-A",
                    "version": "15.2(4.0.55)E",
                },
            },
        },
        llm=DummyLLM(),
        answer_llm=DummyAnswerLLM(),
        run_cli_tool=DummyRunCliTool(),
    )

    tool_messages = [
        msg
        for msg in result["messages"]
        if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "run_cli"
    ]
    assert {msg.additional_kwargs["tool_args"]["host"] for msg in tool_messages} == {"R2", "R3"}


def test_missing_logical_hosts_requires_protocol_evidence_and_skips_access_switches():
    from src.graph.agents.free_run_agent import _missing_logical_hosts

    missing = _missing_logical_hosts(
        {
            "R1": {"device_role": "router"},
            "R2": {"device_role": "dist_switch"},
            "SW1": {"device_role": "access_switch"},
        },
        [
            ToolMessage(
                content="[Device: R1 | IP: 10.0.0.1 | OS: cisco_ios]\nRouting Protocol is \"ospf 1\"",
                tool_call_id="call-1",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "R1", "command": "show ip protocols"},
                    "tool_status": "success",
                },
            ),
            ToolMessage(
                content="[Device: R2 | IP: 10.0.0.2 | OS: cisco_ios]\nO 10.1.0.0/16 [110/2] via 10.0.0.1",
                tool_call_id="call-2",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "R2", "command": "show ip route"},
                    "tool_status": "success",
                },
            ),
        ],
    )

    assert missing == ["R2"]


def test_free_run_node_reminds_llm_when_logical_topology_lacks_host_level_protocol_coverage():
    from src.graph.agents.free_run_agent import free_run_node

    class DummyLLM:
        def __init__(self):
            self.calls = []

        def bind_tools(self, _tools):
            return self

        def invoke(self, messages):
            self.calls.append(messages)
            call_no = len(self.calls)
            if call_no == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "run_cli", "args": {"host": "R1", "command": "show ip protocols"}, "id": "call-1"},
                        {"name": "run_cli", "args": {"host": "R2", "command": "show ip route"}, "id": "call-2"},
                    ],
                )
            if call_no == 2:
                return AIMessage(content="summary so far", tool_calls=[])
            if call_no == 3:
                reminder_texts = [str(m.content) for m in messages if hasattr(m, "content")]
                assert any("direct logical/control-plane evidence" in text for text in reminder_texts)
                assert any("routing-capable devices: R2" in text for text in reminder_texts)
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "run_cli", "args": {"host": "R2", "command": "show ip ospf neighbor"}, "id": "call-3"},
                    ],
                )
            return AIMessage(content="summary", tool_calls=[])

    class DummyRunCliTool:
        def invoke(self, args):
            if args["command"] == "show ip protocols":
                return "[Device: R1 | IP: 10.0.0.1 | OS: cisco_ios]\nRouting Protocol is \"ospf 1\""
            if args["command"] == "show ip route":
                return "[Device: R2 | IP: 10.0.0.2 | OS: cisco_ios]\nO 10.1.0.0/16 [110/2] via 10.0.0.1"
            return (
                "[Device: R2 | IP: 10.0.0.2 | OS: cisco_ios]\n"
                "Neighbor ID     Pri   State           Dead Time   Address         Interface\n"
                "10.0.0.1          1   FULL/DR         00:00:36    10.0.0.1        GigabitEthernet0/0\n"
            )

    class DummyAnswerLLM:
        def invoke(self, _messages):
            return AIMessage(content="logical coverage ครบสำหรับ routing-capable devices แล้ว")

    result = free_run_node(
        {
            "messages": [HumanMessage(content="ช่วยทำ logical topology ของอุปกรณ์ทุกตัวให้ครบ")],
            "device_cache": {
                "R1": {
                    "ip_address": "10.0.0.1",
                    "os_platform": "cisco_ios",
                    "device_role": "router",
                    "site": "HQ",
                    "version": "15.6(2)T",
                },
                "R2": {
                    "ip_address": "10.0.0.2",
                    "os_platform": "cisco_ios",
                    "device_role": "dist_switch",
                    "site": "HQ",
                    "version": "15.6(2)T",
                },
            },
        },
        llm=DummyLLM(),
        answer_llm=DummyAnswerLLM(),
        run_cli_tool=DummyRunCliTool(),
    )

    tool_messages = [
        msg
        for msg in result["messages"]
        if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "run_cli"
    ]
    assert any(msg.additional_kwargs["tool_args"]["command"] == "show ip ospf neighbor" for msg in tool_messages)


def test_repair_answer_with_exact_facts_receives_exact_totals():
    from src.graph.agents.free_run_agent import _repair_answer_with_exact_facts
    from langchain_core.messages import SystemMessage

    class DummyAnswerLLM:
        def __init__(self):
            self.messages = None

        def invoke(self, messages):
            self.messages = messages
            return AIMessage(content="SSH เข้าได้ 8/9 อุปกรณ์ และเข้าไม่ได้ 1 ตัวคือ HQ-DIST-GW02")

    llm = DummyAnswerLLM()
    text = _repair_answer_with_exact_facts(
        answer_llm=llm,
        system_msg=SystemMessage(content="system"),
        candidate_answer="SSH เข้าได้ 7/8 อุปกรณ์",
        evidence_digest="[Evidence Digest]\nrun_cli_total=9\nrun_cli_success=8\nrun_cli_error=1",
        stats={
            "total": 9,
            "success": 8,
            "error": 1,
            "blocked": 0,
            "failed_hosts": [{"host": "HQ-DIST-GW02", "detail": "[TIMEOUT ERROR] timeout"}],
        },
        user_query="ช่วย test ssh เข้าอุปกรณ์ทุกตัวหน่อย ว่าเข้าได้ครบปล่าว",
    )

    assert text.startswith("SSH เข้าได้ 8/9")
    assert any("success=8, error=1, total=9" in str(msg.content) for msg in llm.messages)


def test_synthesize_final_answer_polishes_relationship_answers_with_ascii_or_tables():
    from src.graph.agents.free_run_agent import _synthesize_final_answer
    from langchain_core.messages import SystemMessage

    class DummyAnswerLLM:
        def __init__(self):
            self.call_count = 0
            self.calls = []

        def invoke(self, messages):
            self.call_count += 1
            self.calls.append(messages)
            if self.call_count == 1:
                return AIMessage(content="## Topology\n```\nASCII\n```\n|A|B|")
            return AIMessage(content="สรุป topology แบบ bullet ไม่มี ASCII")

    llm = DummyAnswerLLM()
    result = _synthesize_final_answer(
        answer_llm=llm,
        system_msg=SystemMessage(content="system"),
        device_cache={"R1": {"device_role": "router"}, "R2": {"device_role": "router"}},
        original_context=[HumanMessage(content="ช่วยหาความสัมพันธ์ของอุปกรณ์ทุกตัว")],
        session_messages=[HumanMessage(content="ช่วยหาความสัมพันธ์ของอุปกรณ์ทุกตัว")],
        result_messages=[
            ToolMessage(
                content="[Device: R1 | IP: 10.0.0.1 | OS: cisco_ios]\nDevice ID: R2",
                tool_call_id="call-1",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "R1", "command": "show cdp neighbors detail"},
                    "tool_status": "success",
                },
            )
        ],
        user_query="ช่วยหาความสัมพันธ์ของอุปกรณ์ทุกตัว",
    )

    assert result is not None
    assert result.additional_kwargs["phase"] == "topology_repair"
    assert result.content == "สรุป topology แบบ bullet ไม่มี ASCII"
    assert any(
        "Relationship answer repair" in str(msg.content) and "No ASCII diagrams." in str(msg.content)
        for call in llm.calls
        for msg in call
    )


def test_synthesize_final_answer_repairs_traceroute_symmetry_verdict():
    from src.graph.agents.free_run_agent import _synthesize_final_answer
    from langchain_core.messages import SystemMessage

    class DummyAnswerLLM:
        def __init__(self):
            self.call_count = 0

        def invoke(self, _messages):
            self.call_count += 1
            if self.call_count == 1:
                return AIMessage(content="เส้นทางไม่สมมาตร เพราะ hop แต่ละฝั่งเป็นคนละ IP")
            return AIMessage(
                content=(
                    "เส้นทางน่าจะสมมาตร\n"
                    "ไป: 10.255.10.14 -> 172.16.10.2 -> 192.168.99.11\n"
                    "กลับ: 192.168.99.1 -> 172.16.10.1 -> 10.255.10.13\n"
                    "เมื่ออ่านเส้นทางกลับย้อนลำดับ จะเป็นลิงก์ชุดเดียวกัน"
                )
            )

    result = _synthesize_final_answer(
        answer_llm=DummyAnswerLLM(),
        system_msg=SystemMessage(content="system"),
        device_cache={},
        original_context=[HumanMessage(content="traceroute ไป-กลับแล้ววิเคราะห์หน่อยว่าทางเดียวกันหรือไม่")],
        session_messages=[HumanMessage(content="traceroute ไป-กลับแล้ววิเคราะห์หน่อยว่าทางเดียวกันหรือไม่")],
        result_messages=[
            ToolMessage(
                content=(
                    "[DIAGNOSTIC] kind=traceroute requested_target=BRANCH-A-Switch\n"
                    "[EXECUTED COMMAND] traceroute 192.168.99.11\n"
                    "[RESOLVED TARGET] BRANCH-A-Switch (192.168.99.11)\n"
                    "[Device: HQ-CORE-RT01 | IP: 10.255.1.11 | OS: cisco_ios]\n"
                    "  1 10.255.10.14 1 msec\n"
                    "  2 172.16.10.2 2 msec\n"
                    "  3 192.168.99.11 3 msec\n"
                    "[TRACE HOP ANNOTATION]\n"
                    "- hop 1: 10.255.10.14 -> exact=HQ-DIST-GW01 GigabitEthernet0/1 [routed]\n"
                    "- hop 2: 172.16.10.2 -> exact=BRANCH-A-RTR Tunnel10 [tunnel]\n"
                    "- hop 3: 192.168.99.11 -> exact=BRANCH-A-Switch Vlan99 [svi]\n"
                ),
                tool_call_id="call-1",
                name="run_diagnostic",
                additional_kwargs={
                    "tool_args": {"host": "HQ-CORE-RT01", "kind": "traceroute", "target": "BRANCH-A-Switch"},
                    "tool_status": "success",
                    "executed_command": "traceroute 192.168.99.11",
                },
            ),
            ToolMessage(
                content=(
                    "[DIAGNOSTIC] kind=traceroute requested_target=10.255.1.11\n"
                    "[EXECUTED COMMAND] traceroute 10.255.1.11\n"
                    "[Device: BRANCH-A-Switch | IP: 192.168.99.11 | OS: cisco_ios]\n"
                    "  1 192.168.99.1 1 msec\n"
                    "  2 172.16.10.1 2 msec\n"
                    "  3 10.255.10.13 3 msec\n"
                    "[TRACE TARGET CONTEXT]\n"
                    "- exact owner(s): HQ-CORE-RT01 Loopback0 [loopback]\n"
                    "[TRACE HOP ANNOTATION]\n"
                    "- hop 1: 192.168.99.1 -> exact=BRANCH-A-RTR GigabitEthernet0/3.99 [subinterface]\n"
                    "- hop 2: 172.16.10.1 -> exact=HQ-DIST-GW01 Tunnel10 [tunnel]\n"
                    "- hop 3: 10.255.10.13 -> exact=HQ-CORE-RT01 GigabitEthernet0/2 [routed]\n"
                ),
                tool_call_id="call-2",
                name="run_diagnostic",
                additional_kwargs={
                    "tool_args": {"host": "BRANCH-A-Switch", "kind": "traceroute", "target": "10.255.1.11"},
                    "tool_status": "success",
                    "executed_command": "traceroute 10.255.1.11",
                },
            ),
        ],
        user_query="traceroute ไป-กลับแล้ววิเคราะห์หน่อยว่าทางเดียวกันหรือไม่",
    )

    assert result is not None
    assert result.additional_kwargs["phase"] == "symmetry_repair"
    assert "สมมาตร" in result.content
    assert "10.255.10.14" in result.content
    assert "172.16.10.1" in result.content


def test_synthesize_final_answer_repairs_route_language_and_destination_owner():
    from src.graph.agents.free_run_agent import _synthesize_final_answer
    from langchain_core.messages import SystemMessage

    class DummyAnswerLLM:
        def __init__(self):
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages)
            if len(self.calls) == 1:
                return AIMessage(
                    content=(
                        "Best path คือ HQ-CORE-RT01 -> HQ-DIST-GW01 -> BRANCH-A-RTR\n"
                        "จบทarget device: 192.168.99.11"
                    )
                )
            return AIMessage(
                content=(
                    "Best path จาก HQ-CORE-RT01 ไป 192.168.99.11 คือออก GigabitEthernet0/2 ไปหา 10.255.10.14 "
                    "ที่ HQ-DIST-GW01 จากนั้นออก Tunnel10 ไปหา 172.16.10.2 ที่ BRANCH-A-RTR "
                    "ก่อนส่งต่อเข้าเครือข่าย 192.168.99.0/24 ผ่าน GigabitEthernet0/3.99\n"
                    "ปลายทางคือ BRANCH-A-Switch Vlan99 (192.168.99.11)"
                )
            )

    result = _synthesize_final_answer(
        answer_llm=DummyAnswerLLM(),
        system_msg=SystemMessage(content="system"),
        device_cache={
            "HQ-CORE-RT01": {"device_role": "core_router"},
            "HQ-DIST-GW01": {"device_role": "dist_switch"},
            "BRANCH-A-RTR": {"device_role": "router"},
            "BRANCH-A-Switch": {"device_role": "access_switch"},
        },
        original_context=[
            HumanMessage(
                content="จาก HQ-CORE-RT01 route ไป 192.168.99.11 ตอนนี้ best path คืออะไร ช่วยไล่ให้ครบว่าออก interface ไหน, next-hop อะไร, ผ่าน transit network ไหนบ้าง, แล้วไปจบที่ device/interface ไหน"
            )
        ],
        session_messages=[
            HumanMessage(
                content="จาก HQ-CORE-RT01 route ไป 192.168.99.11 ตอนนี้ best path คืออะไร ช่วยไล่ให้ครบว่าออก interface ไหน, next-hop อะไร, ผ่าน transit network ไหนบ้าง, แล้วไปจบที่ device/interface ไหน"
            )
        ],
        result_messages=[
            ToolMessage(
                content=(
                    "[Device: HQ-CORE-RT01 | IP: 10.255.1.11 | OS: cisco_ios]\n"
                    "Routing entry for 192.168.99.0/24\n"
                    "  Known via \"ospf 10\", distance 110, metric 21\n"
                    "  * 10.255.10.14, from 10.255.2.21, via GigabitEthernet0/2\n"
                ),
                tool_call_id="call-1",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "HQ-CORE-RT01", "command": "show ip route 192.168.99.11"},
                    "tool_status": "success",
                },
            ),
            ToolMessage(
                content=(
                    "[Device: HQ-DIST-GW01 | IP: 10.255.2.21 | OS: cisco_ios]\n"
                    "Routing entry for 192.168.99.0/24\n"
                    "  Known via \"eigrp 100\", distance 90, metric 25856256, type internal\n"
                    "  * 172.16.10.2, from 172.16.10.2, via Tunnel10\n"
                ),
                tool_call_id="call-2",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "HQ-DIST-GW01", "command": "show ip route 192.168.99.11"},
                    "tool_status": "success",
                },
            ),
            ToolMessage(
                content=(
                    "[Device: BRANCH-A-RTR | IP: 10.255.3.101 | OS: cisco_ios]\n"
                    "Routing entry for 192.168.99.0/24\n"
                    "  Known via \"connected\", distance 0, metric 0 (connected)\n"
                    "  * directly connected, via GigabitEthernet0/3.99\n"
                ),
                tool_call_id="call-3",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "BRANCH-A-RTR", "command": "show ip route 192.168.99.11"},
                    "tool_status": "success",
                },
            ),
        ],
        user_query="จาก HQ-CORE-RT01 route ไป 192.168.99.11 ตอนนี้ best path คืออะไร ช่วยไล่ให้ครบว่าออก interface ไหน, next-hop อะไร, ผ่าน transit network ไหนบ้าง, แล้วไปจบที่ device/interface ไหน",
    )

    assert result is not None
    assert result.additional_kwargs["phase"] == "route_repair"
    assert "ปลายทางคือ BRANCH-A-Switch Vlan99" in result.content
    assert "จบทarget device" not in result.content


def test_execute_run_cli_batch_runs_calls_concurrently_and_preserves_order():
    from src.graph.agents.free_run_agent import _execute_run_cli_batch

    class DummyRunCliTool:
        def invoke(self, args):
            time.sleep(0.15)
            return (
                f"[Device: {args['host']} | IP: 10.0.0.{1 if args['host'] == 'R1' else 2} | "
                f"OS: cisco_ios]\noutput for {args['command']}"
            )

    tool_calls = [
        {"name": "run_cli", "args": {"host": "R1", "command": "show version"}, "id": "call-1"},
        {"name": "run_cli", "args": {"host": "R2", "command": "show version"}, "id": "call-2"},
    ]

    start = time.monotonic()
    results = _execute_run_cli_batch(
        tool_calls,
        run_cli_tool=DummyRunCliTool(),
        executed_calls=set(),
        terminal_failures=set(),
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.27
    assert [item["tc"]["id"] for item in results] == ["call-1", "call-2"]
    assert results[0]["tool_metadata"]["tool_status"] == "success"
    assert results[1]["tool_metadata"]["tool_status"] == "success"


def test_summarize_show_version_condenses_verbose_output():
    from src.graph.agents.free_run_agent import _summarize_show_version

    summary = _summarize_show_version(
        "Cisco IOS Software, IOSv Software, Version 15.6(2)T, RELEASE SOFTWARE\n"
        "R1 uptime is 22 minutes\n"
        'System image file is "flash0:/vios-adventerprisek9-m"\n'
        "Configuration register is 0x0\n"
    )

    assert "show version summary:" in summary
    assert "version: 15.6(2)T" in summary
    assert "uptime: 22 minutes" in summary
    assert "config register: 0x0" in summary


def test_answer_matches_stats_requires_exact_ratio_and_failed_host():
    from src.graph.agents.free_run_agent import _answer_matches_stats

    stats = {
        "total": 9,
        "success": 8,
        "error": 1,
        "blocked": 0,
        "failed_hosts": [{"host": "HQ-DIST-GW02", "detail": "timeout"}],
    }

    assert _answer_matches_stats(
        "SSH เข้าได้ 8/9 อุปกรณ์ และเข้าไม่ได้ 1/9 อุปกรณ์ คือ HQ-DIST-GW02",
        stats,
    ) is True
    assert _answer_matches_stats(
        "SSH เข้าได้ 8/9 อุปกรณ์ แต่ไม่ได้บอกชื่อเครื่องเสีย",
        stats,
    ) is False


def test_synthesize_final_answer_marks_consistency_repair_phase_when_needed():
    from src.graph.agents.free_run_agent import _synthesize_final_answer
    from langchain_core.messages import SystemMessage

    class DummyAnswerLLM:
        def __init__(self):
            self.call_count = 0

        def invoke(self, _messages):
            self.call_count += 1
            if self.call_count == 1:
                return AIMessage(content="SSH เข้าได้ 7/8 อุปกรณ์")
            return AIMessage(content="SSH เข้าได้ 8/9 อุปกรณ์ และเข้าไม่ได้ 1/9 คือ HQ-DIST-GW02")

    result = _synthesize_final_answer(
        answer_llm=DummyAnswerLLM(),
        system_msg=SystemMessage(content="system"),
        device_cache={},
        original_context=[HumanMessage(content="ช่วย test ssh เข้าอุปกรณ์ทุกตัวหน่อย ว่าเข้าได้ครบปล่าว")],
        session_messages=[HumanMessage(content="ช่วย test ssh เข้าอุปกรณ์ทุกตัวหน่อย ว่าเข้าได้ครบปล่าว")],
        result_messages=[
            ToolMessage(
                content="[Device: R1 | IP: 10.0.0.1 | OS: cisco_ios]\noutput",
                tool_call_id="call-1",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "R1", "command": "show version"},
                    "tool_status": "success",
                },
            ),
            ToolMessage(
                content="[TIMEOUT ERROR] 10.255.2.22 (OS: cisco_ios): Timed out after 30s",
                tool_call_id="call-2",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "HQ-DIST-GW02", "command": "show version"},
                    "tool_status": "error",
                },
            ),
        ],
        user_query="ช่วย test ssh เข้าอุปกรณ์ทุกตัวหน่อย ว่าเข้าได้ครบปล่าว",
    )

    assert result is not None
    assert result.additional_kwargs["phase"] == "consistency_repair"


def test_synthesize_final_answer_includes_language_instruction():
    from src.graph.agents.free_run_agent import _synthesize_final_answer
    from langchain_core.messages import SystemMessage

    class DummyAnswerLLM:
        def __init__(self):
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages)
            return AIMessage(content="BGP ทำงานปกติ")

    llm = DummyAnswerLLM()
    result = _synthesize_final_answer(
        answer_llm=llm,
        system_msg=SystemMessage(content="system"),
        device_cache={"HQ-CORE-RT01": {"ip_address": "10.255.1.11"}},
        original_context=[HumanMessage(content="HQ-CORE-RT01 เช็ค bgp หน่อย")],
        session_messages=[HumanMessage(content="HQ-CORE-RT01 เช็ค bgp หน่อย")],
        result_messages=[
            ToolMessage(
                content="[Device: HQ-CORE-RT01 | IP: 10.255.1.11 | OS: cisco_ios]\nBGP summary output",
                tool_call_id="call-1",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "HQ-CORE-RT01", "command": "show ip bgp summary"},
                    "tool_status": "success",
                },
            )
        ],
        progress_sink=None,
        user_query="HQ-CORE-RT01 เช็ค bgp หน่อย",
    )

    assert result is not None
    assert any(
        "Thai" in str(msg.content) and "language" in str(msg.content).lower()
        for call in llm.calls
        for msg in call
    )


def test_summarize_neighbor_details_condenses_adjacency_evidence():
    from src.graph.agents.free_run_agent import _summarize_neighbor_details

    summary = _summarize_neighbor_details(
        "Device ID: HQ-DIST-GW01\n"
        "Entry address(es):\n"
        "  IP address: 10.255.2.21\n"
        "Interface: GigabitEthernet0/0,  Port ID (outgoing port): GigabitEthernet0/1\n"
    )

    assert "neighbor summary: entries=1" in summary
    assert "neighbor=HQ-DIST-GW01" in summary
    assert "mgmt_ip=10.255.2.21" in summary
    assert "local_intf=GigabitEthernet0/0" in summary
    assert "remote_port=GigabitEthernet0/1" in summary


def test_relationship_queries_add_relationship_specific_synthesis_instruction():
    from src.graph.agents.free_run_agent import _synthesize_final_answer
    from langchain_core.messages import SystemMessage

    class DummyAnswerLLM:
        def __init__(self):
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages)
            return AIMessage(content="สรุป topology แบบ partial map")

    llm = DummyAnswerLLM()
    result = _synthesize_final_answer(
        answer_llm=llm,
        system_msg=SystemMessage(content="system"),
        device_cache={"R1": {"ip_address": "10.0.0.1"}, "R2": {"ip_address": "10.0.0.2"}},
        original_context=[HumanMessage(content="ช่วยหาความสัมพันธ์ของอุปกรณ์ทุกตัว")],
        session_messages=[
            HumanMessage(content="ช่วยหาความสัมพันธ์ของอุปกรณ์ทุกตัว"),
            ToolMessage(
                content="[Device: R2 | IP: 10.0.0.2 | OS: cisco_ios]\nNeighbor        V    AS MsgRcvd MsgSent TblVer InQ OutQ Up/Down  State/PfxRcd\n100.66.0.2      4 64512      86      84      3   0    0 01:14:21 2",
                tool_call_id="prev-call-1",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "R2", "command": "show ip bgp summary"},
                    "tool_status": "success",
                },
            ),
            AIMessage(content="รอบก่อนสรุปเบื้องต้นไปแล้ว"),
        ],
        result_messages=[
            ToolMessage(
                content="[Device: R1 | IP: 10.0.0.1 | OS: cisco_ios]\nDevice ID: R2\nEntry address(es):\n  IP address: 10.0.0.2\nInterface: Gi0/0,  Port ID (outgoing port): Gi0/1",
                tool_call_id="call-1",
                name="run_cli",
                additional_kwargs={
                    "tool_args": {"host": "R1", "command": "show cdp neighbors detail"},
                    "tool_status": "success",
                },
            )
        ],
        progress_sink=None,
        user_query="ช่วยหาความสัมพันธ์ของอุปกรณ์ทุกตัว",
    )

    assert result is not None
    assert any(
        "confirmed relationships and inference" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "Do not repeat earlier capability explanations" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "Do not reduce adjacency evidence to only counts of neighbors." in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "`Device-A <-> Device-B`" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "Confirmed links" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "Treat earlier assistant summaries as non-authoritative context." in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "session_hosts_with_direct_cli=2" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "[Session Logical Relationship Digest]" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "session_hosts_without_logical_evidence=1" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "Logical relationships" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "Confirmed physical links" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "one-link-per-line lists" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "Avoid large ASCII topology diagrams" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "For `Confirmed physical links`, prefer markdown bullets" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "For `Topology interpretation`, prefer short bullets grouped by layer" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "For `Logical relationships`, prefer bullets" in str(msg.content)
        for call in llm.calls
        for msg in call
    )
    assert any(
        "For `Limitations`, prefer short bullet points" in str(msg.content)
        for call in llm.calls
        for msg in call
    )


def test_free_run_node_answers_from_existing_incident_evidence_before_running_tools():
    from src.graph.agents.free_run_agent import free_run_node

    class UnexpectedToolLLM:
        def bind_tools(self, _tools):
            raise AssertionError("tool-calling LLM should not run for existing-evidence follow-ups")

    class DummyAnswerLLM:
        def __init__(self):
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages)
            return AIMessage(content="ควรแก้ปัญหา SSH ที่บันทึกไว้ก่อน แล้วค่อยตรวจ BGP จากหลักฐานเดิม")

    class DummyRunCliTool:
        def invoke(self, _args):
            raise AssertionError("run_cli should not execute when recorded incident evidence is sufficient")

    answer_llm = DummyAnswerLLM()
    result = free_run_node(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "[System: Existing incident troubleshoot evidence]\n"
                        "Recorded troubleshoot summary: Assessment: SSH failed to HQ-CORE-RT01."
                    )
                ),
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
                HumanMessage(content="ควรเช็คอะไรต่อดี"),
            ],
            "device_cache": {
                "HQ-CORE-RT01": {
                    "ip_address": "10.255.1.11",
                    "os_platform": "cisco_ios",
                    "device_role": "router",
                    "site": "HQ",
                    "version": "15.6(2)T",
                }
            },
            "incident_context": (
                "Incident: INC-000001\n"
                "Recorded Troubleshoot Result:\n"
                "  Summary: Assessment: SSH failed to HQ-CORE-RT01.\n"
            ),
        },
        llm=UnexpectedToolLLM(),
        answer_llm=answer_llm,
        run_cli_tool=DummyRunCliTool(),
    )

    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)
    assert "SSH" in result["messages"][0].content
    assert answer_llm.calls
    assert any(
        "Do not pretend a fresh check happened." in str(msg.content)
        for msg in answer_llm.calls[0]
        if hasattr(msg, "content")
    )
