from __future__ import annotations

import csv
import logging
import os
import queue
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Json

from src.aiops.db import connect, parse_database_urls
from src.aiops.llm import decide_incident_bundle, generate_ai_summary, run_llm_troubleshoot
from src.aiops.parser import parse_syslog
from src.tools.config_executor import execute_config, run_show_commands

_INVENTORY_PATH = Path(__file__).parent.parent.parent / "inventory" / "inventory.csv"
_OPEN_INCIDENT_STATUSES = (
    "new",
    "triaged",
    "investigating",
    "active",
    "recovering",
    "monitoring",
    "awaiting_approval",
    "approved",
    "executing",
    "verifying",
    "reopened",
    "escalated",
)
_PIPELINE_STATUSES = ("pending_parse",)
_GROUP_WINDOW = timedelta(minutes=10)
# Event states the LLM may return to indicate recovery (not just "up")
_RECOVERY_EVENT_STATES = frozenset({"up", "resolved", "recovered", "established", "restored", "cleared", "restart"})
# Incident statuses that indicate the fault was previously considered over — a new DOWN event here is a re-fault
_POST_RECOVERY_STATUSES = frozenset({"recovering", "monitoring", "resolved", "resolved_uncertain"})
_GROUP_DECISION_DEBOUNCE = timedelta(seconds=max(1, int(os.getenv("AIOPS_GROUP_DECISION_DEBOUNCE_SECONDS", "5"))))
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
# Max parallel troubleshoot threads (each uses one LLM slot — keep low to avoid GPU OOM)
_TROUBLESHOOT_MAX_WORKERS = max(1, int(os.getenv("AIOPS_TROUBLESHOOT_MAX_WORKERS", "2")))
# Delay before auto-troubleshoot per severity (critical=0s, warning=10s, info=30s)
_TROUBLESHOOT_DELAY: dict[str, int] = {
    "critical": int(os.getenv("AIOPS_TROUBLESHOOT_DELAY_CRITICAL", "0")),
    "warning":  int(os.getenv("AIOPS_TROUBLESHOOT_DELAY_WARNING",  "10")),
    "info":     int(os.getenv("AIOPS_TROUBLESHOOT_DELAY_INFO",     "30")),
}
logger = logging.getLogger(__name__)


def _is_test_runtime() -> bool:
    return "pytest" in sys.modules or bool(os.getenv("PYTEST_CURRENT_TEST"))


class AIOpsService:
    def __init__(self) -> None:
        self._bootstrapped = False
        self._pipeline_lock = threading.Lock()
        # Priority queue for troubleshoot jobs: (priority, incident_no)
        # Lower number = higher priority  (critical=0, warning=1, info=2)
        self._troubleshoot_queue: queue.PriorityQueue[tuple[int, str]] = queue.PriorityQueue()
        self._troubleshoot_workers: list[threading.Thread] = []
        self._troubleshoot_running: set[str] = set()   # incident_nos currently being worked
        self._troubleshoot_lock = threading.Lock()
        self._start_troubleshoot_pool()

    def bootstrap(self) -> None:
        if self._bootstrapped:
            return
        urls = parse_database_urls()
        with connect(urls.admin_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (urls.database_name,))
                exists = cur.fetchone()
                if not exists:
                    cur.execute(f'CREATE DATABASE "{urls.database_name}"')
        self._init_schema()
        self.sync_inventory()
        self._recover_stuck_groups()
        self._bootstrapped = True

    def _init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS devices (
            id BIGSERIAL PRIMARY KEY,
            hostname TEXT NOT NULL UNIQUE,
            ip_address TEXT NOT NULL UNIQUE,
            os_platform TEXT NOT NULL,
            device_role TEXT NOT NULL,
            site TEXT NOT NULL,
            version TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS raw_logs (
            id BIGSERIAL PRIMARY KEY,
            source_ip TEXT NOT NULL,
            hostname TEXT,
            raw_message TEXT NOT NULL,
            event_time TIMESTAMPTZ NOT NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            parse_status TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        );

        CREATE TABLE IF NOT EXISTS events (
            id BIGSERIAL PRIMARY KEY,
            raw_log_id BIGINT NOT NULL REFERENCES raw_logs(id) ON DELETE CASCADE,
            device_id BIGINT REFERENCES devices(id) ON DELETE SET NULL,
            event_family TEXT NOT NULL,
            event_state TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            correlation_key TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            parser_name TEXT NOT NULL DEFAULT 'heuristic_v1',
            parser_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.6,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS incidents (
            id BIGSERIAL PRIMARY KEY,
            incident_no TEXT UNIQUE,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            severity TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'unknown',
            summary TEXT NOT NULL DEFAULT '',
            probable_cause TEXT NOT NULL DEFAULT '',
            confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            site TEXT NOT NULL DEFAULT '',
            primary_device_id BIGINT REFERENCES devices(id) ON DELETE SET NULL,
            primary_source_ip TEXT NOT NULL,
            correlation_key TEXT NOT NULL,
            event_family TEXT NOT NULL,
            event_count INTEGER NOT NULL DEFAULT 0,
            current_recovery_state TEXT NOT NULL DEFAULT 'none',
            resolution_type TEXT,
            opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ,
            reopened_count INTEGER NOT NULL DEFAULT 0,
            latest_ai_summary_id BIGINT,
            current_proposal_id BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS incident_events (
            id BIGSERIAL PRIMARY KEY,
            incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (incident_id, event_id)
        );

        CREATE TABLE IF NOT EXISTS incident_timeline (
            id BIGSERIAL PRIMARY KEY,
            incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS ai_summaries (
            id BIGSERIAL PRIMARY KEY,
            incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            trigger TEXT NOT NULL,
            summary TEXT NOT NULL,
            probable_cause TEXT NOT NULL,
            confidence_score DOUBLE PRECISION NOT NULL,
            category TEXT NOT NULL,
            impact TEXT NOT NULL,
            suggested_checks JSONB NOT NULL DEFAULT '[]'::jsonb,
            raw_response TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS troubleshoot_runs (
            id BIGSERIAL PRIMARY KEY,
            incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            disposition TEXT NOT NULL,
            summary TEXT NOT NULL,
            conclusion TEXT NOT NULL,
            steps JSONB NOT NULL DEFAULT '[]'::jsonb,
            raw_response TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS proposals (
            id BIGSERIAL PRIMARY KEY,
            incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            rationale TEXT NOT NULL,
            target_devices JSONB NOT NULL DEFAULT '[]'::jsonb,
            commands JSONB NOT NULL DEFAULT '[]'::jsonb,
            rollback_plan TEXT NOT NULL DEFAULT '',
            expected_impact TEXT NOT NULL DEFAULT '',
            verification_commands JSONB NOT NULL DEFAULT '[]'::jsonb,
            risk_level TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            approved_at TIMESTAMPTZ,
            approved_by TEXT
        );

        CREATE TABLE IF NOT EXISTS executions (
            id BIGSERIAL PRIMARY KEY,
            incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            proposal_id BIGINT NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            executed_by TEXT NOT NULL,
            output TEXT NOT NULL DEFAULT '',
            verification_status TEXT NOT NULL DEFAULT 'pending',
            verification_notes TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS ingest_jobs (
            id BIGSERIAL PRIMARY KEY,
            raw_log_id BIGINT NOT NULL UNIQUE REFERENCES raw_logs(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            event_id BIGINT,
            candidate_group_id BIGINT,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            locked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS candidate_groups (
            id BIGSERIAL PRIMARY KEY,
            source_ip TEXT NOT NULL,
            hostname TEXT,
            device_id BIGINT REFERENCES devices(id) ON DELETE SET NULL,
            event_family TEXT NOT NULL,
            correlation_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            severity_rollup TEXT NOT NULL DEFAULT 'warning',
            latest_event_state TEXT NOT NULL DEFAULT 'info',
            recovery_seen BOOLEAN NOT NULL DEFAULT FALSE,
            event_count INTEGER NOT NULL DEFAULT 0,
            first_event_at TIMESTAMPTZ NOT NULL,
            last_event_at TIMESTAMPTZ NOT NULL,
            title_hint TEXT NOT NULL DEFAULT '',
            linked_incident_id BIGINT REFERENCES incidents(id) ON DELETE SET NULL,
            decision_status TEXT NOT NULL DEFAULT 'idle',
            decision_requested_at TIMESTAMPTZ,
            decision_attempts INTEGER NOT NULL DEFAULT 0,
            decision_locked_at TIMESTAMPTZ,
            last_decision_event_count INTEGER NOT NULL DEFAULT 0,
            last_decision_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS candidate_group_events (
            id BIGSERIAL PRIMARY KEY,
            candidate_group_id BIGINT NOT NULL REFERENCES candidate_groups(id) ON DELETE CASCADE,
            event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (candidate_group_id, event_id)
        );

        CREATE TABLE IF NOT EXISTS llm_incident_decisions (
            id BIGSERIAL PRIMARY KEY,
            candidate_group_id BIGINT NOT NULL REFERENCES candidate_groups(id) ON DELETE CASCADE,
            incident_id BIGINT REFERENCES incidents(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            incident_no TEXT,
            title TEXT NOT NULL,
            event_family TEXT NOT NULL,
            event_state TEXT NOT NULL,
            severity TEXT NOT NULL,
            summary TEXT NOT NULL,
            correlation_key TEXT NOT NULL,
            category TEXT NOT NULL,
            reasoning TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            raw_response TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS incident_events_incident_id_event_id_idx ON incident_events (incident_id, event_id)")
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS candidate_group_events_group_id_event_id_idx ON candidate_group_events (candidate_group_id, event_id)")
                # Column migrations — check existence first to avoid AccessExclusiveLock
                # deadlocks when the server is already running with active transactions.
                _col_migrations: list[tuple[str, str, str]] = [
                    ("raw_logs",         "parse_status",                "TEXT NOT NULL DEFAULT 'ingested'"),
                    ("events",           "parser_name",                 "TEXT NOT NULL DEFAULT 'heuristic_v1'"),
                    ("events",           "parser_confidence",           "DOUBLE PRECISION NOT NULL DEFAULT 0.6"),
                    ("candidate_groups", "decision_status",             "TEXT NOT NULL DEFAULT 'idle'"),
                    ("candidate_groups", "decision_requested_at",       "TIMESTAMPTZ"),
                    ("candidate_groups", "decision_attempts",           "INTEGER NOT NULL DEFAULT 0"),
                    ("candidate_groups", "decision_locked_at",          "TIMESTAMPTZ"),
                    ("candidate_groups", "last_decision_event_count",   "INTEGER NOT NULL DEFAULT 0"),
                    ("proposals",        "rollback_commands",           "JSONB NOT NULL DEFAULT '[]'::jsonb"),
                ]
                cur.execute(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_name = ANY(%s)
                    """,
                    ([t for t, _, _ in _col_migrations],),
                )
                existing_cols: set[tuple[str, str]] = {(r["table_name"], r["column_name"]) for r in cur.fetchall()}
                for table, col, col_def in _col_migrations:
                    if (table, col) not in existing_cols:
                        cur.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_def}')
            conn.commit()

    def sync_inventory(self) -> None:
        if not _INVENTORY_PATH.exists():
            return
        with _INVENTORY_PATH.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        with connect() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        """
                        INSERT INTO devices (hostname, ip_address, os_platform, device_role, site, version)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (hostname) DO UPDATE
                        SET ip_address = EXCLUDED.ip_address,
                            os_platform = EXCLUDED.os_platform,
                            device_role = EXCLUDED.device_role,
                            site = EXCLUDED.site,
                            version = EXCLUDED.version,
                            updated_at = NOW()
                        """,
                        (
                            row["hostname"],
                            row["ip_address"],
                            row["os_platform"],
                            row["device_role"],
                            row["site"],
                            row["version"],
                        ),
                    )
            conn.commit()

    def _recover_stuck_groups(self) -> None:
        """Reset candidate groups stuck in 'running' for more than 5 minutes (crashed worker)."""
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE candidate_groups
                    SET decision_status = 'pending',
                        decision_locked_at = NULL,
                        decision_requested_at = NOW(),
                        updated_at = NOW()
                    WHERE decision_status = 'running'
                      AND decision_locked_at < NOW() - INTERVAL '5 minutes'
                    """
                )
                recovered = cur.rowcount
            conn.commit()
        if recovered:
            logger.info("Recovered %d stuck candidate_group(s) after lock timeout", recovered)

    def _fetch_device_cache(self) -> dict[str, dict[str, Any]]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, hostname, ip_address, os_platform, device_role, site, version
                    FROM devices
                    ORDER BY hostname
                    """
                )
                rows = cur.fetchall()
        return {row["hostname"]: row for row in rows}

    def _find_device(
        self,
        cur: psycopg.Cursor[Any],
        *,
        source_ip: str,
        hostname: str | None,
    ) -> dict[str, Any] | None:
        cur.execute(
            """
            SELECT id, hostname, ip_address, os_platform, device_role, site, version
            FROM devices
            WHERE ip_address = %(ip)s OR (%(hn)s::text IS NOT NULL AND hostname = %(hn)s)
            ORDER BY CASE WHEN ip_address = %(ip)s THEN 0 ELSE 1 END
            LIMIT 1
            """,
            {"ip": source_ip, "hn": hostname},
        )
        return cur.fetchone()

    def _next_incident_no(self, cur: psycopg.Cursor[Any], incident_id: int) -> str:
        incident_no = f"INC-{incident_id:06d}"
        cur.execute(
            "UPDATE incidents SET incident_no = %s, updated_at = NOW() WHERE id = %s",
            (incident_no, incident_id),
        )
        return incident_no

    def _record_timeline(
        self,
        cur: psycopg.Cursor[Any],
        incident_id: int,
        kind: str,
        title: str,
        body: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        cur.execute(
            """
            INSERT INTO incident_timeline (incident_id, kind, title, body, payload)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (incident_id, kind, title, body, Json(payload or {})),
        )

    def enqueue_syslog(
        self,
        source_ip: str,
        raw_message: str,
        hostname: str | None = None,
        event_time: datetime | None = None,
    ) -> dict[str, Any]:
        event_time = event_time or datetime.now(timezone.utc)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO raw_logs (source_ip, hostname, raw_message, event_time, parse_status, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        source_ip,
                        hostname,
                        raw_message,
                        event_time,
                        "queued",
                        Json({"source": "syslog"}),
                    ),
                )
                raw_log_id = cur.fetchone()["id"]
                cur.execute(
                    """
                    INSERT INTO ingest_jobs (raw_log_id, status)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (raw_log_id, "pending_parse"),
                )
                job_id = cur.fetchone()["id"]
            conn.commit()
        return {
            "accepted": True,
            "raw_log_id": raw_log_id,
            "job_id": job_id,
            "status": "queued",
        }

    def process_pending_jobs(self, limit: int = 20, max_parse: int = 5, max_decisions: int = 1) -> dict[str, int]:
        if not self._pipeline_lock.acquire(blocking=False):
            return {"processed": 0, "parsed": 0, "decided": 0}
        processed = 0
        parse_count = 0
        decision_count = 0
        try:
            # Auto-resolve incidents that have been stable (no fault) past the window
            self._auto_resolve_stable_incidents()
            # Mark exhausted groups as idle so they stop consuming LLM cycles
            self._retire_exhausted_groups()
            while processed < limit and parse_count < max_parse:
                if not self._process_parse_job():
                    break
                processed += 1
                parse_count += 1
            while processed < limit and decision_count < max_decisions:
                if not self._process_decision_job():
                    break
                processed += 1
                decision_count += 1
            return {"processed": processed, "parsed": parse_count, "decided": decision_count}
        finally:
            self._pipeline_lock.release()

    def _auto_resolve_stable_incidents(self) -> int:
        """Auto-resolve incidents that have been in recovering/monitoring status
        for the stability window with no new fault events."""
        stability_secs = int(os.getenv("AIOPS_RECOVERY_STABILITY_SECONDS", "300"))  # 5 min default
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE incidents
                    SET status = 'resolved',
                        resolution_type = 'auto_recovered',
                        resolved_at = NOW(),
                        current_recovery_state = 'recovered',
                        updated_at = NOW()
                    WHERE status IN ('recovering', 'monitoring')
                      AND last_seen_at < NOW() - (interval '1 second' * %s)
                    RETURNING id, incident_no
                    """,
                    (stability_secs,),
                )
                resolved = cur.fetchall()
                for row in resolved:
                    self._record_timeline(
                        cur,
                        row["id"],
                        "recovery",
                        "Auto-resolved after stability window",
                        f"No new fault events for {stability_secs // 60} minutes. Incident auto-closed.",
                        {"stability_seconds": stability_secs},
                    )
            conn.commit()
        if resolved:
            logger.info("Auto-resolved %d incident(s) after stability window", len(resolved))
        return len(resolved)

    def _retire_exhausted_groups(self) -> None:
        """Move candidate_groups that exceeded max attempts to idle so they stop looping."""
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE candidate_groups
                    SET decision_status = 'idle',
                        decision_locked_at = NULL,
                        updated_at = NOW()
                    WHERE decision_status IN ('pending', 'running')
                      AND decision_attempts >= 5
                    """
                )
                retired = cur.rowcount
            conn.commit()
        if retired:
            logger.warning("Retired %d exhausted candidate_group(s) (>= 5 attempts)", retired)

    def _claim_job(self, status: str) -> dict[str, Any] | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM ingest_jobs
                    WHERE status = %s
                      AND available_at <= NOW()
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (status,),
                )
                job = cur.fetchone()
                if job is None:
                    conn.rollback()
                    return None
                cur.execute(
                    """
                    UPDATE ingest_jobs
                    SET attempts = attempts + 1,
                        locked_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (job["id"],),
                )
                claimed = cur.fetchone()
            conn.commit()
        return claimed

    def _complete_job(
        self,
        job_id: int,
        *,
        status: str,
        event_id: int | None = None,
        candidate_group_id: int | None = None,
        last_error: str = "",
        retry_delay: timedelta | None = None,
    ) -> None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ingest_jobs
                    SET status = %s,
                        event_id = COALESCE(%s, event_id),
                        candidate_group_id = COALESCE(%s, candidate_group_id),
                        last_error = %s,
                        available_at = %s,
                        locked_at = NULL,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        status,
                        event_id,
                        candidate_group_id,
                        last_error,
                        datetime.now(timezone.utc) + retry_delay if retry_delay else datetime.now(timezone.utc),
                        job_id,
                    ),
                )
            conn.commit()

    def _process_parse_job(self) -> bool:
        job = self._claim_job("pending_parse")
        if job is None:
            return False
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM raw_logs WHERE id = %s", (job["raw_log_id"],))
                    raw_log = cur.fetchone()
                    if raw_log is None:
                        raise KeyError(f"Raw log {job['raw_log_id']} not found")
                    device = self._find_device(cur, source_ip=raw_log["source_ip"], hostname=raw_log["hostname"])
                    if device is None:
                        # Source IP not in inventory — discard silently.
                        # Unknown IPs may be test packets, misconfigured devices, or spoofed sources.
                        # Do not create events or incidents; store as unknown_source for audit.
                        logger.debug(
                            "Discarding log from unknown source IP=%s hostname=%s — not in inventory",
                            raw_log["source_ip"], raw_log["hostname"],
                        )
                        cur.execute(
                            "UPDATE raw_logs SET parse_status = 'unknown_source' WHERE id = %s",
                            (raw_log["id"],),
                        )
                        conn.commit()
                        self._complete_job(job["id"], status="completed")
                        return True
                    parsed = parse_syslog(
                        source_ip=raw_log["source_ip"],
                        hostname=raw_log["hostname"],
                        raw_message=raw_log["raw_message"],
                        event_time=raw_log["event_time"],
                    )
                    if parsed is None:
                        # Noise / boot banner / admin-down — parser already decided to discard
                        cur.execute(
                            "UPDATE raw_logs SET parse_status = 'noise' WHERE id = %s",
                            (raw_log["id"],),
                        )
                        conn.commit()
                        self._complete_job(job["id"], status="completed")
                        return True
                    cur.execute(
                        """
                        INSERT INTO events (
                            raw_log_id, device_id, event_family, event_state, severity,
                            title, summary, correlation_key, metadata, parser_name, parser_confidence
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            raw_log["id"],
                            device["id"] if device else None,
                            parsed["event_family"],
                            parsed["event_state"],
                            parsed["severity"],
                            parsed["title"],
                            parsed["summary"],
                            parsed["correlation_key"],
                            Json(parsed["metadata"]),
                            "heuristic_v1",
                            0.72,
                        ),
                    )
                    event = cur.fetchone()
                    group = self._upsert_candidate_group(
                        cur,
                        raw_log=raw_log,
                        event=event,
                        device=device,
                    )
                    cur.execute(
                        """
                        INSERT INTO candidate_group_events (candidate_group_id, event_id)
                        VALUES (%s, %s)
                        ON CONFLICT (candidate_group_id, event_id) DO NOTHING
                        """,
                        (group["id"], event["id"]),
                    )
                    cur.execute(
                        """
                        UPDATE raw_logs
                        SET parse_status = %s,
                            metadata = metadata || %s::jsonb
                        WHERE id = %s
                        """,
                        (
                            "parsed",
                            Json({
                                "event_family": event["event_family"],
                                "event_state": event["event_state"],
                                "correlation_key": event["correlation_key"],
                            }),
                            raw_log["id"],
                        ),
                    )
                    # Tracking/IP SLA "down" events: merge into a related EIGRP/tunnel/interface
                    # incident if one occurred recently on the same device, to avoid a
                    # duplicate incident for what is effectively the same path failure.
                    # "up" events always use the normal recovery path so the tracking incident
                    # itself gets its own recovery signal (not just the EIGRP/tunnel incident).
                    if event["event_family"] == "tracking" and event["event_state"] != "up":
                        merged = self._try_merge_tracking_into_related_incident(
                            cur, event=event, raw_log=raw_log, group=group
                        )
                        if not merged:
                            self._mark_candidate_group_pending_decision(cur, group_id=group["id"])
                    # Short-circuit: recovery signals skip LLM and update incident directly.
                    # Covers all healed states: up, established, recovered, restored, cleared.
                    # Config events are never recovery signals. System events (restart) go via LLM.
                    elif event["metadata"].get("recovery_signal") and event["event_family"] not in ("system", "config"):
                        self._apply_recovery_signal(cur, event=event, raw_log=raw_log, group=group)
                    else:
                        self._mark_candidate_group_pending_decision(cur, group_id=group["id"])
                conn.commit()
            self._complete_job(
                job["id"],
                status="completed",
                event_id=event["id"],
                candidate_group_id=group["id"],
            )
            return True
        except Exception as exc:
            self._complete_job(job["id"], status="pending_parse", last_error=str(exc), retry_delay=timedelta(seconds=5))
            return False

    def _apply_recovery_signal(
        self,
        cur: psycopg.Cursor[Any],
        *,
        event: dict[str, Any],
        raw_log: dict[str, Any],
        group: dict[str, Any],
    ) -> None:
        """Directly update a matching open incident on recovery — no LLM needed."""
        cur.execute(
            """
            SELECT id, incident_no, status
            FROM incidents
            WHERE correlation_key = %s
              AND status = ANY(%s)
            ORDER BY last_seen_at DESC
            LIMIT 1
            """,
            (event["correlation_key"], list(_OPEN_INCIDENT_STATUSES)),
        )
        incident = cur.fetchone()
        if incident is None:
            # No matching open incident → let LLM decide
            self._mark_candidate_group_pending_decision(cur, group_id=group["id"])
            return
        # Update incident to recovering and bump event count
        cur.execute(
            """
            UPDATE incidents
            SET status = 'recovering',
                current_recovery_state = 'signal_detected',
                event_count = event_count + 1,
                last_seen_at = NOW(),
                updated_at = NOW()
            WHERE id = %s
            """,
            (incident["id"],),
        )
        self._record_timeline(
            cur,
            incident["id"],
            "recovery",
            "Recovery signal detected",
            f"Received {event['event_state']} event: {raw_log['raw_message'][:120]}",
            {"event_id": event["id"], "event_state": event["event_state"]},
        )
        # Link raw log to incident
        cur.execute(
            "UPDATE raw_logs SET parse_status = 'llm_decided' WHERE id = %s",
            (raw_log["id"],),
        )
        # Mark group idle — no LLM needed
        cur.execute(
            "UPDATE candidate_groups SET decision_status = 'idle', updated_at = NOW() WHERE id = %s",
            (group["id"],),
        )
        logger.info("Recovery signal applied to %s → recovering", incident["incident_no"])

    def _try_merge_tracking_into_related_incident(
        self,
        cur: psycopg.Cursor[Any],
        *,
        event: dict[str, Any],
        raw_log: dict[str, Any],
        group: dict[str, Any],
    ) -> bool:
        """Merge a tracking/IP SLA "down" event into a recent EIGRP/tunnel/interface
        incident from the same device instead of creating a separate incident.

        Only called for non-recovery events. "up" events always use the normal
        _apply_recovery_signal path so the tracking incident recovers on its own.

        Returns True if merged (caller skips normal LLM pipeline).
        """
        window_seconds = int(os.getenv("AIOPS_TRACKING_MERGE_WINDOW_SECONDS", "60"))
        cur.execute(
            """
            SELECT id, incident_no, status, event_family
            FROM incidents
            WHERE primary_source_ip = %s
              AND event_family = ANY(%s)
              AND status = ANY(%s)
              AND last_seen_at >= NOW() - make_interval(secs => %s)
            ORDER BY last_seen_at DESC
            LIMIT 1
            """,
            (
                raw_log["source_ip"],
                ["eigrp", "tunnel", "interface"],
                list(_OPEN_INCIDENT_STATUSES),
                window_seconds,
            ),
        )
        incident = cur.fetchone()
        if incident is None:
            return False

        detail = raw_log["raw_message"][:140]
        cur.execute(
            """
            UPDATE incidents
            SET event_count = event_count + 1,
                last_seen_at = NOW(),
                updated_at = NOW()
            WHERE id = %s
            """,
            (incident["id"],),
        )
        self._record_timeline(
            cur,
            incident["id"],
            "event",
            "IP SLA / Track correlated",
            f"IP SLA/track event merged (same device, within {window_seconds}s window): {detail}",
            {"event_id": event["id"], "event_state": event["event_state"], "cross_family": True},
        )
        cur.execute(
            "UPDATE raw_logs SET parse_status = 'llm_decided' WHERE id = %s",
            (raw_log["id"],),
        )
        cur.execute(
            "UPDATE candidate_groups SET decision_status = 'idle', updated_at = NOW() WHERE id = %s",
            (group["id"],),
        )
        logger.info(
            "Tracking event merged into %s (%s) via cross-family correlation",
            incident["incident_no"],
            incident["event_family"],
        )
        return True

    def _find_peer_device_incident(
        self,
        cur: psycopg.Cursor[Any],
        *,
        peer_ip: str,
        event_family: str,
    ) -> dict[str, Any] | None:
        """Find an open EIGRP/tunnel incident from the peer device (by tunnel IP).

        When EIGRP/tunnel goes down on both sides of the link, both devices create
        incidents. This method detects that the peer already has an open incident
        so we can merge instead of creating a duplicate.
        """
        from src.tools.inventory_tools import _load_rows
        try:
            rows = _load_rows()
        except Exception:
            return None

        peer_mgmt_ip: str | None = None
        for row in rows:
            if row["ip_address"] == peer_ip:
                peer_mgmt_ip = row["ip_address"]
                break
            tunnel_ips_raw = row.get("tunnel_ips", "") or ""
            tunnel_ips = [ip.strip() for ip in tunnel_ips_raw.split() if ip.strip()]
            if peer_ip in tunnel_ips:
                peer_mgmt_ip = row["ip_address"]
                break

        if not peer_mgmt_ip:
            return None

        cur.execute(
            """
            SELECT id, incident_no, status, event_family, correlation_key
            FROM incidents
            WHERE primary_source_ip = %s
              AND event_family = ANY(%s)
              AND status = ANY(%s)
            ORDER BY opened_at DESC
            LIMIT 1
            """,
            (
                peer_mgmt_ip,
                [event_family, "eigrp", "tunnel", "interface"],
                list(_OPEN_INCIDENT_STATUSES),
            ),
        )
        return cur.fetchone()

    def _upsert_candidate_group(
        self,
        cur: psycopg.Cursor[Any],
        *,
        raw_log: dict[str, Any],
        event: dict[str, Any],
        device: dict[str, Any] | None,
    ) -> dict[str, Any]:
        cur.execute(
            """
            SELECT *
            FROM candidate_groups
            WHERE correlation_key = %s
              AND status = 'open'
              AND last_event_at >= %s
            ORDER BY last_event_at DESC
            LIMIT 1
            """,
            (event["correlation_key"], raw_log["event_time"] - _GROUP_WINDOW),
        )
        group = cur.fetchone()
        severity = self._rollup_severity(
            group["severity_rollup"] if group else None,
            event["severity"],
        )
        recovery_seen = bool(group["recovery_seen"]) if group else False
        recovery_seen = recovery_seen or event["event_state"] == "up"
        if group:
            cur.execute(
                """
                UPDATE candidate_groups
                SET hostname = COALESCE(%s, hostname),
                    device_id = COALESCE(%s, device_id),
                    severity_rollup = %s,
                    latest_event_state = %s,
                    recovery_seen = %s,
                    event_count = event_count + 1,
                    title_hint = %s,
                    last_event_at = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    raw_log["hostname"],
                    device["id"] if device else None,
                    severity,
                    event["event_state"],
                    recovery_seen,
                    event["title"],
                    raw_log["event_time"],
                    group["id"],
                ),
            )
            return cur.fetchone()
        cur.execute(
            """
            INSERT INTO candidate_groups (
                source_ip, hostname, device_id, event_family, correlation_key, severity_rollup,
                latest_event_state, recovery_seen, event_count, first_event_at, last_event_at, title_hint
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s)
            RETURNING *
            """,
            (
                raw_log["source_ip"],
                raw_log["hostname"],
                device["id"] if device else None,
                event["event_family"],
                event["correlation_key"],
                severity,
                event["event_state"],
                recovery_seen,
                raw_log["event_time"],
                raw_log["event_time"],
                event["title"],
            ),
        )
        return cur.fetchone()

    def _rollup_severity(self, current: str | None, new: str) -> str:
        if current is None:
            return new
        return current if _SEVERITY_RANK.get(current, 0) >= _SEVERITY_RANK.get(new, 0) else new

    def _mark_candidate_group_pending_decision(self, cur: psycopg.Cursor[Any], *, group_id: int) -> None:
        skip_debounce = _is_test_runtime() or os.getenv("AIOPS_INLINE_PIPELINE", "").strip() == "1"
        if skip_debounce:
            # Use DB NOW() to avoid Python/DB clock skew within the same transaction
            cur.execute(
                """
                UPDATE candidate_groups
                SET decision_status = CASE
                        WHEN decision_status = 'idle' THEN 'pending'
                        ELSE decision_status
                    END,
                    decision_requested_at = CASE
                        WHEN decision_status = 'idle' OR decision_requested_at IS NULL THEN NOW()
                        ELSE decision_requested_at
                    END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (group_id,),
            )
        else:
            requested_at = datetime.now(timezone.utc) + _GROUP_DECISION_DEBOUNCE
            cur.execute(
                """
                UPDATE candidate_groups
                SET decision_status = CASE
                        WHEN decision_status = 'idle' THEN 'pending'
                        ELSE decision_status
                    END,
                    decision_requested_at = CASE
                        WHEN decision_status = 'idle' OR decision_requested_at IS NULL THEN %s
                        ELSE decision_requested_at
                    END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (requested_at, group_id),
            )

    def _claim_candidate_group_for_decision(self) -> dict[str, Any] | None:
        with connect() as conn:
            with conn.cursor() as cur:
                # First: recover any stuck 'running' groups (locked > 5 min ago)
                cur.execute(
                    """
                    UPDATE candidate_groups
                    SET decision_status = 'pending',
                        decision_locked_at = NULL,
                        decision_requested_at = NOW(),
                        updated_at = NOW()
                    WHERE decision_status = 'running'
                      AND decision_locked_at < NOW() - INTERVAL '5 minutes'
                    """
                )
                if cur.rowcount > 0:
                    conn.commit()

                cur.execute(
                    """
                    SELECT *
                    FROM candidate_groups
                    WHERE decision_status = 'pending'
                      AND decision_attempts < 5
                      AND COALESCE(decision_requested_at, NOW()) <= NOW()
                    ORDER BY
                        CASE severity_rollup
                            WHEN 'critical' THEN 0
                            WHEN 'warning' THEN 1
                            ELSE 2
                        END,
                        decision_requested_at NULLS FIRST,
                        id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                )
                group = cur.fetchone()
                if group is None:
                    conn.rollback()
                    return None
                cur.execute(
                    """
                    UPDATE candidate_groups
                    SET decision_status = 'running',
                        decision_attempts = decision_attempts + 1,
                        decision_locked_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (group["id"],),
                )
                claimed = cur.fetchone()
            conn.commit()
        return claimed

    def _process_decision_job(self) -> bool:
        claimed_group = self._claim_candidate_group_for_decision()
        if claimed_group is None:
            return False
        try:
            incident_id: int | None = None
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM candidate_groups WHERE id = %s", (claimed_group["id"],))
                    group = cur.fetchone()
                    if group is None:
                        raise KeyError(f"Candidate group {claimed_group['id']} not found")
                    cur.execute(
                        """
                        SELECT e.*
                        FROM events e
                        JOIN candidate_group_events cge ON cge.event_id = e.id
                        WHERE cge.candidate_group_id = %s
                        ORDER BY e.created_at DESC
                        LIMIT 12
                        """,
                        (group["id"],),
                    )
                    events = cur.fetchall()
                    cur.execute(
                        """
                        SELECT rl.*
                        FROM raw_logs rl
                        JOIN events e ON e.raw_log_id = rl.id
                        JOIN candidate_group_events cge ON cge.event_id = e.id
                        WHERE cge.candidate_group_id = %s
                        ORDER BY rl.received_at DESC
                        LIMIT 12
                        """,
                        (group["id"],),
                    )
                    raw_logs = cur.fetchall()
                    device = self._find_device(cur, source_ip=group["source_ip"], hostname=group["hostname"])
                    cur.execute(
                        """
                        SELECT i.id, i.incident_no, i.title, i.status, i.event_family, i.correlation_key, i.primary_source_ip,
                               d.hostname AS primary_hostname, d.device_role, d.site, d.os_platform, d.version
                        FROM incidents i
                        LEFT JOIN devices d ON d.id = i.primary_device_id
                        WHERE i.status = ANY(%s)
                        ORDER BY i.last_seen_at DESC
                        LIMIT 20
                        """,
                        (list(_OPEN_INCIDENT_STATUSES),),
                    )
                    open_incidents = cur.fetchall()
                    decision = decide_incident_bundle(
                        candidate_group=group,
                        events=events,
                        raw_logs=raw_logs,
                        device=device,
                        open_incidents=open_incidents,
                    )
                    incident_id = self._apply_bundle_decision(
                        cur,
                        group=group,
                        events=events,
                        raw_logs=raw_logs,
                        device=device,
                        decision=decision,
                        claimed_event_count=claimed_group["event_count"],
                    )
                conn.commit()
            if incident_id is not None:
                try:
                    self.refresh_incident_summary(incident_id)
                except Exception as summary_exc:
                    logger.warning("AIOps summary refresh failed for incident %s: %s", incident_id, summary_exc)
                self._schedule_auto_troubleshoot(incident_id)
            return True
        except Exception as exc:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE candidate_groups
                        SET decision_status = 'pending',
                            decision_requested_at = %s,
                            decision_locked_at = NULL,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (datetime.now(timezone.utc) + timedelta(seconds=10), claimed_group["id"]),
                    )
                conn.commit()
            return False

    def _start_troubleshoot_pool(self) -> None:
        """Start fixed pool of worker threads that drain the priority queue."""
        for i in range(_TROUBLESHOOT_MAX_WORKERS):
            t = threading.Thread(
                target=self._troubleshoot_worker_loop,
                daemon=True,
                name=f"troubleshoot-worker-{i}",
            )
            t.start()
            self._troubleshoot_workers.append(t)

    def _troubleshoot_worker_loop(self) -> None:
        """Long-running worker: dequeue incident_no and troubleshoot it."""
        while True:
            try:
                priority, incident_no = self._troubleshoot_queue.get(timeout=5)
            except queue.Empty:
                continue
            try:
                logger.info("Troubleshoot worker starting %s (priority=%d)", incident_no, priority)
                self.run_troubleshoot(incident_no)
                logger.info("Troubleshoot worker completed %s", incident_no)
            except Exception as exc:
                logger.warning("Troubleshoot worker failed for %s: %s", incident_no, exc)
            finally:
                with self._troubleshoot_lock:
                    self._troubleshoot_running.discard(incident_no)
                self._troubleshoot_queue.task_done()

    def _schedule_auto_troubleshoot(self, incident_id: int) -> None:
        """Enqueue incident for troubleshoot if not already queued/running."""
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT incident_no, status, severity FROM incidents WHERE id = %s",
                    (incident_id,),
                )
                row = cur.fetchone()
        if row is None or row["incident_no"] is None:
            return
        if row["status"] not in ("new", "active", "investigating"):
            return
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM troubleshoot_runs WHERE incident_id = %s LIMIT 1",
                    (incident_id,),
                )
                existing = cur.fetchone()
        if existing:
            return
        incident_no = row["incident_no"]
        severity = row.get("severity", "warning") or "warning"
        priority = 2 - _SEVERITY_RANK.get(severity, 1)  # critical=0, warning=1, info=2
        with self._troubleshoot_lock:
            if incident_no in self._troubleshoot_running:
                return
            self._troubleshoot_running.add(incident_no)
        # Delay non-critical to avoid hammering LLM during burst
        delay = _TROUBLESHOOT_DELAY.get(severity, 10)
        if delay > 0:
            def _delayed_enqueue(prio: int, inc_no: str, wait: int) -> None:
                threading.Event().wait(wait)
                self._troubleshoot_queue.put((prio, inc_no))
            t = threading.Thread(target=_delayed_enqueue, args=(priority, incident_no, delay), daemon=True)
            t.start()
        else:
            self._troubleshoot_queue.put((priority, incident_no))

    def _apply_bundle_decision(
        self,
        cur: psycopg.Cursor[Any],
        *,
        group: dict[str, Any],
        events: list[dict[str, Any]],
        raw_logs: list[dict[str, Any]],
        device: dict[str, Any] | None,
        decision: dict[str, Any],
        claimed_event_count: int,
    ) -> int | None:
        latest_event = events[0] if events else None
        cur.execute(
            """
            SELECT id, incident_no, status
            FROM incidents
            WHERE incident_no = %s
            LIMIT 1
            """,
            (decision.get("incident_no"),),
        )
        incident = cur.fetchone() if decision.get("incident_no") else None
        if incident is None and decision["action"] == "update_incident":
            cur.execute(
                """
                SELECT id, incident_no, status
                FROM incidents
                WHERE correlation_key = %s
                  AND status = ANY(%s)
                ORDER BY opened_at DESC
                LIMIT 1
                """,
                (decision["correlation_key"], list(_OPEN_INCIDENT_STATUSES)),
            )
            incident = cur.fetchone()

        incident_id: int | None = None
        if decision["action"] != "ignore":
            # Cross-device dedup: if this is a new EIGRP/tunnel DOWN event and the
            # peer device already has an open incident for the same failure, merge
            # into the peer's incident rather than creating a duplicate.
            peer_ip = (decision.get("metadata") or {}).get("peer_ip")
            if (
                decision["action"] == "create_incident"
                and decision.get("event_family") in ("eigrp", "tunnel", "interface")
                and decision["event_state"] != "up"
                and peer_ip
                and incident is None
            ):
                peer_incident = self._find_peer_device_incident(
                    cur,
                    peer_ip=peer_ip,
                    event_family=decision.get("event_family", "eigrp"),
                )
                if peer_incident:
                    # Merge: append event to peer's incident, no new incident created
                    cur.execute(
                        """
                        UPDATE incidents
                        SET event_count = event_count + 1,
                            last_seen_at = NOW(),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (peer_incident["id"],),
                    )
                    self._record_timeline(
                        cur,
                        peer_incident["id"],
                        "event",
                        f"Cross-device correlation: {decision.get('title', 'Related fault')}",
                        f"Peer device also reported the same failure. Merged to avoid duplicate incident. "
                        f"Peer correlation: {decision.get('correlation_key', '')}",
                        {"cross_device": True, "peer_correlation_key": decision.get("correlation_key")},
                    )
                    if latest_event:
                        cur.execute(
                            """
                            INSERT INTO incident_events (incident_id, event_id)
                            SELECT %s, cge.event_id
                            FROM candidate_group_events cge
                            WHERE cge.candidate_group_id = %s
                            ON CONFLICT (incident_id, event_id) DO NOTHING
                            """,
                            (peer_incident["id"], group["id"]),
                        )
                    incident_id = peer_incident["id"]
                    # Record decision and skip normal create/update flow
                    cur.execute(
                        """
                        INSERT INTO llm_incident_decisions (
                            candidate_group_id, incident_id, action, incident_no, title, event_family,
                            event_state, severity, summary, correlation_key, category, reasoning, metadata, raw_response
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            group["id"],
                            peer_incident["id"],
                            "cross_device_merge",
                            peer_incident["incident_no"],
                            decision["title"],
                            decision.get("event_family", ""),
                            decision.get("event_state", ""),
                            decision.get("severity", ""),
                            decision.get("summary", ""),
                            decision.get("correlation_key", ""),
                            decision.get("category", ""),
                            f"Merged into peer incident {peer_incident['incident_no']} (cross-device dedup)",
                            Json(decision.get("metadata") or {}),
                            decision.get("raw_response", ""),
                        ),
                    )
                    logger.info(
                        "Cross-device dedup: merged %s into peer incident %s",
                        decision.get("correlation_key"),
                        peer_incident["incident_no"],
                    )
                    return incident_id  # early return, skip normal flow

            if decision["event_state"] in _RECOVERY_EVENT_STATES:
                next_status = "recovering"
            elif incident:
                # Only mark "reopened" if fault arrives after the incident was recovering/resolved.
                # If it's still active/new/investigating, keep the current status (more DOWN context).
                next_status = "reopened" if incident["status"] in _POST_RECOVERY_STATUSES else incident["status"]
            else:
                next_status = "new"
            recovery_state = "signal_detected" if decision["event_state"] in _RECOVERY_EVENT_STATES else "watching"
            if incident:
                cur.execute(
                    """
                    UPDATE incidents
                    SET title = %s,
                        severity = %s,
                        category = COALESCE(%s, category),
                        status = %s,
                        current_recovery_state = %s,
                        event_count = %s,
                        site = %s,
                        primary_device_id = COALESCE(%s, primary_device_id),
                        primary_source_ip = %s,
                        last_seen_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        decision["title"],
                        decision["severity"],
                        decision.get("category"),
                        next_status,
                        recovery_state,
                        group["event_count"],
                        device["site"] if device else "",
                        device["id"] if device else None,
                        device["ip_address"] if device else group["source_ip"],
                        incident["id"],
                    ),
                )
                incident_row = cur.fetchone()
            else:
                cur.execute(
                    """
                    INSERT INTO incidents (
                        title, status, severity, category, summary, primary_source_ip, correlation_key,
                        event_family, event_count, site, primary_device_id, current_recovery_state, opened_at, last_seen_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    RETURNING *
                    """,
                    (
                        decision["title"],
                        next_status,
                        decision["severity"],
                        decision.get("category") or "unknown",
                        decision["summary"],
                        device["ip_address"] if device else group["source_ip"],
                        decision["correlation_key"],
                        decision["event_family"],
                        group["event_count"],
                        device["site"] if device else "",
                        device["id"] if device else None,
                        recovery_state,
                    ),
                )
                incident_row = cur.fetchone()
                incident_row["incident_no"] = self._next_incident_no(cur, incident_row["id"])
            incident_id = incident_row["id"]
            cur.execute(
                """
                INSERT INTO incident_events (incident_id, event_id)
                SELECT %s, cge.event_id
                FROM candidate_group_events cge
                WHERE cge.candidate_group_id = %s
                ON CONFLICT (incident_id, event_id) DO NOTHING
                """,
                (incident_id, group["id"]),
            )
            self._record_timeline(
                cur,
                incident_id,
                "decision",
                "LLM incident decision",
                decision.get("reasoning", "No reasoning returned."),
                {"action": decision["action"], "candidate_group_id": group["id"]},
            )
            if latest_event:
                self._record_timeline(
                    cur,
                    incident_id,
                    "event",
                    latest_event["title"],
                    latest_event["summary"],
                    {"event_state": latest_event["event_state"], "event_family": latest_event["event_family"]},
                )

        cur.execute(
            """
            INSERT INTO llm_incident_decisions (
                candidate_group_id, incident_id, action, incident_no, title, event_family,
                event_state, severity, summary, correlation_key, category, reasoning, metadata, raw_response
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                group["id"],
                incident_id,
                decision["action"],
                decision.get("incident_no"),
                decision["title"],
                decision["event_family"],
                decision["event_state"],
                decision["severity"],
                decision["summary"],
                decision["correlation_key"],
                decision.get("category") or "unknown",
                decision.get("reasoning", ""),
                Json(decision.get("metadata") or {}),
                decision.get("raw_response", ""),
            ),
        )
        cur.execute(
            """
            UPDATE candidate_groups
            SET linked_incident_id = %s,
                last_decision_at = NOW(),
                last_decision_event_count = %s,
                decision_status = CASE
                    WHEN event_count > %s THEN 'pending'
                    ELSE 'idle'
                END,
                decision_requested_at = CASE
                    WHEN event_count > %s THEN %s
                    ELSE NULL
                END,
                decision_locked_at = NULL,
                status = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                incident_id,
                claimed_event_count,
                claimed_event_count,
                claimed_event_count,
                datetime.now(timezone.utc) + _GROUP_DECISION_DEBOUNCE,
                "idle" if decision["action"] == "ignore" else "open",
                group["id"],
            ),
        )
        if raw_logs:
            cur.execute(
                """
                UPDATE raw_logs
                SET parse_status = %s
                WHERE id = ANY(%s)
                """,
                ("llm_decided", [row["id"] for row in raw_logs]),
            )
        return incident_id

    def refresh_incident_summary(self, incident_id: int) -> None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT i.*, d.hostname AS primary_hostname, d.site, d.device_role, d.os_platform, d.version
                    FROM incidents i
                    LEFT JOIN devices d ON d.id = i.primary_device_id
                    WHERE i.id = %s
                    """,
                    (incident_id,),
                )
                incident_row = cur.fetchone()
                if incident_row is None:
                    return
                device = None
                if incident_row.get("primary_device_id"):
                    device = {
                        "id": incident_row["primary_device_id"],
                        "hostname": incident_row.get("primary_hostname"),
                        "site": incident_row.get("site", ""),
                        "device_role": incident_row.get("device_role", ""),
                        "os_platform": incident_row.get("os_platform", ""),
                        "version": incident_row.get("version", ""),
                    }
                cur.execute(
                    """
                    SELECT rl.*
                    FROM raw_logs rl
                    JOIN events e ON e.raw_log_id = rl.id
                    JOIN incident_events ie ON ie.event_id = e.id
                    WHERE ie.incident_id = %s
                    ORDER BY rl.received_at DESC
                    LIMIT 12
                    """,
                    (incident_id,),
                )
                raw_logs = cur.fetchall()
                self._refresh_incident_summary(cur, incident_row=incident_row, device=device, raw_logs=raw_logs)
            conn.commit()

    def _refresh_incident_summary(
        self,
        cur: psycopg.Cursor[Any],
        *,
        incident_row: dict[str, Any],
        device: dict[str, Any] | None,
        raw_logs: list[dict[str, Any]],
    ) -> None:
        incident_payload = {
            "id": incident_row["id"],
            "incident_no": incident_row["incident_no"],
            "title": incident_row["title"],
            "status": incident_row["status"],
            "severity": incident_row["severity"],
            "event_family": incident_row["event_family"],
            "primary_source_ip": incident_row["primary_source_ip"],
            "event_count": incident_row["event_count"],
            "primary_hostname": device["hostname"] if device else None,
            "site": device["site"] if device else incident_row.get("site", ""),
            "device_role": device["device_role"] if device else "",
            "os_platform": device["os_platform"] if device else "",
            "version": device["version"] if device else "",
            "current_recovery_state": incident_row["current_recovery_state"],
            "category": incident_row["category"],
        }
        summary = generate_ai_summary(incident_payload, raw_logs)
        cur.execute(
            """
            INSERT INTO ai_summaries (
                incident_id, trigger, summary, probable_cause, confidence_score,
                category, impact, suggested_checks, raw_response
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                incident_row["id"],
                "pipeline",
                summary["summary"],
                summary["probable_cause"],
                summary["confidence_score"],
                summary["category"],
                summary["impact"],
                Json(summary["suggested_checks"]),
                summary["raw_response"],
            ),
        )
        summary_id = cur.fetchone()["id"]
        cur.execute(
            """
            UPDATE incidents
            SET summary = %s,
                probable_cause = %s,
                confidence_score = %s,
                category = %s,
                latest_ai_summary_id = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                summary["summary"],
                summary["probable_cause"],
                summary["confidence_score"],
                summary["category"],
                summary_id,
                incident_row["id"],
            ),
        )
        self._record_timeline(
            cur,
            incident_row["id"],
            "summary",
            "AI summary updated",
            summary["summary"],
            {"category": summary["category"], "confidence_score": summary["confidence_score"]},
        )

    def _fetch_open_incidents(self, *, include_resolved: bool) -> list[dict[str, Any]]:
        where_clause = "" if include_resolved else "WHERE status <> ALL(%s)"
        params: list[Any] = [] if include_resolved else [["resolved", "closed"]]
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT i.*, d.hostname AS primary_hostname
                    FROM incidents i
                    LEFT JOIN devices d ON d.id = i.primary_device_id
                    {where_clause}
                    ORDER BY i.last_seen_at DESC
                    """,
                    params,
                )
                return cur.fetchall()

    def dashboard(self) -> dict[str, Any]:
        incidents = self._fetch_open_incidents(include_resolved=False)
        history = self.history(limit=20)
        approvals = self.approvals(limit=20)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS count FROM incidents WHERE status = 'recovering'")
                recovering = cur.fetchone()["count"]
                cur.execute("SELECT COUNT(*) AS count FROM incidents WHERE status = 'resolved' AND resolved_at >= NOW() - INTERVAL '1 day'")
                resolved_today = cur.fetchone()["count"]
                cur.execute("SELECT COUNT(*) AS count FROM incidents WHERE reopened_count > 0 AND last_seen_at >= NOW() - INTERVAL '7 day'")
                reopened = cur.fetchone()["count"]
        return {
            "metrics": {
                "active_incidents": len(incidents),
                "recovering_incidents": recovering,
                "pending_approvals": len(approvals),
                "resolved_today": resolved_today,
                "reopened_this_week": reopened,
            },
            "incidents": incidents[:8],
            "approvals": approvals[:6],
            "history": history[:6],
        }

    def incidents(self) -> list[dict[str, Any]]:
        return self._fetch_open_incidents(include_resolved=False)

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT i.*, d.hostname AS primary_hostname
                    FROM incidents i
                    LEFT JOIN devices d ON d.id = i.primary_device_id
                    WHERE i.status IN ('resolved', 'closed')
                    ORDER BY COALESCE(i.resolved_at, i.updated_at) DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return cur.fetchall()

    def logs(self, incident_no: str | None = None, limit: int = 200) -> dict[str, Any]:
        with connect() as conn:
            with conn.cursor() as cur:
                if incident_no:
                    cur.execute(
                        """
                        SELECT rl.*, i.incident_no
                        FROM raw_logs rl
                        JOIN events e ON e.raw_log_id = rl.id
                        JOIN incident_events ie ON ie.event_id = e.id
                        JOIN incidents i ON i.id = ie.incident_id
                        WHERE i.incident_no = %s
                        ORDER BY rl.received_at DESC
                        LIMIT %s
                        """,
                        (incident_no, limit),
                    )
                    raw_logs = cur.fetchall()
                    cur.execute(
                        """
                        SELECT e.*, i.incident_no, d.hostname
                        FROM events e
                        LEFT JOIN devices d ON d.id = e.device_id
                        JOIN incident_events ie ON ie.event_id = e.id
                        JOIN incidents i ON i.id = ie.incident_id
                        WHERE i.incident_no = %s
                        ORDER BY e.created_at DESC
                        LIMIT %s
                        """,
                        (incident_no, limit),
                    )
                    events = cur.fetchall()
                else:
                    cur.execute(
                        """
                        SELECT * FROM (
                            SELECT
                                rl.*,
                                i.incident_no,
                                ROW_NUMBER() OVER (PARTITION BY rl.id ORDER BY ie.id DESC NULLS LAST, i.id DESC NULLS LAST) AS row_rank
                            FROM raw_logs rl
                            LEFT JOIN events e ON e.raw_log_id = rl.id
                            LEFT JOIN incident_events ie ON ie.event_id = e.id
                            LEFT JOIN incidents i ON i.id = ie.incident_id
                        ) dedup
                        WHERE row_rank = 1
                        ORDER BY received_at DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                    raw_logs = cur.fetchall()
                    cur.execute(
                        """
                        SELECT * FROM (
                            SELECT
                                e.*,
                                i.incident_no,
                                d.hostname,
                                ROW_NUMBER() OVER (PARTITION BY e.id ORDER BY ie.id DESC NULLS LAST, i.id DESC NULLS LAST) AS row_rank
                            FROM events e
                            LEFT JOIN devices d ON d.id = e.device_id
                            LEFT JOIN incident_events ie ON ie.event_id = e.id
                            LEFT JOIN incidents i ON i.id = ie.incident_id
                        ) dedup
                        WHERE row_rank = 1
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                    events = cur.fetchall()
        return {"raw_logs": raw_logs, "events": events}

    def devices(self) -> list[dict[str, Any]]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        d.*,
                        COUNT(*) FILTER (WHERE i.status <> ALL(ARRAY['resolved','closed'])) AS open_incident_count,
                        MAX(i.last_seen_at) AS last_incident_seen
                    FROM devices d
                    LEFT JOIN incidents i ON i.primary_device_id = d.id
                    GROUP BY d.id
                    ORDER BY d.hostname
                    """
                )
                return cur.fetchall()

    def device_detail(self, hostname: str) -> dict[str, Any] | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        d.*,
                        COUNT(*) FILTER (WHERE i.status <> ALL(ARRAY['resolved','closed'])) AS open_incident_count,
                        MAX(i.last_seen_at) AS last_incident_seen
                    FROM devices d
                    LEFT JOIN incidents i ON i.primary_device_id = d.id
                    WHERE LOWER(d.hostname) = LOWER(%s) OR d.ip_address = %s
                    GROUP BY d.id
                    """,
                    (hostname, hostname),
                )
                device = cur.fetchone()
                if not device:
                    return None
                cur.execute(
                    """
                    SELECT i.* FROM incidents i
                    WHERE i.primary_source_ip = %s
                    ORDER BY i.last_seen_at DESC
                    LIMIT 50
                    """,
                    (device["ip_address"],),
                )
                incidents = cur.fetchall()
                cur.execute(
                    """
                    SELECT e.* FROM events e
                    JOIN raw_logs rl ON rl.id = e.raw_log_id
                    WHERE rl.source_ip = %s
                    ORDER BY e.created_at DESC
                    LIMIT 30
                    """,
                    (device["ip_address"],),
                )
                events = cur.fetchall()
                return {
                    "device": device,
                    "incidents": incidents,
                    "events": events,
                }

    def approvals(self, limit: int = 100) -> list[dict[str, Any]]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.*, i.incident_no, i.title AS incident_title
                    FROM proposals p
                    JOIN incidents i ON i.id = p.incident_id
                    ORDER BY p.created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return cur.fetchall()

    def reset_incident_data(self) -> dict[str, int]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS count FROM incidents")
                incident_count = cur.fetchone()["count"]
                cur.execute("SELECT COUNT(*) AS count FROM raw_logs")
                raw_log_count = cur.fetchone()["count"]
                cur.execute("SELECT COUNT(*) AS count FROM events")
                event_count = cur.fetchone()["count"]
                cur.execute(
                    """
                    TRUNCATE TABLE
                        llm_incident_decisions,
                        candidate_group_events,
                        candidate_groups,
                        ingest_jobs,
                        executions,
                        proposals,
                        troubleshoot_runs,
                        ai_summaries,
                        incident_timeline,
                        incident_events,
                        events,
                        raw_logs,
                        incidents
                    RESTART IDENTITY
                    """
                )
            conn.commit()
        return {
            "incidents_removed": incident_count,
            "events_removed": event_count,
            "raw_logs_removed": raw_log_count,
        }

    def get_incident(self, incident_no: str) -> dict[str, Any]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT i.*, d.hostname AS primary_hostname, d.os_platform, d.device_role
                    FROM incidents i
                    LEFT JOIN devices d ON d.id = i.primary_device_id
                    WHERE i.incident_no = %s
                    """,
                    (incident_no,),
                )
                incident = cur.fetchone()
                if incident is None:
                    raise KeyError(f"Incident {incident_no} not found")
                cur.execute(
                    """
                    SELECT * FROM incident_timeline
                    WHERE incident_id = %s
                    ORDER BY created_at DESC
                    LIMIT 40
                    """,
                    (incident["id"],),
                )
                timeline = cur.fetchall()
                cur.execute(
                    """
                    SELECT *
                    FROM ai_summaries
                    WHERE incident_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (incident["id"],),
                )
                ai_summary = cur.fetchone()
                cur.execute(
                    """
                    SELECT *
                    FROM troubleshoot_runs
                    WHERE incident_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (incident["id"],),
                )
                troubleshoot = cur.fetchone()
                cur.execute(
                    """
                    SELECT *
                    FROM proposals
                    WHERE incident_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (incident["id"],),
                )
                proposal = cur.fetchone()
                cur.execute(
                    """
                    SELECT *
                    FROM executions
                    WHERE incident_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (incident["id"],),
                )
                execution = cur.fetchone()
                cur.execute(
                    """
                    SELECT rl.*
                    FROM raw_logs rl
                    JOIN events e ON e.raw_log_id = rl.id
                    JOIN incident_events ie ON ie.event_id = e.id
                    WHERE ie.incident_id = %s
                    ORDER BY rl.received_at DESC
                    LIMIT 20
                    """,
                    (incident["id"],),
                )
                raw_logs = cur.fetchall()
                cur.execute(
                    """
                    SELECT e.*, rl.raw_message
                    FROM events e
                    LEFT JOIN raw_logs rl ON rl.id = e.raw_log_id
                    JOIN incident_events ie ON ie.event_id = e.id
                    WHERE ie.incident_id = %s
                    ORDER BY e.created_at DESC
                    LIMIT 20
                    """,
                    (incident["id"],),
                )
                events = cur.fetchall()
        return {
            "incident": incident,
            "timeline": timeline,
            "ai_summary": ai_summary,
            "troubleshoot": troubleshoot,
            "proposal": proposal,
            "execution": execution,
            "raw_logs": raw_logs,
            "events": events,
        }

    def run_troubleshoot(self, incident_no: str) -> dict[str, Any]:
        detail = self.get_incident(incident_no)
        incident = detail["incident"]
        device_cache = self._fetch_device_cache()
        result = run_llm_troubleshoot(incident, detail["raw_logs"], device_cache)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO troubleshoot_runs (
                        incident_id, status, disposition, summary, conclusion, steps, raw_response
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        incident["id"],
                        result["status"],
                        result["disposition"],
                        result["summary"],
                        result["conclusion"],
                        Json(result["steps"]),
                        result["raw_response"],
                    ),
                )
                # Build proposal first — status depends on whether one was actually created
                proposal = None
                if result.get("proposal"):
                    proposal_data = result["proposal"]
                    target_devices = [
                        device_cache[name]["hostname"] if name in device_cache else name
                        for name in proposal_data["target_devices"]
                    ]
                    cur.execute(
                        """
                        INSERT INTO proposals (
                            incident_id, title, rationale, target_devices, commands,
                            rollback_commands, rollback_plan, expected_impact,
                            verification_commands, risk_level
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            incident["id"],
                            proposal_data["title"],
                            proposal_data["rationale"],
                            Json(target_devices),
                            Json(proposal_data["commands"]),
                            Json(proposal_data.get("rollback_commands") or []),
                            proposal_data.get("rollback_plan") or "",
                            proposal_data.get("expected_impact") or "",
                            Json(proposal_data.get("verification_commands") or []),
                            proposal_data.get("risk_level", "medium"),
                        ),
                    )
                    proposal = cur.fetchone()

                # Determine next incident status based on disposition + whether proposal was created
                disposition = result["disposition"]
                status_map = {
                    "self_recovered": ("monitoring", "self_recovered"),
                    "monitor_further": ("investigating", None),
                    "physical_issue": ("escalated", "physical_handoff"),
                    "external_issue": ("escalated", "external_handoff"),
                    "config_fix_possible": ("awaiting_approval" if proposal else "active", None),
                    "needs_human_review": ("active", None),
                }
                next_status, resolution_type = status_map.get(disposition, ("active", None))
                # Re-fetch current status — a recovery signal may have arrived while
                # troubleshoot was running and already advanced the incident to
                # "recovering" or "monitoring".  Never downgrade those states.
                _RECOVERY_FORWARD_STATUSES = frozenset({"recovering", "monitoring", "resolved", "resolved_uncertain"})
                cur.execute("SELECT status FROM incidents WHERE id = %s", (incident["id"],))
                current_row = cur.fetchone()
                current_status = current_row["status"] if current_row else None
                if current_status in _RECOVERY_FORWARD_STATUSES and next_status not in _RECOVERY_FORWARD_STATUSES:
                    # Preserve the recovery-forward status; only log the troubleshoot result
                    next_status = current_status
                if proposal:
                    cur.execute(
                        """
                        UPDATE incidents
                        SET status = 'awaiting_approval',
                            current_proposal_id = %s,
                            resolution_type = COALESCE(%s, resolution_type),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (proposal["id"], resolution_type, incident["id"]),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE incidents
                        SET status = %s,
                            resolution_type = COALESCE(%s, resolution_type),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (next_status, resolution_type, incident["id"]),
                    )
                self._record_timeline(
                    cur,
                    incident["id"],
                    "troubleshoot",
                    "AI troubleshooting completed",
                    result["summary"],
                    {"disposition": disposition},
                )
                if proposal:
                    self._record_timeline(
                        cur,
                        incident["id"],
                        "proposal",
                        "Remediation proposal created",
                        proposal["title"],
                        {"proposal_id": proposal["id"]},
                    )
            conn.commit()
        return self.get_incident(incident_no)

    def approve_proposal(self, incident_no: str, actor: str) -> dict[str, Any]:
        detail = self.get_incident(incident_no)
        proposal = detail["proposal"]
        if proposal is None:
            raise KeyError(f"No proposal found for incident {incident_no}")
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE proposals
                    SET status = 'approved', approved_at = NOW(), approved_by = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (actor, proposal["id"]),
                )
                cur.execute(
                    """
                    UPDATE incidents
                    SET status = 'approved', updated_at = NOW()
                    WHERE id = %s
                    """,
                    (detail["incident"]["id"],),
                )
                self._record_timeline(
                    cur,
                    detail["incident"]["id"],
                    "approval",
                    "Proposal approved",
                    f"Approved by {actor}",
                    {"proposal_id": proposal["id"], "actor": actor},
                )
            conn.commit()
        return self.get_incident(incident_no)

    def execute_proposal(self, incident_no: str, actor: str) -> dict[str, Any]:
        detail = self.get_incident(incident_no)
        proposal = detail["proposal"]
        incident = detail["incident"]
        if proposal is None:
            raise KeyError(f"No proposal found for incident {incident_no}")
        commands = proposal["commands"] or []
        rollback_commands = proposal.get("rollback_commands") or []
        verification_commands = proposal.get("verification_commands") or []
        target_devices = proposal["target_devices"] or []
        device_cache = self._fetch_device_cache()

        # ── Phase 1: Apply config changes ─────────────────────────────────────
        outputs: list[str] = []
        status = "completed"
        for name in target_devices:
            if name not in device_cache:
                outputs.append(f"[CONFIG ERROR] Device '{name}' not found in cache")
                status = "failed"
                continue
            device = device_cache[name]
            output = execute_config(device["ip_address"], device["os_platform"], commands)
            outputs.append(output)
            if not output.startswith("[CONFIG APPLIED]"):
                status = "failed"

        # ── Phase 2a: Auto-rollback on execution failure ───────────────────────
        if status == "failed" and rollback_commands:
            rollback_outputs: list[str] = []
            for name in target_devices:
                if name not in device_cache:
                    continue
                device = device_cache[name]
                rb_out = execute_config(device["ip_address"], device["os_platform"], rollback_commands)
                rollback_outputs.append(rb_out)
            outputs.append("[ROLLBACK TRIGGERED]\n" + "\n".join(rollback_outputs))

        # ── Phase 2b: Auto-verify on execution success ─────────────────────────
        verification_status = "pending"
        verification_notes = "Manual verification required."
        if status == "completed" and verification_commands:
            verify_parts: list[str] = []
            for name in target_devices:
                if name not in device_cache:
                    continue
                device = device_cache[name]
                v_out = run_show_commands(device["ip_address"], device["os_platform"], verification_commands)
                verify_parts.append(v_out)
            if verify_parts:
                verification_status = "auto_checked"
                verification_notes = "\n\n".join(verify_parts)

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO executions (
                        incident_id, proposal_id, status, executed_by, output,
                        verification_status, verification_notes, completed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    RETURNING *
                    """,
                    (
                        incident["id"],
                        proposal["id"],
                        status,
                        actor,
                        "\n\n".join(outputs),
                        verification_status,
                        verification_notes,
                    ),
                )
                execution = cur.fetchone()
                # failed + rollback → back to active; success → verifying
                next_status = "verifying" if status == "completed" else "active"
                cur.execute(
                    "UPDATE incidents SET status = %s, updated_at = NOW() WHERE id = %s",
                    (next_status, incident["id"]),
                )
                cur.execute(
                    "UPDATE proposals SET status = 'executed' WHERE id = %s",
                    (proposal["id"],),
                )
                timeline_note = f"Execution status: {status}"
                if status == "failed" and rollback_commands:
                    timeline_note += " — rollback commands automatically applied"
                elif status == "completed" and verification_status == "auto_checked":
                    timeline_note += " — verification commands collected automatically"
                self._record_timeline(
                    cur,
                    incident["id"],
                    "execution",
                    "Approved commands executed",
                    timeline_note,
                    {"execution_id": execution["id"], "actor": actor, "auto_verified": verification_status == "auto_checked"},
                )
            conn.commit()
        return self.get_incident(incident_no)

    def verify_recovery(self, incident_no: str, healed: bool, note: str) -> dict[str, Any]:
        detail = self.get_incident(incident_no)
        incident = detail["incident"]
        # healed=False from verifying state → re-open as active for re-investigation
        if not healed and incident.get("status") == "verifying":
            next_status = "active"
        else:
            next_status = "resolved" if healed else "monitoring"
        resolution_type = "verified_recovery" if healed else None
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE incidents
                    SET status = %s,
                        resolution_type = COALESCE(%s, resolution_type),
                        resolved_at = CASE WHEN %s THEN NOW() ELSE resolved_at END,
                        current_recovery_state = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (next_status, resolution_type, healed, "recovered" if healed else "monitoring", incident["id"]),
                )
                self._record_timeline(
                    cur,
                    incident["id"],
                    "recovery",
                    "Recovery judgment updated",
                    note,
                    {"healed": healed},
                )
            conn.commit()
        return self.get_incident(incident_no)
