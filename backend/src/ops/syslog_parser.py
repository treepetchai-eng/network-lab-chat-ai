"""Parse syslog-ng stored device logs into structured events."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

MONTHS = {name: idx for idx, name in enumerate(calendar.month_abbr) if name}
_MAX_FUTURE_SKEW = timedelta(days=1)
SEVERITY_NAMES = {
    0: "emergency",
    1: "alert",
    2: "critical",
    3: "error",
    4: "warning",
    5: "notice",
    6: "informational",
    7: "debug",
}

SYSLOG_RE = re.compile(
    r"^(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<source_ip>\d+\.\d+\.\d+\.\d+)\s+"
    r"(?P<body>.+)$"
)
EVENT_RE = re.compile(
    r"^%(?P<facility>[A-Z0-9_]+)-(?P<severity>\d)-(?P<mnemonic>[A-Z0-9_]+):\s*(?P<message>.*)$"
)
OSPF_RE = re.compile(
    r"Process\s+(?P<process>\S+),\s+Nbr\s+(?P<neighbor>\S+)\s+on\s+"
    r"(?P<interface>\S+)\s+from\s+(?P<old_state>\S+)\s+to\s+(?P<new_state>\S+),\s+(?P<reason>.+)"
)
EIGRP_RE = re.compile(
    r"EIGRP-IPv4\s+(?P<asn>\d+):\s+Neighbor\s+(?P<neighbor>\S+)\s+\((?P<interface>[^)]+)\)\s+"
    r"is\s+(?P<state>up|down):\s+(?P<reason>.+)",
    re.IGNORECASE,
)
LINEPROTO_RE = re.compile(
    r"Line protocol on Interface\s+(?P<interface>[^,]+),\s+changed state to\s+(?P<state>\S+)",
    re.IGNORECASE,
)
TRACK_RE = re.compile(
    r"(?P<track_id>\d+)\s+(?P<object>.+?)\s+(?P<old_state>\S+)\s+->\s+(?P<new_state>\S+)"
)
BGP_NEIGHBOR_RE = re.compile(r"neighbor\s+(?P<neighbor>\S+)\s+(?P<state>Up|Down)\b", re.IGNORECASE)
BGP_NOTIFICATION_RE = re.compile(r"neighbor\s+(?P<neighbor>\S+)", re.IGNORECASE)
INTVULN_RE = re.compile(
    r"intfc=(?P<interface>[^ ]+)\s+-Process=\s+\"(?P<process>[^\"]+)\"",
    re.IGNORECASE,
)
LINK_CHANGED_RE = re.compile(
    r"Interface\s+(?P<interface>\S+?),?\s+changed state to\s+(?P<state>.+)",
    re.IGNORECASE,
)
CPUHOG_RE = re.compile(
    r"Process\s+(?P<process>.+?),\s+(?:pid\s+\d+.*?)?(?:holding|held)\s+(?:the CPU\s+)?for\s+(?P<duration>\d+)\s*ms",
    re.IGNORECASE,
)


@dataclass
class ParsedEvent:
    source_ip: str
    event_time: datetime | None
    severity_num: int | None
    severity: str
    facility: str
    mnemonic: str
    event_code: str
    event_type: str
    protocol: str | None
    interface_name: str | None
    neighbor: str | None
    state: str | None
    correlation_key: str
    summary: str
    details: dict


def _file_date(file_path: str) -> tuple[int, int, int] | None:
    stem = Path(file_path).stem
    if len(stem) == 8 and stem.isdigit():
        return int(stem[:4]), int(stem[4:6]), int(stem[6:8])
    return None


def _shift_year(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # Handle leap-day rollover safely when moving across years.
        return value.replace(month=2, day=28, year=value.year + years)


def _parse_event_time(
    month: str,
    day: str,
    time_str: str,
    file_path: str,
    *,
    reference_time: datetime | None = None,
) -> tuple[datetime | None, dict]:
    file_bits = _file_date(file_path)
    try:
        hh, mm, ss = [int(part) for part in time_str.split(":")]
        if file_bits:
            year, month_num, day_num = file_bits
            anchor = datetime(year, month_num, day_num, 23, 59, 59, tzinfo=timezone.utc)
            timestamp_source = "file_date"
        else:
            anchor = reference_time or datetime.now(timezone.utc)
            timestamp_source = "reference_time" if reference_time else "current_time"

        month_num = MONTHS[month]
        candidate = datetime(anchor.year, month_num, int(day), hh, mm, ss, tzinfo=timezone.utc)
        year_adjustment = 0

        if candidate > anchor + _MAX_FUTURE_SKEW:
            candidate = _shift_year(candidate, -1)
            year_adjustment = -1
        elif anchor - candidate > timedelta(days=366):
            candidate = _shift_year(candidate, 1)
            year_adjustment = 1

        return candidate, {
            "timestamp_source": timestamp_source,
            "timestamp_reference": anchor.isoformat(),
            "timestamp_year_adjustment": year_adjustment,
        }
    except Exception:
        return None, {"timestamp_source": "parse_error"}


def _severity_name(num: int | None) -> str:
    if num is None:
        return "unknown"
    return SEVERITY_NAMES.get(num, "unknown")


def _build_generic_summary(source_ip: str, event_code: str, message: str) -> str:
    return f"{source_ip}: {event_code} {message}".strip()


def parse_syslog_line(
    line: str,
    file_path: str,
    *,
    reference_time: datetime | None = None,
) -> ParsedEvent | None:
    """Parse a single raw syslog line into a structured event."""
    value = line.strip()
    if not value:
        return None

    match = SYSLOG_RE.match(value)
    if not match:
        # Lines without a device IP prefix (e.g., IOS boot banners, copyright strings)
        # carry no actionable information — discard them.
        return None

    source_ip = match.group("source_ip")
    body = match.group("body")
    event_time, timestamp_meta = _parse_event_time(
        match.group("month"),
        match.group("day"),
        match.group("time"),
        file_path,
        reference_time=reference_time,
    )

    if body.startswith("-Traceback="):
        summary = f"{source_ip}: traceback captured after a device fault"
        return ParsedEvent(
            source_ip=source_ip,
            event_time=event_time,
            severity_num=2,
            severity="critical",
            facility="TRACEBACK",
            mnemonic="TRACEBACK",
            event_code="TRACEBACK",
            event_type="device_traceback",
            protocol=None,
            interface_name=None,
            neighbor=None,
            state=None,
            correlation_key=f"fault:{source_ip}:intvuln",
            summary=summary,
            details={
                "traceback": body.removeprefix("-Traceback=").strip(),
                **timestamp_meta,
            },
        )

    event_match = EVENT_RE.match(body)
    if not event_match:
        summary = _build_generic_summary(source_ip, "RAW", body)
        return ParsedEvent(
            source_ip=source_ip,
            event_time=event_time,
            severity_num=None,
            severity="unknown",
            facility="GENERIC",
            mnemonic="RAW",
            event_code="RAW",
            event_type="generic_syslog",
            protocol=None,
            interface_name=None,
            neighbor=None,
            state=None,
            correlation_key=f"generic:{source_ip}",
            summary=summary,
            details={"message": body, **timestamp_meta},
        )

    facility = event_match.group("facility")
    severity_num = int(event_match.group("severity"))
    mnemonic = event_match.group("mnemonic")
    message = event_match.group("message")
    event_code = f"{facility}-{severity_num}-{mnemonic}"

    protocol: str | None = None
    interface_name: str | None = None
    neighbor: str | None = None
    state: str | None = None
    event_type = "generic_syslog"
    correlation_key = f"event:{source_ip}:{facility}:{mnemonic}"
    details: dict = {"message": message, **timestamp_meta}

    if facility == "OSPF" and mnemonic == "ADJCHG":
        ospf = OSPF_RE.search(message)
        if ospf:
            protocol = "OSPF"
            neighbor = ospf.group("neighbor")
            interface_name = ospf.group("interface")
            state = ospf.group("new_state").upper()
            reason = ospf.group("reason")
            details.update({
                "process": ospf.group("process"),
                "old_state": ospf.group("old_state"),
                "reason": reason,
            })
            correlation_key = f"neighbor:OSPF:{source_ip}:{neighbor}:{interface_name}"
            if state == "DOWN":
                event_type = "ospf_neighbor_down"
                summary = f"OSPF neighbor {neighbor} went down on {source_ip} via {interface_name}: {reason}"
            else:
                event_type = "ospf_neighbor_up"
                summary = f"OSPF neighbor {neighbor} recovered on {source_ip} via {interface_name}"
            return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility in {"BGP", "BGP_SESSION"}:
        bgp = BGP_NEIGHBOR_RE.search(message)
        notification = BGP_NOTIFICATION_RE.search(message)
        protocol = "BGP"
        neighbor = bgp.group("neighbor") if bgp else (notification.group("neighbor") if notification else None)
        if bgp:
            state = bgp.group("state").upper()
            event_type = "bgp_neighbor_up" if state == "UP" else "bgp_neighbor_down"
            summary = (
                f"BGP neighbor {neighbor} recovered on {source_ip}"
                if state == "UP"
                else f"BGP neighbor {neighbor} went down on {source_ip}"
            )
        elif mnemonic in {"NOTIFICATION", "NBR_RESET", "ADJCHANGE"} and neighbor:
            state = "DOWN"
            event_type = "bgp_neighbor_down"
            summary = f"BGP neighbor {neighbor} triggered a session reset on {source_ip}"
        else:
            summary = _build_generic_summary(source_ip, event_code, message)
        correlation_key = f"neighbor:BGP:{source_ip}:{neighbor or 'unknown'}"
        return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility == "DUAL" and mnemonic == "NBRCHANGE":
        eigrp = EIGRP_RE.search(message)
        if eigrp:
            protocol = "EIGRP"
            neighbor = eigrp.group("neighbor")
            interface_name = eigrp.group("interface")
            state = eigrp.group("state").upper()
            details.update({"reason": eigrp.group("reason"), "asn": eigrp.group("asn")})
            correlation_key = f"neighbor:EIGRP:{source_ip}:{neighbor}:{interface_name}"
            event_type = "eigrp_neighbor_up" if state == "UP" else "eigrp_neighbor_down"
            summary = (
                f"EIGRP neighbor {neighbor} recovered on {source_ip} via {interface_name}"
                if state == "UP"
                else f"EIGRP neighbor {neighbor} went down on {source_ip} via {interface_name}"
            )
            return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility == "LINEPROTO" and mnemonic == "UPDOWN":
        lineproto = LINEPROTO_RE.search(message)
        if lineproto:
            protocol = "LINEPROTO"
            interface_name = lineproto.group("interface")
            state = lineproto.group("state").upper()
            correlation_key = f"interface:{source_ip}:{interface_name}"
            event_type = "interface_up" if state == "UP" else "interface_down"
            summary = (
                f"Interface {interface_name} came back up on {source_ip}"
                if state == "UP"
                else f"Interface {interface_name} went down on {source_ip}"
            )
            return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility == "TRACK" and mnemonic == "STATE":
        track = TRACK_RE.search(message)
        if track:
            old_state = track.group("old_state").upper()
            new_state = track.group("new_state").upper()
            state = new_state
            details.update({
                "track_id": track.group("track_id"),
                "object": track.group("object"),
                "old_state": old_state,
            })
            correlation_key = f"track:{source_ip}:{track.group('track_id')}"
            if new_state == "DOWN":
                event_type = "track_down"
                summary = f"Track {track.group('track_id')} changed to down on {source_ip}"
            elif new_state == "UP":
                event_type = "track_up"
                summary = f"Track {track.group('track_id')} changed to up on {source_ip}"
            else:
                event_type = "track_state_change"
                summary = f"Track {track.group('track_id')} changed state on {source_ip}: {old_state} -> {new_state}"
            return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility == "SYS" and mnemonic == "CONFIG_I":
        correlation_key = f"config:{source_ip}"
        event_type = "config_change"
        summary = f"Configuration changed on {source_ip}"
        return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility == "SYS" and mnemonic == "RESTART":
        correlation_key = f"restart:{source_ip}"
        event_type = "device_restart"
        summary = f"Device {source_ip} restarted"
        return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility == "SYS" and mnemonic == "CPUHOG":
        cpuhog = CPUHOG_RE.search(message)
        if cpuhog:
            process_name = cpuhog.group("process")
            duration_ms = cpuhog.group("duration")
            details.update({"process": process_name, "duration_ms": duration_ms})
            summary = f"CPU hog on {source_ip}: process '{process_name}' held CPU for {duration_ms}ms"
        else:
            summary = f"CPU hog warning on {source_ip}: {message}"
        correlation_key = f"perf:{source_ip}:cpuhog"
        event_type = "cpu_hog"
        return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility == "SSH" and mnemonic == "ENABLED":
        correlation_key = f"service:{source_ip}:ssh"
        event_type = "ssh_enabled"
        summary = f"SSH was enabled on {source_ip}"
        return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility == "LINK" and mnemonic == "CHANGED":
        link_changed = LINK_CHANGED_RE.search(message)
        if link_changed:
            interface_name = link_changed.group("interface").rstrip(",")
            new_state = link_changed.group("state").strip().lower()
            state = new_state.upper()
            details.update({"state": new_state})
            if "administratively" in new_state:
                correlation_key = f"admin_down:{source_ip}:{interface_name}"
                event_type = "interface_admin_down"
                summary = f"Interface {interface_name} was administratively shut down on {source_ip}"
            else:
                correlation_key = f"interface:{source_ip}:{interface_name}"
                event_type = "interface_link_change"
                summary = f"Interface {interface_name} changed state to {new_state} on {source_ip}"
            return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility == "LINK" and mnemonic == "INTVULN":
        vuln = INTVULN_RE.search(message)
        if vuln:
            interface_name = vuln.group("interface")
            details.update({"process": vuln.group("process")})
        correlation_key = f"fault:{source_ip}:intvuln"
        event_type = "critical_region_fault"
        summary = f"Critical-region fault reported on {source_ip}"
        return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility == "PLATFORM" and mnemonic == "SIGNATURE_VERIFIED":
        correlation_key = f"platform:{source_ip}:signature"
        event_type = "platform_notice"
        summary = f"Platform image verification completed on {source_ip}"
        return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    if facility == "GRUB":
        correlation_key = f"config-write:{source_ip}"
        event_type = "config_write"
        summary = f"Boot configuration write event on {source_ip}"
        return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)

    summary = _build_generic_summary(source_ip, event_code, message)
    return ParsedEvent(source_ip, event_time, severity_num, _severity_name(severity_num), facility, mnemonic, event_code, event_type, protocol, interface_name, neighbor, state, correlation_key, summary, details)
