from __future__ import annotations

import os
import re
from datetime import datetime, timezone

# Mnemonics that are known IOS image artefacts in EVE-NG lab environments.
# Set AIOPS_FILTER_LAB_NOISE=0 to re-enable on real hardware.
_FILTER_LAB_NOISE: bool = os.getenv("AIOPS_FILTER_LAB_NOISE", "1") == "1"
_LAB_NOISE_MNEMONICS: frozenset[str] = frozenset({
    "INTVULN",    # critical-region interrupt bug — always accompanies Traceback in EVE-NG
    "CPUHOG",     # scheduler hog — EVE-NG IOS image artefact
    "EXCEPTION",  # IOS exception dump
    "TRACEBACK",  # explicit %SYS-TRACEBACK mnemonic (distinct from -Traceback= hex lines)
    "FATAL",      # fatal error dump
    "HALTED",     # system halted
})

# ── Regex patterns ──────────────────────────────────────────────────────────

# Cisco IOS mnemonic: %FACILITY-SEVERITY-MNEMONIC
# Facility may contain hyphens (e.g. SPANNING-TREE, IP-BGP)
_CISCO_MNEMONIC_RE = re.compile(r"%([A-Z0-9_-]+)-(\d)-([A-Z0-9_]+)")

# Strict IPv4 — only valid octets 0-255, not e.g. 300.x.x.x
_VALID_OCTET = r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"
_IPV4_PATTERN = rf"(?:{_VALID_OCTET}\.){{3}}{_VALID_OCTET}"
_IP_RE = re.compile(rf"\b{_IPV4_PATTERN}\b")
_NEIGHBOR_RE = re.compile(
    rf"(?:neighbor|peer|Nbr)\s+({_IPV4_PATTERN})",
    re.IGNORECASE,
)

_INTERFACE_RE = re.compile(
    r"\b(?:GigabitEthernet|Gi|FastEthernet|Fa|TenGigabitEthernet|TenGig|Te|"
    r"Ethernet|Eth|Loopback|Lo|Tunnel|Tu|Port-channel|Po|Vlan|Vl|Serial|Se)"
    r"(?:\d|/)[^\s(),:;]*",   # stop before closing parens / punctuation
    re.IGNORECASE,
)

# Syslog timestamp prefix — strip before analysis.
# Handles all common formats in the wild:
#   "Mar 23 01:39:50 10.255.0.1 %OSPF-5-ADJCHG: ..."           plain
#   "<163>Mar 23 01:39:50 10.255.0.1 %OSPF-5-ADJCHG: ..."      RAWMSG (RFC 3164 with priority)
#   "000123: Mar 23 01:39:50 10.255.0.1 %OSPF-5-ADJCHG: ..."   Cisco sequence number
#   "000123: *Mar 23 01:39:50.123 10.255.0.1 ..."               Cisco seq + star + milliseconds
#   "<186>60: -Traceback= ..."                                   EVE-NG: RFC priority + seq (no timestamp)
_SYSLOG_PREFIX_RE = re.compile(
    r"^"
    r"(?:<\d+>)?"                                                 # RFC 3164 priority <n>  ← MUST come first
    r"(?:\d+:\s*)?"                                               # Cisco sequence number (after priority)
    r"\*?"                                                        # Cisco star timestamp marker
    r"(?:[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+)?"  # timestamp (opt .ms)
    r"(?:" + _IPV4_PATTERN + r"\s+)?"                            # source IP
)

# ── Boot / noise patterns to ignore ─────────────────────────────────────────

_NOISE_PATTERNS = [
    re.compile(r"^\s*$"),                                        # blank lines
    re.compile(r"^\s*-?Traceback=", re.IGNORECASE),             # IOS traceback dumps (no seq prefix)
    re.compile(r"^\s*\d+:\s*-?Traceback=", re.IGNORECASE),     # IOS traceback with seq number (EVE-NG)
    re.compile(r"-Traceback=\s*[0-9A-Fa-f]{4}", re.IGNORECASE),# Traceback anywhere + hex content (belt-and-suspenders)
    re.compile(r"^\s*Cisco IOS Software", re.IGNORECASE),       # version banner
    re.compile(r"^\s*Technical Support:", re.IGNORECASE),       # support URL
    re.compile(r"^\s*Copyright \(c\)", re.IGNORECASE),          # copyright line
    re.compile(r"^\s*Compiled\s+", re.IGNORECASE),              # compile info
    re.compile(r"^\s*[0-9A-Fa-f]{6,}[zZ]?\s"),                 # pure hex traceback lines
]

# ── Cisco facility → event family mapping ────────────────────────────────────

_FACILITY_MAP: dict[str, str] = {
    # ── Routing protocols ────────────────────────────────────────────────────
    "BGP":            "bgp",
    "IP-BGP":         "bgp",
    "OSPF":           "ospf",
    "EIGRP":          "eigrp",
    "DUAL":           "eigrp",          # EIGRP DUAL FSM
    "IGRP":           "eigrp",
    "RIP":            "routing",
    "ISIS":           "routing",
    "IS-IS":          "routing",
    "PIM":            "multicast",
    "MSDP":           "multicast",
    "IGMP":           "multicast",
    "RSVP":           "routing",
    # ── Interface / physical ─────────────────────────────────────────────────
    "LINK":           "interface",
    "LINEPROTO":      "interface",
    "LACP":           "interface",
    "PAGP":           "interface",
    "DTP":            "interface",
    "ETHCHANNEL":     "interface",
    # ── Tunnels / VPN ────────────────────────────────────────────────────────
    "TUNNEL":         "tunnel",
    "CRYPTO":         "tunnel",
    "GRE":            "tunnel",
    "IPSEC":          "tunnel",
    "IKMP":           "tunnel",         # IKE/ISAKMP
    "ISAKMP":         "tunnel",
    # ── High-availability / redundancy ───────────────────────────────────────
    "HSRP":           "hsrp",
    "VRRP":           "hsrp",
    "GLBP":           "hsrp",
    # ── Tracking / IP SLA ────────────────────────────────────────────────────
    "TRACKING":       "tracking",
    "TRACK":          "tracking",
    "SLA":            "tracking",
    "IPSLA":          "tracking",
    # ── Spanning Tree (L2) ───────────────────────────────────────────────────
    "SPANTREE":       "spanning_tree",
    "SPANNING-TREE":  "spanning_tree",
    "STP":            "spanning_tree",
    "MSTP":           "spanning_tree",
    "RSTP":           "spanning_tree",
    # ── Security ─────────────────────────────────────────────────────────────
    "SEC":            "security",
    "AUTHMGR":        "security",
    "DOT1X":          "security",
    "RADIUS":         "security",
    "TACACS":         "security",
    "AAA":            "security",
    "ACL":            "security",
    "FW":             "security",
    "IPS":            "security",
    # ── System ───────────────────────────────────────────────────────────────
    "SYS":            "system",
    "SNMP":           "system",
    "SSH":            "system",
    "NTP":            "system",
    "PLATFORM":       "system",
    "CONFIG":         "config",
    "PARSER":         "config",
    "ARCHIVE":        "config",
    # ── Device health / hardware ─────────────────────────────────────────────
    "ENV":            "device_health",
    "FAN":            "device_health",
    "POWER":          "device_health",
    "TEMPERATURE":    "device_health",
    "DIAG":           "device_health",
    "HARDWARE":       "device_health",
    "ROMMON":         "device_health",
    # ── Neighbor discovery ────────────────────────────────────────────────────
    "CDP":            "neighbor_discovery",
    "LLDP":           "neighbor_discovery",
}

# Cisco severity numbers → (our severity label, base state)
_CISCO_SEVERITY_MAP: dict[int, tuple[str, str]] = {
    0: ("critical", "down"),      # emergencies
    1: ("critical", "down"),      # alerts
    2: ("critical", "down"),      # critical
    3: ("warning",  "degraded"),  # errors
    4: ("warning",  "degraded"),  # warnings
    5: ("info",     "info"),      # notifications
    6: ("info",     "info"),      # informational
    7: ("info",     "info"),      # debugging
}

# ── Mnemonic-driven state detection ─────────────────────────────────────────

# Mnemonics that require special state interpretation beyond keyword scanning
_MNEMONIC_STATE_MAP: dict[str, tuple[str, str]] = {
    "RESTART":    ("restart",  "warning"),
    "COLDSTART":  ("restart",  "info"),
    "INTVULN":    ("fault",    "critical"),
    "CPUHOG":     ("degraded", "warning"),
    "TRACEBACK":  ("crash",    "critical"),
    "EXCEPTION":  ("crash",    "critical"),
    "FATAL":      ("crash",    "critical"),
    "RELOAD":     ("restart",  "warning"),
    "HALTED":     ("down",     "critical"),
}

# INTVULN always signals a device-level critical fault regardless of facility
_INTVULN_FAMILY_OVERRIDE = "device_health"
# Mnemonics that override family → device_health
_DEVICE_HEALTH_MNEMONICS = {"INTVULN", "TRACEBACK", "EXCEPTION", "FATAL", "CPUHOG", "HALTED"}

# ── Up / down keyword sets ───────────────────────────────────────────────────

_UP_KEYWORDS = frozenset({
    "FULL",          # OSPF full adjacency
    "established",   # BGP established
    "up",            # generic
    "recovered",
    "restored",
    "Loading Done",  # OSPF loading complete
    "LOADING to FULL",
    "cleared",
    "reachable",
})

# Genuine "fault/down" indicators.
# EXSTART / INIT / 2WAY are intentionally kept: regression from FULL to any of
# these is a fault condition even if not a complete outage.
_DOWN_KEYWORDS = frozenset({
    "down",
    "lost",
    "failed",
    "EXSTART",
    "INIT",
    "2WAY",
    "went down",
    "not responding",
    "administratively down",
    "unreachable",
    "Dead timer expired",
})

# State-transition arrows — definitive: final direction wins.
# e.g. "Down -> Up" (recovery), "Up -> Down" (fault)
_TRANSITION_UP_RE   = re.compile(r"\bdown\s*[-–>]+\s*up\b",   re.IGNORECASE)
_TRANSITION_DOWN_RE = re.compile(r"\bup\s*[-–>]+\s*down\b",   re.IGNORECASE)

# All state values that represent a healed/recovered condition
_RECOVERY_STATES = frozenset({
    "up", "established", "recovered", "restored", "cleared", "restart",
})


# ── Internal helpers ─────────────────────────────────────────────────────────

def _clean_token(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().rstrip(",:;.")


def _is_noise(raw_message: str) -> bool:
    """Return True if this line is boot-banner / hex-dump noise."""
    stripped = raw_message.strip()
    for pattern in _NOISE_PATTERNS:
        if pattern.search(stripped):
            return True
    return False


def _strip_syslog_prefix(raw_message: str) -> str:
    """Strip timestamp / source-IP / priority prefix from a raw syslog line."""
    return _SYSLOG_PREFIX_RE.sub("", raw_message).strip()


def _detect_family_cisco(facility: str) -> str:
    return _FACILITY_MAP.get(facility.upper(), "syslog")


def _detect_family_keyword(message: str) -> str:
    """Keyword-based family fallback when no Cisco mnemonic is present."""
    lowered = message.lower()
    if "bgp" in lowered:
        return "bgp"
    if "ospf" in lowered:
        return "ospf"
    if "eigrp" in lowered:
        return "eigrp"
    if "isis" in lowered:
        return "routing"
    if "tunnel" in lowered or "ipsec" in lowered or "gre" in lowered:
        return "tunnel"
    if "spanning" in lowered or "stp" in lowered:
        return "spanning_tree"
    if "hsrp" in lowered or "vrrp" in lowered or "glbp" in lowered:
        return "hsrp"
    if "lineproto" in lowered or "interface" in lowered or "link" in lowered:
        return "interface"
    if "track" in lowered or "sla" in lowered:
        return "tracking"
    if "config" in lowered:
        return "config"
    if "power" in lowered or "fan" in lowered or "temperature" in lowered:
        return "device_health"
    if "traceback" in lowered or "exception" in lowered or "crash" in lowered:
        return "device_health"
    if "radius" in lowered or "dot1x" in lowered or "aaa" in lowered:
        return "security"
    if "cdp" in lowered or "lldp" in lowered:
        return "neighbor_discovery"
    return "syslog"


def _detect_state(
    message: str,
    cisco_severity: int | None = None,
    mnemonic: str | None = None,
) -> tuple[str, str]:
    """Return (event_state, severity) from message content + Cisco mnemonic/severity."""
    lowered = message.lower()

    # 1. State-transition arrows are definitive ("Down -> Up", "Up -> Down").
    #    Check first so conflicting keywords (e.g. "FULL to DOWN") resolve correctly.
    if _TRANSITION_UP_RE.search(message):
        return "up", "info"
    if _TRANSITION_DOWN_RE.search(message):
        return "down", "critical"

    # 2. Keyword presence
    has_up   = any(kw.lower() in lowered for kw in _UP_KEYWORDS)
    has_down = any(kw.lower() in lowered for kw in _DOWN_KEYWORDS)

    # 3. Special mnemonic handlers
    if mnemonic:
        mn = mnemonic.upper()

        # Fixed-state mnemonics (no keyword context needed)
        if mn in _MNEMONIC_STATE_MAP:
            return _MNEMONIC_STATE_MAP[mn]

        # ADJCHG (OSPF): "from X to Y" — whichever keyword appears LAST wins
        if mn == "ADJCHG":
            if has_down and has_up:
                down_positions = [lowered.rfind(kw.lower()) for kw in _DOWN_KEYWORDS if kw.lower() in lowered]
                up_positions   = [lowered.rfind(kw.lower()) for kw in _UP_KEYWORDS   if kw.lower() in lowered]
                if down_positions and up_positions:
                    return ("down", "critical") if max(down_positions) > max(up_positions) else ("up", "info")
            if has_up:
                return "up", "info"
            if has_down:
                return "down", "critical"
            return "degraded", "warning"   # unknown OSPF state change

        # ADJCHANGE (BGP): "neighbor X Up/Down"
        if mn == "ADJCHANGE":
            if has_down:
                return "down", "critical"
            if has_up:
                return "up", "info"
            return "degraded", "warning"

        # UPDOWN / CHANGED (LINEPROTO / LINK)
        # NOTE: Do NOT use has_down/has_up here — the mnemonic string "UPDOWN"
        # itself contains "down", causing has_down=True for every message,
        # including "changed state to up". Use targeted phrase matching instead.
        if mn in ("UPDOWN", "CHANGED"):
            if "administratively down" in lowered:
                return "admin_down", "info"
            # "changed state to up" / "line protocol … is up"
            if re.search(r"\bstate\s+to\s+up\b|protocol\b.*\bis\s+up\b", lowered):
                return "up", "info"
            # "changed state to down" / "line protocol … is down"
            if re.search(r"\bstate\s+to\s+down\b|protocol\b.*\bis\s+down\b", lowered):
                return "down", "critical"
            # Generic fallback for non-standard variants
            if has_up and "down" not in lowered.replace("updown", "").replace("changed", ""):
                return "up", "info"
            return "down", "critical"

    # 4. Cisco severity number with keyword override
    if cisco_severity is not None:
        sev, state = _CISCO_SEVERITY_MAP.get(cisco_severity, ("info", "info"))
        if has_down:
            return "down", "critical" if sev != "info" else "warning"
        if has_up:
            return "up", "info"
        return state, sev

    # 5. Pure keyword fallback
    if has_down:
        return "down", "critical"
    if has_up:
        return "up", "info"
    if any(kw in lowered for kw in ("warning", "degraded", "flap", "unstable")):
        return "degraded", "warning"
    return "info", "info"


# ── Public API ───────────────────────────────────────────────────────────────

def parse_syslog(
    source_ip: str,
    hostname: str | None,
    raw_message: str,
    event_time: datetime | None = None,
) -> dict | None:
    """Parse a single syslog line.

    Returns a structured event dict, or None for lines that should be silently
    discarded (noise, boot banners, admin-initiated shutdowns).
    """
    cleaned = _strip_syslog_prefix(raw_message)

    if _is_noise(cleaned):
        return None

    # ── Cisco mnemonic detection ─────────────────────────────────────────────
    cisco_match = _CISCO_MNEMONIC_RE.search(cleaned)
    cisco_facility: str | None = None
    cisco_severity: int | None = None
    mnemonic: str | None = None

    if cisco_match:
        cisco_facility = cisco_match.group(1)
        cisco_severity = int(cisco_match.group(2))
        mnemonic       = cisco_match.group(3)
        family         = _detect_family_cisco(cisco_facility)
    else:
        family = _detect_family_keyword(cleaned)

    # ── Drop known EVE-NG / IOS-image artefact mnemonics ────────────────────
    if _FILTER_LAB_NOISE and mnemonic and mnemonic.upper() in _LAB_NOISE_MNEMONICS:
        return None

    # ── Family override for critical device-fault mnemonics ─────────────────
    if mnemonic and mnemonic.upper() in _DEVICE_HEALTH_MNEMONICS:
        family = _INTVULN_FAMILY_OVERRIDE

    # ── State / severity detection ───────────────────────────────────────────
    state, severity = _detect_state(cleaned, cisco_severity, mnemonic)

    # ── Suppress admin-down events (operator-initiated, no recovery needed) ──
    if state == "admin_down":
        return None

    # ── Extract interface and neighbor ───────────────────────────────────────
    interface_match = _INTERFACE_RE.search(cleaned)
    neighbor_match  = _NEIGHBOR_RE.search(cleaned)
    interface_name  = _clean_token(interface_match.group(0) if interface_match else None)
    neighbor_ip     = _clean_token(neighbor_match.group(1) if neighbor_match else None)

    # Peer IP: prefer explicit neighbor keyword; fall back to any non-source IP
    peer_ip = neighbor_ip
    if peer_ip is None:
        ip_match = _IP_RE.search(cleaned)
        if ip_match and ip_match.group(0) != source_ip:
            peer_ip = _clean_token(ip_match.group(0))

    # ── Correlation key ──────────────────────────────────────────────────────
    # Interface-family events without an extracted interface use a placeholder
    # so that "10.1.1.1|interface" never swallows all link events on a device.
    key_parts: list[str] = [source_ip, family]
    if interface_name:
        key_parts.append(interface_name.lower())
    elif family == "interface":
        key_parts.append("unknown_intf")
    if neighbor_ip:
        key_parts.append(neighbor_ip)
    correlation_key = "|".join(key_parts)

    # ── Title ────────────────────────────────────────────────────────────────
    summary = cleaned.strip()
    if len(summary) > 300:
        summary = f"{summary[:297]}..."

    title_scope    = interface_name or neighbor_ip or hostname or source_ip
    mnemonic_label = f" ({mnemonic})" if mnemonic else ""
    title = f"{family.replace('_', ' ').title()} {state} on {title_scope}{mnemonic_label}"

    return {
        "source_ip":       source_ip,
        "hostname":        hostname,
        "event_time":      event_time or datetime.now(timezone.utc),
        "event_family":    family,
        "event_state":     state,
        "severity":        severity,
        "title":           title,
        "summary":         summary,
        "correlation_key": correlation_key,
        "metadata": {
            "interface":       interface_name,
            "neighbor_ip":     neighbor_ip,
            "peer_ip":         peer_ip,
            "recovery_signal": state in _RECOVERY_STATES,
            "cisco_facility":  cisco_facility,
            "cisco_severity":  cisco_severity,
            "mnemonic":        mnemonic,
        },
    }
