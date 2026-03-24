"""
src/tools/db_tools.py
=====================
LangChain tools for querying historical log and incident data from PostgreSQL.

All queries use parameterized statements (no dynamic SQL) and are read-only.
Results are trimmed to avoid flooding the LLM context window.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from langchain_core.tools import tool

from src.aiops.db import connect

logger = logging.getLogger(__name__)

_MAX_LOG_LIMIT = int(os.getenv("DB_TOOL_MAX_LOGS", "150"))
_MAX_INCIDENT_LIMIT = int(os.getenv("DB_TOOL_MAX_INCIDENTS", "30"))
_IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


def _fmt_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _resolve_device(cur, device: str) -> tuple[str | None, str | None]:
    """Return (ip_address, hostname) for a device string (hostname or IP)."""
    if not device:
        return None, None
    d = device.strip()
    if _IP_RE.match(d):
        cur.execute("SELECT hostname, ip_address FROM devices WHERE ip_address = %s LIMIT 1", (d,))
        row = cur.fetchone()
        if row:
            return row["ip_address"], row["hostname"]
        return d, None
    # Try exact hostname match (case-insensitive)
    cur.execute("SELECT hostname, ip_address FROM devices WHERE lower(hostname) = lower(%s) LIMIT 1", (d,))
    row = cur.fetchone()
    if row:
        return row["ip_address"], row["hostname"]
    # Try partial match
    cur.execute("SELECT hostname, ip_address FROM devices WHERE lower(hostname) LIKE lower(%s) LIMIT 1", (f"%{d}%",))
    row = cur.fetchone()
    if row:
        return row["ip_address"], row["hostname"]
    return None, None


# ---------------------------------------------------------------------------
# Tool 1: search_logs
# ---------------------------------------------------------------------------

@tool
def search_logs(
    device: str = "",
    severity: str = "",
    keyword: str = "",
    hours_back: int = 24,
    limit: int = 50,
) -> str:
    """Search raw syslog records stored in the database.

    Use this tool when the user asks about:
    - Recent logs from a specific device
    - Log patterns (e.g. interface down, OSPF, CPU spikes)
    - How many times an event occurred
    - What happened on a device in a time range

    Args:
        device: Device hostname (e.g. "HQ-CORE-RT01") or IP address. Leave empty for all devices.
        severity: Filter by parse_status or severity keyword (e.g. "critical", "noise"). Leave empty for all.
        keyword: Text to search in raw_message (case-insensitive substring). Leave empty to skip.
        hours_back: How many hours back to search. Default 24. Max 168 (7 days).
        limit: Max rows to return. Default 50. Max 150.

    Returns:
        JSON string with matching log records or an error message.
    """
    hours_back = min(max(1, hours_back), 168)
    limit = min(max(1, limit), _MAX_LOG_LIMIT)

    try:
        with connect() as conn:
            cur = conn.cursor()

            ip_addr, hostname = _resolve_device(cur, device)
            if device and ip_addr is None:
                return json.dumps({"error": f"Device '{device}' not found in inventory."})

            conditions = ["rl.received_at >= NOW() - (%s || ' hours')::interval"]
            params: list = [str(hours_back)]

            if ip_addr:
                conditions.append("rl.source_ip = %s")
                params.append(ip_addr)

            if severity:
                conditions.append("rl.parse_status ILIKE %s")
                params.append(f"%{severity}%")

            if keyword:
                conditions.append("rl.raw_message ILIKE %s")
                params.append(f"%{keyword}%")

            where = " AND ".join(conditions)
            params.append(limit)

            cur.execute(
                f"""
                SELECT
                    rl.id,
                    rl.source_ip,
                    COALESCE(d.hostname, rl.hostname, rl.source_ip) AS hostname,
                    rl.raw_message,
                    rl.parse_status,
                    rl.event_time,
                    rl.received_at
                FROM raw_logs rl
                LEFT JOIN devices d ON d.ip_address = rl.source_ip
                WHERE {where}
                ORDER BY rl.received_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()

        if not rows:
            device_label = hostname or ip_addr or "all devices"
            return json.dumps({
                "count": 0,
                "device": device_label,
                "hours_back": hours_back,
                "message": f"No logs found for {device_label} in the last {hours_back} hours.",
            })

        records = [
            {
                "id": r["id"],
                "hostname": r["hostname"],
                "source_ip": r["source_ip"],
                "event_time": _fmt_dt(r["event_time"]),
                "parse_status": r["parse_status"],
                "message": r["raw_message"][:300],  # trim very long messages
            }
            for r in rows
        ]
        return json.dumps({"count": len(records), "hours_back": hours_back, "logs": records}, ensure_ascii=False)

    except Exception as exc:
        logger.exception("search_logs failed")
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Tool 2: search_incidents
# ---------------------------------------------------------------------------

@tool
def search_incidents(
    device: str = "",
    status: str = "",
    severity: str = "",
    days_back: int = 7,
    limit: int = 20,
) -> str:
    """Search the incident list from the AIOps database.

    Use this tool when the user asks about:
    - Current open / active incidents
    - Incident history for a device
    - How many incidents by severity or status
    - Recent incidents in the last N days

    Args:
        device: Device hostname or IP to filter by. Leave empty for all devices.
        status: Filter by incident status. Options: new, triaged, investigating, active,
                escalated, awaiting_approval, approved, executing, verifying, monitoring,
                recovering, resolved, resolved_uncertain, reopened. Leave empty for all.
        severity: Filter by severity: critical, warning, info. Leave empty for all.
        days_back: How many days back to search. Default 7. Max 90.
        limit: Max incidents to return. Default 20. Max 30.

    Returns:
        JSON string with matching incidents or an error message.
    """
    days_back = min(max(1, days_back), 90)
    limit = min(max(1, limit), _MAX_INCIDENT_LIMIT)

    try:
        with connect() as conn:
            cur = conn.cursor()

            ip_addr, hostname = _resolve_device(cur, device)
            if device and ip_addr is None:
                return json.dumps({"error": f"Device '{device}' not found in inventory."})

            conditions = ["i.opened_at >= NOW() - (%s || ' days')::interval"]
            params: list = [str(days_back)]

            if ip_addr:
                conditions.append("i.primary_source_ip = %s")
                params.append(ip_addr)

            if status:
                conditions.append("i.status ILIKE %s")
                params.append(f"%{status}%")

            if severity:
                conditions.append("i.severity ILIKE %s")
                params.append(f"%{severity}%")

            where = " AND ".join(conditions)
            params.append(limit)

            cur.execute(
                f"""
                SELECT
                    i.incident_no,
                    i.title,
                    i.status,
                    i.severity,
                    i.category,
                    i.summary,
                    i.probable_cause,
                    i.confidence_score,
                    i.primary_source_ip,
                    COALESCE(d.hostname, i.primary_source_ip) AS primary_hostname,
                    i.event_family,
                    i.event_count,
                    i.opened_at,
                    i.last_seen_at,
                    i.resolved_at
                FROM incidents i
                LEFT JOIN devices d ON d.ip_address = i.primary_source_ip
                WHERE {where}
                ORDER BY i.opened_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()

        if not rows:
            return json.dumps({
                "count": 0,
                "days_back": days_back,
                "message": "No incidents found matching the criteria.",
            })

        records = [
            {
                "incident_no": r["incident_no"],
                "title": r["title"],
                "status": r["status"],
                "severity": r["severity"],
                "primary_hostname": r["primary_hostname"],
                "primary_source_ip": r["primary_source_ip"],
                "event_family": r["event_family"],
                "event_count": r["event_count"],
                "summary": (r["summary"] or "")[:400],
                "probable_cause": (r["probable_cause"] or "")[:300],
                "confidence_score": r["confidence_score"],
                "opened_at": _fmt_dt(r["opened_at"]),
                "last_seen_at": _fmt_dt(r["last_seen_at"]),
                "resolved_at": _fmt_dt(r["resolved_at"]),
            }
            for r in rows
        ]
        return json.dumps({"count": len(records), "days_back": days_back, "incidents": records}, ensure_ascii=False)

    except Exception as exc:
        logger.exception("search_incidents failed")
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Tool 3: get_incident_detail
# ---------------------------------------------------------------------------

@tool
def get_incident_detail(incident_no: str) -> str:
    """Get full detail of a single incident including timeline and related events.

    Use this tool when the user asks about:
    - Root cause / RCA of a specific incident
    - Timeline of what happened in an incident
    - Remediation steps taken
    - Detailed summary of INC-XXXXXX

    Args:
        incident_no: Incident number, e.g. "INC-000042" or "INC-42".
                     Both zero-padded and short formats are accepted.

    Returns:
        JSON string with full incident detail, timeline, and related events.
    """
    # Normalise: accept "INC-42", "INC-000042", "42"
    no = incident_no.strip().upper()
    if no.isdigit():
        no = f"INC-{int(no):06d}"
    elif re.match(r"^INC-\d+$", no):
        num = int(no.split("-")[1])
        no = f"INC-{num:06d}"

    try:
        with connect() as conn:
            cur = conn.cursor()

            # Main incident row
            cur.execute(
                """
                SELECT
                    i.*,
                    COALESCE(d.hostname, i.primary_source_ip) AS primary_hostname
                FROM incidents i
                LEFT JOIN devices d ON d.ip_address = i.primary_source_ip
                WHERE i.incident_no = %s
                LIMIT 1
                """,
                (no,),
            )
            inc = cur.fetchone()
            if not inc:
                return json.dumps({"error": f"Incident '{no}' not found."})

            incident_id = inc["id"]

            # Timeline entries
            cur.execute(
                """
                SELECT kind, title, body, created_at
                FROM incident_timeline
                WHERE incident_id = %s
                ORDER BY created_at ASC
                LIMIT 50
                """,
                (incident_id,),
            )
            timeline = [
                {
                    "kind": t["kind"],
                    "title": t["title"],
                    "body": (t["body"] or "")[:500],
                    "at": _fmt_dt(t["created_at"]),
                }
                for t in cur.fetchall()
            ]

            # Related events
            cur.execute(
                """
                SELECT
                    e.event_family,
                    e.event_state,
                    e.severity,
                    e.title,
                    e.summary,
                    e.created_at,
                    COALESCE(d.hostname, e.correlation_key) AS device
                FROM events e
                JOIN incident_events ie ON ie.event_id = e.id
                LEFT JOIN devices d ON d.id = e.device_id
                WHERE ie.incident_id = %s
                ORDER BY e.created_at ASC
                LIMIT 30
                """,
                (incident_id,),
            )
            events = [
                {
                    "device": ev["device"],
                    "event_family": ev["event_family"],
                    "event_state": ev["event_state"],
                    "severity": ev["severity"],
                    "title": ev["title"],
                    "summary": (ev["summary"] or "")[:300],
                    "at": _fmt_dt(ev["created_at"]),
                }
                for ev in cur.fetchall()
            ]

        result = {
            "incident_no": inc["incident_no"],
            "title": inc["title"],
            "status": inc["status"],
            "severity": inc["severity"],
            "category": inc["category"],
            "primary_hostname": inc["primary_hostname"],
            "primary_source_ip": inc["primary_source_ip"],
            "event_family": inc["event_family"],
            "event_count": inc["event_count"],
            "summary": inc["summary"],
            "probable_cause": inc["probable_cause"],
            "confidence_score": inc["confidence_score"],
            "resolution_type": inc["resolution_type"],
            "opened_at": _fmt_dt(inc["opened_at"]),
            "last_seen_at": _fmt_dt(inc["last_seen_at"]),
            "resolved_at": _fmt_dt(inc["resolved_at"]),
            "timeline": timeline,
            "events": events,
        }
        return json.dumps(result, ensure_ascii=False)

    except Exception as exc:
        logger.exception("get_incident_detail failed")
        return json.dumps({"error": str(exc)})
