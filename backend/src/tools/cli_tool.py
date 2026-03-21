"""Factory for the session-scoped ``run_cli`` LangChain tool."""

from __future__ import annotations

from langchain_core.tools import tool

from src.tools.safety import validate_command
from src.tools.ssh_executor import execute_cli


def create_run_cli_tool(device_cache: dict):
    """Create a ``run_cli`` tool bound to *device_cache* via closure."""

    @tool
    def run_cli(host: str, command: str) -> str:
        """Execute a validated read-only CLI command on a cached device."""
        device_ip: str | None = None
        info: dict | None = None

        if host in device_cache:
            info = device_cache[host]
            device_ip = info["ip_address"]
        else:
            host_upper = host.upper()
            for hostname, item in device_cache.items():
                if hostname.upper() == host_upper or item["ip_address"] == host:
                    host = hostname
                    info = item
                    device_ip = item["ip_address"]
                    break

        if device_ip is None or info is None:
            available = ", ".join(sorted(device_cache.keys())) or "(empty)"
            return (
                f"[ERROR] Device '{host}' not found in inventory cache. "
                f"Available devices: {available}"
            )

        allowed, reason = validate_command(command)
        if not allowed:
            return f"[BLOCKED] {reason}"
        return execute_cli(device_ip, info["os_platform"], command)

    return run_cli
