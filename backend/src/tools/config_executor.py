"""Approval-gated config execution and read-only verification helpers for network devices."""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

load_dotenv()

_HARD_BLOCK_RE = re.compile(
    r"^\s*(reload|reboot|write\s+erase|erase\s+startup-config|format|delete\s+/|"
    r"copy\s+.*\s+startup-config|archive\s+download-sw)\b",
    re.IGNORECASE,
)


def validate_change_commands(commands: list[str]) -> tuple[bool, str]:
    """Reject clearly destructive commands even after approval."""
    for command in commands:
        value = command.strip()
        if not value:
            continue
        if _HARD_BLOCK_RE.search(value):
            return False, f"Command '{value}' is blocked even in approval mode."
    return True, ""


def execute_config(device_ip: str, os_platform: str, commands: list[str]) -> str:
    """Apply approved config commands without saving the startup config."""
    username: str | None = os.getenv("ROUTER_USER")
    password: str | None = os.getenv("ROUTER_PASS")
    if not username or not password:
        return (
            f"[CONFIG ERROR] {device_ip} (OS: {os_platform}): "
            "ROUTER_USER or ROUTER_PASS is not set in .env"
        )

    allowed, reason = validate_change_commands(commands)
    if not allowed:
        return f"[BLOCKED] {reason}"

    try:
        connection = ConnectHandler(
            device_type=os_platform,
            host=device_ip,
            username=username,
            password=password,
            fast_cli=False,
            conn_timeout=int(os.getenv("SSH_CONN_TIMEOUT", "10")),
        )
        try:
            output = connection.send_config_set(commands)
        finally:
            connection.disconnect()
        return f"[CONFIG APPLIED] {device_ip}\n{output}"
    except NetmikoAuthenticationException as exc:
        return f"[AUTH ERROR] Authentication failed for {device_ip}: {exc}"
    except NetmikoTimeoutException as exc:
        return f"[TIMEOUT ERROR] {device_ip} (OS: {os_platform}): {exc}"
    except Exception as exc:
        return f"[CONFIG ERROR] {device_ip} (OS: {os_platform}): {exc}"


def run_show_commands(device_ip: str, os_platform: str, commands: list[str]) -> str:
    """Run read-only show commands on a device and return combined output.

    Used for post-execution verification — never sends config, never modifies state.
    """
    username: str | None = os.getenv("ROUTER_USER")
    password: str | None = os.getenv("ROUTER_PASS")
    if not username or not password:
        return (
            f"[VERIFY ERROR] {device_ip}: ROUTER_USER or ROUTER_PASS is not set in .env"
        )
    if not commands:
        return "[VERIFY SKIP] No verification commands provided."

    try:
        connection = ConnectHandler(
            device_type=os_platform,
            host=device_ip,
            username=username,
            password=password,
            fast_cli=False,
            conn_timeout=int(os.getenv("SSH_CONN_TIMEOUT", "10")),
        )
        try:
            outputs: list[str] = []
            for cmd in commands:
                cmd = cmd.strip()
                if not cmd:
                    continue
                result = connection.send_command(cmd, read_timeout=30)
                outputs.append(f"--- {cmd} ---\n{result}")
        finally:
            connection.disconnect()
        return "\n\n".join(outputs) or "[VERIFY OK] Commands returned empty output."
    except NetmikoAuthenticationException as exc:
        return f"[VERIFY AUTH ERROR] Authentication failed for {device_ip}: {exc}"
    except NetmikoTimeoutException as exc:
        return f"[VERIFY TIMEOUT] {device_ip}: {exc}"
    except Exception as exc:
        return f"[VERIFY ERROR] {device_ip}: {exc}"
