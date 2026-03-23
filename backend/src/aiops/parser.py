from __future__ import annotations

import re
from datetime import datetime, timezone

# ── Regex patterns ──────────────────────────────────────────────────────────

# Cisco IOS mnemonic: %FACILITY-SEVERITY-MNEMONIC
_CISCO_MNEMONIC_RE = re.compile(r"%([A-Z0-9_]+)-(\d)-([A-Z0-9_]+)")

_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_INTERFACE_RE = re.compile(
    r"\b(?:GigabitEthernet|Gi|FastEthernet|Fa|TenGigabitEthernet|TenGig|Te|"
    r"Ethernet|Eth|Loopback|Lo|Tunnel|Tu|Port-channel|Po|Vlan|Vl|Serial|Se)"
    r"(?:\d|/)\S*",
    re.IGNORECASE,
)
_NEIGHBOR_RE = re.compile(r"(?:neighbor|peer|Nbr)\s+(\d{1,3}(?:\.\d{1,3}){3})", re.IGNORECASE)

# Syslog timestamp prefix (strip before analysis)
# e.g. "Mar 23 01:39:50 10.255.0.1 "
_SYSLOG_PREFIX_RE = re.compile(
    r"^(?:[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+)?"  # timestamp
    r"(?:\d{1,3}(?:\.\d{1,3}){3}\s+)?"                        # source IP
)

# ── Boot / noise patterns to ignore ─────────────────────────────────────────

_NOISE_PATTERNS = [
    re.compile(r"^\s*$"),                                       # empty
    re.compile(r"^-?Traceback=", re.IGNORECASE),               # IOS traceback hex dumps
    re.compile(r"^Cisco IOS Software", re.IGNORECASE),          # version banner
    re.compile(r"^Technical Support:", re.IGNORECASE),          # support URL
    re.compile(r"^Copyright \(c\)", re.IGNORECASE),            # copyright
    re.compile(r"^Compiled\s+", re.IGNORECASE),                # compile info
    re.compile(r"^[0-9A-Fa-f]{6,}[zZ]?\s"),                   # pure hex traceback lines
]

# ── Cisco facility → event family mapping ────────────────────────────────────

_FACILITY_MAP: dict[str, str] = {
    "BGP":       "bgp",
    "OSPF":      "ospf",
    "EIGRP":     "eigrp",
    "DUAL":      "eigrp",
    "LINK":      "interface",
    "LINEPROTO": "interface",
    "TRACKING":  "tracking",
    "SLA":       "tracking",
    "TUNNEL":    "tunnel",
    "CRYPTO":    "tunnel",
    "GRE":       "tunnel",
    "IPSEC":     "tunnel",
    "SYS":       "system",
    "SNMP":      "system",
    "SSH":       "system",
    "SEC":       "security",
    "AUTHMGR":   "security",
    "CONFIG":    "config",
    "PARSER":    "config",
    "ENV":       "device_health",
    "FAN":       "device_health",
    "POWER":     "device_health",
    "TEMPERATURE": "device_health",
    "PLATFORM":  "system",
    "HSRP":      "hsrp",
    "VRRP":      "hsrp",
    "TRACK":     "tracking",
}

# Cisco severity numbers → our severity + state
_CISCO_SEVERITY_MAP: dict[int, tuple[str, str]] = {
    0: ("critical", "down"),     # emergencies
    1: ("critical", "down"),     # alerts
    2: ("critical", "down"),     # critical
    3: ("warning", "degraded"),  # errors
    4: ("warning", "degraded"),  # warnings
    5: ("info", "info"),         # notifications
    6: ("info", "info"),         # informational
    7: ("info", "info"),         # debugging
}

# Mnemonics that indicate specific states
_DOWN_MNEMONICS = {
    "ADJCHG",      # OSPF FULL → up, but DOWN → down (check message)
    "UPDOWN",      # LINEPROTO / LINK up/down
    "CHANGED",     # LINK changed state
    "RESTART",     # system restart
    "COLDSTART",   # SNMP cold start
    "INTVULN",     # interrupt vulnerability (critical)
    "CPUHOG",      # CPU hog
}

_UP_KEYWORDS = {"FULL", "established", "up", "recovered", "restored", "Loading Done", "LOADING to FULL"}
_DOWN_KEYWORDS = {"down", "lost", "failed", "EXSTART", "INIT", "2WAY", "went down", "not responding", "administratively down"}

# Cisco state-transition arrow patterns  e.g. "Down -> Up",  "Up -> Down"
# These are definitive — the final state is what matters, not keywords elsewhere.
_TRANSITION_RE = re.compile(r"\bdown\s*[-–>]+\s*up\b", re.IGNORECASE)       # → Up  (recovery)
_TRANSITION_DOWN_RE = re.compile(r"\bup\s*[-–>]+\s*down\b", re.IGNORECASE)  # → Down (fault)


def _clean_token(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().rstrip(",:;.")


def _is_noise(raw_message: str) -> bool:
    """Return True if this message is boot banner / noise that should not create events."""
    stripped = raw_message.strip()
    for pattern in _NOISE_PATTERNS:
        if pattern.search(stripped):
            return True
    return False


def _strip_syslog_prefix(raw_message: str) -> str:
    """Strip timestamp + source IP prefix from raw syslog line."""
    return _SYSLOG_PREFIX_RE.sub("", raw_message).strip()


def _detect_family_cisco(facility: str) -> str:
    """Map Cisco facility to event family."""
    return _FACILITY_MAP.get(facility.upper(), "syslog")


def _detect_family_keyword(message: str) -> str:
    """Fallback keyword-based family detection."""
    lowered = message.lower()
    if "bgp" in lowered:
        return "bgp"
    if "ospf" in lowered:
        return "ospf"
    if "eigrp" in lowered:
        return "eigrp"
    if "tunnel" in lowered or "ipsec" in lowered or "gre" in lowered:
        return "tunnel"
    if "lineproto" in lowered or "interface" in lowered or "link" in lowered:
        return "interface"
    if "track" in lowered or "sla" in lowered:
        return "tracking"
    if "config" in lowered:
        return "config"
    if "power" in lowered or "fan" in lowered or "temperature" in lowered:
        return "device_health"
    if "hsrp" in lowered or "vrrp" in lowered:
        return "hsrp"
    return "syslog"


def _detect_state(message: str, cisco_severity: int | None = None, mnemonic: str | None = None) -> tuple[str, str]:
    """Detect event state and severity from message content + Cisco mnemonic."""
    lowered = message.lower()

    # Cisco state-transition arrows are definitive — "Down -> Up" = recovery, "Up -> Down" = fault.
    # Check before keyword scanning so both-keyword messages resolve correctly.
    if _TRANSITION_RE.search(message):
        return "up", "info"
    if _TRANSITION_DOWN_RE.search(message):
        return "down", "critical"

    # Check for explicit up/down keywords
    has_up = any(kw.lower() in lowered for kw in _UP_KEYWORDS)
    has_down = any(kw.lower() in lowered for kw in _DOWN_KEYWORDS)

    # Special mnemonics
    if mnemonic:
        mn_upper = mnemonic.upper()
        if mn_upper == "ADJCHG":
            # For ADJCHG "from X to Y", the final state matters.
            # "from FULL to DOWN" → down (even though FULL is an up keyword)
            if has_down and has_up:
                # Both present — check which comes last in the message
                last_down = max(lowered.rfind(kw.lower()) for kw in _DOWN_KEYWORDS if kw.lower() in lowered)
                last_up = max(lowered.rfind(kw.lower()) for kw in _UP_KEYWORDS if kw.lower() in lowered)
                if last_down > last_up:
                    return "down", "critical"
                return "up", "info"
            if has_up:
                return "up", "info"
            if has_down:
                return "down", "critical"
        if mn_upper in ("UPDOWN", "CHANGED"):
            if "administratively down" in lowered:
                return "admin_down", "info"
            if has_down:
                return "down", "critical"
            if has_up:
                return "up", "info"
        if mn_upper == "RESTART":
            return "restart", "warning"
        if mn_upper == "COLDSTART":
            return "restart", "info"
        if mn_upper == "INTVULN":
            return "fault", "critical"
        if mn_upper == "CPUHOG":
            return "degraded", "warning"

    # Use Cisco severity number
    if cisco_severity is not None:
        sev, state = _CISCO_SEVERITY_MAP.get(cisco_severity, ("info", "info"))
        # Override state with keyword if clear
        if has_down:
            return "down", "critical" if sev != "info" else "warning"
        if has_up:
            return "up", "info"
        return state, sev

    # Pure keyword fallback
    if has_down:
        return "down", "critical"
    if has_up:
        return "up", "info"
    if any(kw in lowered for kw in ["warning", "degraded", "flap", "unstable"]):
        return "degraded", "warning"
    return "info", "info"


def parse_syslog(source_ip: str, hostname: str | None, raw_message: str, event_time: datetime | None = None) -> dict | None:
    """Parse a single syslog message.

    Returns None for noise/boot-banner messages that should be ignored.
    """
    cleaned = _strip_syslog_prefix(raw_message)

    # Filter noise
    if _is_noise(cleaned):
        return None

    # Try Cisco IOS mnemonic parsing
    cisco_match = _CISCO_MNEMONIC_RE.search(cleaned)
    cisco_facility: str | None = None
    cisco_severity: int | None = None
    mnemonic: str | None = None

    if cisco_match:
        cisco_facility = cisco_match.group(1)
        cisco_severity = int(cisco_match.group(2))
        mnemonic = cisco_match.group(3)
        family = _detect_family_cisco(cisco_facility)
    else:
        family = _detect_family_keyword(cleaned)

    state, severity = _detect_state(cleaned, cisco_severity, mnemonic)

    # Extract interface and neighbor
    interface_match = _INTERFACE_RE.search(cleaned)
    neighbor_match = _NEIGHBOR_RE.search(cleaned)
    interface_name = _clean_token(interface_match.group(0) if interface_match else None)
    neighbor_ip = _clean_token(neighbor_match.group(1) if neighbor_match else None)

    peer_ip = neighbor_ip
    if peer_ip is None:
        ip_match = _IP_RE.search(cleaned)
        if ip_match and ip_match.group(0) != source_ip:
            peer_ip = _clean_token(ip_match.group(0))

    # Build correlation key
    key_parts = [source_ip, family]
    if interface_name:
        key_parts.append(interface_name.lower())
    if neighbor_ip:
        key_parts.append(neighbor_ip)
    correlation_key = "|".join(key_parts)

    # Build title
    summary = cleaned.strip()
    if len(summary) > 300:
        summary = f"{summary[:297]}..."

    title_scope = interface_name or neighbor_ip or hostname or source_ip
    mnemonic_label = f" ({mnemonic})" if mnemonic else ""
    title = f"{family.replace('_', ' ').title()} {state} on {title_scope}{mnemonic_label}"

    return {
        "source_ip": source_ip,
        "hostname": hostname,
        "event_time": event_time or datetime.now(timezone.utc),
        "event_family": family,
        "event_state": state,
        "severity": severity,
        "title": title,
        "summary": summary,
        "correlation_key": correlation_key,
        "metadata": {
            "interface": interface_name,
            "neighbor_ip": neighbor_ip,
            "peer_ip": peer_ip,
            "recovery_signal": state == "up",
            "cisco_facility": cisco_facility,
            "cisco_severity": cisco_severity,
            "mnemonic": mnemonic,
        },
    }
