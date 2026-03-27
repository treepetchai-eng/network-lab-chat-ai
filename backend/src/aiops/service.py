from __future__ import annotations

import csv
import json
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
from src.tools.interface_inventory import resolve_link_context
from src.tools.config_executor import execute_config, run_show_commands

_INVENTORY_PATH = Path(__file__).parent.parent.parent / "inventory" / "inventory.csv"
_OPEN_INCIDENT_STATUSES = (
    "active",
    "recovering",
    "monitoring",
)
_PIPELINE_STATUSES = ("pending_parse",)
_GROUP_WINDOW = timedelta(minutes=10)
# Primary operator-facing statuses only.
_PRIMARY_INCIDENT_STATUSES = frozenset({"active", "recovering", "monitoring", "resolved"})
_ROOT_INCIDENT_ROLE = "root"
_SYMPTOM_INCIDENT_ROLE = "symptom"
_RELATED_INCIDENT_PREFIX = "link|"
# Event states the LLM may return to indicate recovery (not just "up")
_RECOVERY_EVENT_STATES = frozenset({"up", "resolved", "recovered", "established", "restored", "cleared", "restart"})
# Incident statuses that indicate the fault was previously considered over — a new DOWN event here is a re-fault
_POST_RECOVERY_STATUSES = frozenset({"recovering", "monitoring", "resolved", "resolved_uncertain"})
_PENDING_APPROVAL_WORKFLOW_PHASES = frozenset({"remediation_available", "approved_to_execute"})
_FAULT_PRESERVED_WORKFLOW_PHASES = frozenset({
    "intent_confirmation_required",
    "remediation_available",
    "approved_to_execute",
    "escalated_physical",
    "escalated_external",
})
_TIME_BASED_AUTO_ADVANCE_WORKFLOW_PHASES = frozenset({
    "none",
    "intent_confirmation_required",
    "remediation_available",
    "approved_to_execute",
})
_LINKED_ADMIN_DOWN_EVENT_FAMILIES = frozenset({"interface", "bgp", "ospf", "eigrp", "tunnel"})
_TOPOLOGY_ROOT_EVENT_FAMILIES = frozenset({"interface", "bgp", "ospf", "eigrp", "tunnel", "tracking"})
_LINKED_ADMIN_DOWN_WINDOW_SECONDS = max(1, int(os.getenv("AIOPS_LINKED_ADMIN_DOWN_WINDOW_SECONDS", "120")))
_RECOVERY_SIGNAL_MONITORING_SECONDS = max(
    1,
    int(os.getenv("AIOPS_RECOVERY_SIGNAL_MONITORING_SECONDS", "60")),
)
_RECOVERY_STABILITY_SECONDS = max(
    1,
    int(os.getenv("AIOPS_RECOVERY_STABILITY_SECONDS", "60")),
)
_SYSTEM_BOOT_MONITORING_SECONDS = max(
    1,
    int(os.getenv("AIOPS_SYSTEM_BOOT_MONITORING_SECONDS", "60")),
)
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


def _incident_metadata(row: dict[str, Any] | None) -> dict[str, Any]:
    metadata = (row or {}).get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def _linked_admin_down_summary(metadata: dict[str, Any]) -> str:
    root_host = metadata.get("root_host") or "the linked peer"
    root_interface = metadata.get("root_interface") or "the linked interface"
    remote_host = metadata.get("remote_host") or "the impacted device"
    remote_interface = metadata.get("remote_interface") or "the impacted interface"
    return (
        f"Remote impact on {remote_host} {remote_interface} correlates with an admin shutdown on "
        f"{root_host} {root_interface}. Confirm whether that shutdown was intentional before generating a "
        "config remediation."
    )


def _linked_admin_down_cause(metadata: dict[str, Any]) -> str:
    root_host = metadata.get("root_host") or "the linked peer"
    root_interface = metadata.get("root_interface") or "the linked interface"
    return (
        f"The leading hypothesis is peer-induced impact from an operator-triggered shutdown on "
        f"{root_host} {root_interface}, not an independent physical failure on the remote side."
    )


def _needs_intent_confirmation(row: dict[str, Any] | None) -> bool:
    metadata = _incident_metadata(row)
    return _incident_workflow_phase(row) == "intent_confirmation_required" or metadata.get("intent_status") == "needs_confirmation"


def _incident_workflow_phase(row: dict[str, Any] | None) -> str:
    phase = (row or {}).get("workflow_phase")
    return phase if isinstance(phase, str) and phase else "none"


def _fault_workflow_phase(row: dict[str, Any] | None) -> str:
    phase = _incident_workflow_phase(row)
    return phase if phase in _FAULT_PRESERVED_WORKFLOW_PHASES else "none"


def _proposal_workflow_phase(proposal_status: str | None) -> str:
    if proposal_status == "approved":
        return "approved_to_execute"
    if proposal_status == "pending":
        return "remediation_available"
    if proposal_status == "executed":
        return "awaiting_verification"
    return "none"


def _incident_role(row: dict[str, Any] | None) -> str:
    role = (row or {}).get("incident_role")
    return role if isinstance(role, str) and role else _ROOT_INCIDENT_ROLE


def _root_incident_id(row: dict[str, Any] | None) -> int | None:
    value = (row or {}).get("root_incident_id")
    return int(value) if isinstance(value, int) else None


def _is_root_incident(row: dict[str, Any] | None) -> bool:
    return _incident_role(row) == _ROOT_INCIDENT_ROLE


def _relation_group_key_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    data = metadata or {}
    link_id = str(data.get("link_id") or "").strip()
    confidence = str(data.get("topology_confidence") or "").strip().lower()
    if link_id and confidence == "high":
        return f"{_RELATED_INCIDENT_PREFIX}{link_id}"
    return None


def _is_deprecated_root(row: dict[str, Any] | None) -> bool:
    metadata = _incident_metadata(row)
    return bool(metadata.get("deprecated_root"))


def _incident_remediation_owner_id(row: dict[str, Any] | None) -> int | None:
    value = (row or {}).get("remediation_owner_incident_id")
    return int(value) if isinstance(value, int) else None


def _incident_owns_remediation(row: dict[str, Any] | None) -> bool:
    if row is None or not isinstance(row.get("id"), int):
        return False
    owner_id = _incident_remediation_owner_id(row)
    return owner_id is None or owner_id == row["id"]


def _verification_signal_state(output: str | None) -> str:
    text = (output or "").lower()
    if not text.strip():
        return "unknown"
    positive_markers = (
        "is up, line protocol is up",
        "changed state to up",
        "from loading to full",
        "from init to full",
        "from exchange to full",
        "neighbor state is full",
        "protocol is up",
    )
    negative_markers = (
        "administratively down",
        "is down, line protocol is down",
        "line protocol is down",
        "dead timer expired",
        "from full to down",
        "changed state to down",
        "protocol is down",
    )
    if any(marker in text for marker in positive_markers):
        return "positive"
    if any(marker in text for marker in negative_markers):
        return "negative"
    return "unknown"


def _parse_link_id(link_id: str | None) -> list[tuple[str, str]]:
    raw = (link_id or "").strip()
    if not raw or "<->" not in raw:
        return []
    endpoints: list[tuple[str, str]] = []
    for endpoint in raw.split("<->"):
        host, _, interface_name = endpoint.partition(":")
        host = host.strip()
        interface_name = interface_name.strip()
        if host and interface_name:
            endpoints.append((host, interface_name))
    return endpoints


def _topology_path_label(metadata: dict[str, Any]) -> str:
    endpoints = _parse_link_id(metadata.get("link_id"))
    if len(endpoints) == 2:
        return f"{endpoints[0][0]} {endpoints[0][1]} and {endpoints[1][0]} {endpoints[1][1]}"
    root_side = [metadata.get("root_host"), metadata.get("root_interface")]
    remote_side = [metadata.get("remote_host"), metadata.get("remote_interface")]
    root_label = " ".join(part for part in root_side if part).strip()
    remote_label = " ".join(part for part in remote_side if part).strip()
    if root_label and remote_label:
        return f"{root_label} and {remote_label}"
    return root_label or remote_label or "the linked path"


def _generic_topology_summary(metadata: dict[str, Any], *, dominant_family: str, dominant_host: str, active_child_count: int) -> str:
    family_label = dominant_family.replace("_", " ").upper() if dominant_family else "NETWORK"
    path_label = _topology_path_label(metadata)
    impacted_host = dominant_host or "the linked device"
    sibling_text = "symptom" if active_child_count == 1 else "symptoms"
    return (
        f"{family_label} impact is being tracked on the linked path between {path_label}. "
        f"{impacted_host} is the current dominant symptom source, with {max(active_child_count, 1)} active {sibling_text} still attached to this root cause thread."
    )


def _generic_topology_cause(metadata: dict[str, Any], *, dominant_family: str) -> str:
    path_label = _topology_path_label(metadata)
    family_label = dominant_family.replace("_", " ").upper() if dominant_family else "network"
    return (
        f"Multiple {family_label} signals on {path_label} are being correlated as one shared link problem "
        "until all related symptoms recover."
    )


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
        if not _is_test_runtime():
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
            workflow_phase TEXT NOT NULL DEFAULT 'none',
            incident_role TEXT NOT NULL DEFAULT 'root',
            parent_incident_id BIGINT REFERENCES incidents(id) ON DELETE SET NULL,
            root_incident_id BIGINT REFERENCES incidents(id) ON DELETE SET NULL,
            suppressed_in_list BOOLEAN NOT NULL DEFAULT FALSE,
            relation_group_key TEXT,
            remediation_owner_incident_id BIGINT REFERENCES incidents(id) ON DELETE SET NULL,
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
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
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

        CREATE TABLE IF NOT EXISTS device_vuln_scans (
            id BIGSERIAL PRIMARY KEY,
            device_id BIGINT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            ios_version TEXT NOT NULL,
            advisory_count INTEGER NOT NULL DEFAULT 0,
            critical_count INTEGER NOT NULL DEFAULT 0,
            high_count INTEGER NOT NULL DEFAULT 0,
            medium_count INTEGER NOT NULL DEFAULT 0,
            low_count INTEGER NOT NULL DEFAULT 0,
            llm_summary TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'completed',
            error_message TEXT,
            scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS device_vulnerabilities (
            id BIGSERIAL PRIMARY KEY,
            device_id BIGINT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            scan_id BIGINT NOT NULL REFERENCES device_vuln_scans(id) ON DELETE CASCADE,
            advisory_id TEXT NOT NULL,
            title TEXT NOT NULL,
            sir TEXT NOT NULL,
            cvss_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            cves JSONB NOT NULL DEFAULT '[]'::jsonb,
            publication_url TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            workaround TEXT NOT NULL DEFAULT '',
            first_fixed JSONB NOT NULL DEFAULT '[]'::jsonb,
            first_published TIMESTAMPTZ,
            last_updated TIMESTAMPTZ,
            UNIQUE (scan_id, advisory_id)
        );

        CREATE TABLE IF NOT EXISTS advisory_checks (
            id BIGSERIAL PRIMARY KEY,
            device_id BIGINT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            advisory_id TEXT NOT NULL,
            advisory_title TEXT NOT NULL DEFAULT '',
            verdict TEXT NOT NULL DEFAULT 'pending',
            confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
            explanation TEXT NOT NULL DEFAULT '',
            commands_run JSONB NOT NULL DEFAULT '[]'::jsonb,
            llm_model TEXT NOT NULL DEFAULT '',
            has_workaround BOOLEAN,
            workaround_text TEXT NOT NULL DEFAULT '',
            feature_checked TEXT NOT NULL DEFAULT '',
            checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS interface_descriptions (
            id BIGSERIAL PRIMARY KEY,
            device_id BIGINT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            interface_name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            protocol TEXT NOT NULL DEFAULT '',
            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (device_id, interface_name)
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
                    ("incidents",        "metadata",                    "JSONB NOT NULL DEFAULT '{}'::jsonb"),
                    ("incidents",        "workflow_phase",              "TEXT NOT NULL DEFAULT 'none'"),
                    ("incidents",        "incident_role",               "TEXT NOT NULL DEFAULT 'root'"),
                    ("incidents",        "parent_incident_id",          "BIGINT REFERENCES incidents(id) ON DELETE SET NULL"),
                    ("incidents",        "root_incident_id",            "BIGINT REFERENCES incidents(id) ON DELETE SET NULL"),
                    ("incidents",        "suppressed_in_list",          "BOOLEAN NOT NULL DEFAULT FALSE"),
                    ("incidents",        "relation_group_key",          "TEXT"),
                    ("incidents",        "remediation_owner_incident_id", "BIGINT REFERENCES incidents(id) ON DELETE SET NULL"),
                    ("candidate_groups", "decision_status",             "TEXT NOT NULL DEFAULT 'idle'"),
                    ("candidate_groups", "decision_requested_at",       "TIMESTAMPTZ"),
                    ("candidate_groups", "decision_attempts",           "INTEGER NOT NULL DEFAULT 0"),
                    ("candidate_groups", "decision_locked_at",          "TIMESTAMPTZ"),
                    ("candidate_groups", "last_decision_event_count",   "INTEGER NOT NULL DEFAULT 0"),
                    ("proposals",        "rollback_commands",           "JSONB NOT NULL DEFAULT '[]'::jsonb"),
                    ("proposals",        "cancelled_reason",            "TEXT"),
                    ("device_vuln_scans",      "scan_source",    "TEXT NOT NULL DEFAULT 'unknown'"),
                    ("device_vulnerabilities", "first_published", "TIMESTAMPTZ"),
                    ("device_vulnerabilities", "last_updated",    "TIMESTAMPTZ"),
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
                cur.execute(
                    """
                    UPDATE incidents
                    SET
                        workflow_phase = CASE
                            WHEN status = 'awaiting_approval' THEN 'remediation_available'
                            WHEN status = 'approved' THEN 'approved_to_execute'
                            WHEN status IN ('executing', 'verifying') THEN 'awaiting_verification'
                            WHEN status = 'escalated' THEN
                                CASE
                                    WHEN COALESCE(resolution_type, '') = 'physical_handoff' THEN 'escalated_physical'
                                    ELSE 'escalated_external'
                                END
                            WHEN COALESCE(metadata->>'cause_hint', '') = 'linked_admin_down'
                                 AND COALESCE(metadata->>'intent_status', '') = 'needs_confirmation'
                                THEN 'intent_confirmation_required'
                            ELSE COALESCE(NULLIF(workflow_phase, ''), 'none')
                        END,
                        status = CASE
                            WHEN status IN ('resolved', 'closed') THEN 'resolved'
                            WHEN status = 'monitoring' THEN 'monitoring'
                            WHEN status = 'recovering' THEN 'recovering'
                            ELSE 'active'
                        END
                    WHERE status NOT IN ('active', 'recovering', 'monitoring', 'resolved')
                       OR COALESCE(workflow_phase, '') = ''
                       OR (
                           COALESCE(workflow_phase, 'none') = 'none'
                           AND COALESCE(metadata->>'cause_hint', '') = 'linked_admin_down'
                           AND COALESCE(metadata->>'intent_status', '') = 'needs_confirmation'
                       )
                    """
                )
                cur.execute(
                    """
                    UPDATE incidents i
                    SET workflow_phase = CASE
                        WHEN p.status = 'approved' THEN 'approved_to_execute'
                        WHEN p.status = 'pending' THEN 'remediation_available'
                        WHEN p.status = 'executed' THEN 'awaiting_verification'
                        ELSE i.workflow_phase
                    END
                    FROM proposals p
                    WHERE p.id = i.current_proposal_id
                      AND i.status IN ('active', 'recovering', 'monitoring')
                      AND COALESCE(i.workflow_phase, 'none') = 'none'
                    """
                )
                cur.execute(
                    """
                    UPDATE incidents
                    SET workflow_phase = 'none'
                    WHERE status = 'resolved'
                      AND COALESCE(workflow_phase, 'none') <> 'none'
                    """
                )
                cur.execute(
                    """
                    UPDATE incidents
                    SET incident_role = COALESCE(NULLIF(incident_role, ''), 'root'),
                        suppressed_in_list = COALESCE(suppressed_in_list, FALSE)
                    WHERE COALESCE(incident_role, '') = ''
                       OR suppressed_in_list IS NULL
                    """
                )
                cur.execute(
                    """
                    UPDATE incidents
                    SET root_incident_id = id
                    WHERE incident_role = 'root'
                      AND root_incident_id IS NULL
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS incidents_relation_group_key_idx ON incidents (relation_group_key)")
                cur.execute("CREATE INDEX IF NOT EXISTS incidents_remediation_owner_idx ON incidents (remediation_owner_incident_id)")
                self._flatten_synthetic_roots(cur)
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

    def _enrich_parsed_event_metadata(
        self,
        *,
        parsed: dict[str, Any],
        device: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if device is None:
            return parsed

        metadata = dict(parsed.get("metadata") or {})
        metadata["device_hostname"] = device.get("hostname", "")
        metadata["device_mgmt_ip"] = device.get("ip_address", "")

        interface_name = metadata.get("interface")
        peer_ip = metadata.get("peer_ip")
        topology = resolve_link_context(
            device.get("hostname", ""),
            interface_name if isinstance(interface_name, str) else None,
            peer_ip=peer_ip if isinstance(peer_ip, str) else None,
        )
        if topology.get("link_id"):
            metadata.update({
                "link_id": topology["link_id"],
                "local_interface": topology.get("local_interface", ""),
                "remote_hostname": topology.get("remote_hostname", ""),
                "remote_interface": topology.get("remote_interface", ""),
                "remote_mgmt_ip": topology.get("remote_mgmt_ip", ""),
                "topology_confidence": topology.get("topology_confidence", ""),
                "topology_resolution_method": topology.get("resolution_method", ""),
            })
            if topology.get("local_interface"):
                metadata["interface"] = topology["local_interface"]
                if parsed.get("event_family") == "interface":
                    key_parts = [parsed["source_ip"], parsed["event_family"], topology["local_interface"].lower()]
                    if metadata.get("neighbor_ip"):
                        key_parts.append(str(metadata["neighbor_ip"]))
                    parsed["correlation_key"] = "|".join(key_parts)

        parsed["metadata"] = metadata
        return parsed

    def _link_group_events_to_incident(self, cur: psycopg.Cursor[Any], *, incident_id: int, group_id: int) -> None:
        cur.execute(
            """
            INSERT INTO incident_events (incident_id, event_id)
            SELECT %s, cge.event_id
            FROM candidate_group_events cge
            WHERE cge.candidate_group_id = %s
            ON CONFLICT (incident_id, event_id) DO NOTHING
            """,
            (incident_id, group_id),
        )

    def _link_event_to_incident(self, cur: psycopg.Cursor[Any], *, incident_id: int, event_id: int) -> None:
        cur.execute(
            """
            INSERT INTO incident_events (incident_id, event_id)
            VALUES (%s, %s)
            ON CONFLICT (incident_id, event_id) DO NOTHING
            """,
            (incident_id, event_id),
        )

    def _update_incident_event_count(self, cur: psycopg.Cursor[Any], *, incident_id: int) -> None:
        cur.execute("SELECT COUNT(*) AS count FROM incident_events WHERE incident_id = %s", (incident_id,))
        count = cur.fetchone()["count"]
        cur.execute(
            """
            UPDATE incidents
            SET event_count = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (count, incident_id),
        )

    def _should_use_topology_root(self, event: dict[str, Any]) -> bool:
        metadata = event.get("metadata") or {}
        return (
            event.get("event_family") in _TOPOLOGY_ROOT_EVENT_FAMILIES
            and event.get("event_state") not in _RECOVERY_EVENT_STATES
            and event.get("event_state") != "admin_down"
            and bool(metadata.get("link_id"))
            and str(metadata.get("topology_confidence") or "").strip().lower() == "high"
        )

    def _find_open_topology_root(self, cur: psycopg.Cursor[Any], *, link_id: str) -> dict[str, Any] | None:
        cur.execute(
            """
            SELECT *
            FROM incidents
            WHERE incident_role = %s
              AND COALESCE(root_incident_id, id) = id
              AND COALESCE(metadata->>'link_id', '') = %s
              AND status = ANY(%s)
            ORDER BY last_seen_at DESC
            LIMIT 1
            """,
            (_ROOT_INCIDENT_ROLE, link_id, list(_OPEN_INCIDENT_STATUSES)),
        )
        return cur.fetchone()

    def _upsert_symptom_incident(
        self,
        cur: psycopg.Cursor[Any],
        *,
        title: str,
        severity: str,
        category: str,
        summary: str,
        probable_cause: str,
        primary_source_ip: str,
        correlation_key: str,
        event_family: str,
        current_recovery_state: str,
        metadata: dict[str, Any],
        device: dict[str, Any] | None,
        reopened: bool,
        workflow_phase: str = "none",
        relation_group_key: str | None = None,
        remediation_owner_incident_id: int | None = None,
    ) -> dict[str, Any]:
        cur.execute(
            """
            SELECT *
            FROM incidents
            WHERE correlation_key = %s
              AND COALESCE(suppressed_in_list, FALSE) = FALSE
              AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
              AND status = ANY(%s)
            ORDER BY opened_at DESC
            LIMIT 1
            """,
            (correlation_key, list(_OPEN_INCIDENT_STATUSES)),
        )
        existing = cur.fetchone()
        next_status = "recovering" if current_recovery_state == "signal_detected" else "active"
        if existing:
            cur.execute(
                """
                UPDATE incidents
                SET title = %s,
                    status = %s,
                    severity = %s,
                    category = %s,
                    summary = %s,
                    probable_cause = %s,
                    primary_device_id = COALESCE(%s, primary_device_id),
                    primary_source_ip = %s,
                    event_family = %s,
                    relation_group_key = COALESCE(%s, relation_group_key),
                    remediation_owner_incident_id = COALESCE(%s, remediation_owner_incident_id),
                    current_recovery_state = %s,
                    workflow_phase = %s,
                    metadata = %s::jsonb,
                    resolution_type = CASE WHEN %s THEN NULL ELSE resolution_type END,
                    resolved_at = CASE WHEN %s THEN NULL ELSE resolved_at END,
                    reopened_count = CASE WHEN %s THEN reopened_count + 1 ELSE reopened_count END,
                    last_seen_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    title,
                    next_status,
                    severity,
                    category,
                    summary,
                    probable_cause,
                    device["id"] if device else None,
                    primary_source_ip,
                    event_family,
                    relation_group_key,
                    remediation_owner_incident_id,
                    current_recovery_state,
                    workflow_phase,
                    Json(metadata),
                    reopened,
                    reopened,
                    reopened,
                    existing["id"],
                ),
            )
            return cur.fetchone()

        cur.execute(
            """
            INSERT INTO incidents (
                title, status, workflow_phase, incident_role, parent_incident_id, root_incident_id, suppressed_in_list,
                relation_group_key, remediation_owner_incident_id,
                severity, category, summary, probable_cause, confidence_score, site,
                primary_device_id, primary_source_ip, correlation_key, event_family,
                current_recovery_state, metadata, opened_at, last_seen_at
            )
            VALUES (%s, %s, %s, %s, NULL, NULL, FALSE, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW())
            RETURNING *
            """,
            (
                title,
                next_status,
                workflow_phase,
                _ROOT_INCIDENT_ROLE,
                relation_group_key,
                remediation_owner_incident_id,
                severity,
                category,
                summary,
                probable_cause,
                device["site"] if device else "",
                device["id"] if device else None,
                primary_source_ip,
                correlation_key,
                event_family,
                current_recovery_state,
                Json(metadata),
            ),
        )
        symptom = cur.fetchone()
        symptom["incident_no"] = self._next_incident_no(cur, symptom["id"])
        cur.execute(
            "UPDATE incidents SET root_incident_id = id, remediation_owner_incident_id = COALESCE(remediation_owner_incident_id, %s) WHERE id = %s",
            (remediation_owner_incident_id or symptom["id"], symptom["id"]),
        )
        symptom["root_incident_id"] = symptom["id"]
        symptom["remediation_owner_incident_id"] = remediation_owner_incident_id or symptom["id"]
        return symptom

    def _build_root_title(self, metadata: dict[str, Any], *, dominant_family: str, dominant_host: str) -> str:
        family_label = dominant_family.replace("_", " ").title() if dominant_family else "Linked Path"
        if metadata.get("cause_hint") == "linked_admin_down":
            return f"{family_label} impact on {dominant_host or 'the remote peer'} linked to peer admin shutdown"
        return f"{family_label} impact on {dominant_host or 'the linked path'} across correlated link path"

    def _ensure_topology_root(
        self,
        cur: psycopg.Cursor[Any],
        *,
        link_id: str,
        metadata: dict[str, Any],
        dominant_family: str,
        dominant_host: str,
        severity: str,
        category: str,
        primary_source_ip: str,
        device: dict[str, Any] | None,
        workflow_phase: str,
        cause_hint: str | None = None,
    ) -> dict[str, Any]:
        root = self._find_open_topology_root(cur, link_id=link_id)
        root_metadata = {
            **_incident_metadata(root),
            **metadata,
            "link_id": link_id,
            "topology_confidence": metadata.get("topology_confidence") or "",
        }
        endpoints = _parse_link_id(link_id)
        if len(endpoints) == 2:
            root_metadata.setdefault("endpoint_a_host", endpoints[0][0])
            root_metadata.setdefault("endpoint_a_interface", endpoints[0][1])
            root_metadata.setdefault("endpoint_b_host", endpoints[1][0])
            root_metadata.setdefault("endpoint_b_interface", endpoints[1][1])
        if cause_hint:
            root_metadata["cause_hint"] = cause_hint

        title = self._build_root_title(root_metadata, dominant_family=dominant_family, dominant_host=dominant_host)
        summary = _linked_admin_down_summary(root_metadata) if cause_hint == "linked_admin_down" else _generic_topology_summary(
            root_metadata,
            dominant_family=dominant_family,
            dominant_host=dominant_host,
            active_child_count=1,
        )
        probable_cause = _linked_admin_down_cause(root_metadata) if cause_hint == "linked_admin_down" else _generic_topology_cause(
            root_metadata,
            dominant_family=dominant_family,
        )

        if root:
            next_workflow = workflow_phase or _incident_workflow_phase(root)
            cur.execute(
                """
                UPDATE incidents
                SET title = %s,
                    status = CASE WHEN status = 'resolved' THEN 'active' ELSE status END,
                    workflow_phase = %s,
                    severity = %s,
                    category = %s,
                    summary = %s,
                    probable_cause = %s,
                    primary_device_id = COALESCE(%s, primary_device_id),
                    primary_source_ip = %s,
                    event_family = %s,
                    metadata = %s::jsonb,
                    suppressed_in_list = FALSE,
                    last_seen_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    title,
                    next_workflow,
                    severity,
                    category,
                    summary,
                    probable_cause,
                    device["id"] if device else None,
                    primary_source_ip,
                    dominant_family or root["event_family"],
                    Json(root_metadata),
                    root["id"],
                ),
            )
            return cur.fetchone()

        cur.execute(
            """
            INSERT INTO incidents (
                title, status, workflow_phase, incident_role, parent_incident_id, root_incident_id, suppressed_in_list,
                severity, category, summary, probable_cause, confidence_score, site,
                primary_device_id, primary_source_ip, correlation_key, event_family,
                current_recovery_state, metadata, opened_at, last_seen_at
            )
            VALUES (%s, 'active', %s, %s, NULL, NULL, FALSE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'watching', %s::jsonb, NOW(), NOW())
            RETURNING *
            """,
            (
                title,
                workflow_phase,
                _ROOT_INCIDENT_ROLE,
                severity,
                category,
                summary,
                probable_cause,
                0.88 if cause_hint == "linked_admin_down" else 0.72,
                device["site"] if device else "",
                device["id"] if device else None,
                primary_source_ip,
                f"topology|{link_id}",
                dominant_family or "topology",
                Json(root_metadata),
            ),
        )
        root = cur.fetchone()
        root["incident_no"] = self._next_incident_no(cur, root["id"])
        cur.execute("UPDATE incidents SET root_incident_id = id WHERE id = %s", (root["id"],))
        root["root_incident_id"] = root["id"]
        return root

    def _sync_root_incident(self, cur: psycopg.Cursor[Any], *, root_id: int) -> None:
        cur.execute(
            """
            SELECT *
            FROM incidents
            WHERE id = %s
              AND incident_role = %s
            """,
            (root_id, _ROOT_INCIDENT_ROLE),
        )
        root = cur.fetchone()
        if root is None:
            return

        cur.execute(
            """
            SELECT i.*, d.hostname AS primary_hostname, d.ip_address AS primary_ip
            FROM incidents i
            LEFT JOIN devices d ON d.id = i.primary_device_id
            WHERE i.root_incident_id = %s
              AND i.incident_role = %s
            ORDER BY
                CASE i.status
                    WHEN 'active' THEN 0
                    WHEN 'recovering' THEN 1
                    WHEN 'monitoring' THEN 2
                    ELSE 3
                END,
                CASE i.severity
                    WHEN 'critical' THEN 0
                    WHEN 'warning' THEN 1
                    ELSE 2
                END,
                i.last_seen_at DESC
            """,
            (root_id, _SYMPTOM_INCIDENT_ROLE),
        )
        children = cur.fetchall()
        if not children:
            return

        open_children = [child for child in children if child["status"] in _OPEN_INCIDENT_STATUSES]
        active_children = [child for child in open_children if child["status"] == "active"]
        recovering_children = [child for child in open_children if child["status"] == "recovering"]
        monitoring_children = [child for child in open_children if child["status"] == "monitoring"]
        dominant_child = open_children[0] if open_children else children[0]
        latest_seen = max((child["last_seen_at"] for child in children if child.get("last_seen_at")), default=root["last_seen_at"])

        next_status = "active"
        next_recovery_state = "watching"
        workflow_phase = _incident_workflow_phase(root)
        resolution_type = root.get("resolution_type")
        resolved_at: datetime | None = None

        if not open_children and root["status"] == "resolved":
            next_status = "resolved"
            next_recovery_state = root.get("current_recovery_state") or "recovered"
            workflow_phase = "none"
            resolved_at = root.get("resolved_at")
        elif active_children:
            next_status = "active"
            next_recovery_state = "watching"
        elif recovering_children:
            next_status = "recovering"
            next_recovery_state = "signal_detected"
        elif monitoring_children:
            next_status = "monitoring"
            next_recovery_state = "monitoring"
        else:
            next_status = "monitoring"
            next_recovery_state = "monitoring"
            stability_cutoff = datetime.now(timezone.utc) - timedelta(seconds=_RECOVERY_STABILITY_SECONDS)
            if latest_seen and latest_seen <= stability_cutoff and workflow_phase in _TIME_BASED_AUTO_ADVANCE_WORKFLOW_PHASES:
                next_status = "resolved"
                next_recovery_state = "recovered"
                workflow_phase = "none"
                resolution_type = resolution_type or "auto_recovered"
                resolved_at = datetime.now(timezone.utc)
                cur.execute(
                    """
                    UPDATE proposals
                    SET status = 'cancelled',
                        cancelled_reason = 'incident_auto_resolved'
                    WHERE incident_id = %s
                      AND status IN ('pending', 'approved')
                    """,
                    (root_id,),
                )

        metadata = _incident_metadata(root)
        title = self._build_root_title(
            metadata,
            dominant_family=dominant_child["event_family"],
            dominant_host=dominant_child.get("primary_hostname") or dominant_child["primary_source_ip"],
        )
        summary = _linked_admin_down_summary(metadata) if metadata.get("cause_hint") == "linked_admin_down" else _generic_topology_summary(
            metadata,
            dominant_family=dominant_child["event_family"],
            dominant_host=dominant_child.get("primary_hostname") or dominant_child["primary_source_ip"],
            active_child_count=len(open_children),
        )
        probable_cause = _linked_admin_down_cause(metadata) if metadata.get("cause_hint") == "linked_admin_down" else _generic_topology_cause(
            metadata,
            dominant_family=dominant_child["event_family"],
        )

        cur.execute(
            """
            UPDATE incidents
            SET title = %s,
                status = %s,
                workflow_phase = %s,
                severity = %s,
                category = %s,
                summary = %s,
                probable_cause = %s,
                primary_device_id = COALESCE(%s, primary_device_id),
                primary_source_ip = %s,
                event_family = %s,
                current_recovery_state = %s,
                event_count = (
                    SELECT COUNT(*)
                    FROM incident_events ie
                    WHERE ie.incident_id = %s
                ),
                resolution_type = %s,
                current_proposal_id = CASE WHEN %s = 'resolved' THEN NULL ELSE current_proposal_id END,
                last_seen_at = %s,
                resolved_at = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                title,
                next_status,
                workflow_phase,
                dominant_child["severity"],
                "config-related" if metadata.get("cause_hint") == "linked_admin_down" else (dominant_child.get("category") or root["category"]),
                summary,
                probable_cause,
                dominant_child.get("primary_device_id"),
                dominant_child.get("primary_ip") or dominant_child["primary_source_ip"],
                dominant_child["event_family"],
                next_recovery_state,
                root_id,
                resolution_type if next_status == "resolved" else (None if next_status == "active" else resolution_type),
                next_status,
                latest_seen,
                resolved_at,
                root_id,
            ),
        )

    def _sync_parent_roots_for_incident(self, cur: psycopg.Cursor[Any], *, incident_row: dict[str, Any] | None) -> None:
        root_id = _root_incident_id(incident_row)
        if root_id is None or _incident_role(incident_row) != _SYMPTOM_INCIDENT_ROLE:
            return
        self._sync_root_incident(cur, root_id=root_id)

    def _sync_all_root_incidents(self, cur: psycopg.Cursor[Any]) -> int:
        cur.execute(
            """
            SELECT DISTINCT i.id
            FROM incidents i
            WHERE i.incident_role = %s
              AND EXISTS (
                    SELECT 1
                    FROM incidents child
                    WHERE child.root_incident_id = i.id
                      AND child.incident_role = %s
                )
            ORDER BY i.id
            """,
            (_ROOT_INCIDENT_ROLE, _SYMPTOM_INCIDENT_ROLE),
        )
        rows = cur.fetchall()
        for row in rows:
            self._sync_root_incident(cur, root_id=row["id"])
        return len(rows)

    def _attach_symptom_to_root(self, cur: psycopg.Cursor[Any], *, symptom_id: int, root_id: int) -> None:
        cur.execute(
            """
            UPDATE incidents
            SET incident_role = %s,
                parent_incident_id = %s,
                root_incident_id = %s,
                suppressed_in_list = TRUE,
                updated_at = NOW()
            WHERE id = %s
            """,
            (_SYMPTOM_INCIDENT_ROLE, root_id, root_id, symptom_id),
        )
        cur.execute(
            """
            INSERT INTO incident_events (incident_id, event_id)
            SELECT %s, ie.event_id
            FROM incident_events ie
            WHERE ie.incident_id = %s
            ON CONFLICT (incident_id, event_id) DO NOTHING
            """,
            (root_id, symptom_id),
        )
        self._update_incident_event_count(cur, incident_id=symptom_id)
        self._update_incident_event_count(cur, incident_id=root_id)

    def _flatten_synthetic_roots(self, cur: psycopg.Cursor[Any]) -> int:
        cur.execute(
            """
            SELECT *
            FROM incidents
            WHERE incident_role = %s
              AND COALESCE(suppressed_in_list, FALSE) = FALSE
              AND EXISTS (
                    SELECT 1
                    FROM incidents child
                    WHERE child.root_incident_id = incidents.id
                      AND child.incident_role = %s
                )
            ORDER BY id
            """,
            (_ROOT_INCIDENT_ROLE, _SYMPTOM_INCIDENT_ROLE),
        )
        roots = cur.fetchall()
        flattened = 0
        for root in roots:
            cur.execute(
                """
                SELECT *
                FROM incidents
                WHERE root_incident_id = %s
                  AND incident_role = %s
                ORDER BY opened_at ASC, id ASC
                """,
                (root["id"], _SYMPTOM_INCIDENT_ROLE),
            )
            children = cur.fetchall()
            if not children:
                continue

            root_metadata = _incident_metadata(root)
            relation_group_key = (
                root.get("relation_group_key")
                or _relation_group_key_from_metadata(root_metadata)
                or f"legacy-root|{root['id']}"
            )
            owner = next(
                (
                    child for child in children
                    if str(_incident_metadata(child).get("operator_initiated_hint")).lower() == "true"
                ),
                None,
            ) or children[0]
            owner_id = owner["id"]

            cur.execute(
                """
                UPDATE incidents
                SET incident_role = %s,
                    parent_incident_id = NULL,
                    root_incident_id = id,
                    suppressed_in_list = FALSE,
                    relation_group_key = %s,
                    remediation_owner_incident_id = %s,
                    updated_at = NOW()
                WHERE id = ANY(%s)
                """,
                (_ROOT_INCIDENT_ROLE, relation_group_key, owner_id, [child["id"] for child in children]),
            )

            cur.execute(
                """
                UPDATE proposals
                SET incident_id = %s
                WHERE incident_id = %s
                """,
                (owner_id, root["id"]),
            )
            cur.execute(
                """
                UPDATE executions
                SET incident_id = %s
                WHERE incident_id = %s
                """,
                (owner_id, root["id"]),
            )
            cur.execute(
                """
                UPDATE troubleshoot_runs
                SET incident_id = %s
                WHERE incident_id = %s
                """,
                (owner_id, root["id"]),
            )
            cur.execute(
                """
                UPDATE ai_summaries
                SET incident_id = %s
                WHERE incident_id = %s
                """,
                (owner_id, root["id"]),
            )
            cur.execute(
                """
                UPDATE incidents
                SET current_proposal_id = COALESCE(current_proposal_id, %s),
                    latest_ai_summary_id = COALESCE(latest_ai_summary_id, %s),
                    summary = CASE WHEN COALESCE(summary, '') = '' THEN %s ELSE summary END,
                    probable_cause = CASE WHEN COALESCE(probable_cause, '') = '' THEN %s ELSE probable_cause END,
                    metadata = metadata || %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    root.get("current_proposal_id"),
                    root.get("latest_ai_summary_id"),
                    root.get("summary") or "",
                    root.get("probable_cause") or "",
                    Json({
                        "relation_group_key": relation_group_key,
                        "flattened_from_root_incident": root.get("incident_no"),
                    }),
                    owner_id,
                ),
            )

            deprecated_root_metadata = {
                **root_metadata,
                "deprecated_root": True,
                "flattened_relation_group_key": relation_group_key,
                "flattened_owner_incident_id": owner_id,
            }
            cur.execute(
                """
                UPDATE incidents
                SET suppressed_in_list = TRUE,
                    workflow_phase = 'none',
                    current_proposal_id = NULL,
                    metadata = %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (Json(deprecated_root_metadata), root["id"]),
            )
            flattened += 1
        return flattened

    def _find_relation_owner_incident(
        self,
        cur: psycopg.Cursor[Any],
        *,
        relation_group_key: str | None,
    ) -> dict[str, Any] | None:
        if not relation_group_key:
            return None
        cur.execute(
            """
            SELECT *
            FROM incidents
            WHERE relation_group_key = %s
              AND COALESCE(suppressed_in_list, FALSE) = FALSE
              AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
              AND COALESCE(metadata->>'operator_initiated_hint', 'false') = 'true'
              AND status <> 'resolved'
            ORDER BY last_seen_at DESC
            LIMIT 1
            """,
            (relation_group_key,),
        )
        return cur.fetchone()

    def _find_recent_linked_admin_down(
        self,
        cur: psycopg.Cursor[Any],
        *,
        event: dict[str, Any],
        raw_log: dict[str, Any],
        device: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        metadata = event.get("metadata") or {}
        link_id = str(metadata.get("link_id") or "").strip()
        if (
            device is None
            or not link_id
            or event.get("event_family") not in _LINKED_ADMIN_DOWN_EVENT_FAMILIES
            or event.get("event_state") in _RECOVERY_EVENT_STATES
            or event.get("event_state") == "admin_down"
        ):
            return None

        cur.execute(
            """
            SELECT
                e.*,
                rl.id AS admin_raw_log_id,
                rl.raw_message AS admin_raw_message,
                cg.id AS admin_group_id,
                d.hostname AS admin_hostname,
                d.ip_address AS admin_source_ip,
                e.title AS admin_title,
                e.summary AS admin_summary,
                e.severity AS admin_severity,
                e.correlation_key AS admin_correlation_key,
                e.event_family AS admin_event_family
            FROM events e
            JOIN raw_logs rl ON rl.id = e.raw_log_id
            LEFT JOIN devices d ON d.id = e.device_id
            LEFT JOIN candidate_group_events cge ON cge.event_id = e.id
            LEFT JOIN candidate_groups cg ON cg.id = cge.candidate_group_id
            WHERE e.event_state = 'admin_down'
              AND e.metadata->>'link_id' = %s
              AND COALESCE(e.metadata->>'operator_initiated_hint', 'false') = 'true'
              AND rl.event_time >= %s - make_interval(secs => %s)
              AND rl.event_time <= %s
            ORDER BY e.created_at DESC
            LIMIT 1
            """,
            (
                link_id,
                raw_log["event_time"],
                _LINKED_ADMIN_DOWN_WINDOW_SECONDS,
                raw_log["event_time"],
            ),
        )
        admin_event = cur.fetchone()
        if admin_event is None:
            return None

        admin_metadata = admin_event.get("metadata") or {}
        return {
            "admin_event_id": admin_event["id"],
            "admin_raw_log_id": admin_event.get("admin_raw_log_id"),
            "admin_raw_message": admin_event.get("admin_raw_message") or admin_event.get("summary") or "",
            "admin_group_id": admin_event.get("admin_group_id"),
            "admin_title": admin_event.get("admin_title") or "Administrative shutdown observed",
            "admin_summary": admin_event.get("admin_summary") or admin_event.get("summary") or "",
            "admin_severity": admin_event.get("admin_severity") or "info",
            "admin_correlation_key": admin_event.get("admin_correlation_key") or "",
            "admin_event_family": admin_event.get("admin_event_family") or "interface",
            "admin_metadata": admin_metadata,
            "admin_device_id": admin_event.get("device_id"),
            "admin_source_ip": admin_event.get("admin_source_ip") or admin_metadata.get("device_mgmt_ip") or "",
            "link_id": link_id,
            "topology_confidence": metadata.get("topology_confidence")
            or admin_metadata.get("topology_confidence")
            or "high",
            "root_host": admin_event.get("admin_hostname")
            or admin_metadata.get("device_hostname")
            or admin_metadata.get("root_host")
            or "",
            "root_interface": admin_metadata.get("interface")
            or admin_metadata.get("local_interface")
            or admin_metadata.get("root_interface")
            or "",
            "remote_host": metadata.get("device_hostname")
            or device.get("hostname")
            or metadata.get("remote_host")
            or admin_metadata.get("remote_hostname")
            or "",
            "remote_interface": metadata.get("interface")
            or metadata.get("local_interface")
            or admin_metadata.get("remote_interface")
            or "",
        }

    def _apply_linked_admin_down_correlation(
        self,
        cur: psycopg.Cursor[Any],
        *,
        event: dict[str, Any],
        raw_log: dict[str, Any],
        group: dict[str, Any],
        device: dict[str, Any] | None,
        admin_context: dict[str, Any],
    ) -> int:
        relation_group_key = (
            _relation_group_key_from_metadata(event.get("metadata") or {})
            or f"{_RELATED_INCIDENT_PREFIX}{admin_context['link_id']}"
        )
        existing_owner = self._find_relation_owner_incident(cur, relation_group_key=relation_group_key)
        existing_owner_metadata = _incident_metadata(existing_owner)
        intent_status = existing_owner_metadata.get("intent_status")
        if intent_status not in {"confirmed_intentional", "confirmed_unintentional"}:
            intent_status = "needs_confirmation"

        owner_metadata = {
            **existing_owner_metadata,
            **(admin_context.get("admin_metadata") or {}),
            "intent_status": intent_status,
            "topology_confidence": admin_context.get("topology_confidence") or "",
            "link_id": admin_context["link_id"],
            "root_host": admin_context.get("root_host") or "",
            "root_interface": admin_context.get("root_interface") or "",
            "remote_host": admin_context.get("remote_host") or "",
            "remote_interface": admin_context.get("remote_interface") or "",
            "cause_hint": "linked_admin_down",
            "operator_initiated_hint": True,
        }
        admin_device = self._find_device(
            cur,
            source_ip=admin_context.get("admin_source_ip") or owner_metadata.get("device_mgmt_ip") or "",
            hostname=admin_context.get("root_host") or None,
        )
        admin_incident = self._upsert_symptom_incident(
            cur,
            title=admin_context.get("admin_title") or "Administrative shutdown observed",
            severity=admin_context.get("admin_severity") or "info",
            category="config-related",
            summary=admin_context.get("admin_summary") or admin_context.get("admin_raw_message") or "Administrative shutdown observed on the linked interface.",
            probable_cause=_linked_admin_down_cause(owner_metadata),
            primary_source_ip=admin_context.get("admin_source_ip") or owner_metadata.get("root_mgmt_ip") or "",
            correlation_key=admin_context.get("admin_correlation_key") or f"{admin_context['link_id']}|admin_down",
            event_family=admin_context.get("admin_event_family") or "interface",
            current_recovery_state="watching",
            metadata=owner_metadata,
            device=admin_device,
            reopened=False,
            workflow_phase="intent_confirmation_required" if intent_status == "needs_confirmation" else _incident_workflow_phase(existing_owner),
            relation_group_key=relation_group_key,
        )
        cur.execute(
            """
            UPDATE incidents
            SET remediation_owner_incident_id = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (admin_incident["id"], admin_incident["id"]),
        )
        admin_incident["remediation_owner_incident_id"] = admin_incident["id"]

        peer_metadata = {
            **(event.get("metadata") or {}),
            "link_id": admin_context["link_id"],
            "topology_confidence": admin_context.get("topology_confidence") or "",
            "root_host": admin_context.get("root_host") or "",
            "root_interface": admin_context.get("root_interface") or "",
            "remote_host": admin_context.get("remote_host") or "",
            "remote_interface": admin_context.get("remote_interface") or "",
            "cause_hint": "linked_admin_down",
            "relation_reason": "peer_admin_shutdown",
            "related_admin_incident_id": admin_incident["id"],
            "related_admin_incident_no": admin_incident.get("incident_no"),
        }
        current_incident = self._upsert_symptom_incident(
            cur,
            title=event["title"],
            severity=event["severity"],
            category="config-related",
            summary=event["summary"],
            probable_cause=_linked_admin_down_cause(owner_metadata),
            primary_source_ip=device["ip_address"] if device else raw_log["source_ip"],
            correlation_key=event["correlation_key"],
            event_family=event["event_family"],
            current_recovery_state="watching",
            metadata=peer_metadata,
            device=device,
            reopened=False,
            relation_group_key=relation_group_key,
            remediation_owner_incident_id=admin_incident["id"],
        )

        self._link_group_events_to_incident(cur, incident_id=current_incident["id"], group_id=group["id"])
        self._link_event_to_incident(cur, incident_id=admin_incident["id"], event_id=admin_context["admin_event_id"])
        self._update_incident_event_count(cur, incident_id=current_incident["id"])
        self._update_incident_event_count(cur, incident_id=admin_incident["id"])

        cur.execute(
            """
            UPDATE raw_logs
            SET parse_status = 'llm_decided'
            WHERE id IN (
                SELECT rl.id
                FROM raw_logs rl
                JOIN events e ON e.raw_log_id = rl.id
                JOIN candidate_group_events cge ON cge.event_id = e.id
                WHERE cge.candidate_group_id = %s
            )
            """,
            (group["id"],),
        )
        if admin_context.get("admin_raw_log_id") is not None:
            cur.execute(
                "UPDATE raw_logs SET parse_status = 'llm_decided' WHERE id = %s",
                (admin_context["admin_raw_log_id"],),
            )

        cur.execute(
            """
            UPDATE candidate_groups
            SET linked_incident_id = %s,
                last_decision_at = NOW(),
                last_decision_event_count = %s,
                decision_status = 'idle',
                decision_requested_at = NULL,
                decision_locked_at = NULL,
                status = 'open',
                updated_at = NOW()
            WHERE id = %s
            """,
            (current_incident["id"], group["event_count"], group["id"]),
        )
        if admin_context.get("admin_group_id") is not None:
            cur.execute(
                """
                UPDATE candidate_groups
                SET linked_incident_id = %s,
                    decision_status = 'idle',
                    decision_requested_at = NULL,
                    decision_locked_at = NULL,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (admin_incident["id"], admin_context["admin_group_id"]),
            )

        self._record_timeline(
            cur,
            current_incident["id"],
            "decision",
            "Related incident linked to peer admin shutdown",
            _linked_admin_down_summary(owner_metadata),
            {
                "cause_hint": "linked_admin_down",
                "link_id": admin_context["link_id"],
                "root_host": owner_metadata.get("root_host"),
                "root_interface": owner_metadata.get("root_interface"),
                "remote_host": owner_metadata.get("remote_host"),
                "remote_interface": owner_metadata.get("remote_interface"),
                "remediation_owner_incident_id": admin_incident["id"],
                "remediation_owner_incident_no": admin_incident.get("incident_no"),
            },
        )
        self._record_timeline(
            cur,
            admin_incident["id"],
            "event",
            "Peer impact correlated",
            event["summary"],
            {
                "event_id": event["id"],
                "link_id": admin_context["link_id"],
                "related_incident_id": current_incident["id"],
                "related_incident_no": current_incident.get("incident_no"),
            },
        )
        self._record_timeline(
            cur,
            admin_incident["id"],
            "event",
            "Administrative shutdown observed",
            admin_context.get("admin_raw_message") or "Linked peer reported an administrative shutdown.",
            {
                "event_id": admin_context["admin_event_id"],
                "link_id": admin_context["link_id"],
                "event_state": "admin_down",
            },
        )
        self._record_timeline(
            cur,
            current_incident["id"],
            "event",
            "Related admin-down incident",
            (
                f"Administrative shutdown on {owner_metadata.get('root_host') or 'peer'} "
                f"{owner_metadata.get('root_interface') or 'interface'} is being tracked separately as "
                f"{admin_incident.get('incident_no') or 'the owning incident'}."
            ),
            {
                "related_incident_id": admin_incident["id"],
                "related_incident_no": admin_incident.get("incident_no"),
            },
        )
        self._record_timeline(
            cur,
            current_incident["id"],
            "event",
            event["title"],
            event["summary"],
            {
                "event_id": event["id"],
                "link_id": admin_context["link_id"],
            },
        )
        return current_incident["id"]

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
            # Refresh recovery lifecycle so wall-clock-based transitions do not
            # depend on a fresh syslog arriving.
            self._refresh_time_based_incident_states()
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

    def _promote_recovering_system_incidents(self) -> int:
        """Move boot-progressing system incidents from recovering to monitoring."""
        settle_secs = int(os.getenv("AIOPS_SYSTEM_BOOT_MONITORING_SECONDS", str(_SYSTEM_BOOT_MONITORING_SECONDS)))
        allowed_workflow_phases = list(_TIME_BASED_AUTO_ADVANCE_WORKFLOW_PHASES)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE incidents
                    SET status = 'monitoring',
                        current_recovery_state = 'monitoring',
                        updated_at = NOW()
                    WHERE id IN (
                        SELECT DISTINCT i.id
                        FROM incidents i
                        JOIN incident_events ie ON ie.incident_id = i.id
                        JOIN events e ON e.id = ie.event_id
                        LEFT JOIN raw_logs rl ON rl.id = e.raw_log_id
                        WHERE i.status = 'recovering'
                          AND COALESCE(i.suppressed_in_list, FALSE) = FALSE
                          AND COALESCE(i.metadata->>'deprecated_root', 'false') <> 'true'
                          AND COALESCE(i.workflow_phase, 'none') = ANY(%s)
                          AND i.event_family = 'system'
                          AND i.current_recovery_state = 'signal_detected'
                          AND i.last_seen_at < NOW() - (interval '1 second' * %s)
                          AND (
                                UPPER(COALESCE(e.metadata->>'mnemonic', '')) IN (
                                    'COLDSTART',
                                    'LOGGINGHOST_STARTSTOP',
                                    'SIGNATURE_VERIFIED'
                                )
                                OR COALESCE(rl.raw_message, '') ILIKE '%%code signing verification%%'
                                OR COALESCE(rl.raw_message, '') ILIKE '%%logging to host%%started%%'
                          )
                    )
                    RETURNING id, incident_no
                    """,
                    (allowed_workflow_phases, settle_secs),
                )
                promoted = cur.fetchall()
                for row in promoted:
                    self._record_timeline(
                        cur,
                        row["id"],
                        "recovery",
                        "Auto-advanced to monitoring",
                        (
                            "Observed boot-progress signals after the restart and no new failure "
                            f"for {max(1, settle_secs // 60)} minute(s), so the incident moved to monitoring."
                        ),
                        {"boot_settle_seconds": settle_secs},
                    )
            conn.commit()
        if promoted:
            logger.info("Promoted %d recovering system incident(s) to monitoring", len(promoted))
            for row in promoted:
                try:
                    self.refresh_incident_summary(row["id"])
                except Exception as exc:
                    logger.warning("AIOps summary refresh failed for promoted incident %s: %s", row["incident_no"], exc)
        return len(promoted)

    def _promote_recovering_signal_incidents(self) -> int:
        """Move non-system recovery-signal incidents from recovering to monitoring."""
        settle_secs = int(
            os.getenv(
                "AIOPS_RECOVERY_SIGNAL_MONITORING_SECONDS",
                str(_RECOVERY_SIGNAL_MONITORING_SECONDS),
            )
        )
        allowed_workflow_phases = list(_TIME_BASED_AUTO_ADVANCE_WORKFLOW_PHASES)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE incidents
                    SET status = 'monitoring',
                        current_recovery_state = 'monitoring',
                        updated_at = NOW()
                    WHERE status = 'recovering'
                      AND COALESCE(suppressed_in_list, FALSE) = FALSE
                      AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
                      AND COALESCE(workflow_phase, 'none') = ANY(%s)
                      AND event_family <> 'system'
                      AND current_recovery_state = 'signal_detected'
                      AND last_seen_at < NOW() - (interval '1 second' * %s)
                    RETURNING id, incident_no
                    """,
                    (allowed_workflow_phases, settle_secs),
                )
                promoted = cur.fetchall()
                for row in promoted:
                    self._record_timeline(
                        cur,
                        row["id"],
                        "recovery",
                        "Auto-advanced to monitoring",
                        (
                            "Observed a recovery signal and no repeat failure "
                            f"for {max(1, settle_secs // 60)} minute(s), so the incident moved to monitoring."
                        ),
                        {"recovery_settle_seconds": settle_secs},
                    )
            conn.commit()
        if promoted:
            logger.info("Promoted %d recovering signal incident(s) to monitoring", len(promoted))
            for row in promoted:
                try:
                    self.refresh_incident_summary(row["id"])
                except Exception as exc:
                    logger.warning("AIOps summary refresh failed for promoted incident %s: %s", row["incident_no"], exc)
        return len(promoted)

    def _refresh_time_based_incident_states(self) -> dict[str, int]:
        """Apply time-based lifecycle transitions before reads and pipeline work."""
        promoted_system = self._promote_recovering_system_incidents()
        promoted_signals = self._promote_recovering_signal_incidents()
        resolved = self._auto_resolve_stable_incidents()
        return {
            "promoted_system": promoted_system,
            "promoted_signals": promoted_signals,
            "resolved": resolved,
            "synced_roots": 0,
        }

    def _auto_resolve_stable_incidents(self) -> int:
        """Auto-resolve incidents that have remained stable in monitoring."""
        stability_secs = int(
            os.getenv(
                "AIOPS_RECOVERY_STABILITY_SECONDS",
                str(_RECOVERY_STABILITY_SECONDS),
            )
        )
        allowed_workflow_phases = list(_TIME_BASED_AUTO_ADVANCE_WORKFLOW_PHASES)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE incidents
                    SET status = 'resolved',
                        resolution_type = COALESCE(resolution_type, 'auto_recovered'),
                        resolved_at = NOW(),
                        current_recovery_state = 'recovered',
                        workflow_phase = 'none',
                        current_proposal_id = NULL,
                        updated_at = NOW()
                    WHERE status = 'monitoring'
                      AND COALESCE(suppressed_in_list, FALSE) = FALSE
                      AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
                      AND COALESCE(workflow_phase, 'none') = ANY(%s)
                      AND last_seen_at < NOW() - (interval '1 second' * %s)
                    RETURNING id, incident_no
                    """,
                    (allowed_workflow_phases, stability_secs),
                )
                resolved = cur.fetchall()
                if resolved:
                    resolved_ids = [row["id"] for row in resolved]
                    # Cancel any pending/approved proposals whose incident just resolved
                    cur.execute(
                        """
                        UPDATE proposals
                        SET status = 'cancelled',
                            cancelled_reason = 'incident_auto_resolved'
                        WHERE incident_id = ANY(%s)
                          AND status IN ('pending', 'approved')
                        """,
                        (resolved_ids,),
                    )
                for row in resolved:
                    self._record_timeline(
                        cur,
                        row["id"],
                        "recovery",
                        "Auto-resolved after stability window",
                        f"Incident remained stable in monitoring for {stability_secs // 60} minutes, so it was auto-closed.",
                        {"stability_seconds": stability_secs},
                    )
            conn.commit()
        if resolved:
            logger.info("Auto-resolved %d incident(s) after stability window", len(resolved))
            for row in resolved:
                try:
                    self.refresh_incident_summary(row["id"])
                except Exception as exc:
                    logger.warning("AIOps summary refresh failed for resolved incident %s: %s", row["incident_no"], exc)
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
            summary_refresh_incident_id: int | None = None
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
                        # Noise / boot banner — parser already decided to discard
                        cur.execute(
                            "UPDATE raw_logs SET parse_status = 'noise' WHERE id = %s",
                            (raw_log["id"],),
                        )
                        conn.commit()
                        self._complete_job(job["id"], status="completed")
                        return True
                    parsed = self._enrich_parsed_event_metadata(parsed=parsed, device=device)
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
                                **({
                                    "link_id": event["metadata"].get("link_id"),
                                    "topology_confidence": event["metadata"].get("topology_confidence"),
                                } if event["metadata"].get("link_id") else {}),
                            }),
                            raw_log["id"],
                        ),
                    )
                    admin_context = self._find_recent_linked_admin_down(
                        cur,
                        event=event,
                        raw_log=raw_log,
                        device=device,
                    )
                    # Tracking/IP SLA "down" events: merge into a related EIGRP/tunnel/interface
                    # incident if one occurred recently on the same device, to avoid a
                    # duplicate incident for what is effectively the same path failure.
                    # "up" events always use the normal recovery path so the tracking incident
                    # itself gets its own recovery signal (not just the EIGRP/tunnel incident).
                    if not event["metadata"].get("eligible_for_standalone_incident", True):
                        cur.execute(
                            "UPDATE candidate_groups SET decision_status = 'idle', updated_at = NOW() WHERE id = %s",
                            (group["id"],),
                        )
                    elif admin_context is not None:
                        summary_refresh_incident_id = self._apply_linked_admin_down_correlation(
                            cur,
                            event=event,
                            raw_log=raw_log,
                            group=group,
                            device=device,
                            admin_context=admin_context,
                        )
                    elif event["event_family"] == "tracking" and event["event_state"] != "up":
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
            if summary_refresh_incident_id is not None:
                try:
                    self.refresh_incident_summary(summary_refresh_incident_id)
                except Exception as summary_exc:
                    logger.warning(
                        "AIOps summary refresh failed for linked admin-down incident %s: %s",
                        summary_refresh_incident_id,
                        summary_exc,
                    )
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
            SELECT *
            FROM incidents
            WHERE correlation_key = %s
              AND COALESCE(suppressed_in_list, FALSE) = FALSE
              AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
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

        next_resolution_type = "verified_recovery" if _incident_workflow_phase(incident) in {"approved_to_execute", "awaiting_verification"} else (incident.get("resolution_type") or "auto_recovered")
        cur.execute(
            """
            UPDATE incidents
            SET status = 'resolved',
                workflow_phase = 'none',
                current_recovery_state = 'recovered',
                resolution_type = %s,
                current_proposal_id = NULL,
                resolved_at = NOW(),
                last_seen_at = NOW(),
                updated_at = NOW()
            WHERE id = %s
            RETURNING *
            """,
            (next_resolution_type, incident["id"]),
        )
        updated_incident = cur.fetchone()
        self._link_group_events_to_incident(cur, incident_id=updated_incident["id"], group_id=group["id"])
        self._update_incident_event_count(cur, incident_id=updated_incident["id"])
        cur.execute(
            """
            UPDATE proposals
            SET status = 'cancelled',
                cancelled_reason = 'incident_auto_resolved'
            WHERE incident_id = %s
              AND status IN ('pending', 'approved')
            """,
            (updated_incident["id"],),
        )
        self._record_timeline(
            cur,
            updated_incident["id"],
            "recovery",
            "Recovery signal resolved incident",
            f"Received positive recovery evidence ({event['event_state']}): {raw_log['raw_message'][:120]}",
            {"event_id": event["id"], "event_state": event["event_state"], "resolution_type": next_resolution_type},
        )
        linked_incident_id = updated_incident["id"]

        # Link raw log to incident
        cur.execute(
            "UPDATE raw_logs SET parse_status = 'llm_decided' WHERE id = %s",
            (raw_log["id"],),
        )
        # Mark group idle — no LLM needed
        cur.execute(
            """
            UPDATE candidate_groups
            SET linked_incident_id = %s,
                decision_status = 'idle',
                decision_requested_at = NULL,
                decision_locked_at = NULL,
                updated_at = NOW()
            WHERE id = %s
            """,
            (linked_incident_id, group["id"]),
        )
        logger.info("Recovery signal resolved %s", updated_incident["incident_no"])

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
              AND COALESCE(suppressed_in_list, FALSE) = FALSE
              AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
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

    # ── Interface description cache ──────────────────────────────────────────────

    # How long (seconds) to trust a cached interface description before re-fetching.
    _INTF_DESC_CACHE_TTL: int = 600  # 10 minutes

    def _get_interface_description_cached(
        self,
        cur: psycopg.Cursor[Any],
        device_id: int,
        interface_name: str,
    ) -> tuple[str | None, bool]:
        """Return (description, cache_fresh).

        cache_fresh=False means the cache is missing or stale — caller should re-fetch.
        """
        cur.execute(
            """
            SELECT description, refreshed_at
            FROM interface_descriptions
            WHERE device_id = %s
              AND lower(interface_name) = lower(%s)
            """,
            (device_id, interface_name),
        )
        row = cur.fetchone()
        if row is None:
            return None, False
        age = (datetime.now(timezone.utc) - row["refreshed_at"]).total_seconds()
        return row["description"], age < self._INTF_DESC_CACHE_TTL

    def _refresh_interface_descriptions(
        self,
        device: dict[str, Any],
    ) -> dict[str, str]:
        """SSH to device, run 'show interfaces description', parse and persist to DB.

        Returns dict {interface_name: description}. Never raises — errors return {}.
        """
        hostname = device.get("hostname", "")
        ip_address = device.get("ip_address", "")
        os_platform = device.get("os_platform", "cisco_ios")
        device_id = device.get("id")
        if not ip_address or not device_id:
            return {}
        try:
            from src.tools.cli_tool import create_run_cli_tool
            device_cache = {
                hostname: {
                    "ip_address": ip_address,
                    "os_platform": os_platform,
                    "device_role": device.get("device_role", ""),
                    "site": device.get("site", ""),
                }
            }
            run_cli = create_run_cli_tool(device_cache)
            raw_output = str(run_cli.invoke({"host": hostname, "command": "show interfaces description"}))
        except Exception as exc:
            logger.warning("refresh_interface_descriptions SSH failed for %s: %s", hostname, exc)
            return {}

        # Parse 'show interfaces description' output:
        # Interface   Status    Protocol  Description
        # Gi0/0       up        up        TO-PROXMOX-LAN
        desc_map: dict[str, dict[str, str]] = {}
        import re as _re
        for line in raw_output.splitlines():
            # Match: <intf>   <status>   <protocol>   [description]
            m = _re.match(
                r"^(\S+)\s+(admin\s+down|up|down)\s+(up|down|-)\s*(.*)?$",
                line.strip(),
                _re.IGNORECASE,
            )
            if m:
                intf = m.group(1)
                status = m.group(2).strip().lower()
                protocol = m.group(3).strip().lower()
                desc = m.group(4).strip() if m.group(4) else ""
                desc_map[intf] = {"status": status, "protocol": protocol, "description": desc}

        if not desc_map:
            return {}

        # Persist to DB
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    for intf, info in desc_map.items():
                        cur.execute(
                            """
                            INSERT INTO interface_descriptions
                                (device_id, interface_name, description, status, protocol, refreshed_at)
                            VALUES (%s, %s, %s, %s, %s, NOW())
                            ON CONFLICT (device_id, interface_name)
                            DO UPDATE SET
                                description  = EXCLUDED.description,
                                status       = EXCLUDED.status,
                                protocol     = EXCLUDED.protocol,
                                refreshed_at = NOW()
                            """,
                            (device_id, intf, info["description"], info["status"], info["protocol"]),
                        )
                conn.commit()
        except Exception as exc:
            logger.warning("Failed to persist interface_descriptions for %s: %s", hostname, exc)

        return {intf: info["description"] for intf, info in desc_map.items()}

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
                          AND COALESCE(i.suppressed_in_list, FALSE) = FALSE
                          AND COALESCE(i.metadata->>'deprecated_root', 'false') <> 'true'
                        ORDER BY i.last_seen_at DESC
                        LIMIT 20
                        """,
                        (list(_OPEN_INCIDENT_STATUSES),),
                    )
                    open_incidents = cur.fetchall()

                    # ── Interface description context for LLM ────────────────
                    # For interface events, look up the cached description so the
                    # LLM can judge business impact (e.g. "unused" → ignore).
                    interface_desc_context: str | None = None
                    if group.get("event_family") == "interface" and device:
                        latest_event = events[0] if events else {}
                        intf_name = (latest_event.get("metadata") or {}).get("interface")
                        if intf_name:
                            cached_desc, fresh = self._get_interface_description_cached(
                                cur, device["id"], intf_name
                            )
                            if not fresh:
                                # Refresh in background; use stale value if available
                                import threading as _threading
                                _threading.Thread(
                                    target=self._refresh_interface_descriptions,
                                    args=(device,),
                                    daemon=True,
                                ).start()
                            if cached_desc is not None:
                                interface_desc_context = (
                                    f"Interface {intf_name} description from device: '{cached_desc}'"
                                )

                    decision = decide_incident_bundle(
                        candidate_group=group,
                        events=events,
                        raw_logs=raw_logs,
                        device=device,
                        open_incidents=open_incidents,
                        interface_desc_context=interface_desc_context,
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
        if _is_test_runtime():
            return
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT incident_no, status, workflow_phase, severity FROM incidents WHERE id = %s",
                    (incident_id,),
                )
                row = cur.fetchone()
        if row is None or row["incident_no"] is None:
            return
        if row["status"] != "active":
            return
        if row.get("workflow_phase") in {"remediation_available", "approved_to_execute", "awaiting_verification"}:
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
                  AND COALESCE(suppressed_in_list, FALSE) = FALSE
                  AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
                  AND status = ANY(%s)
                ORDER BY opened_at DESC
                LIMIT 1
                """,
                (decision["correlation_key"], list(_OPEN_INCIDENT_STATUSES)),
            )
            incident = cur.fetchone()

        relation_group_key = _relation_group_key_from_metadata({
            **(latest_event.get("metadata") if latest_event else {}),
            **(decision.get("metadata") or {}),
        })
        relation_owner = self._find_relation_owner_incident(cur, relation_group_key=relation_group_key)

        if latest_event is not None and decision["action"] != "ignore" and self._should_use_topology_root(latest_event):
            topology_metadata = {
                **(latest_event.get("metadata") or {}),
                **(decision.get("metadata") or {}),
            }
            link_id = str(topology_metadata.get("link_id") or "").strip()
            if link_id:
                relation_group_key = _relation_group_key_from_metadata(topology_metadata)
                relation_owner = self._find_relation_owner_incident(cur, relation_group_key=relation_group_key)
                related_incident = self._upsert_symptom_incident(
                    cur,
                    title=decision["title"],
                    severity=decision["severity"],
                    category=decision.get("category") or "unknown",
                    summary=decision["summary"],
                    probable_cause=decision.get("reasoning") or decision["summary"],
                    primary_source_ip=device["ip_address"] if device else group["source_ip"],
                    correlation_key=decision["correlation_key"],
                    event_family=decision["event_family"],
                    current_recovery_state="signal_detected" if decision["event_state"] in _RECOVERY_EVENT_STATES else "watching",
                    metadata=topology_metadata,
                    device=device,
                    reopened=False,
                    relation_group_key=relation_group_key,
                    remediation_owner_incident_id=relation_owner["id"] if relation_owner else None,
                )
                self._link_group_events_to_incident(cur, incident_id=related_incident["id"], group_id=group["id"])
                self._update_incident_event_count(cur, incident_id=related_incident["id"])
                self._record_timeline(
                    cur,
                    related_incident["id"],
                    "decision",
                    "Topology relation detected",
                    decision.get("reasoning", "A high-confidence link context was detected, so this incident was related to the other incidents on the same path."),
                    {
                        "action": decision["action"],
                        "candidate_group_id": group["id"],
                        "relation_group_key": relation_group_key,
                        "remediation_owner_incident_id": relation_owner["id"] if relation_owner else None,
                        "link_id": link_id,
                    },
                )
                self._record_timeline(
                    cur,
                    related_incident["id"],
                    "event",
                    latest_event["title"],
                    latest_event["summary"],
                    {
                        "event_state": latest_event["event_state"],
                        "event_family": latest_event["event_family"],
                    },
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
                        related_incident["id"],
                        decision["action"],
                        related_incident.get("incident_no"),
                        decision["title"],
                        decision["event_family"],
                        decision["event_state"],
                        decision["severity"],
                        decision["summary"],
                        decision["correlation_key"],
                        decision.get("category") or "unknown",
                        decision.get("reasoning", ""),
                        Json(topology_metadata),
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
                        status = 'open',
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        related_incident["id"],
                        claimed_event_count,
                        claimed_event_count,
                        claimed_event_count,
                        datetime.now(timezone.utc) + _GROUP_DECISION_DEBOUNCE,
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
                return related_incident["id"]

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

            refault_after_recovery = bool(
                incident
                and decision["event_state"] not in _RECOVERY_EVENT_STATES
                and incident["status"] in _POST_RECOVERY_STATUSES
            )
            current_workflow_phase = _incident_workflow_phase(incident)
            if decision["event_state"] in _RECOVERY_EVENT_STATES:
                next_status = "recovering"
                next_workflow_phase = current_workflow_phase
            elif incident:
                next_status = "active"
                next_workflow_phase = _fault_workflow_phase(incident)
            else:
                next_status = "active"
                next_workflow_phase = "none"
            recovery_state = "signal_detected" if decision["event_state"] in _RECOVERY_EVENT_STATES else "watching"
            if incident:
                cur.execute(
                    """
                    UPDATE incidents
                    SET title = %s,
                        severity = %s,
                        category = COALESCE(%s, category),
                        status = %s,
                        workflow_phase = %s,
                        relation_group_key = COALESCE(%s, relation_group_key),
                        remediation_owner_incident_id = COALESCE(%s, remediation_owner_incident_id),
                        current_recovery_state = %s,
                        event_count = %s,
                        site = %s,
                        primary_device_id = COALESCE(%s, primary_device_id),
                        primary_source_ip = %s,
                        resolution_type = CASE WHEN %s THEN NULL ELSE resolution_type END,
                        resolved_at = CASE WHEN %s THEN NULL ELSE resolved_at END,
                        reopened_count = CASE WHEN %s THEN reopened_count + 1 ELSE reopened_count END,
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
                        next_workflow_phase,
                        relation_group_key,
                        relation_owner["id"] if relation_owner else None,
                        recovery_state,
                        group["event_count"],
                        device["site"] if device else "",
                        device["id"] if device else None,
                        device["ip_address"] if device else group["source_ip"],
                        refault_after_recovery,
                        refault_after_recovery,
                        refault_after_recovery,
                        incident["id"],
                    ),
                )
                incident_row = cur.fetchone()
            else:
                cur.execute(
                    """
                    INSERT INTO incidents (
                        title, status, workflow_phase, severity, category, summary, primary_source_ip, correlation_key,
                        event_family, event_count, site, primary_device_id, current_recovery_state,
                        relation_group_key, remediation_owner_incident_id, opened_at, last_seen_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    RETURNING *
                    """,
                    (
                        decision["title"],
                        next_status,
                        next_workflow_phase,
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
                        relation_group_key,
                        relation_owner["id"] if relation_owner else None,
                    ),
                )
                incident_row = cur.fetchone()
                incident_row["incident_no"] = self._next_incident_no(cur, incident_row["id"])
                cur.execute(
                    "UPDATE incidents SET root_incident_id = id, remediation_owner_incident_id = COALESCE(remediation_owner_incident_id, %s) WHERE id = %s",
                    ((relation_owner["id"] if relation_owner else incident_row["id"]), incident_row["id"]),
                )
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
            "workflow_phase": incident_row.get("workflow_phase") or "none",
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
            "metadata": incident_row.get("metadata") or {},
        }
        summary = generate_ai_summary(incident_payload, raw_logs)
        if incident_payload["metadata"].get("cause_hint") == "linked_admin_down":
            summary["category"] = "config-related"
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

    @staticmethod
    def _resolved_raw_log_hostname_sql(raw_alias: str, device_alias: str) -> str:
        return (
            "COALESCE("
            f"NULLIF(NULLIF(BTRIM({raw_alias}.hostname), ''), {raw_alias}.source_ip), "
            f"{device_alias}.hostname, "
            f"{raw_alias}.source_ip"
            ")"
        )

    def _fetch_open_incidents(self, *, include_resolved: bool) -> list[dict[str, Any]]:
        where_clauses = [
            "COALESCE(i.suppressed_in_list, FALSE) = FALSE",
            "COALESCE(i.metadata->>'deprecated_root', 'false') <> 'true'",
        ]
        params: list[Any] = []
        if not include_resolved:
            where_clauses.append("i.status <> ALL(%s)")
            params.append(["resolved", "closed"])
        where_clause = f"WHERE {' AND '.join(where_clauses)}"
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        i.*,
                        d.hostname AS primary_hostname,
                        (
                            SELECT COUNT(*)
                            FROM incidents related
                            WHERE related.relation_group_key = i.relation_group_key
                              AND related.id <> i.id
                              AND COALESCE(related.suppressed_in_list, FALSE) = FALSE
                              AND COALESCE(related.metadata->>'deprecated_root', 'false') <> 'true'
                        ) AS child_count,
                        (
                            SELECT COUNT(*)
                            FROM incidents related
                            WHERE related.relation_group_key = i.relation_group_key
                              AND related.id <> i.id
                              AND COALESCE(related.suppressed_in_list, FALSE) = FALSE
                              AND COALESCE(related.metadata->>'deprecated_root', 'false') <> 'true'
                              AND related.status = ANY(%s)
                        ) AS active_child_count
                    FROM incidents i
                    LEFT JOIN devices d ON d.id = i.primary_device_id
                    {where_clause}
                    ORDER BY i.last_seen_at DESC
                    """,
                    [list(_OPEN_INCIDENT_STATUSES), *params],
                )
                return cur.fetchall()

    def dashboard(self) -> dict[str, Any]:
        self._refresh_time_based_incident_states()
        incidents = self._fetch_open_incidents(include_resolved=False)
        history = self.history(limit=20)
        approvals = self.approvals(limit=20)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM incidents
                    WHERE status = 'active'
                      AND COALESCE(suppressed_in_list, FALSE) = FALSE
                      AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
                    """,
                )
                active = cur.fetchone()["count"]
                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM incidents
                    WHERE status IN ('recovering', 'monitoring')
                      AND COALESCE(suppressed_in_list, FALSE) = FALSE
                      AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
                    """,
                )
                recovering = cur.fetchone()["count"]
                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM incidents
                    WHERE status = 'resolved'
                      AND resolved_at >= NOW() - INTERVAL '1 day'
                      AND COALESCE(suppressed_in_list, FALSE) = FALSE
                      AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
                    """,
                )
                resolved_today = cur.fetchone()["count"]
                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM incidents
                    WHERE reopened_count > 0
                      AND last_seen_at >= NOW() - INTERVAL '7 day'
                      AND COALESCE(suppressed_in_list, FALSE) = FALSE
                      AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
                    """,
                )
                reopened = cur.fetchone()["count"]
        return {
            "metrics": {
                "active_incidents": active,
                "recovering_incidents": recovering,
                "pending_approvals": sum(1 for p in approvals if p["status"] in ("pending", "approved")),
                "resolved_today": resolved_today,
                "reopened_this_week": reopened,
            },
            "incidents": incidents[:8],
            "approvals": approvals[:6],
            "history": history[:6],
        }

    def incidents(self) -> list[dict[str, Any]]:
        self._refresh_time_based_incident_states()
        return self._fetch_open_incidents(include_resolved=False)

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        i.*,
                        d.hostname AS primary_hostname,
                        (
                            SELECT COUNT(*)
                            FROM incidents related
                            WHERE related.relation_group_key = i.relation_group_key
                              AND related.id <> i.id
                              AND COALESCE(related.suppressed_in_list, FALSE) = FALSE
                              AND COALESCE(related.metadata->>'deprecated_root', 'false') <> 'true'
                        ) AS child_count,
                        (
                            SELECT COUNT(*)
                            FROM incidents related
                            WHERE related.relation_group_key = i.relation_group_key
                              AND related.id <> i.id
                              AND COALESCE(related.suppressed_in_list, FALSE) = FALSE
                              AND COALESCE(related.metadata->>'deprecated_root', 'false') <> 'true'
                              AND related.status = ANY(%s)
                        ) AS active_child_count
                    FROM incidents i
                    LEFT JOIN devices d ON d.id = i.primary_device_id
                    WHERE i.status IN ('resolved', 'closed')
                      AND COALESCE(i.suppressed_in_list, FALSE) = FALSE
                      AND COALESCE(i.metadata->>'deprecated_root', 'false') <> 'true'
                    ORDER BY COALESCE(i.resolved_at, i.updated_at) DESC
                    LIMIT %s
                    """,
                    (list(_OPEN_INCIDENT_STATUSES), limit),
                )
                return cur.fetchall()

    def logs(
        self,
        incident_no: str | None = None,
        limit: int = 200,
        device: str | None = None,
        hours_back: int | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        with connect() as conn:
            with conn.cursor() as cur:
                # Resolve device hostname → IP
                device_ip: str | None = None
                if device:
                    cur.execute(
                        "SELECT ip_address FROM devices WHERE lower(hostname) = lower(%s) OR ip_address = %s LIMIT 1",
                        (device, device),
                    )
                    row = cur.fetchone()
                    if row:
                        device_ip = row["ip_address"]

                if incident_no:
                    conditions = ["i.incident_no = %s"]
                    params: list = [incident_no]
                    if device_ip:
                        conditions.append("rl.source_ip = %s")
                        params.append(device_ip)
                    if hours_back:
                        conditions.append("rl.received_at >= NOW() - (%s || ' hours')::interval")
                        params.append(str(hours_back))
                    if keyword:
                        conditions.append("rl.raw_message ILIKE %s")
                        params.append(f"%{keyword}%")
                    where = " AND ".join(conditions)
                    cur.execute(
                        f"""
                        SELECT
                            rl.id,
                            rl.source_ip,
                            {self._resolved_raw_log_hostname_sql("rl", "d_src")} AS hostname,
                            rl.raw_message,
                            rl.event_time,
                            rl.received_at,
                            rl.parse_status,
                            rl.metadata,
                            i.incident_no
                        FROM raw_logs rl
                        LEFT JOIN devices d_src ON d_src.ip_address = rl.source_ip
                        JOIN events e ON e.raw_log_id = rl.id
                        JOIN incident_events ie ON ie.event_id = e.id
                        JOIN incidents i ON i.id = ie.incident_id
                        WHERE {where}
                        ORDER BY rl.received_at DESC
                        LIMIT %s
                        """,
                        params + [limit],
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
                    conditions = []
                    params = []
                    if device_ip:
                        conditions.append("rl.source_ip = %s")
                        params.append(device_ip)
                    if hours_back:
                        conditions.append("rl.received_at >= NOW() - (%s || ' hours')::interval")
                        params.append(str(hours_back))
                    if keyword:
                        conditions.append("rl.raw_message ILIKE %s")
                        params.append(f"%{keyword}%")
                    extra_where = ("AND " + " AND ".join(conditions)) if conditions else ""
                    cur.execute(
                        f"""
                        SELECT * FROM (
                            SELECT
                                rl.id,
                                rl.source_ip,
                                {self._resolved_raw_log_hostname_sql("rl", "d_src")} AS hostname,
                                rl.raw_message,
                                rl.event_time,
                                rl.received_at,
                                rl.parse_status,
                                rl.metadata,
                                i.incident_no,
                                ROW_NUMBER() OVER (PARTITION BY rl.id ORDER BY ie.id DESC NULLS LAST, i.id DESC NULLS LAST) AS row_rank
                            FROM raw_logs rl
                            LEFT JOIN devices d_src ON d_src.ip_address = rl.source_ip
                            LEFT JOIN events e ON e.raw_log_id = rl.id
                            LEFT JOIN incident_events ie ON ie.event_id = e.id
                            LEFT JOIN incidents i ON i.id = ie.incident_id
                            WHERE 1=1 {extra_where}
                        ) dedup
                        WHERE row_rank = 1
                        ORDER BY received_at DESC
                        LIMIT %s
                        """,
                        params + [limit],
                    )
                    raw_logs = cur.fetchall()
                    # Events: apply device/time filters too
                    ev_conditions = []
                    ev_params: list = []
                    if device_ip:
                        ev_conditions.append("d.ip_address = %s")
                        ev_params.append(device_ip)
                    if hours_back:
                        ev_conditions.append("e.created_at >= NOW() - (%s || ' hours')::interval")
                        ev_params.append(str(hours_back))
                    ev_extra = ("AND " + " AND ".join(ev_conditions)) if ev_conditions else ""
                    cur.execute(
                        f"""
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
                            WHERE 1=1 {ev_extra}
                        ) dedup
                        WHERE row_rank = 1
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        ev_params + [limit],
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
                        COUNT(*) FILTER (
                            WHERE i.status <> ALL(ARRAY['resolved','closed'])
                              AND COALESCE(i.suppressed_in_list, FALSE) = FALSE
                              AND COALESCE(i.metadata->>'deprecated_root', 'false') <> 'true'
                        ) AS open_incident_count,
                        MAX(i.last_seen_at) AS last_incident_seen
                    FROM devices d
                    LEFT JOIN incidents i ON i.primary_device_id = d.id
                    GROUP BY d.id
                    ORDER BY d.hostname
                    """,
                )
                return cur.fetchall()

    def device_detail(self, hostname: str) -> dict[str, Any] | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        d.*,
                        COUNT(*) FILTER (
                            WHERE i.status <> ALL(ARRAY['resolved','closed'])
                              AND COALESCE(i.suppressed_in_list, FALSE) = FALSE
                              AND COALESCE(i.metadata->>'deprecated_root', 'false') <> 'true'
                        ) AS open_incident_count,
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
                      AND COALESCE(i.suppressed_in_list, FALSE) = FALSE
                      AND COALESCE(i.metadata->>'deprecated_root', 'false') <> 'true'
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

    # ── Vulnerability scanning ────────────────────────────────────────────────

    def get_device_check_summary(self, hostname: str) -> dict[str, Any]:
        """Return aggregated impact-check verdicts for a device (latest check per advisory)."""
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT verdict, COUNT(*) AS cnt
                    FROM (
                        SELECT DISTINCT ON (ac.advisory_id) ac.verdict
                        FROM advisory_checks ac
                        JOIN devices d ON d.id = ac.device_id
                        WHERE LOWER(d.hostname) = LOWER(%s) OR d.ip_address = %s
                        ORDER BY ac.advisory_id, ac.checked_at DESC
                    ) sub
                    GROUP BY verdict
                    """,
                    (hostname, hostname),
                )
                rows = cur.fetchall()
        counts: dict[str, int] = {"affected": 0, "not_affected": 0, "uncertain": 0}
        for row in rows:
            v = row["verdict"]
            if v in counts:
                counts[v] += int(row["cnt"])
        return {
            "checked": sum(counts.values()),
            "affected": counts["affected"],
            "not_affected": counts["not_affected"],
            "uncertain": counts["uncertain"],
        }

    def get_vulnerability_summary(self) -> dict[str, Any]:
        """Return vulnerability summary across all devices with their latest scan results."""
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        d.id, d.hostname, d.ip_address, d.os_platform,
                        d.device_role, d.site, d.version,
                        s.id          AS scan_id,
                        s.ios_version,
                        s.advisory_count,
                        s.critical_count,
                        s.high_count,
                        s.medium_count,
                        s.low_count,
                        s.llm_summary,
                        s.status      AS scan_status,
                        s.error_message,
                        s.scanned_at,
                        chk.check_affected,
                        chk.check_not_affected,
                        chk.check_uncertain
                    FROM devices d
                    LEFT JOIN LATERAL (
                        SELECT * FROM device_vuln_scans
                        WHERE device_id = d.id
                        ORDER BY scanned_at DESC
                        LIMIT 1
                    ) s ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT
                            COUNT(*) FILTER (WHERE verdict = 'affected')     AS check_affected,
                            COUNT(*) FILTER (WHERE verdict = 'not_affected') AS check_not_affected,
                            COUNT(*) FILTER (WHERE verdict = 'uncertain')    AS check_uncertain
                        FROM (
                            SELECT DISTINCT ON (advisory_id) verdict
                            FROM advisory_checks
                            WHERE device_id = d.id
                            ORDER BY advisory_id, checked_at DESC
                        ) latest
                    ) chk ON TRUE
                    ORDER BY
                        COALESCE(s.critical_count, -1) DESC,
                        COALESCE(s.high_count,     -1) DESC,
                        d.hostname
                    """
                )
                devices = cur.fetchall()

        scanned = [d for d in devices if d["scan_id"] is not None]
        return {
            "summary": {
                "total_devices":        len(devices),
                "scanned_devices":      len(scanned),
                "unscanned_devices":    len(devices) - len(scanned),
                "devices_with_critical": sum(1 for d in scanned if (d["critical_count"] or 0) > 0),
                "devices_with_high":    sum(1 for d in scanned if (d["high_count"] or 0) > 0),
                "total_critical":       sum(d["critical_count"] or 0 for d in scanned),
                "total_high":           sum(d["high_count"]     or 0 for d in scanned),
                "total_medium":         sum(d["medium_count"]   or 0 for d in scanned),
                "total_low":            sum(d["low_count"]      or 0 for d in scanned),
                "total_advisories":     sum(d["advisory_count"] or 0 for d in scanned),
            },
            "devices": devices,
        }

    def run_vuln_scan_all(self) -> dict[str, Any]:
        """Scan all devices against Cisco PSIRT. Skips devices scanned in last 12 hours."""
        from datetime import timezone

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT hostname FROM devices ORDER BY hostname")
                all_devices = [r["hostname"] for r in cur.fetchall()]

                # Skip only devices with a SUCCESSFUL scan in the last 12 hours.
                # Error scans are always retried.
                cur.execute(
                    """
                    SELECT DISTINCT d.hostname
                    FROM devices d
                    JOIN device_vuln_scans s ON s.device_id = d.id
                    WHERE s.status = 'completed'
                      AND s.scanned_at > NOW() - INTERVAL '12 hours'
                    """
                )
                recent = {r["hostname"] for r in cur.fetchall()}

        to_scan = [h for h in all_devices if h not in recent]
        skipped = list(recent)
        scanned: list[str] = []
        errors: list[dict] = []

        for hostname in to_scan:
            try:
                self.run_vuln_scan(hostname)
                scanned.append(hostname)
            except Exception as exc:
                logger.error("Vuln scan failed for %s: %s", hostname, exc)
                errors.append({"hostname": hostname, "error": str(exc)})

        return {
            "scanned": scanned,
            "skipped": skipped,
            "errors":  errors,
        }

    def get_device_vulnerabilities(self, hostname: str) -> dict[str, Any] | None:
        """Return the latest vuln scan result + advisory list for a device."""
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, ip_address, version FROM devices WHERE LOWER(hostname) = LOWER(%s) OR ip_address = %s",
                    (hostname, hostname),
                )
                device = cur.fetchone()
                if not device:
                    return None

                cur.execute(
                    """
                    SELECT * FROM device_vuln_scans
                    WHERE device_id = %s
                    ORDER BY scanned_at DESC
                    LIMIT 1
                    """,
                    (device["id"],),
                )
                scan = cur.fetchone()
                if not scan:
                    return {"device_id": device["id"], "scan": None, "advisories": []}

                cur.execute(
                    """
                    SELECT * FROM device_vulnerabilities
                    WHERE scan_id = %s
                    ORDER BY
                        first_published DESC NULLS LAST,
                        CASE sir
                            WHEN 'Critical' THEN 4
                            WHEN 'High'     THEN 3
                            WHEN 'Medium'   THEN 2
                            WHEN 'Low'      THEN 1
                            ELSE 0
                        END DESC,
                        cvss_score DESC
                    """,
                    (scan["id"],),
                )
                advisories = cur.fetchall()
                return {"device_id": device["id"], "scan": scan, "advisories": advisories}

    def run_vuln_scan(self, hostname: str) -> dict[str, Any]:
        """Trigger a fresh vulnerability scan (PSIRT → NVD fallback). Blocks until complete."""
        from src.aiops.vuln_scanner import fetch_advisories_for_version, generate_vuln_summary

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM devices WHERE LOWER(hostname) = LOWER(%s) OR ip_address = %s",
                    (hostname, hostname),
                )
                device = cur.fetchone()
                if not device:
                    raise KeyError(f"Device not found: {hostname!r}")

        ios_version = device.get("version", "") or ""
        error_message: str | None = None
        advisories = []
        scan_source = "none"
        status = "completed"

        try:
            advisories, scan_source = fetch_advisories_for_version(
                ios_version, os_platform=device.get("os_platform", "cisco_ios") or "cisco_ios"
            )
        except Exception as exc:
            logger.error("Vuln fetch failed for %s: %s", hostname, exc)
            error_message = str(exc)
            status = "error"

        llm_summary = generate_vuln_summary(dict(device), advisories, scan_source)

        counts = {
            "advisory_count": len(advisories),
            "critical_count": sum(1 for a in advisories if a.sir == "Critical"),
            "high_count":     sum(1 for a in advisories if a.sir == "High"),
            "medium_count":   sum(1 for a in advisories if a.sir == "Medium"),
            "low_count":      sum(1 for a in advisories if a.sir == "Low"),
        }

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO device_vuln_scans
                        (device_id, ios_version, advisory_count, critical_count, high_count,
                         medium_count, low_count, llm_summary, status, error_message, scan_source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        device["id"], ios_version,
                        counts["advisory_count"], counts["critical_count"],
                        counts["high_count"], counts["medium_count"], counts["low_count"],
                        llm_summary, status, error_message, scan_source,
                    ),
                )
                scan_id = cur.fetchone()["id"]

                for adv in advisories:
                    fp = adv.first_published or None
                    lu = adv.last_updated or None
                    cur.execute(
                        """
                        INSERT INTO device_vulnerabilities
                            (device_id, scan_id, advisory_id, title, sir, cvss_score,
                             cves, publication_url, summary, workaround, first_fixed,
                             first_published, last_updated)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s, %s)
                        ON CONFLICT (scan_id, advisory_id) DO NOTHING
                        """,
                        (
                            device["id"], scan_id,
                            adv.advisory_id, adv.title, adv.sir, adv.cvss_score,
                            Json(adv.cves), adv.publication_url,
                            adv.summary, adv.workaround, Json(adv.first_fixed),
                            fp, lu,
                        ),
                    )
                conn.commit()

        return self.get_device_vulnerabilities(hostname) or {}

    def get_advisory_checks(self, hostname: str, advisory_id: str) -> list[dict[str, Any]]:
        """Return past impact checks for a device + advisory, newest first."""
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ac.*
                    FROM advisory_checks ac
                    JOIN devices d ON d.id = ac.device_id
                    WHERE (LOWER(d.hostname) = LOWER(%s) OR d.ip_address = %s)
                      AND ac.advisory_id = %s
                    ORDER BY ac.checked_at DESC
                    LIMIT 10
                    """,
                    (hostname, hostname, advisory_id),
                )
                rows = cur.fetchall()
        result = []
        for row in rows:
            r = dict(row)
            cmds = r.get("commands_run")
            if isinstance(cmds, str):
                try:
                    r["commands_run"] = json.loads(cmds)
                except Exception:
                    r["commands_run"] = []
            result.append(r)
        return result

    def clear_advisory_checks(self, hostname: str) -> int:
        """Delete all advisory impact checks for a device. Returns count deleted."""
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM advisory_checks
                    WHERE device_id IN (
                        SELECT id FROM devices
                        WHERE LOWER(hostname) = LOWER(%s) OR ip_address = %s
                    )
                    """,
                    (hostname, hostname),
                )
                deleted = cur.rowcount
                conn.commit()
                return deleted

    def save_advisory_check(
        self,
        hostname: str,
        advisory_id: str,
        advisory_title: str,
        verdict: str,
        confidence: float,
        explanation: str,
        commands_run: list[dict[str, str]],
        llm_model: str = "",
        has_workaround: bool | None = None,
        workaround_text: str = "",
        feature_checked: str = "",
    ) -> dict[str, Any]:
        """Persist an advisory impact check result and return the saved row."""
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM devices WHERE LOWER(hostname) = LOWER(%s) OR ip_address = %s",
                    (hostname, hostname),
                )
                device = cur.fetchone()
                if not device:
                    raise KeyError(f"Device not found: {hostname!r}")
                cur.execute(
                    """
                    INSERT INTO advisory_checks
                        (device_id, advisory_id, advisory_title, verdict, confidence,
                         explanation, commands_run, llm_model,
                         has_workaround, workaround_text, feature_checked)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        device["id"], advisory_id, advisory_title, verdict,
                        confidence, explanation,
                        Json(commands_run), llm_model,
                        has_workaround, workaround_text, feature_checked,
                    ),
                )
                row = dict(cur.fetchone())
            conn.commit()
        cmds = row.get("commands_run")
        if isinstance(cmds, str):
            try:
                row["commands_run"] = json.loads(cmds)
            except Exception:
                row["commands_run"] = []
        return row

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

    def add_incident_note(self, incident_no: str, author: str, body: str) -> None:
        """Insert an engineer note into the incident timeline."""
        if not body.strip():
            raise ValueError("Note body cannot be empty")
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM incidents WHERE incident_no = %s", (incident_no,))
                row = cur.fetchone()
                if row is None:
                    raise KeyError(f"Incident {incident_no} not found")
                self._record_timeline(
                    cur,
                    row["id"],
                    "engineer_note",
                    f"Note by {author or 'engineer'}",
                    body.strip(),
                    {"author": author or "engineer"},
                )
            conn.commit()

    def get_incident(self, incident_no: str) -> dict[str, Any]:
        self._refresh_time_based_incident_states()
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        i.*,
                        d.hostname AS primary_hostname,
                        d.os_platform,
                        d.device_role,
                        (
                            SELECT COUNT(*)
                            FROM incidents related
                            WHERE related.relation_group_key = i.relation_group_key
                              AND related.id <> i.id
                              AND COALESCE(related.suppressed_in_list, FALSE) = FALSE
                              AND COALESCE(related.metadata->>'deprecated_root', 'false') <> 'true'
                        ) AS child_count,
                        (
                            SELECT COUNT(*)
                            FROM incidents related
                            WHERE related.relation_group_key = i.relation_group_key
                              AND related.id <> i.id
                              AND COALESCE(related.suppressed_in_list, FALSE) = FALSE
                              AND COALESCE(related.metadata->>'deprecated_root', 'false') <> 'true'
                              AND related.status = ANY(%s)
                        ) AS active_child_count
                    FROM incidents i
                    LEFT JOIN devices d ON d.id = i.primary_device_id
                    WHERE i.incident_no = %s
                      AND COALESCE(i.suppressed_in_list, FALSE) = FALSE
                      AND COALESCE(i.metadata->>'deprecated_root', 'false') <> 'true'
                    """,
                    (list(_OPEN_INCIDENT_STATUSES), incident_no),
                )
                incident = cur.fetchone()
                if incident is None:
                    raise KeyError(f"Incident {incident_no} not found")
                related_incidents: list[dict[str, Any]] = []
                remediation_owner_incident: dict[str, Any] | None = None
                relation_group_key = incident.get("relation_group_key")
                if relation_group_key:
                    cur.execute(
                        """
                        SELECT
                            i.*,
                            d.hostname AS primary_hostname,
                            (
                                SELECT COUNT(*)
                                FROM incidents related
                                WHERE related.relation_group_key = i.relation_group_key
                                  AND related.id <> i.id
                                  AND COALESCE(related.suppressed_in_list, FALSE) = FALSE
                                  AND COALESCE(related.metadata->>'deprecated_root', 'false') <> 'true'
                            ) AS child_count,
                            (
                                SELECT COUNT(*)
                                FROM incidents related
                                WHERE related.relation_group_key = i.relation_group_key
                                  AND related.id <> i.id
                                  AND COALESCE(related.suppressed_in_list, FALSE) = FALSE
                                  AND COALESCE(related.metadata->>'deprecated_root', 'false') <> 'true'
                                  AND related.status = ANY(%s)
                            ) AS active_child_count
                        FROM incidents i
                        LEFT JOIN devices d ON d.id = i.primary_device_id
                        WHERE i.relation_group_key = %s
                          AND i.id <> %s
                          AND COALESCE(i.suppressed_in_list, FALSE) = FALSE
                          AND COALESCE(i.metadata->>'deprecated_root', 'false') <> 'true'
                        ORDER BY i.last_seen_at DESC
                        """,
                        (list(_OPEN_INCIDENT_STATUSES), relation_group_key, incident["id"]),
                    )
                    related_rows = cur.fetchall()
                    related_incidents = [
                        {
                            "incident": row,
                            "relation_reason": _incident_metadata(row).get("relation_reason")
                            or _incident_metadata(incident).get("cause_hint")
                            or "linked_context",
                            "relation_confidence": _incident_metadata(row).get("topology_confidence")
                            or _incident_metadata(incident).get("topology_confidence")
                            or "",
                            "owns_remediation": _incident_owns_remediation(row),
                        }
                        for row in related_rows
                    ]

                owner_id = _incident_remediation_owner_id(incident)
                if owner_id is not None and owner_id != incident["id"]:
                    cur.execute(
                        """
                        SELECT
                            i.*,
                            d.hostname AS primary_hostname,
                            (
                                SELECT COUNT(*)
                                FROM incidents related
                                WHERE related.relation_group_key = i.relation_group_key
                                  AND related.id <> i.id
                                  AND COALESCE(related.suppressed_in_list, FALSE) = FALSE
                                  AND COALESCE(related.metadata->>'deprecated_root', 'false') <> 'true'
                            ) AS child_count,
                            (
                                SELECT COUNT(*)
                                FROM incidents related
                                WHERE related.relation_group_key = i.relation_group_key
                                  AND related.id <> i.id
                                  AND COALESCE(related.suppressed_in_list, FALSE) = FALSE
                                  AND COALESCE(related.metadata->>'deprecated_root', 'false') <> 'true'
                                  AND related.status = ANY(%s)
                            ) AS active_child_count
                        FROM incidents i
                        LEFT JOIN devices d ON d.id = i.primary_device_id
                        WHERE i.id = %s
                        """,
                        (list(_OPEN_INCIDENT_STATUSES), owner_id),
                    )
                    remediation_owner_incident = cur.fetchone()
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
                    SELECT
                        rl.id,
                        rl.source_ip,
                        COALESCE(NULLIF(NULLIF(BTRIM(rl.hostname), ''), rl.source_ip), d_src.hostname, rl.source_ip) AS hostname,
                        rl.raw_message,
                        rl.event_time,
                        rl.received_at,
                        rl.parse_status,
                        rl.metadata,
                        i.incident_no,
                        i.title AS incident_title,
                        COALESCE(d_inc.hostname, i.primary_source_ip) AS incident_hostname
                    FROM raw_logs rl
                    LEFT JOIN devices d_src ON d_src.ip_address = rl.source_ip
                    JOIN events e ON e.raw_log_id = rl.id
                    JOIN incident_events ie ON ie.event_id = e.id
                    JOIN incidents i ON i.id = ie.incident_id
                    LEFT JOIN devices d_inc ON d_inc.id = i.primary_device_id
                    WHERE ie.incident_id = %s
                    ORDER BY rl.received_at DESC
                    LIMIT 20
                    """,
                    (incident["id"],),
                )
                raw_logs = cur.fetchall()
                cur.execute(
                    """
                    SELECT
                        e.*,
                        rl.raw_message,
                        i.incident_no,
                        i.title AS incident_title,
                        COALESCE(d_inc.hostname, i.primary_source_ip) AS incident_hostname
                    FROM events e
                    LEFT JOIN raw_logs rl ON rl.id = e.raw_log_id
                    JOIN incident_events ie ON ie.event_id = e.id
                    JOIN incidents i ON i.id = ie.incident_id
                    LEFT JOIN devices d_inc ON d_inc.id = i.primary_device_id
                    WHERE ie.incident_id = %s
                    ORDER BY e.created_at DESC
                    LIMIT 20
                    """,
                    (incident["id"],),
                )
                events = cur.fetchall()
        return {
            "incident": incident,
            "related_incidents": related_incidents,
            "remediation_owner_incident": remediation_owner_incident,
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
        existing_proposal = detail.get("proposal")
        previous_workflow_phase = _incident_workflow_phase(incident)
        requires_intent_confirmation = _needs_intent_confirmation(incident)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE incidents
                    SET workflow_phase = 'ai_investigating',
                        updated_at = NOW()
                    WHERE id = %s
                      AND status <> 'resolved'
                    """,
                    (incident["id"],),
                )
            conn.commit()
        incident = {**incident, "workflow_phase": "ai_investigating"}
        device_cache = self._fetch_device_cache()
        result = run_llm_troubleshoot(incident, detail["raw_logs"], device_cache)
        if requires_intent_confirmation:
            result["disposition"] = "needs_human_review"
            result["proposal"] = None
            if "intent confirmation" not in result["conclusion"].lower():
                result["conclusion"] += (
                    " Human intent confirmation is required before any no shutdown remediation can be proposed."
                )
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
                proposal_allowed = not requires_intent_confirmation
                if result.get("proposal") and proposal_allowed:
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
                    "no_action_needed": ("resolved", "no_action_needed"),
                    "self_recovered": ("monitoring", "self_recovered"),
                    "monitor_further": ("active", None),
                    "physical_issue": ("active", "physical_handoff"),
                    "external_issue": ("active", "external_handoff"),
                    "config_fix_possible": ("active", None),
                    "needs_human_review": ("active", None),
                }
                next_status, resolution_type = status_map.get(disposition, ("active", None))
                if requires_intent_confirmation:
                    next_status, resolution_type = ("active", None)
                next_workflow_phase = {
                    "no_action_needed": "none",
                    "self_recovered": "none",
                    "monitor_further": "none",
                    "physical_issue": "escalated_physical",
                    "external_issue": "escalated_external",
                    "config_fix_possible": "remediation_available" if proposal else "none",
                    "needs_human_review": "none",
                }.get(disposition, "none")
                if requires_intent_confirmation:
                    next_workflow_phase = "intent_confirmation_required"
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
                if (
                    proposal is None
                    and existing_proposal is not None
                    and next_workflow_phase == "none"
                    and disposition in {"monitor_further", "needs_human_review", "config_fix_possible"}
                ):
                    next_workflow_phase = _proposal_workflow_phase(existing_proposal.get("status"))
                # no_action_needed: port is intentionally inactive — resolve immediately, skip proposal
                if disposition == "no_action_needed":
                    proposal = None  # discard any proposal that may have been built
                    cur.execute(
                        """
                        UPDATE incidents
                        SET status = 'resolved',
                            workflow_phase = 'none',
                            resolution_type = 'no_action_needed',
                            current_proposal_id = NULL,
                            resolved_at = NOW(),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (incident["id"],),
                    )
                elif proposal:
                    cur.execute(
                        """
                        UPDATE incidents
                        SET status = %s,
                            workflow_phase = 'remediation_available',
                            current_proposal_id = %s,
                            resolution_type = COALESCE(%s, resolution_type),
                            resolved_at = NULL,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (next_status, proposal["id"], resolution_type, incident["id"]),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE incidents
                        SET status = %s,
                            workflow_phase = %s,
                            current_proposal_id = CASE WHEN %s = 'none' THEN NULL ELSE current_proposal_id END,
                            resolution_type = COALESCE(%s, resolution_type),
                            resolved_at = CASE WHEN %s = 'resolved' THEN COALESCE(resolved_at, NOW()) ELSE NULL END,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (next_status, next_workflow_phase, next_workflow_phase, resolution_type, next_status, incident["id"]),
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
                    SET workflow_phase = 'approved_to_execute', updated_at = NOW()
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

    def confirm_incident_intent(
        self,
        incident_no: str,
        *,
        intent: str,
        note: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        if intent not in {"intentional", "unintentional"}:
            raise ValueError("intent must be 'intentional' or 'unintentional'")

        detail = self.get_incident(incident_no)
        incident = detail["incident"]
        owner_id = _incident_remediation_owner_id(incident)
        if owner_id is not None and owner_id != incident["id"]:
            raise ValueError("shutdown intent must be confirmed on the remediation-owning incident")
        metadata = _incident_metadata(incident)
        if metadata.get("cause_hint") != "linked_admin_down":
            raise ValueError("incident does not require shutdown intent confirmation")

        current_intent_status = metadata.get("intent_status")
        if intent == "intentional" and current_intent_status == "confirmed_intentional":
            return detail
        if intent == "unintentional" and current_intent_status == "confirmed_unintentional" and detail.get("proposal"):
            return detail
        if current_intent_status != "needs_confirmation":
            raise ValueError("shutdown intent has already been confirmed")

        updated_metadata = {
            **metadata,
            "intent_status": "confirmed_intentional" if intent == "intentional" else "confirmed_unintentional",
            "intent_note": note,
            "intent_actor": actor or "",
            "intent_confirmed_at": datetime.now(timezone.utc).isoformat(),
        }

        with connect() as conn:
            with conn.cursor() as cur:
                if intent == "intentional":
                    cur.execute(
                        """
                        UPDATE proposals
                        SET status = 'cancelled',
                            cancelled_reason = 'confirmed_intentional_shutdown'
                        WHERE incident_id = %s
                          AND status IN ('pending', 'approved')
                        """,
                        (incident["id"],),
                    )
                    relation_group_key = incident.get("relation_group_key")
                    if relation_group_key:
                        cur.execute(
                            """
                            UPDATE incidents
                            SET status = 'resolved',
                                workflow_phase = 'none',
                                resolution_type = CASE
                                    WHEN id = %s THEN 'confirmed_intentional_shutdown'
                                    ELSE 'resolved_by_related_decision'
                                END,
                                resolved_at = NOW(),
                                current_proposal_id = NULL,
                                current_recovery_state = 'resolved',
                                updated_at = NOW()
                            WHERE relation_group_key = %s
                              AND COALESCE(suppressed_in_list, FALSE) = FALSE
                              AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
                              AND status <> 'resolved'
                            """,
                            (incident["id"], relation_group_key),
                        )
                    cur.execute(
                        """
                        UPDATE incidents
                        SET status = 'resolved',
                            workflow_phase = 'none',
                            resolution_type = 'confirmed_intentional_shutdown',
                            resolved_at = NOW(),
                            current_proposal_id = NULL,
                            current_recovery_state = 'resolved',
                            metadata = %s::jsonb,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (Json(updated_metadata), incident["id"]),
                    )
                    self._record_timeline(
                        cur,
                        incident["id"],
                        "decision",
                        "Shutdown intent confirmed",
                        note,
                        {"intent": intent, "actor": actor or "", "cause_hint": "linked_admin_down"},
                    )
                else:
                    root_host = str(metadata.get("root_host") or "").strip()
                    root_interface = str(metadata.get("root_interface") or "").strip()
                    remote_host = str(metadata.get("remote_host") or "").strip()
                    remote_interface = str(metadata.get("remote_interface") or "").strip()
                    if not root_host or not root_interface:
                        raise ValueError("incident is missing root interface context for remediation")

                    rationale = (
                        f"Remote impact on {remote_host or 'the peer'} {remote_interface or 'interface'} correlates with "
                        f"an admin shutdown on {root_host} {root_interface}. After human confirmation that the shutdown "
                        "was unintended, restoring the interface is the safest remediation path."
                    )
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
                            f"Restore {root_host} {root_interface}",
                            rationale,
                            Json([root_host]),
                            Json([f"interface {root_interface}", "no shutdown"]),
                            Json([f"interface {root_interface}", "shutdown"]),
                            f"Re-apply shutdown on {root_interface} if verification fails or the change is later deemed intentional.",
                            f"Should restore the linked path impacted by the admin shutdown on {root_host} {root_interface}.",
                            Json([f"show interface {root_interface}"]),
                            "medium",
                        ),
                    )
                    proposal = cur.fetchone()
                    cur.execute(
                        """
                        UPDATE incidents
                        SET status = 'active',
                            workflow_phase = 'remediation_available',
                            current_proposal_id = %s,
                            resolution_type = NULL,
                            resolved_at = NULL,
                            metadata = %s::jsonb,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (proposal["id"], Json(updated_metadata), incident["id"]),
                    )
                    cur.execute(
                        """
                        UPDATE incidents
                        SET remediation_owner_incident_id = %s,
                            updated_at = NOW()
                        WHERE relation_group_key = %s
                          AND id <> %s
                          AND COALESCE(suppressed_in_list, FALSE) = FALSE
                          AND COALESCE(metadata->>'deprecated_root', 'false') <> 'true'
                        """,
                        (incident["id"], incident.get("relation_group_key"), incident["id"]),
                    )
                    self._record_timeline(
                        cur,
                        incident["id"],
                        "decision",
                        "Shutdown intent confirmed",
                        note,
                        {"intent": intent, "actor": actor or "", "cause_hint": "linked_admin_down"},
                    )
                    self._record_timeline(
                        cur,
                        incident["id"],
                        "proposal",
                        "Remediation proposal created after intent confirmation",
                        proposal["title"],
                        {"proposal_id": proposal["id"], "intent": intent},
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

        verification_state = _verification_signal_state(verification_notes if verification_status == "auto_checked" else None)

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
                if status != "completed":
                    next_status = "active"
                    next_workflow_phase = "none"
                    next_resolution_type = None
                    resolved_at: datetime | None = None
                elif verification_state == "positive":
                    next_status = "resolved"
                    next_workflow_phase = "none"
                    next_resolution_type = "verified_recovery"
                    resolved_at = datetime.now(timezone.utc)
                elif verification_state == "negative":
                    next_status = "active"
                    next_workflow_phase = "none"
                    next_resolution_type = None
                    resolved_at = None
                else:
                    next_status = "active"
                    next_workflow_phase = "none"
                    next_resolution_type = None
                    resolved_at = None
                cur.execute(
                    """
                    UPDATE incidents
                    SET status = %s,
                        workflow_phase = %s,
                        current_proposal_id = NULL,
                        resolution_type = COALESCE(%s, resolution_type),
                        resolved_at = %s,
                        current_recovery_state = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        next_status,
                        next_workflow_phase,
                        next_resolution_type,
                        resolved_at,
                        "resolved" if next_status == "resolved" else "watching",
                        incident["id"],
                    ),
                )
                cur.execute(
                    "UPDATE proposals SET status = 'executed' WHERE id = %s",
                    (proposal["id"],),
                )
                timeline_note = f"Execution status: {status}"
                if status == "failed" and rollback_commands:
                    timeline_note += " — rollback commands automatically applied"
                elif status == "completed" and verification_status == "auto_checked":
                    if verification_state == "positive":
                        timeline_note += " — explicit positive verification evidence resolved the incident"
                    elif verification_state == "negative":
                        timeline_note += " — verification output still shows the fault"
                    else:
                        timeline_note += " — verification commands collected automatically"
                self._record_timeline(
                    cur,
                    incident["id"],
                    "execution",
                    "Approved commands executed",
                    timeline_note,
                    {
                        "execution_id": execution["id"],
                        "actor": actor,
                        "auto_verified": verification_status == "auto_checked",
                        "verification_state": verification_state,
                    },
                )
            conn.commit()
        return self.get_incident(incident_no)

    def verify_recovery(self, incident_no: str, healed: bool, note: str) -> dict[str, Any]:
        detail = self.get_incident(incident_no)
        incident = detail["incident"]
        next_status = "monitoring" if healed else "active"
        resolution_type = "verified_recovery" if healed else None
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE incidents
                    SET status = %s,
                        workflow_phase = 'none',
                        resolution_type = CASE WHEN %s THEN COALESCE(%s, resolution_type) ELSE NULL END,
                        resolved_at = NULL,
                        current_proposal_id = CASE WHEN %s THEN current_proposal_id ELSE NULL END,
                        current_recovery_state = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (next_status, healed, resolution_type, healed, "monitoring" if healed else "watching", incident["id"]),
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
