"""
src/tools/safety.py
===================
Command safety validation for network device CLI execution.

Implements a deny-by-default allowlist: only commands starting with known
safe prefixes are permitted.  Configuration and destructive commands are
explicitly blocked.

The module is deliberately standalone so it can be unit-tested without
importing Netmiko or LangChain.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Allowed command prefixes  (read-only / diagnostic)
# ---------------------------------------------------------------------------

ALLOWED_CMD_PREFIXES: tuple[str, ...] = (
    "show", "sh ", "display",           # read-only display
    "ping", "traceroute", "tracert",    # diagnostics
    "dir", "more",                      # file listing (read-only)
)

# ---------------------------------------------------------------------------
# Blocked command patterns  (configuration / destructive)
# ---------------------------------------------------------------------------

BLOCKED_CMD_RE = re.compile(
    r"^\s*(configure|conf\s|config\s|reload|reboot|write|wr\s|"
    r"copy\s|delete\s|erase|no\s|set\s|clear\s|debug|"
    r"format|squeeze|crypto|license|shutdown)",
    re.IGNORECASE,
)

# Linux/Unix pipes that are invalid on network devices
_LINUX_PIPE_RE = re.compile(
    r"\|\s*(head|tail|grep|awk|sed|wc|sort|uniq|cut|tee|xargs)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_command(command: str) -> tuple[bool, str]:
    """Validate whether a CLI command is safe to execute.

    Returns:
        (True, "")            if the command is allowed.
        (False, reason_str)   if the command is blocked.
    """
    cmd = command.strip()
    if not cmd:
        return False, "Empty command."

    # Block dangerous commands first (takes priority)
    if BLOCKED_CMD_RE.match(cmd):
        return False, (
            f"Command '{cmd.split()[0]}' is blocked — "
            "configuration and destructive commands are not allowed."
        )

    # Block Linux/Unix pipes
    if _LINUX_PIPE_RE.search(cmd):
        return False, (
            "Linux-style pipes (grep, head, tail, awk, sed) are not valid on "
            "network devices. Use IOS pipes: | include, | exclude, | begin, "
            "| section, | count."
        )

    # Allow known safe prefixes
    cmd_lower = cmd.lower()
    for prefix in ALLOWED_CMD_PREFIXES:
        if cmd_lower.startswith(prefix):
            return True, ""

    # Deny by default
    return False, (
        f"Command '{cmd}' does not start with a known safe prefix. "
        f"Allowed: {', '.join(ALLOWED_CMD_PREFIXES)}."
    )


def is_command_safe(command: str) -> bool:
    """Convenience wrapper — returns True if the command is allowed."""
    safe, _ = validate_command(command)
    return safe
