"""
src/graph/agents/free_run_agent.py
==================================
Pure free-run SSH agent for LLM-first execution.

This node intentionally avoids strict-mode helpers such as:
  - heuristic target-host resolution
  - deterministic command selection
  - command profile fallback / auto-repair
  - rigid backend routing workflows

The LLM decides:
  - whether to ground a device
  - whether to load full inventory
  - which command to run
  - when to stop

The backend only provides runtime guardrails:
  - tool execution
  - duplicate-call blocking
  - terminal connectivity failure blocking
  - context assembly / memory plumbing
  - lightweight inventory-derived routing hints
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import json
import logging
import os
import re
import threading
from typing import Any, Protocol, Sequence

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from src.prompts.ssh_compact import SSH_COMPACT_PROMPT
from src.prompts.ssh_synthesis import SSH_SYNTHESIS_PROMPT
from src.tools.interface_inventory import (
    interface_inventory_hostnames,
    resolve_ip_context,
    resolve_prefix_context,
)
from src.tools.inventory_tools import list_all_devices, lookup_device, resolve_inventory_record
from src.tools.db_tools import search_logs, search_incidents, get_incident_detail
from src.formatters import extract_executed_command, is_command_error, parse_output, strip_tool_metadata

logger = logging.getLogger(__name__)


class SupportsInvoke(Protocol):
    def invoke(self, input: Any, **kwargs: Any) -> Any:
        ...


class SupportsToolBinding(SupportsInvoke, Protocol):
    def bind_tools(self, tools: Sequence[Any]) -> SupportsInvoke:
        ...

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_TERMINAL_ERROR_PREFIXES = (
    "[AUTH ERROR]",
    "[SSH ERROR]",
    "[TIMEOUT ERROR]",
    "[DETECTION ERROR]",
    "[CONFIG ERROR]",
)

_DEFAULT_CONTEXT_CHAR_BUDGET = max(
    18000,
    min(
        int(os.getenv("LLM_NUM_CTX", os.getenv("OLLAMA_NUM_CTX", "65536"))) * 3,
        180000,
    ),
)
_FREE_RUN_CONTEXT_CHAR_BUDGET = int(
    os.getenv("SSH_CONTEXT_CHAR_BUDGET", str(_DEFAULT_CONTEXT_CHAR_BUDGET))
)
_FREE_RUN_MAX_ITERATIONS = int(os.getenv("FREE_RUN_MAX_ITERATIONS", "8"))
_FREE_RUN_MAX_ITERATIONS_TROUBLESHOOT = int(
    os.getenv("FREE_RUN_MAX_ITERATIONS_TROUBLESHOOT", "20")
)
_FREE_RUN_MAX_PARALLEL_RUN_CLI = max(
    1, int(os.getenv("FREE_RUN_MAX_PARALLEL_RUN_CLI", "8"))
)
_SYNTHESIS_CONTEXT_CHAR_BUDGET = int(
    os.getenv("SSH_SYNTHESIS_CONTEXT_CHAR_BUDGET", "6000")
)
_TROUBLESHOOT_RE = re.compile(
    r"(troubleshoot|investigate|หาสาเหตุ|ไล่หา|ไล่เช็ค|ไล่ดู|"
    r"root\s*cause|ทำไม.*ถึง|why\s+.*(?:down|fail|timeout|error)|"
    r"diagnose|วิเคราะห์|debug|trace\b|"
    r"หาว่า.*เพราะ|เพราะอะไร|สาเหตุ|"
    r"fix|แก้.*ปัญหา|ปัญหา.*อยู่ที่|"
    r"ไปหา.*ให้|ไปดู.*ให้|เช็คให้|ช่วยหา)",
    re.IGNORECASE,
)
_BATCH_REACHABILITY_RE = re.compile(
    r"(ssh|show\s+version).*(ทุกตัว|ทุกเครื่อง|ทั้งหมด|ครบ|all\s+devices|every\s+device)|"
    r"(ทุกตัว|ทุกเครื่อง|ทั้งหมด|ครบ).*(ssh|show\s+version)|"
    r"(เข้าได้ครบ|เข้าได้ไหม|เข้าได้ครบปล่าว|เข้าได้ครบเปล่า|reachable|reachability)",
    re.IGNORECASE,
)
_RELATIONSHIP_ANALYSIS_RE = re.compile(
    r"(relationship|topology|dependency|dependencies|adjacency|neighbor map|"
    r"how .* connect|connectivity map|path ownership|"
    r"ความสัมพันธ์|เชื่อมต่อกันยังไง|เชื่อมกันยังไง|topo|โทโพโลยี|"
    r"พึ่งพา|dependency|สัมพันธ์กัน|เชื่อมต่อกัน|เส้นทางเชื่อม)",
    re.IGNORECASE,
)
_ALL_DEVICE_SCOPE_RE = re.compile(
    r"(ทุกตัว|ทุกเครื่อง|ทั้งหมด|all\s+devices|every\s+device)",
    re.IGNORECASE,
)
_EXISTING_EVIDENCE_FOLLOWUP_RE = re.compile(
    r"(what\s+next|next\s+(?:step|check|action)|follow[\s-]*up|summary|summari[sz]e|"
    r"explain|interpret|what\s+does\s+this\s+mean|root\s*cause|conclusion|"
    r"recommend|assessment|because|why\b|"
    r"สรุป|อธิบาย|แปลว่า|หมายความว่า|วิเคราะห์|ประเมิน|ข้อสรุป|"
    r"ควร(?:เช็ค|ตรวจ|ทำ)อะไรต่อ|ต้องทำอะไรต่อ|ถัดไป|ต่อยังไง|"
    r"เพราะอะไร|จากข้อมูล|จากที่มี|แนะนำต่อ)",
    re.IGNORECASE,
)
_LIVE_RECHECK_RE = re.compile(
    r"(^|\b)(show|run|ssh|ping|traceroute|trace|recheck|check\s+again|live\s+state|"
    r"current\s+state|current\s+status|refresh|rerun|ตรวจใหม่|เช็คใหม่|รันใหม่|"
    r"ลองใหม่|เข้าไปดู|ดูสด|สถานะปัจจุบัน)(\b|$)",
    re.IGNORECASE,
)
_LOGICAL_TOPOLOGY_RE = re.compile(
    r"(logical\s+topology|routing\s+relationship|control-plane|control plane|"
    r"bgp|ospf|eigrp|เส้นทางเชิงตรรกะ|logical|topology.*logical|"
    r"physical\s+and\s+logical|physical\s*&\s*logical)",
    re.IGNORECASE,
)
_EXECUTION_TOOL_NAMES = {"run_cli", "run_diagnostic"}
_LOGICAL_EVIDENCE_COMMAND_RE = re.compile(
    r"show ip protocols|show ip bgp summary|show ip ospf neighbor|"
    r"show ip eigrp neighbors|show ip default-gateway",
    re.IGNORECASE,
)
_TRACE_RESOLVED_TARGET_RE = re.compile(
    r"^\[RESOLVED TARGET\]\s*(?P<host>.+?)\s*\((?P<ip>[^)]+)\)\s*$",
    re.MULTILINE,
)
_TRACE_TARGET_EXACT_OWNER_RE = re.compile(
    r"^\s*-\s*exact owner\(s\):\s*(?P<host>\S+)",
    re.MULTILINE,
)
_TRACE_HOP_ANNOTATION_RE = re.compile(
    r"^\s*-\s*hop\s+(?P<hop>\d+):\s*(?P<body>.+)$",
    re.MULTILINE,
)
_TRACE_EXACT_HOST_RE = re.compile(r"exact=(?P<host>\S+)")
_TRACE_SAME_NETWORK_HOST_RE = re.compile(r"same_network=(?P<host>\S+)")
_TRACE_CONNECTED_CANDIDATE_HOST_RE = re.compile(r"connected_candidates=(?P<host>\S+)")
_TRACE_HOP_IP_RE = re.compile(r"^(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s*->")
_ROUTING_QUERY_RE = re.compile(
    r"(route|routing|next\s+hop|default\s+route|default-gateway|show\s+ip\s+route|"
    r"traceroute|prefix|subnet|where\s+does.*(?:route|network|prefix)|"
    r"which\s+device.*(?:route|network|prefix|interface)|"
    r"เส้นทาง|หา route|หาเส้นทาง|route ไป|วิ่งทางไหน|วิ่งผ่าน|"
    r"next hop|default route|อยู่ที่อุปกรณ์ตัวไหน|อยู่ที่ไหน|interface ไหน)",
    re.IGNORECASE,
)
_CIDR_LITERAL_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b")
_IP_LITERAL_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_ROUTING_CAPABLE_ROLES = {"router", "core_router", "dist_switch"}
_ROUTING_ROLE_PRIORITY = {
    "core_router": 0,
    "router": 1,
    "dist_switch": 2,
    "access_switch": 3,
}
_TRACE_SYMMETRY_QUERY_RE = re.compile(
    r"(same\s+path|same\s+route|path\s+symmetr|route\s+symmetr|"
    r"symmetric|asymmetric|forward.*reverse.*traceroute|"
    r"ทางเดียวกัน|เส้นทางเดียวกัน|สมมาตร|ไม่สมมาตร|ไปกลับทางเดียวกัน)",
    re.IGNORECASE,
)
_SYMMETRIC_VERDICT_RE = re.compile(
    r"(\blikely\s+symmetric\b|\bsymmetric\b|ทางเดียวกัน|เส้นทางเดียวกัน|สมมาตร)",
    re.IGNORECASE,
)
_ASYMMETRIC_VERDICT_RE = re.compile(
    r"(\blikely\s+asymmetric\b|\basymmetric\b|not\s+symmetric|different\s+path|"
    r"ไม่สมมาตร|คนละทาง|ต่างทาง)",
    re.IGNORECASE,
)
_ANY_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_THAI_CHAR_RE = re.compile(r"[ก-๙]")
_HALF_TRANSLATED_ROUTE_LABEL_RE = re.compile(
    r"(?:[ก-๙](?:target|device|destination)|(?:target|device|destination)[ก-๙]|target\s+device|destination\s+device)",
    re.IGNORECASE,
)
_ROUTE_DESTINATION_DETAIL_RE = re.compile(
    r"(device/interface|interface ไหน|interface อะไร|จบที่|ปลายทาง|destination)",
    re.IGNORECASE,
)


def _build_cache_section(device_cache: dict) -> str:
    if not device_cache:
        return "(empty)"
    lines = [
        f"  - {host}: ip={info['ip_address']}, os={info['os_platform']}, "
        f"role={info['device_role']}, site={info.get('site', '')}, "
        f"version={info.get('version', '') or '?'}"
        for host, info in device_cache.items()
    ]
    return "\n".join(lines)


def _cache_inventory_record(device_cache: dict, record: dict[str, Any] | None) -> None:
    if not record:
        return
    hostname = str(record.get("hostname", "") or "").strip()
    if not hostname:
        return
    tunnel_ips_raw = record.get("tunnel_ips", []) or []
    if isinstance(tunnel_ips_raw, str):
        tunnel_ips = [item.strip() for item in tunnel_ips_raw.split() if item.strip()]
    else:
        tunnel_ips = [str(item).strip() for item in tunnel_ips_raw if str(item).strip()]
    device_cache[hostname] = {
        "ip_address": str(record.get("ip_address", "") or ""),
        "os_platform": str(record.get("os_platform", "") or ""),
        "device_role": str(record.get("device_role", "") or ""),
        "site": str(record.get("site", "") or ""),
        "version": str(record.get("version", "") or ""),
        "tunnel_ips": tunnel_ips,
    }


def _device_info_for_host(hostname: str, device_cache: dict) -> dict[str, Any]:
    for cached_host, info in device_cache.items():
        if cached_host.lower() == hostname.lower():
            return info
    resolved = resolve_inventory_record(hostname)
    if not resolved:
        return {}
    return {
        "ip_address": str(resolved.get("ip_address", "") or ""),
        "os_platform": str(resolved.get("os_platform", "") or ""),
        "device_role": str(resolved.get("device_role", "") or ""),
        "site": str(resolved.get("site", "") or ""),
        "version": str(resolved.get("version", "") or ""),
    }


def _is_routing_capable_role(role: str) -> bool:
    return (role or "").strip().lower() in _ROUTING_CAPABLE_ROLES


def _extract_query_ips_and_prefixes(query: str) -> tuple[list[str], list[str]]:
    cidrs: list[str] = []
    cidr_seen: set[str] = set()
    ip_bases_in_prefixes: set[str] = set()
    for match in _CIDR_LITERAL_RE.findall(query or ""):
        try:
            normalized = str(ipaddress.ip_network(match, strict=False))
        except ValueError:
            continue
        if normalized in cidr_seen:
            continue
        cidrs.append(normalized)
        cidr_seen.add(normalized)
        ip_bases_in_prefixes.add(normalized.split("/", 1)[0])

    ips: list[str] = []
    ip_seen: set[str] = set()
    for match in _IP_LITERAL_RE.findall(query or ""):
        try:
            ipaddress.ip_address(match)
        except ValueError:
            continue
        if match in ip_seen or match in ip_bases_in_prefixes:
            continue
        ips.append(match)
        ip_seen.add(match)

    return ips, cidrs


def _extract_query_host_mentions(query: str) -> list[str]:
    lowered = (query or "").lower()
    matches: list[tuple[int, int, str]] = []
    for hostname in interface_inventory_hostnames():
        position = lowered.find(hostname.lower())
        if position >= 0:
            matches.append((position, -len(hostname), hostname))
    matches.sort()
    ordered: list[str] = []
    seen: set[str] = set()
    for _position, _neg_len, hostname in matches:
        if hostname not in seen:
            seen.add(hostname)
            ordered.append(hostname)
    return ordered


def _format_interface_context(row: dict[str, str]) -> str:
    hostname = row.get("hostname", "?")
    interface_name = row.get("interface_name", "?")
    interface_mode = row.get("interface_mode", "")
    network_cidr = row.get("network_cidr", "")
    description = row.get("description", "")
    role = row.get("device_role", "")
    parts = [f"{hostname} {interface_name}"]
    if interface_mode:
        parts.append(f"[{interface_mode}]")
    extras: list[str] = []
    if role:
        extras.append(f"role={role}")
    if network_cidr:
        extras.append(f"network={network_cidr}")
    access_vlan = row.get("access_vlan", "")
    vlan_tag = row.get("vlan_tag", "")
    vlan_value = vlan_tag or access_vlan
    if vlan_value:
        extras.append(f"vlan={vlan_value}")
    if description:
        extras.append(f"desc={description}")
    summary = " ".join(parts)
    if extras:
        summary += " " + ", ".join(extras)
    return summary


def _role_priority_for_host(hostname: str, device_cache: dict) -> tuple[int, str]:
    role = str(_device_info_for_host(hostname, device_cache).get("device_role", "") or "").strip().lower()
    return (_ROUTING_ROLE_PRIORITY.get(role, 99), hostname)


def _analyze_routing_inventory(user_query: str, device_cache: dict) -> dict[str, Any]:
    query = user_query or ""
    ips, prefixes = _extract_query_ips_and_prefixes(query)
    host_mentions = _extract_query_host_mentions(query)
    is_routing_query = bool(_ROUTING_QUERY_RE.search(query)) or bool(prefixes) or (
        bool(ips) and bool(host_mentions)
    )
    if not is_routing_query:
        return {
            "mentioned_hosts": [],
            "ips": [],
            "prefixes": [],
            "ip_contexts": [],
            "prefix_contexts": [],
            "recommended_hosts": [],
            "candidate_reasons": {},
        }

    candidate_buckets: dict[int, list[str]] = {0: [], 1: [], 2: []}
    candidate_reasons: dict[str, list[str]] = {}

    def add_candidate(hostname: str, reason: str, bucket: int) -> None:
        if not hostname:
            return
        candidate_buckets.setdefault(bucket, []).append(hostname)
        reasons = candidate_reasons.setdefault(hostname, [])
        if reason and reason not in reasons:
            reasons.append(reason)

    for hostname in host_mentions:
        add_candidate(hostname, "user-mentioned device in the request", 0)

    ip_contexts: list[dict[str, Any]] = []
    for ip_value in ips[:3]:
        context = resolve_ip_context(ip_value)
        ip_contexts.append({
            "ip": ip_value,
            "exact_matches": context.get("exact_matches", []),
            "network_matches": context.get("network_matches", []),
        })
        for row in context.get("exact_matches", []):
            hostname = row.get("hostname", "")
            reason = f"exact owner of {ip_value} via {row.get('interface_name', '?')}"
            bucket = 1 if _is_routing_capable_role(row.get("device_role", "")) else 2
            add_candidate(hostname, reason, bucket)
        for row in context.get("network_matches", []):
            hostname = row.get("hostname", "")
            reason = (
                f"connected owner of {row.get('network_cidr', ip_value)} via "
                f"{row.get('interface_name', '?')}"
            )
            bucket = 1 if _is_routing_capable_role(row.get("device_role", "")) else 2
            add_candidate(hostname, reason, bucket)

    prefix_contexts: list[dict[str, Any]] = []
    for prefix_value in prefixes[:3]:
        context = resolve_prefix_context(prefix_value)
        prefix_contexts.append({
            "prefix": prefix_value,
            "normalized_prefix": context.get("normalized_prefix", prefix_value),
            "exact_matches": context.get("exact_matches", []),
            "overlapping_matches": context.get("overlapping_matches", []),
        })
        for row in context.get("exact_matches", []):
            hostname = row.get("hostname", "")
            reason = f"owns connected prefix {context.get('normalized_prefix', prefix_value)}"
            bucket = 1 if _is_routing_capable_role(row.get("device_role", "")) else 2
            add_candidate(hostname, reason, bucket)
        for row in context.get("overlapping_matches", []):
            hostname = row.get("hostname", "")
            reason = (
                f"overlaps {context.get('normalized_prefix', prefix_value)} via "
                f"{row.get('interface_name', '?')}"
            )
            bucket = 1 if _is_routing_capable_role(row.get("device_role", "")) else 2
            add_candidate(hostname, reason, bucket)

    recommended_hosts: list[str] = []
    seen_hosts: set[str] = set()
    for bucket_index in (0, 1, 2):
        hosts = candidate_buckets.get(bucket_index, [])
        if bucket_index == 0:
            ordered_bucket = hosts
        else:
            ordered_bucket = sorted(set(hosts), key=lambda host: _role_priority_for_host(host, device_cache))
        for hostname in ordered_bucket:
            if hostname not in seen_hosts:
                seen_hosts.add(hostname)
                recommended_hosts.append(hostname)

    return {
        "mentioned_hosts": host_mentions,
        "ips": ips,
        "prefixes": prefixes,
        "ip_contexts": ip_contexts,
        "prefix_contexts": prefix_contexts,
        "recommended_hosts": recommended_hosts,
        "candidate_reasons": candidate_reasons,
    }


def _prefill_routing_candidates(device_cache: dict, analysis: dict[str, Any]) -> None:
    for hostname in analysis.get("recommended_hosts", [])[:4]:
        if any(cached_host.lower() == hostname.lower() for cached_host in device_cache):
            continue
        _cache_inventory_record(device_cache, resolve_inventory_record(hostname))


def _build_routing_context_section(analysis: dict[str, Any], device_cache: dict) -> str:
    recommended_hosts = analysis.get("recommended_hosts", [])
    if not recommended_hosts and not analysis.get("ip_contexts") and not analysis.get("prefix_contexts"):
        return ""

    lines = ["=== ACTIVE ROUTING CONTEXT ==="]
    mentioned_hosts = analysis.get("mentioned_hosts", [])
    if mentioned_hosts:
        lines.append("User-mentioned devices: " + ", ".join(mentioned_hosts[:4]))

    for item in analysis.get("ip_contexts", [])[:3]:
        ip_value = item.get("ip", "")
        exact_matches = item.get("exact_matches", [])
        network_matches = item.get("network_matches", [])
        if exact_matches:
            lines.append(f"Target IP {ip_value} exact interface owner(s):")
            for row in exact_matches[:3]:
                lines.append(f"- {_format_interface_context(row)}")
        if network_matches:
            lines.append(f"Target IP {ip_value} matching connected network(s):")
            for row in network_matches[:4]:
                lines.append(f"- {_format_interface_context(row)}")

    for item in analysis.get("prefix_contexts", [])[:3]:
        normalized_prefix = item.get("normalized_prefix", item.get("prefix", ""))
        exact_matches = item.get("exact_matches", [])
        overlapping_matches = item.get("overlapping_matches", [])
        if exact_matches:
            lines.append(f"Prefix {normalized_prefix} exact connected owner(s):")
            for row in exact_matches[:4]:
                lines.append(f"- {_format_interface_context(row)}")
        elif overlapping_matches:
            lines.append(f"Prefix {normalized_prefix} overlapping owner(s):")
            for row in overlapping_matches[:4]:
                lines.append(f"- {_format_interface_context(row)}")

    if recommended_hosts:
        lines.append("Preferred live-check starting hosts:")
        candidate_reasons = analysis.get("candidate_reasons", {})
        for index, hostname in enumerate(recommended_hosts[:4], start=1):
            info = _device_info_for_host(hostname, device_cache)
            role = str(info.get("device_role", "") or "").strip()
            site = str(info.get("site", "") or "").strip()
            tags = ", ".join(part for part in (role, site) if part)
            reason = "; ".join(candidate_reasons.get(hostname, [])[:2])
            if tags:
                lines.append(f"{index}. {hostname} ({tags}) - {reason}")
            else:
                lines.append(f"{index}. {hostname} - {reason}")

        route_target = ""
        prefixes = analysis.get("prefixes", [])
        ips = analysis.get("ips", [])
        if prefixes:
            route_target = prefixes[0]
        elif ips:
            route_target = ips[0]
        if route_target:
            lines.append(
                f"On routing-capable devices, start with `show ip route {route_target}` before broader checks."
            )
        if any(
            str(_device_info_for_host(hostname, device_cache).get("device_role", "") or "").strip().lower()
            == "access_switch"
            for hostname in recommended_hosts[:4]
        ):
            lines.append(
                "If the starting device is an access switch, prefer `show ip default-gateway` or "
                "`show ip interface brief` before assuming full routing-table visibility."
            )
        lines.append("Ground any recommended host not already in cache before running CLI.")

    lines.append("================================")
    return "\n".join(lines)


def _build_compact_prompt(
    *,
    device_cache: dict,
    incident_context: str,
    user_query: str,
) -> tuple[str, str]:
    analysis = _analyze_routing_inventory(user_query, device_cache)
    _prefill_routing_candidates(device_cache, analysis)
    cache_section = _build_cache_section(device_cache)
    prompt = SSH_COMPACT_PROMPT.format(device_cache_section=cache_section)
    routing_context = _build_routing_context_section(analysis, device_cache)

    sections: list[str] = []
    if incident_context:
        sections.append(
            "=== ACTIVE INCIDENT CONTEXT ===\n"
            f"{incident_context}\n"
            "================================\n\n"
            "You are assisting a network engineer who is investigating the incident above. "
            "Focus your investigation on the affected device and interface listed in the context. "
            "If the incident context or recorded troubleshoot evidence already answers a follow-up "
            "question, answer from that existing evidence first. "
            "Do not rerun the same command unless the user explicitly asks for a fresh live re-check "
            "or the existing evidence is clearly insufficient. "
            "You can run additional CLI commands only when new evidence is still needed. "
            "The automated AIOps pipeline may also be running commands on the same device — "
            "your commands will queue safely behind any in-flight pipeline work."
        )
    if routing_context:
        sections.append(routing_context)
    sections.append(prompt)
    return "\n\n".join(sections), cache_section


def _find_user_query(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = str(message.content or "")
            if not content.startswith("[Tool Result") and not content.startswith("[System:"):
                return content
    return ""


def _message_text(message: BaseMessage) -> str:
    return str(getattr(message, "content", "") or "")


def _extract_query_terms(query: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_.:/-]+", query)
        if len(token) >= 3
    }


def _message_relevance_score(
    message: BaseMessage,
    *,
    latest_query: str,
    query_terms: set[str],
    active_hosts: set[str],
) -> int:
    text = _message_text(message)
    lowered = text.lower()
    score = 0
    if isinstance(message, HumanMessage):
        score += 3
    elif isinstance(message, ToolMessage):
        score += 2
    elif isinstance(message, AIMessage):
        score += 1

    for host in active_hosts:
        if host.lower() in lowered:
            score += 6
    for term in query_terms:
        if term in lowered:
            score += 1
    if latest_query and latest_query.lower() in lowered:
        score += 4
    if text.startswith("[Tool Result"):
        score -= 1
    if text.startswith("[System:"):
        score -= 2
    return score


def _assemble_relevant_context(
    messages: list[BaseMessage],
    device_cache: dict,
    *,
    char_budget: int,
) -> list[BaseMessage]:
    # Fast path: if total message content is under budget, skip scoring
    total_chars = sum(len(_message_text(m)) for m in messages)
    if total_chars <= char_budget:
        return list(messages)

    latest_query = _find_user_query(messages)
    query_terms = _extract_query_terms(latest_query)
    active_hosts = {
        host
        for host in device_cache
        if host.lower() in latest_query.lower()
        or device_cache[host].get("ip_address", "") in latest_query
    }

    latest_human_index = -1
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, HumanMessage):
            content = _message_text(message)
            if not content.startswith("[Tool Result") and not content.startswith("[System:"):
                latest_human_index = index
                break

    selected_indexes: set[int] = set()
    if latest_human_index >= 0:
        selected_indexes.add(latest_human_index)
        for index in range(latest_human_index + 1, len(messages)):
            if isinstance(messages[index], ToolMessage):
                selected_indexes.add(index)
                if index > 0 and isinstance(messages[index - 1], AIMessage):
                    selected_indexes.add(index - 1)

    scored_indexes: list[tuple[int, int]] = []
    for index, message in enumerate(messages):
        if index in selected_indexes:
            continue
        score = _message_relevance_score(
            message,
            latest_query=latest_query,
            query_terms=query_terms,
            active_hosts=active_hosts,
        )
        if score > 0:
            scored_indexes.append((score, index))

    scored_indexes.sort(key=lambda item: (item[0], item[1]), reverse=True)

    total_chars = sum(len(_message_text(messages[index])) for index in selected_indexes)
    for score, index in scored_indexes:
        text_len = len(_message_text(messages[index]))
        if total_chars + text_len > char_budget and selected_indexes:
            continue
        selected_indexes.add(index)
        total_chars += text_len

    return [messages[index] for index in sorted(selected_indexes)]


def _sanitize_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    clean: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            continue
        if isinstance(message, HumanMessage):
            clean.append(message)
            continue
        if isinstance(message, ToolMessage):
            tool_name = getattr(message, "name", "tool")
            content = str(message.content)
            metadata = getattr(message, "additional_kwargs", {}) or {}
            if tool_name == "lookup_device":
                try:
                    data = json.loads(content)
                    if isinstance(data, dict) and "hostname" in data and "error" not in data:
                        content = (
                            f"Resolved device: hostname={data.get('hostname', '')}, "
                            f"ip={data.get('ip_address', '')}, os={data.get('os_platform', '')}, "
                            f"role={data.get('device_role', '')}, site={data.get('site', '')}, "
                            f"version={data.get('version', '')}"
                        )
                except json.JSONDecodeError:
                    pass
            elif tool_name == "list_all_devices":
                try:
                    data = json.loads(content)
                    if isinstance(data, list):
                        entries = []
                        for item in data:
                            if not isinstance(item, dict):
                                continue
                            entries.append(
                                f"{item.get('hostname', '')} "
                                f"(ip={item.get('ip_address', '')}, "
                                f"os={item.get('os_platform', '')}, "
                                f"role={item.get('device_role', '')})"
                            )
                        if entries:
                            content = "Inventory devices:\n" + "\n".join(entries)
                except json.JSONDecodeError:
                    pass
            elif tool_name in _EXECUTION_TOOL_NAMES:
                args = metadata.get("tool_args", {})
                command = str(
                    metadata.get("executed_command")
                    or args.get("command", "")
                    or extract_executed_command(content)
                    or ""
                ).strip()
                requested_host = str(args.get("host", "") or "").strip()
                status = str(metadata.get("tool_status", "") or "").strip() or "unknown"
                host, ip, os_type, body = parse_output(content)
                resolved_host = host or requested_host or "unknown"
                details = [f"status={status}", f"host={resolved_host}"]
                if command:
                    details.append(f"command={command}")
                diagnostic_kind = str(args.get("kind", "") or "").strip()
                if diagnostic_kind:
                    details.append(f"kind={diagnostic_kind}")
                if ip:
                    details.append(f"ip={ip}")
                if os_type:
                    details.append(f"os={os_type}")
                payload = _condense_run_cli_output(
                    command=command,
                    status=status,
                    raw_body=body if body else content,
                )
                content = "CLI result: " + ", ".join(details) + f"\n{payload}"
            clean.append(HumanMessage(content=f"[Tool Result — {tool_name}]\n{content}"))
            continue
        if isinstance(message, AIMessage) and not message.tool_calls and message.content:
            content = _THINK_RE.sub("", message.content).strip()
            if content:
                clean.append(AIMessage(content=content))
    return clean


def _build_synthesis_context(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Keep only the most relevant conversational context for final synthesis.

    Final synthesis already receives the current turn's executed tool evidence in
    ``clean_results``. This function trims duplicated historical tool payloads so
    the answer model spends more time on the latest request and evidence.
    """
    relevant: list[BaseMessage] = []
    total_chars = 0

    for message in reversed(messages):
        text = _message_text(message).strip()
        if not text:
            continue
        if text.startswith("[Tool Result"):
            continue
        if isinstance(message, HumanMessage):
            relevant.append(message)
        elif isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
            relevant.append(message)
        if total_chars >= _SYNTHESIS_CONTEXT_CHAR_BUDGET:
            break
        total_chars += len(text)

    relevant.reverse()
    return relevant


def _has_existing_execution_evidence(messages: list[BaseMessage]) -> bool:
    return any(
        isinstance(message, ToolMessage) and getattr(message, "name", "") in _EXECUTION_TOOL_NAMES
        for message in messages
    )


def _should_answer_from_existing_incident_evidence(
    *,
    user_query: str,
    messages: list[BaseMessage],
    incident_context: str,
) -> bool:
    if not incident_context.strip():
        return False
    if not user_query.strip():
        return False
    if not _has_existing_execution_evidence(messages):
        return False
    if _LIVE_RECHECK_RE.search(user_query):
        return False
    return bool(_EXISTING_EVIDENCE_FOLLOWUP_RE.search(user_query))


def _answer_from_existing_incident_evidence(
    *,
    answer_llm: SupportsInvoke,
    user_query: str,
    messages: list[BaseMessage],
    device_cache: dict[str, Any],
    incident_context: str,
    progress_sink: dict | None = None,
) -> AIMessage | None:
    relevant_messages = _assemble_relevant_context(
        messages,
        device_cache,
        char_budget=_FREE_RUN_CONTEXT_CHAR_BUDGET,
    )
    clean_messages = _sanitize_messages(relevant_messages)
    if not clean_messages:
        return None

    system_text = (
        (
            "=== ACTIVE INCIDENT CONTEXT ===\n"
            f"{incident_context}\n"
            "================================\n\n"
        )
        + "You are answering a follow-up question about an active incident. "
        + "The conversation already contains recorded troubleshoot evidence for this incident. "
        + "Use that existing evidence first. Do not propose rerunning the same command unless the "
        + "user explicitly asks for a fresh live re-check or the recorded evidence is insufficient.\n\n"
        + SSH_SYNTHESIS_PROMPT.format(device_cache_section=_build_cache_section(device_cache))
    )
    instruction = HumanMessage(
        content=(
            "[System: Use recorded incident evidence first]\n"
            "Answer the latest user request directly from the recorded troubleshoot evidence "
            "already present in this session.\n"
            "Do not call tools.\n"
            "Do not pretend a fresh check happened.\n"
            "If the recorded evidence includes failed SSH access, say so clearly and base the "
            "next-step guidance on that recorded limitation.\n"
            "If the existing evidence already supports the answer, do not ask to rerun the same command.\n"
            f"Latest user request: {user_query}"
        )
    )

    progress_callback = (progress_sink or {}).get("callback")
    if progress_callback:
        progress_callback({
            "kind": "status",
            "text": "Answering from recorded incident evidence...",
        })

    response = answer_llm.invoke([SystemMessage(content=system_text), *clean_messages, instruction])
    content = _THINK_RE.sub("", str(response.content or "")).strip()
    if not content:
        return None
    return AIMessage(content=content, additional_kwargs={"phase": "existing_evidence_followup"})


def _iter_tool_messages(*message_groups: list[BaseMessage]) -> Any:
    for group in message_groups:
        if not group:
            continue
        for message in group:
            if isinstance(message, ToolMessage):
                yield message


def _extract_run_cli_items(*message_groups: list[BaseMessage]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for message in _iter_tool_messages(*message_groups):
        if getattr(message, "name", "") not in _EXECUTION_TOOL_NAMES:
            continue
        metadata = getattr(message, "additional_kwargs", {}) or {}
        args = metadata.get("tool_args", {})
        status = str(metadata.get("tool_status", "") or "unknown").strip() or "unknown"
        requested_host = str(args.get("host", "") or "").strip()
        command = str(
            metadata.get("executed_command")
            or args.get("command", "")
            or extract_executed_command(str(message.content))
            or ""
        ).strip()
        host, ip, os_type, body = parse_output(str(message.content))
        resolved_host = host or requested_host or "unknown"
        items.append({
            "host": resolved_host,
            "status": status,
            "command": command,
            "body": body or str(message.content),
        })
    return items


def _update_cache_from_lookup_result(device_cache: dict, raw_result: str) -> None:
    try:
        data = json.loads(raw_result)
    except json.JSONDecodeError:
        return

    if isinstance(data, dict) and "hostname" in data and "error" not in data:
        device_cache[data["hostname"]] = {
            "ip_address": data.get("ip_address", ""),
            "os_platform": data.get("os_platform", ""),
            "device_role": data.get("device_role", ""),
            "site": data.get("site", ""),
            "version": data.get("version", ""),
        }
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "hostname" in item:
                device_cache[item["hostname"]] = {
                    "ip_address": item.get("ip_address", ""),
                    "os_platform": item.get("os_platform", ""),
                    "device_role": item.get("device_role", ""),
                    "site": item.get("site", ""),
                    "version": item.get("version", ""),
                }


def _is_terminal_tool_output(output: str) -> bool:
    stripped = output.strip()
    return any(stripped.startswith(prefix) for prefix in _TERMINAL_ERROR_PREFIXES)


def _is_tool_command_error(output: str) -> bool:
    _host, _ip, _os_type, body = parse_output(output)
    return is_command_error(body or output)


def _condense_run_cli_output(*, command: str, status: str, raw_body: str) -> str:
    """Return an LLM-friendly summary while preserving key evidence."""
    body = strip_tool_metadata(raw_body or "").strip()
    if not body:
        return "(empty output)"

    if status == "error":
        return body.splitlines()[0].strip()

    lowered_command = (command or "").strip().lower()
    if lowered_command == "show version":
        return _summarize_show_version(body)
    if "cdp neighbors detail" in lowered_command or "lldp neighbors detail" in lowered_command:
        return _summarize_neighbor_details(body)
    if "show running-config | section" in lowered_command or "show run | section" in lowered_command:
        return _summarize_config_section(body)

    return body


def _summarize_show_version(body: str) -> str:
    """Condense verbose ``show version`` output into key operational facts."""
    version_match = re.search(r"Version\s+([^,\\n]+)", body, re.IGNORECASE)
    uptime_match = re.search(r"^(.+?) uptime is (.+)$", body, re.MULTILINE)
    image_match = re.search(r'System image file is "([^"]+)"', body)
    register_match = re.search(r"Configuration register is (.+)$", body, re.MULTILINE)

    lines = ["show version summary:"]
    if version_match:
        lines.append(f"- version: {version_match.group(1).strip()}")
    if uptime_match:
        lines.append(f"- uptime: {uptime_match.group(2).strip()}")
    if image_match:
        lines.append(f"- system image: {image_match.group(1).strip()}")
    if register_match:
        lines.append(f"- config register: {register_match.group(1).strip()}")
    if len(lines) == 1:
        first_lines = [line.strip() for line in body.splitlines()[:4] if line.strip()]
        lines.extend(f"- {line}" for line in first_lines)
    return "\n".join(lines)


def _summarize_neighbor_details(body: str) -> str:
    """Condense CDP/LLDP detail output into neighbor facts."""
    device_ids = re.findall(r"Device ID:\s*(.+)", body)
    mgmt_ips = re.findall(r"(?:IP address|Management Address):\s*([0-9.]+)", body, re.IGNORECASE)
    local_ports = re.findall(r"Interface:\s*([^,]+)", body)
    remote_ports = re.findall(r"Port ID \(outgoing port\):\s*(.+)", body)

    count = max(len(device_ids), len(mgmt_ips), len(local_ports), len(remote_ports))
    lines = [f"neighbor summary: entries={count}"]
    for idx in range(count):
        parts = []
        if idx < len(device_ids):
            parts.append(f"neighbor={device_ids[idx].strip()}")
        if idx < len(mgmt_ips):
            parts.append(f"mgmt_ip={mgmt_ips[idx].strip()}")
        if idx < len(local_ports):
            parts.append(f"local_intf={local_ports[idx].strip()}")
        if idx < len(remote_ports):
            parts.append(f"remote_port={remote_ports[idx].strip()}")
        if parts:
            lines.append("- " + ", ".join(parts))
    if len(lines) == 1:
        first_lines = [line.strip() for line in body.splitlines()[:12] if line.strip()]
        lines.extend(f"- {line}" for line in first_lines)
    return "\n".join(lines)


def _summarize_config_section(body: str) -> str:
    """Keep focused config sections compact enough for synthesis."""
    non_empty = [line.rstrip() for line in body.splitlines() if line.strip()]
    if len(non_empty) <= 24:
        return "\n".join(non_empty)

    head = non_empty[:18]
    tail = non_empty[-4:]
    omitted = len(non_empty) - len(head) - len(tail)
    lines = ["config section summary:"]
    lines.extend(f"- {line}" for line in head)
    lines.append(f"- ... omitted {omitted} lines ...")
    lines.extend(f"- {line}" for line in tail)
    return "\n".join(lines)


def _execute_run_cli_batch(
    tool_calls: list[dict[str, Any]],
    *,
    progress_sink: dict | None = None,
    run_cli_tool,
    run_diagnostic_tool=None,
    executed_calls: set[tuple[str, str]],
    terminal_failures: set[str],
) -> list[dict[str, Any]]:
    """Execute multiple execution tool calls, sequential per host, parallel across hosts."""
    results: list[dict[str, Any]] = []
    # Group valid (non-blocked) calls by host for sequential execution per device.
    host_queues: dict[str, list[tuple[int, dict, dict]]] = {}
    # total count for progress reporting
    valid_count = 0

    progress_callback = (progress_sink or {}).get("callback")

    for index, tc in enumerate(tool_calls):
        args = dict(tc["args"])
        tool_name = str(tc.get("name", "") or "")
        host = str(args.get("host", "") or "").strip()
        if tool_name == "run_diagnostic":
            signature_detail = (
                f"{tool_name}:"
                f"{str(args.get('kind', '') or '').strip().lower()}:"
                f"{str(args.get('target', '') or '').strip()}:"
                f"{args.get('count', 2)}:{args.get('timeout', 1)}"
            )
        else:
            signature_detail = str(args.get("command", "") or "").strip()
        tool_metadata = {"tool_args": tc.get("args", {})}
        command_sig = (host, signature_detail)

        if host in terminal_failures:
            output = (
                f"[BLOCKED] Host '{host}' already returned a terminal connectivity "
                "failure in this turn. Summarize the limitation instead of retrying."
            )
            tool_metadata["tool_status"] = "blocked"
            results.append({
                "index": index,
                "tc": tc,
                "output": output,
                "tool_metadata": tool_metadata,
            })
            continue

        if command_sig in executed_calls:
            output = (
                f"[BLOCKED] Command '{command}' was already executed on '{host}' "
                "in this turn. Do not repeat the same command without new evidence."
            )
            tool_metadata["tool_status"] = "blocked"
            results.append({
                "index": index,
                "tc": tc,
                "output": output,
                "tool_metadata": tool_metadata,
            })
            continue

        executed_calls.add(command_sig)
        host_queues.setdefault(host, []).append((index, tc, tool_metadata))
        valid_count += 1

    if not host_queues:
        return results

    # For each host, run its commands sequentially to avoid flooding the
    # device with parallel SSH handshakes.  Different hosts run in parallel.
    completed_count = 0
    completed_lock = threading.Lock()

    def _run_host_queue(
        host: str, queue: list[tuple[int, dict, dict]]
    ) -> list[dict[str, Any]]:
        nonlocal completed_count
        host_results: list[dict[str, Any]] = []
        for index, tc, tool_metadata in queue:
            # Re-check terminal failures — an earlier command in this queue
            # may have marked the host as terminal.
            if host in terminal_failures:
                output = (
                    f"[BLOCKED] Host '{host}' already returned a terminal connectivity "
                    "failure in this turn. Summarize the limitation instead of retrying."
                )
                tool_metadata["tool_status"] = "blocked"
            else:
                if tc.get("name") == "run_diagnostic":
                    if run_diagnostic_tool is None:
                        output = "[BLOCKED] Diagnostic tool is not configured for this graph."
                    else:
                        output = run_diagnostic_tool.invoke(dict(tc["args"]))
                        tool_metadata["executed_command"] = extract_executed_command(output)
                else:
                    output = run_cli_tool.invoke(dict(tc["args"]))
                if output.startswith("[BLOCKED]"):
                    tool_metadata["tool_status"] = "blocked"
                elif output.startswith("[ERROR]") or _is_terminal_tool_output(output) or _is_tool_command_error(output):
                    tool_metadata["tool_status"] = "error"
                else:
                    tool_metadata["tool_status"] = "success"
                if _is_terminal_tool_output(output):
                    terminal_failures.add(host)

            host_results.append({
                "index": index,
                "tc": tc,
                "output": output,
                "tool_metadata": tool_metadata,
            })
            with completed_lock:
                completed_count += 1
                if progress_callback and valid_count > 1:
                    progress_callback({
                        "kind": "status",
                        "text": f"Collected {completed_count}/{valid_count} CLI results ...",
                    })
        return host_results

    with ThreadPoolExecutor(max_workers=_FREE_RUN_MAX_PARALLEL_RUN_CLI) as executor:
        host_futures = {
            executor.submit(_run_host_queue, host, queue): host
            for host, queue in host_queues.items()
        }
        for future in as_completed(host_futures):
            results.extend(future.result())

    results.sort(key=lambda item: item["index"])
    return results


def free_run_node(
    state: dict,
    llm: SupportsToolBinding,
    answer_llm: SupportsInvoke,
    run_cli_tool,
    run_diagnostic_tool=None,
    progress_sink: dict | None = None,
) -> dict:
    device_cache: dict = state.get("device_cache", {})
    messages: list[BaseMessage] = list(state["messages"])
    incident_context: str = state.get("incident_context", "") or ""
    user_query = _find_user_query(messages)

    compact_prompt, cache_section = _build_compact_prompt(
        device_cache=device_cache,
        incident_context=incident_context,
        user_query=user_query,
    )
    compact_system_msg = SystemMessage(content=compact_prompt)
    # Synthesis prompt is built lazily only when tools were executed (see below)

    relevant_messages = _assemble_relevant_context(
        messages,
        device_cache,
        char_budget=_FREE_RUN_CONTEXT_CHAR_BUDGET,
    )
    prior_tool_messages = list(_iter_tool_messages(messages))
    clean_msgs = _sanitize_messages(relevant_messages)

    if _should_answer_from_existing_incident_evidence(
        user_query=user_query,
        messages=messages,
        incident_context=incident_context,
    ):
        existing_evidence_answer = _answer_from_existing_incident_evidence(
            answer_llm=answer_llm,
            user_query=user_query,
            messages=messages,
            device_cache=device_cache,
            incident_context=incident_context,
            progress_sink=progress_sink,
        )
        if existing_evidence_answer is not None:
            return {
                "messages": [existing_evidence_answer],
                "device_cache": device_cache,
                "grounded_entities": {
                    "devices": sorted(device_cache.keys()),
                    "latest_query": user_query,
                },
                "active_device": "",
                "active_topic": user_query,
            }

    tools = [
        lookup_device,
        list_all_devices,
        run_cli_tool,
        search_logs,
        search_incidents,
        get_incident_detail,
    ]
    if run_diagnostic_tool is not None:
        tools.insert(3, run_diagnostic_tool)
    llm_with_tools = llm.bind_tools(tools)
    loop_messages: list[BaseMessage] = [compact_system_msg] + clean_msgs
    result_messages: list[BaseMessage] = []
    executed_calls: set[tuple[str, str]] = set()
    terminal_failures: set[str] = set()
    latest_grounded_host = ""
    has_executed_tools = False

    is_troubleshoot = bool(_TROUBLESHOOT_RE.search(user_query))
    max_iter = (
        _FREE_RUN_MAX_ITERATIONS_TROUBLESHOOT
        if is_troubleshoot
        else _FREE_RUN_MAX_ITERATIONS
    )

    for _ in range(max_iter):
        # For troubleshoot queries, clear terminal failures between iterations
        # so the LLM can retry commands after a transient SSH timeout.
        # The stale connection is already invalidated by ssh_executor; a fresh
        # connection will be opened on the next attempt.
        if is_troubleshoot:
            terminal_failures.clear()
        response = llm_with_tools.invoke(loop_messages)
        raw_content = response.content or ""
        if response.content:
            response = AIMessage(
                content=_THINK_RE.sub("", response.content).strip(),
                tool_calls=response.tool_calls,
            )

        if not response.tool_calls:
            # If content was entirely <think> tags and nothing else, the LLM
            # may have "thought" internally without producing a visible answer.
            # Nudge it to produce an actual text answer.
            if not response.content and raw_content.strip():
                logger.warning(
                    "LLM response was entirely <think> block with no visible "
                    "content and no tool calls; nudging for explicit answer"
                )
                loop_messages.append(response)
                loop_messages.append(
                    HumanMessage(content="[System] Please provide your answer as plain text, not inside <think> tags.")
                )
                continue
            if has_executed_tools and _requires_full_topology_coverage(user_query):
                missing_hosts = _missing_run_cli_hosts(
                    device_cache,
                    prior_tool_messages,
                    result_messages,
                )
                if missing_hosts:
                    missing_text = ", ".join(missing_hosts[:6])
                    if len(missing_hosts) > 6:
                        missing_text += ", ..."
                    loop_messages.append(
                        HumanMessage(
                            content=(
                                "[System: Coverage reminder]\n"
                                "The user asked for relationships/topology across "
                                "all devices, but you only collected direct CLI "
                                "evidence for part of the current scope.\n"
                                f"Devices still without direct CLI evidence: {missing_text}\n"
                                "Continue gathering evidence for the remaining "
                                "in-scope devices, or if a device truly does not "
                                "need direct checks, say so explicitly and support "
                                "that limitation from evidence before stopping."
                            )
                        )
                    )
                    continue
            if (
                has_executed_tools
                and _requires_logical_topology(user_query)
                and _requires_full_topology_coverage(user_query)
            ):
                missing_logical_hosts = _missing_logical_hosts(
                    device_cache,
                    prior_tool_messages,
                    result_messages,
                )
                if missing_logical_hosts:
                    missing_text = ", ".join(missing_logical_hosts[:6])
                    if len(missing_logical_hosts) > 6:
                        missing_text += ", ..."
                    loop_messages.append(
                        HumanMessage(
                            content=(
                                "[System: Coverage reminder]\n"
                                "The user explicitly asked for logical topology "
                                "across all devices, but you still lack direct "
                                "logical/control-plane evidence for some routing-"
                                f"capable devices: {missing_text}\n"
                                "Continue gathering protocol/control-plane "
                                "evidence for the remaining devices, or explain "
                                "from evidence why a device does not need that "
                                "check before stopping."
                            )
                        )
                    )
                    continue
            if (
                has_executed_tools
                and _requires_logical_topology(user_query)
                and not _has_logical_evidence(prior_tool_messages, result_messages)
            ):
                loop_messages.append(
                    HumanMessage(
                        content=(
                            "[System: Coverage reminder]\n"
                            "The user explicitly asked for logical topology or "
                            "logical relationships.\n"
                            "You already have some evidence, but you do not yet "
                            "have routing/control-plane relationship evidence.\n"
                            "Continue gathering at least one relevant logical "
                            "evidence set before stopping."
                        )
                    )
                )
                continue
            if response.content and not has_executed_tools:
                result_messages.append(response)
            break

        result_messages.append(response)
        loop_messages.append(response)
        iteration_terminal = True
        has_executed_tools = True

        execution_calls = [
            tc for tc in response.tool_calls
            if tc["name"] in _EXECUTION_TOOL_NAMES
        ]
        execution_results_by_id: dict[str, dict[str, Any]] = {}
        if execution_calls:
            progress_callback = (progress_sink or {}).get("callback")
            if progress_callback and len(execution_calls) > 1:
                progress_callback({
                    "kind": "status",
                    "text": f"Running {len(execution_calls)} execution checks in parallel ...",
                })
            batch_results = _execute_run_cli_batch(
                execution_calls,
                progress_sink=progress_sink,
                run_cli_tool=run_cli_tool,
                run_diagnostic_tool=run_diagnostic_tool,
                executed_calls=executed_calls,
                terminal_failures=terminal_failures,
            )
            execution_results_by_id = {
                item["tc"]["id"]: item for item in batch_results
            }

        for tc in response.tool_calls:
            if tc["name"] == "lookup_device":
                tool_metadata = {"tool_args": tc.get("args", {})}
                output = lookup_device.invoke(tc["args"])
                _update_cache_from_lookup_result(device_cache, output)
                try:
                    data = json.loads(output)
                    if isinstance(data, dict) and "hostname" in data and "error" not in data:
                        latest_grounded_host = data["hostname"]
                except json.JSONDecodeError:
                    pass
            elif tc["name"] == "list_all_devices":
                tool_metadata = {"tool_args": tc.get("args", {})}
                output = list_all_devices.invoke(tc["args"])
                _update_cache_from_lookup_result(device_cache, output)
            elif tc["name"] in _EXECUTION_TOOL_NAMES:
                args = dict(tc["args"])
                host = str(args.get("host", "") or "").strip()
                if host:
                    latest_grounded_host = host
                batch_result = execution_results_by_id[tc["id"]]
                output = batch_result["output"]
                tool_metadata = batch_result["tool_metadata"]
            elif tc["name"] == "search_logs":
                tool_metadata = {"tool_args": tc.get("args", {})}
                output = search_logs.invoke(tc["args"])
            elif tc["name"] == "search_incidents":
                tool_metadata = {"tool_args": tc.get("args", {})}
                output = search_incidents.invoke(tc["args"])
            elif tc["name"] == "get_incident_detail":
                tool_metadata = {"tool_args": tc.get("args", {})}
                output = get_incident_detail.invoke(tc["args"])
            else:
                tool_metadata = {"tool_args": tc.get("args", {})}
                output = f"[ERROR] Unknown tool: {tc['name']}"

            tool_msg = ToolMessage(
                content=output,
                tool_call_id=tc["id"],
                name=tc["name"],
                additional_kwargs=tool_metadata,
            )
            result_messages.append(tool_msg)
            loop_messages.append(tool_msg)
            if not output.startswith("[BLOCKED]"):
                iteration_terminal = False

        # Refresh the system prompt when device_cache changed (after inventory
        # lookup), so the LLM sees newly grounded devices in the cache section
        # without having to parse old tool-result messages.
        new_cache_section = _build_cache_section(device_cache)
        if new_cache_section != cache_section:
            compact_prompt, cache_section = _build_compact_prompt(
                device_cache=device_cache,
                incident_context=incident_context,
                user_query=user_query,
            )
            loop_messages[0] = SystemMessage(content=compact_prompt)

        if terminal_failures and not is_troubleshoot:
            break
        if iteration_terminal:
            break

    # Use the latest cache state for synthesis (may have been updated during loop)
    # Fast path: if no tools were executed, the tool LLM already produced a
    # direct conversational reply (e.g. "สวัสดี", clarification questions).
    # Skip the expensive reasoning-mode synthesis pass entirely.
    if not has_executed_tools:
        return {
            "messages": result_messages,
            "device_cache": device_cache,
            "grounded_entities": {
                "devices": sorted(device_cache.keys()),
                "latest_query": user_query,
            },
            "active_device": latest_grounded_host,
            "active_topic": user_query,
        }

    full_system_msg = SystemMessage(
        content=SSH_SYNTHESIS_PROMPT.format(device_cache_section=_build_cache_section(device_cache))
    )
    synthesized = _synthesize_final_answer(
        answer_llm=answer_llm,
        system_msg=full_system_msg,
        device_cache=device_cache,
        original_context=clean_msgs,
        session_messages=messages,
        result_messages=result_messages,
        progress_sink=progress_sink,
        user_query=user_query,
    )
    if synthesized is not None:
        result_messages.append(synthesized)

    return {
        "messages": result_messages,
        "device_cache": device_cache,
        "grounded_entities": {
            "devices": sorted(device_cache.keys()),
            "latest_query": user_query,
        },
        "active_device": latest_grounded_host,
        "active_topic": user_query,
    }


def _synthesize_final_answer(
    *,
    answer_llm: SupportsInvoke,
    system_msg: SystemMessage,
    device_cache: dict[str, Any],
    original_context: list[BaseMessage],
    session_messages: list[BaseMessage],
    result_messages: list[BaseMessage],
    progress_sink: dict | None = None,
    user_query: str,
) -> AIMessage | None:
    """Run a final no-tools synthesis pass over executed evidence."""
    if not result_messages:
        return None

    clean_results = _sanitize_messages(result_messages)
    evidence_digest = _build_evidence_digest(result_messages)
    trace_symmetry_digest, trace_symmetry_analyses = _assess_traceroute_symmetry(result_messages)
    route_context_digest, route_context = _build_route_context_digest(
        user_query=user_query,
        device_cache=device_cache,
    )
    coverage_digest = _build_scope_coverage_digest(
        device_cache=device_cache,
        session_messages=session_messages,
        result_messages=result_messages,
    )
    session_evidence_digest = _build_session_evidence_digest(
        session_messages=session_messages,
        result_messages=result_messages,
    )
    relationship_instruction = _relationship_analysis_instruction(user_query)
    route_path_instruction = _route_path_instruction(user_query, route_context_digest)
    trace_symmetry_instruction = _trace_symmetry_instruction(user_query, trace_symmetry_digest)
    inventory_only_instruction = _inventory_only_instruction(result_messages)
    synthesis_instruction = HumanMessage(
        content=(
            "[System: Final answer required]\n"
            "Answer the latest user request directly from the evidence above.\n"
            "Do not call tools.\n"
            "Do not dump raw CLI output unless the user explicitly asked for it.\n"
            "Write one final answer only.\n"
            "Do not repeat the same conclusion in multiple formats.\n"
            "Do not repeat earlier capability explanations, menus of options, "
            "or planning text once execution has already happened.\n"
            "Focus on the latest user request, not earlier tentative answers.\n"
            "Treat earlier assistant summaries as non-authoritative context. "
            "Prefer executed tool evidence and the evidence digests below over "
            "any earlier assistant wording.\n"
            "Match the user's language (Thai→Thai, English→English).\n"
            "Lead with the verdict, then explain the evidence.\n"
            "For single-device protocol checks, interpret what the state means "
            "operationally — do not sound like a raw field dump.\n"
            "For multi-device checks, summarize counts, failed hosts, "
            "and reasons from the tool results.\n"
            "The digests below distinguish current-turn execution from "
            "cumulative session evidence.\n"
            "If you mention what ran in this round, use the current-turn "
            "counts exactly.\n"
            "If you mention follow-up progress across the same session, such "
            "as 'now all devices have been checked', use the session coverage "
            "facts exactly and say that it is based on checks gathered across "
            "this session.\n"
            "Do not mix current-turn run counts with cumulative session "
            "coverage counts.\n"
            "For logical-topology requests, use the logical-coverage facts "
            "exactly.\n"
            "Do not claim full logical coverage when the logical-coverage "
            "digest still shows routing-capable devices without direct "
            "protocol/control-plane evidence.\n"
            f"{inventory_only_instruction}"
            f"{relationship_instruction}"
            f"{route_path_instruction}"
            f"{trace_symmetry_instruction}"
            "IMPORTANT: The evidence digest below contains exact verified counts "
            "from executed tools. You MUST use these exact numbers in your answer. "
            "Do not recalculate or invent different numbers.\n"
            f"{coverage_digest}\n"
            f"{evidence_digest}\n"
            f"{route_context_digest}\n"
            f"{session_evidence_digest}\n"
            f"Latest user request: {user_query}"
        )
    )

    synthesis_msgs = [
        system_msg,
        *_build_synthesis_context(original_context),
        *clean_results,
        synthesis_instruction,
    ]
    response = answer_llm.invoke(synthesis_msgs)
    content = _THINK_RE.sub("", str(response.content or "")).strip()
    if not content:
        # Retry once — some models occasionally produce only <think> content
        logger.warning("Synthesis returned empty after <think> strip; retrying once")
        response = answer_llm.invoke(synthesis_msgs)
        content = _THINK_RE.sub("", str(response.content or "")).strip()
    if not content:
        return None
    phase = "final_synthesis"
    progress_callback = (progress_sink or {}).get("callback")
    if progress_callback:
        progress_callback({
            "kind": "status",
            "text": "Synthesizing final answer from evidence...",
        })
    stats = _parse_run_cli_stats(result_messages)
    if stats and _BATCH_REACHABILITY_RE.search(user_query or ""):
        if _answer_matches_stats(content, stats):
            return AIMessage(content=content, additional_kwargs={"phase": phase})
        if progress_callback:
            progress_callback({
                "kind": "status",
                "text": "Verifying counts and polishing final answer...",
            })
        content = _repair_answer_with_exact_facts(
            answer_llm=answer_llm,
            system_msg=system_msg,
            candidate_answer=content,
            evidence_digest=evidence_digest,
            stats=stats,
            user_query=user_query,
        )
        phase = "consistency_repair"
    elif _trace_symmetry_answer_needs_repair(content, user_query, trace_symmetry_analyses):
        if progress_callback:
            progress_callback({
                "kind": "status",
                "text": "Correcting traceroute path analysis...",
            })
        content = _repair_trace_symmetry_answer(
            answer_llm=answer_llm,
            system_msg=system_msg,
            candidate_answer=content,
            trace_symmetry_digest=trace_symmetry_digest,
            evidence_digest=evidence_digest,
            user_query=user_query,
        )
        phase = "symmetry_repair"
    elif _route_answer_needs_repair(content, user_query, route_context):
        if progress_callback:
            progress_callback({
                "kind": "status",
                "text": "Polishing route-path explanation...",
            })
        content = _repair_route_answer(
            answer_llm=answer_llm,
            system_msg=system_msg,
            candidate_answer=content,
            route_context_digest=route_context_digest,
            evidence_digest=evidence_digest,
            user_query=user_query,
        )
        phase = "route_repair"
    elif _relationship_answer_needs_repair(content, user_query):
        if progress_callback:
            progress_callback({
                "kind": "status",
                "text": "Polishing topology answer...",
            })
        content = _repair_relationship_answer(
            answer_llm=answer_llm,
            system_msg=system_msg,
            candidate_answer=content,
            coverage_digest=coverage_digest,
            evidence_digest=evidence_digest,
            session_evidence_digest=session_evidence_digest,
            user_query=user_query,
        )
        phase = "topology_repair"
    return AIMessage(content=content, additional_kwargs={"phase": phase})


def _relationship_analysis_instruction(user_query: str) -> str:
    if not _RELATIONSHIP_ANALYSIS_RE.search(user_query or ""):
        return ""
    return (
        "If the user is asking for relationships, topology, or dependencies, "
        "answer with a clear separation between confirmed relationships and "
        "inference.\n"
        "State whether the evidence is sufficient for a full map or only a "
        "partial map.\n"
        "Prefer explaining physical adjacency, L3/routing relationships, and "
        "control-plane relationships separately when relevant.\n"
        "Do not claim a complete topology if the executed evidence only proves "
        "part of the relationships.\n"
        "If adjacency evidence is present, list the confirmed links explicitly "
        "as device-to-device connections before giving any higher-level "
        "topology summary.\n"
        "When possible, write confirmed links in a compact form such as "
        "`Device-A <-> Device-B`.\n"
        "Use a `Confirmed links` section before `Topology interpretation` when "
        "the evidence supports it.\n"
        "Do not reduce adjacency evidence to only counts of neighbors.\n"
        "If routing or control-plane neighbor evidence exists, add a separate "
        "`Logical relationships` section.\n"
        "`show ip protocols` proves protocol presence/configuration or "
        "redistribution, not active adjacency by itself.\n"
        "Do not turn `show ip protocols` into confirmed neighbors unless "
        "neighbor/peer evidence supports that statement directly.\n"
        "Do not present route-table next hops as confirmed protocol "
        "adjacencies unless neighbor/peer evidence supports that statement "
        "directly.\n"
        "When enough evidence exists, prefer this section order: "
        "`Scope/coverage`, `Confirmed physical links`, `Logical relationships`, "
        "`Topology interpretation`, `Limitations`.\n"
        "If logical evidence exists for only part of the network, say that "
        "explicitly instead of implying full logical coverage.\n"
        "Prefer short bullets or one-link-per-line lists over wide markdown "
        "tables for large topology summaries so the answer stays complete and "
        "readable.\n"
        "Avoid large ASCII topology diagrams and avoid repeating the same "
        "topology twice in different formats.\n"
        "For `Confirmed physical links`, prefer markdown bullets with exactly "
        "one link per line, rather than prose or multiple links on one line.\n"
        "For `Logical relationships`, prefer bullets with one adjacency or "
        "peer relationship per line, optionally grouped by protocol.\n"
        "For `Topology interpretation`, prefer short bullets grouped by layer, "
        "role, or site rather than a dense paragraph.\n"
        "For `Limitations`, prefer short bullet points rather than tables.\n"
        "Explain the topology in operational terms such as core, distribution, "
        "management, branch, access, or uplink roles when the evidence "
        "supports that interpretation.\n"
    )


def _inventory_only_instruction(result_messages: list[BaseMessage]) -> str:
    has_inventory_tool = False
    has_execution_tool = False
    for message in result_messages:
        if not isinstance(message, ToolMessage):
            continue
        tool_name = getattr(message, "name", "")
        if tool_name in {"lookup_device", "list_all_devices"}:
            has_inventory_tool = True
        elif tool_name in _EXECUTION_TOOL_NAMES:
            has_execution_tool = True
    if not has_inventory_tool or has_execution_tool:
        return ""
    return (
        "IMPORTANT: The executed evidence is inventory-only and contains no "
        "live CLI verification.\n"
        "Answer only from inventory facts such as hostname, IP, role, site, "
        "and version.\n"
        "Do NOT claim devices are reachable, healthy, operational, ready, up, "
        "or verified live from inventory alone.\n"
    )


def _build_route_context_digest(
    user_query: str,
    device_cache: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    analysis = _analyze_routing_inventory(user_query, device_cache)
    if not (
        analysis.get("recommended_hosts")
        or analysis.get("ip_contexts")
        or analysis.get("prefix_contexts")
    ):
        return "", {
            "analysis": analysis,
            "exact_owner_hosts": [],
            "exact_owner_interfaces": [],
        }

    lines = ["[Route Context Digest]"]
    mentioned_hosts = analysis.get("mentioned_hosts", [])
    if mentioned_hosts:
        lines.append("- mentioned_hosts=" + " ; ".join(mentioned_hosts[:4]))

    recommended_hosts = analysis.get("recommended_hosts", [])
    candidate_reasons = analysis.get("candidate_reasons", {})
    if recommended_hosts:
        recommended_parts: list[str] = []
        for hostname in recommended_hosts[:5]:
            reasons = candidate_reasons.get(hostname, [])
            if reasons:
                recommended_parts.append(f"{hostname} ({'; '.join(reasons[:2])})")
            else:
                recommended_parts.append(hostname)
        lines.append("- recommended_start_hosts=" + " ; ".join(recommended_parts))

    exact_owner_hosts: list[str] = []
    exact_owner_interfaces: list[str] = []

    for context in analysis.get("ip_contexts", []):
        ip_value = context.get("ip", "")
        exact_matches = context.get("exact_matches", [])
        network_matches = context.get("network_matches", [])
        if exact_matches:
            lines.append(
                f"- queried_ip={ip_value} exact_owner="
                + " ; ".join(_format_interface_context(row) for row in exact_matches[:3])
            )
            for row in exact_matches:
                hostname = str(row.get("hostname", "") or "").strip()
                interface_name = str(row.get("interface_name", "") or "").strip()
                if hostname and hostname not in exact_owner_hosts:
                    exact_owner_hosts.append(hostname)
                if interface_name and interface_name not in exact_owner_interfaces:
                    exact_owner_interfaces.append(interface_name)
        exact_keys = {
            (str(row.get("hostname", "")), str(row.get("interface_name", "")))
            for row in exact_matches
        }
        same_network_matches = [
            row for row in network_matches
            if (str(row.get("hostname", "")), str(row.get("interface_name", ""))) not in exact_keys
        ]
        if same_network_matches:
            lines.append(
                f"- queried_ip={ip_value} same_network_candidates="
                + " ; ".join(_format_interface_context(row) for row in same_network_matches[:3])
            )

    for context in analysis.get("prefix_contexts", []):
        prefix_value = context.get("normalized_prefix", "") or context.get("prefix", "")
        exact_matches = context.get("exact_matches", [])
        overlapping_matches = context.get("overlapping_matches", [])
        if exact_matches:
            lines.append(
                f"- queried_prefix={prefix_value} exact_owner="
                + " ; ".join(_format_interface_context(row) for row in exact_matches[:3])
            )
            for row in exact_matches:
                hostname = str(row.get("hostname", "") or "").strip()
                interface_name = str(row.get("interface_name", "") or "").strip()
                if hostname and hostname not in exact_owner_hosts:
                    exact_owner_hosts.append(hostname)
                if interface_name and interface_name not in exact_owner_interfaces:
                    exact_owner_interfaces.append(interface_name)
        if overlapping_matches:
            lines.append(
                f"- queried_prefix={prefix_value} overlaps="
                + " ; ".join(_format_interface_context(row) for row in overlapping_matches[:3])
            )

    return "\n".join(lines), {
        "analysis": analysis,
        "exact_owner_hosts": exact_owner_hosts,
        "exact_owner_interfaces": exact_owner_interfaces,
    }


def _route_path_instruction(user_query: str, route_context_digest: str) -> str:
    if not route_context_digest:
        return ""
    if not _ROUTING_QUERY_RE.search(user_query or ""):
        return ""
    return (
        "For routing / best-path questions:\n"
        "- explain the path in order from source toward destination.\n"
        "- for each proven step, mention the egress interface, next hop, and "
        "transit network or tunnel when the CLI evidence proves it.\n"
        "- when the route context digest identifies an exact destination owner, "
        "state the final destination as a complete device/interface phrase, not "
        "just a bare IP.\n"
        "- if answering in Thai, keep labels in Thai or neutral technical terms "
        "consistently, and avoid half-translated fragments such as `target device`.\n"
        "- if redistribution appears in the evidence, keep protocol statements "
        "scoped to the device where that evidence was observed.\n"
    )


def _trace_symmetry_instruction(user_query: str, trace_symmetry_digest: str) -> str:
    if not trace_symmetry_digest:
        return ""
    if not _TRACE_SYMMETRY_QUERY_RE.search(user_query or "") and "traceroute" not in (user_query or "").lower():
        return ""
    return (
        "For forward/reverse traceroute comparison:\n"
        "- compare the reverse traceroute after reversing its host/link sequence; "
        "do NOT compare hop 1 forward to hop 1 reverse directly.\n"
        "- different interface IPs on opposite ends of the same network can still "
        "represent the same path.\n"
        "- include the forward and reverse hop IPs in the explanation when the "
        "evidence includes them, not just the final verdict.\n"
        "- when possible, mention which forward hop matches which reverse hop "
        "as the same underlying link or device pair.\n"
        "- if the traceroute symmetry digest says `likely_symmetric`, do not call "
        "it asymmetric just because the interface IPs differ by direction.\n"
        "- if the digest says evidence is partial, say that explicitly instead of "
        "forcing a symmetric/asymmetric verdict.\n"
    )


def _route_answer_needs_repair(
    answer: str,
    user_query: str,
    route_context: dict[str, Any],
) -> bool:
    if not _ROUTING_QUERY_RE.search(user_query or ""):
        return False

    text = answer or ""
    if not text:
        return False

    if _THAI_CHAR_RE.search(user_query or "") and _HALF_TRANSLATED_ROUTE_LABEL_RE.search(text):
        return True

    exact_owner_hosts = route_context.get("exact_owner_hosts", [])
    exact_owner_interfaces = route_context.get("exact_owner_interfaces", [])
    has_owner_host = any(host and host.lower() in text.lower() for host in exact_owner_hosts)
    has_owner_interface = any(
        interface_name and interface_name.lower() in text.lower()
        for interface_name in exact_owner_interfaces
    )

    if exact_owner_hosts and not has_owner_host:
        return True
    if _ROUTE_DESTINATION_DETAIL_RE.search(user_query or "") and exact_owner_interfaces and not has_owner_interface:
        return True
    return False


def _repair_route_answer(
    *,
    answer_llm: SupportsInvoke,
    system_msg: SystemMessage,
    candidate_answer: str,
    route_context_digest: str,
    evidence_digest: str,
    user_query: str,
) -> str:
    repair_instruction = HumanMessage(
        content=(
            "[System: Route answer repair]\n"
            "Rewrite the answer below so it matches the route evidence and reads naturally.\n"
            "Write one final answer only.\n"
            "Match the user's language.\n"
            "Lead with the best-path verdict, then walk the path in order.\n"
            "For each proven step, mention the egress interface, next hop, and "
            "transit network or tunnel when evidence supports it.\n"
            "When the route context digest identifies the exact destination owner, "
            "state the final destination as a complete device/interface phrase.\n"
            "If answering in Thai, keep labels in Thai or neutral technical terms "
            "consistently and avoid mixed fragments such as `target device`.\n"
            "Prefer wording like `ปลายทางคือ ...` rather than half-translated labels.\n"
            f"Latest user request: {user_query}\n"
            f"{route_context_digest}\n"
            f"{evidence_digest}\n"
            f"Candidate answer:\n{candidate_answer}"
        )
    )
    repaired = answer_llm.invoke([system_msg, repair_instruction])
    content = _THINK_RE.sub("", str(repaired.content or "")).strip()
    return content or candidate_answer


def _infer_traceroute_target_host(raw_content: str, metadata: dict[str, Any]) -> str:
    args = metadata.get("tool_args", {})
    target = str(args.get("target", "") or "").strip()
    if target:
        resolved = resolve_inventory_record(target)
        if resolved:
            return str(resolved.get("hostname", "") or "").strip()

    resolved_match = _TRACE_RESOLVED_TARGET_RE.search(raw_content or "")
    if resolved_match:
        return resolved_match.group("host").strip()

    target_match = _TRACE_TARGET_EXACT_OWNER_RE.search(raw_content or "")
    if target_match:
        return target_match.group("host").strip()

    return ""


def _normalize_path_nodes(nodes: list[str]) -> list[str]:
    normalized: list[str] = []
    for node in nodes:
        cleaned = str(node or "").strip()
        if not cleaned:
            continue
        if normalized and normalized[-1].lower() == cleaned.lower():
            continue
        normalized.append(cleaned)
    return normalized


def _first_host_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text or "")
    if not match:
        return ""
    return str(match.group("host") or "").strip()


def _parse_traceroute_hop_details(raw_content: str) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for match in _TRACE_HOP_ANNOTATION_RE.finditer(raw_content or ""):
        hop_number = int(match.group("hop"))
        body = match.group("body").strip()
        ip_match = _TRACE_HOP_IP_RE.search(body)
        hop_ip = ip_match.group("ip").strip() if ip_match else ""
        exact_host = _first_host_match(_TRACE_EXACT_HOST_RE, body)
        same_network_host = _first_host_match(_TRACE_SAME_NETWORK_HOST_RE, body)
        if not same_network_host:
            same_network_host = _first_host_match(_TRACE_CONNECTED_CANDIDATE_HOST_RE, body)
        link_nodes = sorted({
            host for host in (exact_host, same_network_host)
            if host
        })
        details.append({
            "hop": hop_number,
            "ip": hop_ip,
            "body": body,
            "exact_host": exact_host,
            "same_network_host": same_network_host,
            "link_signature": tuple(link_nodes),
        })
    return details


def _path_link_sequence(nodes: list[str]) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for left, right in zip(nodes, nodes[1:]):
        left_clean = str(left or "").strip()
        right_clean = str(right or "").strip()
        if not left_clean or not right_clean or left_clean.lower() == right_clean.lower():
            continue
        links.append(tuple(sorted((left_clean, right_clean))))
    return links


def _extract_traceroute_items(result_messages: list[BaseMessage]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in result_messages:
        if not isinstance(message, ToolMessage) or getattr(message, "name", "") != "run_diagnostic":
            continue
        metadata = getattr(message, "additional_kwargs", {}) or {}
        args = metadata.get("tool_args", {})
        if str(args.get("kind", "") or "").strip().lower() != "traceroute":
            continue
        if str(metadata.get("tool_status", "") or "").strip() != "success":
            continue

        raw_content = str(message.content or "")
        requested_source = str(args.get("host", "") or "").strip()
        parsed_host, _ip, _os_type, _body = parse_output(raw_content)
        source_host = parsed_host or requested_source
        target_host = _infer_traceroute_target_host(raw_content, metadata)
        hop_details = _parse_traceroute_hop_details(raw_content)
        hop_hosts = [
            detail["exact_host"]
            for detail in hop_details
            if detail.get("exact_host")
        ]

        nodes = _normalize_path_nodes([source_host, *hop_hosts])
        if target_host and (not nodes or nodes[-1].lower() != target_host.lower()):
            nodes = _normalize_path_nodes([*nodes, target_host])

        if len(nodes) < 2:
            continue

        items.append({
            "source_host": source_host,
            "target_host": target_host,
            "nodes": nodes,
            "links": _path_link_sequence(nodes),
            "hop_details": hop_details,
        })
    return items


def _assess_traceroute_symmetry(result_messages: list[BaseMessage]) -> tuple[str, list[dict[str, Any]]]:
    traceroutes = _extract_traceroute_items(result_messages)
    if len(traceroutes) < 2:
        return "", []

    analyses: list[dict[str, Any]] = []
    used_indexes: set[int] = set()

    for index, item in enumerate(traceroutes):
        if index in used_indexes:
            continue
        source_host = item["source_host"]
        target_host = item["target_host"]
        if not source_host or not target_host:
            continue

        match_index = None
        for candidate_index in range(index + 1, len(traceroutes)):
            if candidate_index in used_indexes:
                continue
            candidate = traceroutes[candidate_index]
            if (
                candidate["source_host"].lower() == target_host.lower()
                and candidate["target_host"].lower() == source_host.lower()
            ):
                match_index = candidate_index
                break

        if match_index is None:
            continue

        used_indexes.add(index)
        used_indexes.add(match_index)
        reverse_item = traceroutes[match_index]
        forward_nodes = item["nodes"]
        reverse_nodes = reverse_item["nodes"]
        forward_links = item["links"]
        reverse_links = reverse_item["links"]
        forward_hops = item.get("hop_details", [])
        reverse_hops = reverse_item.get("hop_details", [])

        verdict = "insufficient_evidence"
        reason = (
            "reverse traceroute exists, but the host/link sequence does not prove a full "
            "symmetric comparison yet"
        )
        if forward_hops and reverse_hops:
            forward_link_signatures = [
                tuple(detail.get("link_signature", ()))
                for detail in forward_hops
                if detail.get("link_signature")
            ]
            reverse_link_signatures = [
                tuple(detail.get("link_signature", ()))
                for detail in reverse_hops
                if detail.get("link_signature")
            ]
            if forward_link_signatures and forward_link_signatures == list(reversed(reverse_link_signatures)):
                verdict = "likely_symmetric"
                reason = "forward hop links match the reverse traceroute when compared as reversed links"
        if verdict == "insufficient_evidence" and forward_nodes and reverse_nodes and all(
            left.lower() == right.lower()
            for left, right in zip(forward_nodes, list(reversed(reverse_nodes)))
        ) and len(forward_nodes) == len(reverse_nodes):
            verdict = "likely_symmetric"
            reason = "forward host path matches the reverse traceroute when read backwards"
        elif verdict == "insufficient_evidence" and forward_links and reverse_links and forward_links == list(reversed(reverse_links)):
            verdict = "likely_symmetric"
            reason = "forward link sequence matches the reverse traceroute when read backwards"
        elif forward_links and reverse_links and set(forward_links).isdisjoint(set(reverse_links)):
            verdict = "likely_asymmetric"
            reason = "forward and reverse traces do not share any common link segments"

        analyses.append({
            "forward": item,
            "reverse": reverse_item,
            "verdict": verdict,
            "reason": reason,
        })

    if not analyses:
        return "", []

    lines = ["[Traceroute Symmetry Digest]"]
    for analysis in analyses:
        forward = analysis["forward"]
        reverse = analysis["reverse"]
        forward_nodes = forward["nodes"]
        reverse_nodes = reverse["nodes"]
        forward_links = forward["links"]
        reverse_links = reverse["links"]
        lines.append(
            f"- pair={forward['source_host']} -> {forward['target_host']} | "
            f"reverse={reverse['source_host']} -> {reverse['target_host']}"
        )
        lines.append("- forward_path_hosts=" + " -> ".join(forward_nodes))
        lines.append("- reverse_path_hosts=" + " -> ".join(reverse_nodes))
        forward_hops = forward.get("hop_details", [])
        reverse_hops = reverse.get("hop_details", [])
        if forward_hops:
            lines.append(
                "- forward_hops="
                + " ; ".join(
                    f"{detail['hop']}:{detail.get('ip', '?')}/{detail.get('exact_host', '?')}"
                    for detail in forward_hops
                )
            )
        if reverse_hops:
            lines.append(
                "- reverse_hops="
                + " ; ".join(
                    f"{detail['hop']}:{detail.get('ip', '?')}/{detail.get('exact_host', '?')}"
                    for detail in reverse_hops
                )
            )
        lines.append(f"- verdict={analysis['verdict']}")
        lines.append(f"- reason={analysis['reason']}")
        if analysis["verdict"] == "likely_symmetric":
            matched_links = [
                f"{left} <-> {right}"
                for left, right in forward_links
            ]
            if matched_links:
                lines.append("- matched_links=" + " ; ".join(matched_links))
            mirrored_hops: list[str] = []
            for forward_detail, reverse_detail in zip(forward_hops, list(reversed(reverse_hops))):
                left_signature = tuple(forward_detail.get("link_signature", ()))
                right_signature = tuple(reverse_detail.get("link_signature", ()))
                if not left_signature or left_signature != right_signature:
                    continue
                mirrored_hops.append(
                    f"f{forward_detail['hop']} {forward_detail.get('ip', '?')}/{forward_detail.get('exact_host', '?')} "
                    f"<-> r{reverse_detail['hop']} {reverse_detail.get('ip', '?')}/{reverse_detail.get('exact_host', '?')} "
                    f"via {' <-> '.join(left_signature)}"
                )
            if mirrored_hops:
                lines.append("- mirrored_hops=" + " ; ".join(mirrored_hops))
        elif analysis["verdict"] == "likely_asymmetric":
            lines.append(
                "- forward_links=" + " ; ".join(f"{left} <-> {right}" for left, right in forward_links)
            )
            lines.append(
                "- reverse_links=" + " ; ".join(f"{left} <-> {right}" for left, right in reverse_links)
            )

    return "\n".join(lines), analyses


def _build_evidence_digest(result_messages: list[BaseMessage]) -> str:
    """Build a compact factual digest from executed tool results."""
    run_cli_items: list[tuple[str, str, str]] = []
    adjacency_links: set[tuple[str, str]] = set()
    logical_links: set[tuple[str, str, str]] = set()
    for item in _extract_run_cli_items(result_messages):
        resolved_host = item["host"]
        status = item["status"]
        command = item["command"]
        body = item["body"]
        reason = ""
        if status == "error":
            first_line = strip_tool_metadata(body).splitlines()[0].strip()
            reason = first_line
        run_cli_items.append((resolved_host, status, command or reason))
        if status == "success":
            adjacency_links.update(
                _extract_adjacency_links(
                    host=resolved_host,
                    command=command,
                    body=body,
                )
            )
            logical_links.update(
                _extract_logical_links(
                    host=resolved_host,
                    command=command,
                    body=body,
                )
            )

    if not run_cli_items:
        return ""

    success_count = sum(1 for _, status, _ in run_cli_items if status == "success")
    error_count = sum(1 for _, status, _ in run_cli_items if status == "error")
    blocked_count = sum(1 for _, status, _ in run_cli_items if status == "blocked")
    lines = [
        "[Evidence Digest]",
        f"run_cli_total={len(run_cli_items)}",
        f"run_cli_success={success_count}",
        f"run_cli_error={error_count}",
        f"run_cli_blocked={blocked_count}",
    ]
    for host, status, detail in run_cli_items:
        lines.append(f"- host={host}, status={status}, detail={detail}")
    if adjacency_links:
        lines.append("[Adjacency Digest]")
        for left, right in sorted(adjacency_links):
            lines.append(f"- {left} <-> {right}")
    if logical_links:
        lines.append("[Logical Relationship Digest]")
        for protocol, left, right in sorted(logical_links):
            lines.append(f"- protocol={protocol}, {left} <-> {right}")
    traceroute_symmetry_digest, _analyses = _assess_traceroute_symmetry(result_messages)
    if traceroute_symmetry_digest:
        lines.append(traceroute_symmetry_digest)
    return "\n".join(lines)


def _build_scope_coverage_digest(
    *,
    device_cache: dict[str, Any],
    session_messages: list[BaseMessage],
    result_messages: list[BaseMessage],
) -> str:
    inventory_hosts = sorted(device_cache)
    current_hosts = sorted({
        item["host"]
        for item in _extract_run_cli_items(result_messages)
        if item["host"] and item["host"] != "unknown"
    })
    session_hosts = sorted({
        item["host"]
        for item in _extract_run_cli_items(session_messages, result_messages)
        if item["host"] and item["host"] != "unknown"
    })
    if not inventory_hosts and not session_hosts and not current_hosts:
        return ""

    session_missing_hosts = [
        host for host in inventory_hosts
        if host not in set(session_hosts)
    ]
    logical_scope_hosts = _logical_scope_hosts(device_cache)
    session_logical_hosts = sorted({
        item["host"]
        for item in _extract_run_cli_items(session_messages, result_messages)
        if _is_logical_evidence_item(item)
    })
    logical_missing_hosts = [
        host for host in logical_scope_hosts
        if host not in set(session_logical_hosts)
    ]

    lines = [
        "[Coverage Digest]",
        f"inventory_hosts_total={len(inventory_hosts)}",
        f"current_turn_hosts_with_direct_cli={len(current_hosts)}",
        f"session_hosts_with_direct_cli={len(session_hosts)}",
        f"session_hosts_without_direct_cli={len(session_missing_hosts)}",
        f"logical_scope_hosts_total={len(logical_scope_hosts)}",
        f"session_hosts_with_logical_evidence={len(session_logical_hosts)}",
        f"session_hosts_without_logical_evidence={len(logical_missing_hosts)}",
    ]
    if current_hosts:
        lines.append("- current_turn_hosts=" + ", ".join(current_hosts))
    if session_hosts:
        lines.append("- session_hosts=" + ", ".join(session_hosts))
    if session_missing_hosts:
        lines.append("- session_missing_hosts=" + ", ".join(session_missing_hosts))
    if session_logical_hosts:
        lines.append("- session_logical_hosts=" + ", ".join(session_logical_hosts))
    if logical_missing_hosts:
        lines.append("- logical_missing_hosts=" + ", ".join(logical_missing_hosts))
    return "\n".join(lines)


def _build_session_evidence_digest(
    *,
    session_messages: list[BaseMessage],
    result_messages: list[BaseMessage],
) -> str:
    run_cli_items = _extract_run_cli_items(session_messages, result_messages)
    if not run_cli_items:
        return ""

    adjacency_links: set[tuple[str, str]] = set()
    logical_links: set[tuple[str, str, str]] = set()
    for item in run_cli_items:
        if item["status"] != "success":
            continue
        adjacency_links.update(
            _extract_adjacency_links(
                host=item["host"],
                command=item["command"],
                body=item["body"],
            )
        )
        logical_links.update(
            _extract_logical_links(
                host=item["host"],
                command=item["command"],
                body=item["body"],
            )
        )

    if not adjacency_links and not logical_links:
        return ""

    lines = ["[Session Relationship Digest]"]
    if adjacency_links:
        lines.append("[Session Adjacency Digest]")
        for left, right in sorted(adjacency_links):
            lines.append(f"- {left} <-> {right}")
    if logical_links:
        lines.append("[Session Logical Relationship Digest]")
        for protocol, left, right in sorted(logical_links):
            lines.append(f"- protocol={protocol}, {left} <-> {right}")
    return "\n".join(lines)


def _extract_adjacency_links(*, host: str, command: str, body: str) -> set[tuple[str, str]]:
    lowered_command = (command or "").lower()
    if "cdp neighbors detail" not in lowered_command and "lldp neighbors detail" not in lowered_command:
        return set()

    links: set[tuple[str, str]] = set()
    for raw_remote in re.findall(r"Device ID:\s*(.+)", body):
        remote = _normalize_neighbor_name(raw_remote)
        if not remote or remote == host:
            continue
        links.add(tuple(sorted((host, remote))))
    for raw_remote in re.findall(r"System Name:\s*(.+)", body):
        remote = _normalize_neighbor_name(raw_remote)
        if not remote or remote == host:
            continue
        links.add(tuple(sorted((host, remote))))
    return links


def _extract_logical_links(*, host: str, command: str, body: str) -> set[tuple[str, str, str]]:
    lowered_command = (command or "").lower()
    links: set[tuple[str, str, str]] = set()

    if "show ip bgp summary" in lowered_command:
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or not re.match(r"^\d{1,3}(?:\.\d{1,3}){3}\s+", stripped):
                continue
            parts = stripped.split()
            if len(parts) < 10:
                continue
            state = parts[-1]
            if not state.isdigit():
                continue
            peer = parts[0]
            links.add(("bgp", host, peer))

    if "show ip ospf neighbor" in lowered_command:
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.lower().startswith("neighbor id"):
                continue
            parts = stripped.split()
            if len(parts) < 6:
                continue
            state = parts[2]
            if "full" not in state.lower():
                continue
            peer = parts[0]
            links.add(("ospf", host, peer))

    if "show ip eigrp neighbors" in lowered_command:
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.lower().startswith(("address", "h")):
                continue
            match = re.match(r"^\d+\s+(\d{1,3}(?:\.\d{1,3}){3})\s+", stripped)
            if not match:
                continue
            peer = match.group(1)
            links.add(("eigrp", host, peer))

    return links


def _requires_logical_topology(user_query: str) -> bool:
    return bool(_LOGICAL_TOPOLOGY_RE.search(user_query or ""))


def _requires_full_topology_coverage(user_query: str) -> bool:
    text = user_query or ""
    return bool(_RELATIONSHIP_ANALYSIS_RE.search(text) and _ALL_DEVICE_SCOPE_RE.search(text))


def _logical_scope_hosts(device_cache: dict[str, Any]) -> list[str]:
    hosts: list[str] = []
    for host, info in device_cache.items():
        role = str((info or {}).get("device_role", "") or "").strip().lower()
        if role == "access_switch":
            continue
        if not role:
            hosts.append(host)
            continue
        if any(token in role for token in ("router", "core", "dist", "gateway", "l3", "firewall")):
            hosts.append(host)
    return sorted(hosts)


def _is_logical_evidence_item(item: dict[str, str]) -> bool:
    if item["status"] != "success":
        return False
    command = item["command"]
    return bool(_LOGICAL_EVIDENCE_COMMAND_RE.search(command or ""))


def _missing_run_cli_hosts(
    device_cache: dict[str, Any],
    *message_groups: list[BaseMessage],
) -> list[str]:
    attempted_hosts = {
        item["host"]
        for item in _extract_run_cli_items(*message_groups)
        if item["host"] and item["host"] != "unknown"
    }
    return sorted(host for host in device_cache if host not in attempted_hosts)


def _missing_logical_hosts(
    device_cache: dict[str, Any],
    *message_groups: list[BaseMessage],
) -> list[str]:
    covered_hosts = {
        item["host"]
        for item in _extract_run_cli_items(*message_groups)
        if item["host"] and item["host"] != "unknown" and _is_logical_evidence_item(item)
    }
    return sorted(host for host in _logical_scope_hosts(device_cache) if host not in covered_hosts)


def _has_logical_evidence(*message_groups: list[BaseMessage]) -> bool:
    for item in _extract_run_cli_items(*message_groups):
        if item["status"] != "success":
            continue
        if _extract_logical_links(
            host=item["host"],
            command=item["command"],
            body=item["body"],
        ):
            return True
    return False


def _normalize_neighbor_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"\s+\(.*\)$", "", cleaned)
    cleaned = cleaned.split()[0]
    cleaned = cleaned.split(".")[0]
    return cleaned.strip()


def _answer_matches_stats(answer: str, stats: dict[str, Any]) -> bool:
    """Return True when the synthesized answer already matches exact facts."""
    text = (answer or "").lower()
    expected_ratio = f"{stats['success']}/{stats['total']}"
    if expected_ratio not in text:
        return False

    if stats["error"] == 0:
        return True

    failure_ratio = f"{stats['error']}/{stats['total']}"
    if failure_ratio not in text and f"{stats['error']} ตัว" not in text:
        return False

    for item in stats.get("failed_hosts", []):
        if item["host"].lower() not in text:
            return False
    return True


def _relationship_answer_needs_repair(answer: str, user_query: str) -> bool:
    if not _RELATIONSHIP_ANALYSIS_RE.search(user_query or ""):
        return False
    text = answer or ""
    if "```" in text:
        return True
    if text.count("|") >= 6:
        return True
    return False


def _parse_run_cli_stats(result_messages: list[BaseMessage]) -> dict[str, Any] | None:
    """Extract exact run_cli stats for consistency checks."""
    items = [
        {
            "host": item["host"],
            "status": item["status"],
            "command": item["command"],
            "detail": item["body"].splitlines()[0].strip(),
        }
        for item in _extract_run_cli_items(result_messages)
    ]
    if not items:
        return None
    return {
        "total": len(items),
        "success": sum(1 for item in items if item["status"] == "success"),
        "error": sum(1 for item in items if item["status"] == "error"),
        "blocked": sum(1 for item in items if item["status"] == "blocked"),
        "failed_hosts": [item for item in items if item["status"] == "error"],
    }


def _repair_answer_with_exact_facts(
    *,
    answer_llm: SupportsInvoke,
    system_msg: SystemMessage,
    candidate_answer: str,
    evidence_digest: str,
    stats: dict[str, Any],
    user_query: str,
) -> str:
    """Rewrite a batch summary so the wording matches exact evidence counts."""
    failed_lines = "\n".join(
        f"- {item['host']}: {item['detail']}"
        for item in stats.get("failed_hosts", [])
    ) or "- none"
    repair_instruction = HumanMessage(
        content=(
            "[System: Consistency repair]\n"
            "Rewrite the answer below so it matches the exact facts.\n"
            "Keep it concise and conclusion-first.\n"
            "Write one final answer only.\n"
            "Do not include alternate phrasings, duplicate summaries, or repeated tables.\n"
            "Match the user's language. If the user asked in Thai, answer in Thai.\n"
            "Preserve an operational, expert network engineer tone.\n"
            "Do not change the facts below.\n"
            f"Latest user request: {user_query}\n"
            f"Exact totals: success={stats['success']}, error={stats['error']}, total={stats['total']}\n"
            f"Failed hosts:\n{failed_lines}\n"
            f"{evidence_digest}\n"
            f"Candidate answer:\n{candidate_answer}"
        )
    )
    repaired = answer_llm.invoke([system_msg, repair_instruction])
    content = _THINK_RE.sub("", str(repaired.content or "")).strip()
    return content or candidate_answer


def _trace_symmetry_answer_needs_repair(
    answer: str,
    user_query: str,
    analyses: list[dict[str, Any]],
) -> bool:
    if not analyses:
        return False
    if not _TRACE_SYMMETRY_QUERY_RE.search(user_query or "") and "traceroute" not in (user_query or "").lower():
        return False

    text = answer or ""
    has_symmetric_verdict = any(item.get("verdict") == "likely_symmetric" for item in analyses)
    has_asymmetric_verdict = any(item.get("verdict") == "likely_asymmetric" for item in analyses)
    mentions_ip_details = bool(_ANY_IP_RE.search(text))

    if has_symmetric_verdict and _ASYMMETRIC_VERDICT_RE.search(text):
        return True
    if has_asymmetric_verdict and _SYMMETRIC_VERDICT_RE.search(text) and not _ASYMMETRIC_VERDICT_RE.search(text):
        return True
    if analyses and not mentions_ip_details:
        return True
    return False


def _repair_trace_symmetry_answer(
    *,
    answer_llm: SupportsInvoke,
    system_msg: SystemMessage,
    candidate_answer: str,
    trace_symmetry_digest: str,
    evidence_digest: str,
    user_query: str,
) -> str:
    repair_instruction = HumanMessage(
        content=(
            "[System: Traceroute symmetry repair]\n"
            "Rewrite the answer below so it matches the traceroute symmetry evidence.\n"
            "Write one final answer only.\n"
            "Match the user's language.\n"
            "Lead with the verdict, then explain the evidence briefly.\n"
            "For forward/reverse traceroute comparison, compare the reverse path "
            "after reversing its host/link sequence.\n"
            "Do not call a path asymmetric just because opposite interface IPs "
            "appear on the same links in opposite directions.\n"
            "Include the forward and reverse hop IPs in the answer.\n"
            "Prefer short hop-by-hop bullets or concise hop lists for each direction.\n"
            "If the digest says `likely_symmetric`, explain that the traces show "
            "the same path in reverse.\n"
            "If the digest says `likely_asymmetric`, explain which links differ.\n"
            "If the digest is incomplete, say so explicitly.\n"
            f"Latest user request: {user_query}\n"
            f"{trace_symmetry_digest}\n"
            f"{evidence_digest}\n"
            f"Candidate answer:\n{candidate_answer}"
        )
    )
    repaired = answer_llm.invoke([system_msg, repair_instruction])
    content = _THINK_RE.sub("", str(repaired.content or "")).strip()
    return content or candidate_answer


def _repair_relationship_answer(
    *,
    answer_llm: SupportsInvoke,
    system_msg: SystemMessage,
    candidate_answer: str,
    coverage_digest: str,
    evidence_digest: str,
    session_evidence_digest: str,
    user_query: str,
) -> str:
    repair_instruction = HumanMessage(
        content=(
            "[System: Relationship answer repair]\n"
            "Rewrite the topology/relationship answer below so it follows the "
            "required format and stays faithful to the evidence digests.\n"
            "Write one final answer only.\n"
            "Match the user's language.\n"
            "Use short bullets and short sections.\n"
            "No ASCII diagrams.\n"
            "No wide markdown tables.\n"
            "Prefer this order when applicable: Scope/coverage, Confirmed "
            "physical links, Logical relationships, Topology interpretation, "
            "Limitations.\n"
            "If a statement in the candidate answer is not clearly supported by "
            "the evidence digests, remove it or soften it as an inference.\n"
            "`show ip protocols` proves protocol presence/configuration or "
            "redistribution, not active adjacency by itself.\n"
            "Do not present route-table next hops as confirmed protocol "
            "adjacencies unless neighbor/peer evidence supports them.\n"
            "If logical coverage is partial, say that explicitly.\n"
            f"Latest user request: {user_query}\n"
            f"{coverage_digest}\n"
            f"{evidence_digest}\n"
            f"{session_evidence_digest}\n"
            f"Candidate answer:\n{candidate_answer}"
        )
    )
    repaired = answer_llm.invoke([system_msg, repair_instruction])
    content = _THINK_RE.sub("", str(repaired.content or "")).strip()
    return content or candidate_answer
