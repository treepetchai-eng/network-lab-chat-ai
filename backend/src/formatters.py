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


def os_label(os_type: str) -> str:
    """Map an internal OS identifier to a human-readable label."""
    return _OS_LABEL.get(os_type, os_type)


def is_error(body: str) -> bool:
    """Return *True* if *body* starts with a known error prefix."""
    return any(body.startswith(p) for p in _ERROR_PREFIXES)


def fmt_cli_output(
    hostname: str, ip: str, os_type: str, command: str, body: str,
) -> str:
    """Format successful SSH CLI output as Markdown."""
    return (
        f"**{hostname}** `{ip}` ({os_label(os_type)})\n\n"
        f"```\n{hostname}# {command}\n{body}\n```"
    )


def fmt_cli_error(ip: str, body: str) -> str:
    """Format an SSH error as Markdown."""
    return f"**Error** connecting to `{ip}`\n\n```\n{body}\n```"


def fmt_lookup(raw: str) -> str:
    """Format a ``lookup_device`` result as a compact device card."""
    try:
        data = json.loads(raw)
    except Exception:
        return f"```\n{raw}\n```"

    if "error" in data:
        sugg = data.get("suggestions", [])
        sugg_str = ", ".join(sugg[:6]) + ("..." if len(sugg) > 6 else "")
        return f"Not found. Suggestions: {sugg_str}"

    hostname = data.get("hostname", "?")
    ip = data.get("ip_address", "?")
    os_type = data.get("os_platform", "?")
    role = data.get("device_role", "?")
    site = data.get("site", "?")
    version = data.get("version", "?")

    return (
        "Device Resolved\n\n"
        f"{hostname}\n"
        f"IP: {ip}\n"
        f"OS: {os_label(os_type)}\n"
        f"Role: {role}\n"
        f"Site: {site}\n"
        f"Version: {version}"
    )


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
