"""Semantic diagnostic tool for ping/traceroute style operations."""

from __future__ import annotations

import re

from langchain_core.tools import tool

from src.tools.interface_inventory import resolve_ip_context
from src.tools.inventory_tools import resolve_inventory_record
from src.tools.ssh_executor import execute_cli

_SUPPORTED_KINDS = {"ping", "traceroute"}
_IOS_FAMILY = {"cisco_ios", "cisco_xe", "cisco_xr", "cisco_asa"}
_NXOS_FAMILY = {"cisco_nxos"}
_TRACEROUTE_HOP_RE = re.compile(r"^\s*(?P<hop>\d+)\s+(?P<body>.+?)\s*$")
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _resolve_cached_device(device_cache: dict, host: str) -> tuple[str | None, dict | None, str]:
    info: dict | None = None
    device_ip: str | None = None
    resolved_host = host

    if host in device_cache:
        info = device_cache[host]
        device_ip = info["ip_address"]
    else:
        host_upper = host.upper()
        for hostname, item in device_cache.items():
            if hostname.upper() == host_upper or item["ip_address"] == host:
                resolved_host = hostname
                info = item
                device_ip = item["ip_address"]
                break

    return device_ip, info, resolved_host


def _cache_record(device_cache: dict, record: dict) -> None:
    hostname = str(record.get("hostname", "") or "").strip()
    if not hostname:
        return
    device_cache[hostname] = {
        "ip_address": record.get("ip_address", ""),
        "os_platform": record.get("os_platform", ""),
        "device_role": record.get("device_role", ""),
        "site": record.get("site", ""),
        "version": record.get("version", ""),
        "tunnel_ips": record.get("tunnel_ips", []) or [],
    }


def _resolve_target(device_cache: dict, target: str) -> tuple[str, str | None]:
    search = (target or "").strip()
    if not search:
        return "", None

    for hostname, item in device_cache.items():
        if hostname.lower() == search.lower() or item.get("ip_address") == search:
            return str(item.get("ip_address", "") or search), hostname

    try:
        resolved = resolve_inventory_record(search)
    except Exception:
        resolved = None
    if resolved:
        _cache_record(device_cache, resolved)
        return str(resolved.get("ip_address", "") or search), str(resolved.get("hostname", "") or None)

    return search, None


def render_diagnostic_command(
    *,
    os_platform: str,
    kind: str,
    target: str,
    count: int = 2,
    timeout: int = 1,
) -> str:
    """Render a platform-appropriate read-only diagnostic command."""
    normalized_kind = (kind or "").strip().lower()
    normalized_os = (os_platform or "").strip().lower()
    safe_target = (target or "").strip()

    if not safe_target:
        raise ValueError("Diagnostic target is required.")
    if normalized_kind not in _SUPPORTED_KINDS:
        raise ValueError(
            f"Unsupported diagnostic kind '{kind}'. Supported: {', '.join(sorted(_SUPPORTED_KINDS))}."
        )

    count = max(1, min(int(count), 10))
    timeout = max(1, min(int(timeout), 5))

    if normalized_kind == "traceroute":
        return f"traceroute {safe_target}"

    if normalized_os in _NXOS_FAMILY:
        return f"ping {safe_target} count {count} timeout {timeout}"
    if normalized_os in _IOS_FAMILY or not normalized_os:
        return f"ping {safe_target} repeat {count} timeout {timeout}"
    return f"ping {safe_target}"


def _format_interface_match(row: dict) -> str:
    hostname = str(row.get("hostname", "") or "?")
    interface_name = str(row.get("interface_name", "") or "?")
    interface_mode = str(row.get("interface_mode", "") or "").strip()
    role = str(row.get("device_role", "") or "").strip()
    network_cidr = str(row.get("network_cidr", "") or "").strip()
    description = str(row.get("description", "") or "").strip()

    parts = [f"{hostname} {interface_name}"]
    extras: list[str] = []
    if interface_mode:
        extras.append(interface_mode)
    if role:
        extras.append(f"role={role}")
    if network_cidr:
        extras.append(f"network={network_cidr}")
    if description:
        extras.append(f"desc={description}")
    if extras:
        return parts[0] + " [" + ", ".join(extras) + "]"
    return parts[0]


def _parse_traceroute_hops(raw_output: str) -> list[dict]:
    hops: list[dict] = []
    for line in (raw_output or "").splitlines():
        match = _TRACEROUTE_HOP_RE.match(line)
        if not match:
            continue
        hop_index = int(match.group("hop"))
        body = match.group("body").strip()
        responders: list[str] = []
        for ip_value in _IP_RE.findall(body):
            if ip_value not in responders:
                responders.append(ip_value)
        hops.append({
            "hop": hop_index,
            "body": body,
            "responders": responders,
            "timeout": not responders and "*" in body,
        })
    return hops


def _build_trace_target_context_lines(target: str) -> list[str]:
    context = resolve_ip_context(target)
    exact_matches = context.get("exact_matches", [])
    network_matches = context.get("network_matches", [])
    if not exact_matches and not network_matches:
        return []

    lines = ["[TRACE TARGET CONTEXT]"]
    if exact_matches:
        lines.append("- exact owner(s): " + " ; ".join(
            _format_interface_match(row) for row in exact_matches[:3]
        ))

    exact_keys = {
        (str(row.get("hostname", "")), str(row.get("interface_name", "")))
        for row in exact_matches
    }
    peer_matches = [
        row for row in network_matches
        if (str(row.get("hostname", "")), str(row.get("interface_name", ""))) not in exact_keys
    ]
    if peer_matches:
        lines.append("- same-network candidate(s): " + " ; ".join(
            _format_interface_match(row) for row in peer_matches[:3]
        ))
    return lines


def _build_traceroute_annotation_lines(raw_output: str, resolved_target: str) -> list[str]:
    hops = _parse_traceroute_hops(raw_output)
    if not hops:
        return _build_trace_target_context_lines(resolved_target)

    lines = _build_trace_target_context_lines(resolved_target)
    lines.append("[TRACE HOP ANNOTATION]")
    for hop in hops[:16]:
        hop_number = hop["hop"]
        responders = hop["responders"]
        if not responders:
            if hop.get("timeout"):
                lines.append(f"- hop {hop_number}: no IP response (timeout or filtered)")
            else:
                lines.append(f"- hop {hop_number}: {hop.get('body', '').strip()}")
            continue

        responder_summaries: list[str] = []
        for ip_value in responders[:3]:
            context = resolve_ip_context(ip_value)
            exact_matches = context.get("exact_matches", [])
            network_matches = context.get("network_matches", [])
            if exact_matches:
                exact_text = " ; ".join(_format_interface_match(row) for row in exact_matches[:2])
                exact_keys = {
                    (str(row.get("hostname", "")), str(row.get("interface_name", "")))
                    for row in exact_matches
                }
                peer_matches = [
                    row for row in network_matches
                    if (str(row.get("hostname", "")), str(row.get("interface_name", ""))) not in exact_keys
                ]
                if peer_matches:
                    peer_text = " ; ".join(_format_interface_match(row) for row in peer_matches[:2])
                    responder_summaries.append(
                        f"{ip_value} -> exact={exact_text}; same_network={peer_text}"
                    )
                else:
                    responder_summaries.append(f"{ip_value} -> exact={exact_text}")
            elif network_matches:
                candidate_text = " ; ".join(_format_interface_match(row) for row in network_matches[:3])
                responder_summaries.append(f"{ip_value} -> connected_candidates={candidate_text}")
            else:
                responder_summaries.append(f"{ip_value} -> no interface-inventory match")
        lines.append(f"- hop {hop_number}: " + " | ".join(responder_summaries))
    return lines


def create_run_diagnostic_tool(device_cache: dict):
    """Create a semantic diagnostic tool bound to *device_cache*."""

    @tool
    def run_diagnostic(
        host: str,
        kind: str,
        target: str,
        count: int = 2,
        timeout: int = 1,
    ) -> str:
        """Run a semantic network diagnostic on one source device.

        Supported kinds:
        - ``ping``
        - ``traceroute``

        The backend resolves target hostnames to inventory IPs when possible
        and renders the safest supported CLI syntax for the source platform.
        """
        device_ip, info, resolved_host = _resolve_cached_device(device_cache, host)
        if device_ip is None or info is None:
            available = ", ".join(sorted(device_cache.keys())) or "(empty)"
            return (
                f"[ERROR] Device '{host}' not found in inventory cache. "
                f"Available devices: {available}"
            )

        try:
            resolved_target, resolved_target_host = _resolve_target(device_cache, target)
            command = render_diagnostic_command(
                os_platform=str(info.get("os_platform", "") or ""),
                kind=kind,
                target=resolved_target,
                count=count,
                timeout=timeout,
            )
        except ValueError as exc:
            return f"[BLOCKED] {exc}"

        raw_output = execute_cli(device_ip, str(info["os_platform"]), command)
        lines = [
            f"[DIAGNOSTIC] kind={kind.strip().lower()} requested_target={target.strip()}",
            f"[EXECUTED COMMAND] {command}",
        ]
        if resolved_target_host:
            lines.append(f"[RESOLVED TARGET] {resolved_target_host} ({resolved_target})")
        elif resolved_target != target.strip():
            lines.append(f"[RESOLVED TARGET] {resolved_target}")
        lines.append(raw_output)
        if kind.strip().lower() == "traceroute":
            lines.extend(_build_traceroute_annotation_lines(raw_output, resolved_target))
        return "\n".join(lines)

    return run_diagnostic
