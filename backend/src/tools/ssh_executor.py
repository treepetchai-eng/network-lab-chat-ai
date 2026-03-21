"""SSH execution layer for network device CLI commands."""

from __future__ import annotations

import logging
import os
import re
import threading
import time

from dotenv import load_dotenv
from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

from src.tools.safety import validate_command

load_dotenv()

logger = logging.getLogger(__name__)

_HOSTNAME_CACHE: dict[str, str] = {}
_CACHE_LOCK = threading.Lock()
_SESSION_CACHE: dict[str, "_ConnectionEntry"] = {}

_CONN_TIMEOUT: int = int(os.getenv("SSH_CONN_TIMEOUT", "15"))
_READ_TIMEOUT: int = int(os.getenv("SSH_READ_TIMEOUT", "20"))
_DIAG_READ_TIMEOUT: int = int(os.getenv("SSH_DIAG_READ_TIMEOUT", "60"))
_DIAG_LAST_READ: float = float(os.getenv("SSH_DIAG_LAST_READ", "1.0"))
_CONN_IDLE_TTL: float = float(os.getenv("SSH_CONN_IDLE_TTL", "45"))
_PURGE_INTERVAL: float = 10.0  # seconds between stale-connection sweeps
_last_purge_time: float = 0.0

_TIMING_COMMAND_PATTERNS = (
    re.compile(r"^\s*traceroute\b", re.IGNORECASE),
    re.compile(r"^\s*tracert\b", re.IGNORECASE),
    re.compile(r"^\s*ping\b", re.IGNORECASE),
)


class _ConnectionEntry:
    def __init__(self, connection: ConnectHandler, hostname: str):
        self.connection = connection
        self.hostname = hostname
        self.last_used = time.monotonic()
        self.lock = threading.Lock()


def _disconnect_entry(entry: _ConnectionEntry) -> None:
    try:
        entry.connection.disconnect()
    except Exception:
        pass


def _purge_stale_connections() -> None:
    """Remove idle SSH connections, but only if enough time has passed.

    Avoids locking and iterating the session cache on every single SSH call
    when multiple commands execute in rapid succession (e.g., parallel batch).
    """
    global _last_purge_time
    now = time.monotonic()
    if now - _last_purge_time < _PURGE_INTERVAL:
        return
    _last_purge_time = now

    stale_keys: list[str] = []
    stale_entries: list[_ConnectionEntry] = []
    with _CACHE_LOCK:
        for device_ip, entry in _SESSION_CACHE.items():
            if now - entry.last_used > _CONN_IDLE_TTL:
                stale_keys.append(device_ip)
                stale_entries.append(entry)
        for device_ip in stale_keys:
            _SESSION_CACHE.pop(device_ip, None)
    for entry in stale_entries:
        _disconnect_entry(entry)


def _open_connection(
    *,
    device_ip: str,
    os_platform: str,
    username: str,
    password: str,
) -> _ConnectionEntry:
    connection = ConnectHandler(
        device_type=os_platform,
        host=device_ip,
        username=username,
        password=password,
        fast_cli=True,
        conn_timeout=_CONN_TIMEOUT,
    )
    prompt_hostname = connection.base_prompt or device_ip
    with _CACHE_LOCK:
        _HOSTNAME_CACHE[device_ip] = prompt_hostname
    return _ConnectionEntry(connection=connection, hostname=prompt_hostname)


def _get_or_open_connection(
    *,
    device_ip: str,
    os_platform: str,
    username: str,
    password: str,
) -> _ConnectionEntry:
    _purge_stale_connections()
    with _CACHE_LOCK:
        cached = _SESSION_CACHE.get(device_ip)
    if cached is not None:
        return cached

    entry = _open_connection(
        device_ip=device_ip,
        os_platform=os_platform,
        username=username,
        password=password,
    )
    with _CACHE_LOCK:
        existing = _SESSION_CACHE.get(device_ip)
        if existing is not None:
            _disconnect_entry(entry)
            return existing
        _SESSION_CACHE[device_ip] = entry
    return entry


def _invalidate_connection(device_ip: str) -> None:
    with _CACHE_LOCK:
        entry = _SESSION_CACHE.pop(device_ip, None)
    if entry is not None:
        _disconnect_entry(entry)


def _is_timing_command(command: str) -> bool:
    value = (command or "").strip()
    return any(pattern.search(value) for pattern in _TIMING_COMMAND_PATTERNS)


def _execute_command(connection: ConnectHandler, command: str) -> str:
    if _is_timing_command(command):
        return connection.send_command_timing(
            command,
            read_timeout=_DIAG_READ_TIMEOUT,
            last_read=_DIAG_LAST_READ,
            strip_prompt=False,
            strip_command=False,
        )
    return connection.send_command(command, read_timeout=_READ_TIMEOUT)


def execute_cli(device_ip: str, os_platform: str, command: str) -> str:
    """Connect to a network device via SSH and run a single CLI command.

    Returns:
        ``[Device: <hostname> | IP: <ip> | OS: <os>]\\n<output>``
        or an ``[ERROR_TYPE]``-prefixed message on failure.
    """
    username: str | None = os.getenv("ROUTER_USER")
    password: str | None = os.getenv("ROUTER_PASS")

    if not username or not password:
        return (
            f"[CONFIG ERROR] {device_ip} (OS: {os_platform}): "
            "ROUTER_USER or ROUTER_PASS is not set in .env"
        )

    # Safety check
    safe, reason = validate_command(command)
    if not safe:
        logger.warning("BLOCKED unsafe command on %s: %r — %s", device_ip, command, reason)
        return f"[BLOCKED] {reason}"

    # Use cached hostname if available, fall back to IP
    with _CACHE_LOCK:
        hostname = _HOSTNAME_CACHE.get(device_ip, device_ip)

    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            entry = _get_or_open_connection(
                device_ip=device_ip,
                os_platform=os_platform,
                username=username,
                password=password,
            )
            with entry.lock:
                entry.last_used = time.monotonic()
                hostname = entry.hostname or device_ip
                output = _execute_command(entry.connection, command)
                entry.last_used = time.monotonic()
            break  # success

        except NetmikoAuthenticationException as exc:
            with _CACHE_LOCK:
                _HOSTNAME_CACHE.pop(device_ip, None)
            _invalidate_connection(device_ip)
            return f"[AUTH ERROR] Authentication failed for {device_ip}: {exc}"

        except (NetmikoTimeoutException, Exception) as exc:
            _invalidate_connection(device_ip)
            last_exc = exc
            if attempt == 0:
                logger.info(
                    "SSH attempt 1 failed for %s, retrying with fresh connection: %s",
                    device_ip, exc,
                )
                continue  # retry once with a fresh connection
            # Second attempt also failed — return the error
            if isinstance(exc, NetmikoTimeoutException):
                return f"[TIMEOUT ERROR] {device_ip} (OS: {os_platform}): {exc}"
            return f"[SSH ERROR] {device_ip} (OS: {os_platform}): {exc}"
    else:
        # Should not reach here, but just in case
        return f"[SSH ERROR] {device_ip} (OS: {os_platform}): {last_exc}"

    header = f"[Device: {hostname} | IP: {device_ip} | OS: {os_platform}]"
    return f"{header}\n{output}"
