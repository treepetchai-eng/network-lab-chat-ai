"""
src/formatters.py
=================
Pure-function helpers that turn raw tool output into Markdown strings.

Extracted from the original Chainlit ``app.py`` so both the legacy UI and the
new FastAPI / SSE back-end can share the same formatting logic.
"""

import json
import re

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(
    r"^\[Device:\s*(?P<host>.+?)\s*\|\s*IP:\s*(?P<ip>.+?)\s*\|\s*OS:\s*(?P<os>.+?)\]"
)
_EXECUTED_COMMAND_RE = re.compile(r"^\[EXECUTED COMMAND\]\s*(?P<command>.+?)\s*$", re.MULTILINE)
_CLI_COMMAND_ERROR_PATTERNS = (
    re.compile(r"^\s*%\s*Invalid input", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*%\s*Incomplete command", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*%\s*Ambiguous command", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*%\s*Unknown command", re.IGNORECASE | re.MULTILINE),
)

_ERROR_PREFIXES = (
    "[AUTH ERROR]",
    "[SSH ERROR]",
    "[TIMEOUT ERROR]",
    "[DETECTION ERROR]",
    "[CONFIG ERROR]",
    "[BLOCKED]",
    "[ERROR]",
)

_OS_LABEL: dict[str, str] = {
    "cisco_ios":  "Cisco IOS",
    "cisco_xe":   "Cisco IOS XE",
    "cisco_nxos": "Cisco NX-OS",
    "cisco_xr":   "Cisco IOS XR",
    "cisco_asa":  "Cisco ASA",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_output(raw: str) -> tuple[str, str, str, str]:
    """Split the ``[Device: … | IP: … | OS: …]`` header from the body.

    Command repair/fallback wrappers may prepend metadata lines before the
    device header. When that happens, preserve the wrapper text as part of the
    body while still extracting the device identity correctly.
    """
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        m = _HEADER_RE.match(line.strip())
        if not m:
            continue
        prefix = "\n".join(lines[:index]).strip()
        suffix = "\n".join(lines[index + 1:]).strip()
        body = "\n".join(part for part in (prefix, suffix) if part).strip()
        return m.group("host"), m.group("ip"), m.group("os"), body
    return "", "", "", raw.strip()


def extract_executed_command(raw: str) -> str:
    """Extract an explicit executed-command wrapper from tool output."""
    match = _EXECUTED_COMMAND_RE.search(raw or "")
    return match.group("command").strip() if match else ""


def os_label(os_type: str) -> str:
    """Map an internal OS identifier to a human-readable label."""
    return _OS_LABEL.get(os_type, os_type)


def strip_tool_metadata(body: str) -> str:
    """Remove non-CLI metadata wrappers before rendering to the UI."""
    lines = []
    for line in (body or "").splitlines():
        if line.startswith("[DIAGNOSTIC]") or line.startswith("[EXECUTED COMMAND]") or line.startswith("[RESOLVED TARGET]"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def is_command_error(body: str) -> bool:
    """Return True when CLI output shows a device-side command syntax error."""
    cleaned = strip_tool_metadata(body)
    return any(pattern.search(cleaned) for pattern in _CLI_COMMAND_ERROR_PATTERNS)


def is_error(body: str) -> bool:
    """Return *True* if *body* starts with a known error prefix."""
    cleaned = strip_tool_metadata(body)
    return any(cleaned.startswith(p) for p in _ERROR_PREFIXES) or is_command_error(cleaned)


def fmt_cli_output(
    hostname: str, ip: str, os_type: str, command: str, body: str,
) -> str:
    """Format successful SSH CLI output as Markdown."""
    cleaned_body = strip_tool_metadata(body)
    return (
        f"**{hostname}** `{ip}` ({os_label(os_type)})\n\n"
        f"```\n{hostname}# {command}\n{cleaned_body}\n```"
    )


def fmt_cli_error(ip: str, body: str) -> str:
    """Format an SSH error as Markdown."""
    cleaned_body = strip_tool_metadata(body)
    return f"**Error** connecting to `{ip}`\n\n```\n{cleaned_body}\n```"


def fmt_lookup(raw: str) -> str:
    """Format a ``lookup_device`` result as a compact device card."""
    try:
        data = json.loads(raw)
    except Exception:
        return f"```\n{raw}\n```"

    if "error" in data:
        sugg = data.get("suggestions", [])
        candidates = data.get("candidates", [])
        if candidates:
            lines = ["Lookup ambiguous"]
            for candidate in candidates[:4]:
                interface = candidate.get("interface") or {}
                interface_name = interface.get("name", "?")
                ip_address = interface.get("ip_address", "?")
                lines.append(
                    f"- {candidate.get('hostname', '?')} via {interface_name} ({ip_address})"
                )
            return "\n".join(lines)
        sugg_str = ", ".join(sugg[:6]) + ("..." if len(sugg) > 6 else "")
        return f"Not found. Suggestions: {sugg_str}"

    hostname = data.get("hostname", "?")
    ip = data.get("ip_address", "?")
    os_type = data.get("os_platform", "?")
    role = data.get("device_role", "?")
    site = data.get("site", "?")
    version = data.get("version", "?")
    resolved_via = data.get("resolved_via", "")
    matched_value = data.get("matched_value", "")
    matched_interface = data.get("matched_interface") or {}

    lines = [
        "Device Resolved",
        "",
        hostname,
        f"IP: {ip}",
        f"OS: {os_label(os_type)}",
        f"Role: {role}",
        f"Site: {site}",
        f"Version: {version}",
    ]
    if resolved_via == "interface_ip" and matched_value:
        lines.append(f"Resolved via: interface IP {matched_value}")
    elif resolved_via == "tunnel_ip" and matched_value:
        lines.append(f"Resolved via: tunnel IP {matched_value}")
    elif resolved_via == "management_ip" and matched_value:
        lines.append(f"Resolved via: management IP {matched_value}")

    if resolved_via == "interface_ip" and matched_interface:
        interface_name = matched_interface.get("name", "?")
        interface_ip = matched_interface.get("ip_address", "")
        network_cidr = matched_interface.get("network_cidr", "")
        interface_mode = matched_interface.get("interface_mode", "")
        description = matched_interface.get("description", "")
        lines.append(f"Matched Interface: {interface_name}")
        if interface_ip:
            lines.append(f"Interface IP: {interface_ip}")
        if network_cidr:
            lines.append(f"Interface Network: {network_cidr}")
        if interface_mode:
            lines.append(f"Interface Mode: {interface_mode}")
        if description:
            lines.append(f"Interface Description: {description}")

    return "\n".join(lines)


def fmt_device_list(raw: str) -> str:
    """Format a ``list_all_devices`` result as a Markdown table."""
    try:
        devices = json.loads(raw)
    except Exception:
        return f"```\n{raw}\n```"

    if isinstance(devices, dict) and "error" in devices:
        return f"Error: {devices['error']}"

    lines = [
        "| Hostname | IP | OS | Role |",
        "|----------|----|----|------|",
    ]
    for d in devices:
        lines.append(
            f"| {d.get('hostname', '?')} | `{d.get('ip_address', '?')}` "
            f"| {d.get('os_platform', '?')} | {d.get('device_role', '?')} |"
        )
    return "\n".join(lines)
