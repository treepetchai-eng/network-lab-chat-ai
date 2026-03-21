"""Generic incident correlation for structured syslog events."""

from __future__ import annotations

from datetime import timedelta
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from src.ops.db import utcnow
from src.ops.models import Incident, IncidentCluster, IncidentEventLink, IncidentHistory, NormalizedEvent

ISSUE_EVENT_TYPES = {
    "ospf_neighbor_down",
    "bgp_neighbor_down",
    "eigrp_neighbor_down",
    "interface_down",
    "track_down",
    "critical_region_fault",
    "device_traceback",
    "device_restart",
    "cpu_hog",
}
RECOVERY_EVENT_TYPES = {
    "ospf_neighbor_up",
    "bgp_neighbor_up",
    "eigrp_neighbor_up",
    "interface_up",
    "track_up",
}
SEVERITY_BY_EVENT_TYPE = {
    "critical_region_fault": "critical",
    "device_traceback": "critical",
    "bgp_neighbor_down": "high",
    "ospf_neighbor_down": "high",
    "eigrp_neighbor_down": "high",
    "interface_down": "medium",
    "track_down": "medium",
    "device_restart": "medium",
    "cpu_hog": "low",
}
# High-frequency storm types: only refresh the incident record every 5 minutes
# to avoid thousands of identical DB writes during burst periods.
STORM_THROTTLE_TYPES = {"critical_region_fault", "device_traceback"}
STORM_WINDOW_SECONDS = 300  # 5 minutes
CLUSTER_WINDOW_SECONDS = 300  # 5 minutes — group incidents across devices

# Event types that commonly cascade together during a single failure.
# Used by _try_cascade_cluster for cross-event-type correlation.
CASCADE_GROUPS: dict[str, set[str]] = {
    "interface_down": {"eigrp_neighbor_down", "ospf_neighbor_down", "bgp_neighbor_down", "track_down"},
    "eigrp_neighbor_down": {"interface_down", "track_down"},
    "ospf_neighbor_down": {"interface_down", "track_down"},
    "bgp_neighbor_down": {"interface_down"},
    "track_down": {"interface_down", "eigrp_neighbor_down", "ospf_neighbor_down"},
    "device_restart": {"interface_down", "eigrp_neighbor_down", "ospf_neighbor_down", "bgp_neighbor_down"},
    "critical_region_fault": {"device_traceback", "interface_down"},
    "device_traceback": {"critical_region_fault", "interface_down"},
}

OPEN_INCIDENT_STATUSES = ("new", "acknowledged", "in_progress", "monitoring", "open", "investigating")


def _record_history(
    session: Session,
    *,
    incident_id: int,
    action: str,
    summary: str,
    from_status: str | None = None,
    to_status: str | None = None,
    payload: dict | None = None,
) -> None:
    session.add(
        IncidentHistory(
            incident_id=incident_id,
            action=action,
            actor="system",
            actor_role="system",
            from_status=from_status,
            to_status=to_status,
            summary=summary,
            payload_json=payload or {},
        )
    )


_REOPEN_WINDOW_SECONDS = 300  # 5 min — reopen recently resolved incident instead of creating new


def _latest_open_incident(session: Session, correlation_key: str) -> Incident | None:
    return session.scalar(
        select(Incident)
        .where(
            Incident.correlation_key == correlation_key,
            Incident.status.in_(OPEN_INCIDENT_STATUSES),
        )
        .order_by(Incident.id.desc())
        .limit(1)
    )


def _latest_recent_incident(session: Session, correlation_key: str) -> Incident | None:
    """Find recently-resolved incident (within reopen window) to avoid duplicate creation."""
    cutoff = utcnow() - timedelta(seconds=_REOPEN_WINDOW_SECONDS)
    return session.scalar(
        select(Incident)
        .where(
            Incident.correlation_key == correlation_key,
            Incident.status == "resolved",
            Incident.closed_at >= cutoff,
        )
        .order_by(Incident.id.desc())
        .limit(1)
    )


def _link_event(session: Session, incident_id: int, event_id: int) -> None:
    existing = session.scalar(
        select(IncidentEventLink).where(
            IncidentEventLink.incident_id == incident_id,
            IncidentEventLink.event_id == event_id,
        )
    )
    if existing is None:
        session.add(IncidentEventLink(incident_id=incident_id, event_id=event_id))


def _count_recent_resolutions(session: Session, incident_id: int, window_secs: int = 3600) -> int:
    """Count how many times this incident was resolved within the last window_secs."""
    cutoff = utcnow() - timedelta(seconds=window_secs)
    return session.scalar(
        select(func.count()).select_from(IncidentHistory)
        .where(
            IncidentHistory.incident_id == incident_id,
            IncidentHistory.to_status == "resolved",
            IncidentHistory.created_at > cutoff,
        )
    ) or 0


def _is_escalated(session: Session, incident_id: int) -> bool:
    """Return True if this incident has an escalation_needed ops-loop history entry."""
    # ops_loop._persist_loop_event_sync prefixes stage names with "ops_loop_"
    return (session.scalar(
        select(func.count()).select_from(IncidentHistory)
        .where(
            IncidentHistory.incident_id == incident_id,
            IncidentHistory.action == "ops_loop_escalation_needed",
        )
    ) or 0) > 0


_SEVERITY_ORDER = {"low": 0, "medium": 1, "warning": 1, "high": 2, "critical": 3}


def _try_cluster_incident(session: Session, incident: Incident) -> None:
    """Check if this new incident should be grouped with other open incidents across devices."""
    if not incident.event_type or not incident.primary_source_ip:
        return

    cutoff = (incident.opened_at or utcnow()) - timedelta(seconds=CLUSTER_WINDOW_SECONDS)

    siblings = session.scalars(
        select(Incident).where(
            Incident.event_type == incident.event_type,
            Incident.primary_source_ip != incident.primary_source_ip,
            Incident.status.in_(OPEN_INCIDENT_STATUSES),
            Incident.opened_at >= cutoff,
            Incident.id != incident.id,
        )
        .order_by(Incident.opened_at.asc())
        .limit(50)
    ).all()

    if not siblings:
        return

    # Check if any sibling already has a cluster
    existing_cluster_id = None
    for sib in siblings:
        if sib.incident_cluster_id is not None:
            existing_cluster_id = sib.incident_cluster_id
            break

    if existing_cluster_id:
        cluster = session.get(IncidentCluster, existing_cluster_id)
        if cluster:
            incident.incident_cluster_id = existing_cluster_id
            if _SEVERITY_ORDER.get(incident.severity, 0) > _SEVERITY_ORDER.get(cluster.severity, 0):
                cluster.severity = incident.severity
            return

    # Create new cluster
    device_count = len({s.primary_source_ip for s in siblings}) + 1
    cluster = IncidentCluster(
        title=f"Cross-device: {incident.event_type} ({device_count} devices)",
        status="open",
        severity=max((incident.severity, *(s.severity for s in siblings)),
                     key=lambda s: _SEVERITY_ORDER.get(s, 0)),
    )
    session.add(cluster)
    session.flush()

    incident.incident_cluster_id = cluster.id
    for sib in siblings:
        if sib.incident_cluster_id is None:
            sib.incident_cluster_id = cluster.id


def _extract_interface_from_key(key: str) -> str | None:
    """Extract interface name from correlation_key if present.

    Patterns: interface:{ip}:{intf} | neighbor:{proto}:{ip}:{nbr}:{intf}
    """
    parts = key.split(":")
    if parts[0] == "interface" and len(parts) >= 3:
        return parts[2]
    if parts[0] == "neighbor" and len(parts) >= 5:
        return parts[4]
    return None


def _try_cascade_cluster(session: Session, incident: Incident) -> None:
    """Second-pass clustering: group related event types on same/connected devices."""
    if not incident.event_type or not incident.primary_source_ip:
        return

    cascade_types = CASCADE_GROUPS.get(incident.event_type)
    if not cascade_types:
        return

    cutoff = (incident.opened_at or utcnow()) - timedelta(seconds=CLUSTER_WINDOW_SECONDS)

    # Strategy 1: Same device, different related event types
    same_device = session.scalars(
        select(Incident).where(
            Incident.event_type.in_(cascade_types),
            Incident.primary_source_ip == incident.primary_source_ip,
            Incident.status.in_(OPEN_INCIDENT_STATUSES),
            Incident.opened_at >= cutoff,
            Incident.id != incident.id,
        ).limit(20)
    ).all()

    # Strategy 2: Cross-device, same interface name
    intf_name = _extract_interface_from_key(incident.correlation_key or "")
    cross_device: list[Incident] = []
    if intf_name:
        cross_device = session.scalars(
            select(Incident).where(
                Incident.event_type.in_(cascade_types | {incident.event_type}),
                Incident.primary_source_ip != incident.primary_source_ip,
                Incident.correlation_key.contains(intf_name),
                Incident.status.in_(OPEN_INCIDENT_STATUSES),
                Incident.opened_at >= cutoff,
                Incident.id != incident.id,
            ).limit(20)
        ).all()

    all_siblings = list({s.id: s for s in same_device + cross_device}.values())
    if not all_siblings:
        return

    # Collect all existing cluster IDs (from first-pass or previous cascade)
    cluster_ids: set[int] = set()
    if incident.incident_cluster_id:
        cluster_ids.add(incident.incident_cluster_id)
    for sib in all_siblings:
        if sib.incident_cluster_id:
            cluster_ids.add(sib.incident_cluster_id)

    if cluster_ids:
        target_cluster_id = min(cluster_ids)
        cluster = session.get(IncidentCluster, target_cluster_id)
        if cluster:
            incident.incident_cluster_id = target_cluster_id
            for sib in all_siblings:
                if sib.incident_cluster_id != target_cluster_id:
                    sib.incident_cluster_id = target_cluster_id
            # Merge any other clusters into this one
            for cid in cluster_ids:
                if cid != target_cluster_id:
                    orphans = session.scalars(
                        select(Incident).where(Incident.incident_cluster_id == cid)
                    ).all()
                    for o in orphans:
                        o.incident_cluster_id = target_cluster_id
            # Update severity + title
            all_items = [incident] + all_siblings
            highest = max(all_items, key=lambda i: _SEVERITY_ORDER.get(i.severity, 0))
            if _SEVERITY_ORDER.get(highest.severity, 0) > _SEVERITY_ORDER.get(cluster.severity, 0):
                cluster.severity = highest.severity
            event_types = sorted({i.event_type for i in all_items if i.event_type})
            device_count = len({i.primary_source_ip for i in all_items if i.primary_source_ip})
            cluster.title = f"Correlated: {' + '.join(event_types)} ({device_count} devices)"
            return

    # No existing cluster — create new one
    all_items = [incident] + all_siblings
    event_types = sorted({i.event_type for i in all_items if i.event_type})
    device_count = len({i.primary_source_ip for i in all_items if i.primary_source_ip})
    cluster = IncidentCluster(
        title=f"Correlated: {' + '.join(event_types)} ({device_count} devices)",
        status="open",
        severity=max(
            (i.severity for i in all_items),
            key=lambda s: _SEVERITY_ORDER.get(s, 0),
        ),
    )
    session.add(cluster)
    session.flush()
    incident.incident_cluster_id = cluster.id
    for sib in all_siblings:
        if sib.incident_cluster_id is None:
            sib.incident_cluster_id = cluster.id


def correlate_event(
    session: Session, event: NormalizedEvent
) -> tuple[Incident | None, str]:
    """Create or update an incident based on a normalized event.

    Returns ``(incident, action)`` where *action* is one of:
    ``"no_match"`` | ``"issue"`` | ``"auto_resolved"`` |
    ``"flap_detected"`` | ``"recovery_pending_verify"``
    """
    if not event.correlation_key:
        return None, "no_match"

    if event.event_type in ISSUE_EVENT_TYPES:
        # Suppress false-positive incidents when an interface was intentionally shut down.
        # LINK-5-CHANGED (admin down) fires ~1s before LINEPROTO-5-UPDOWN, so the
        # interface_admin_down event is already in the session when we get here.
        if event.event_type == "interface_down" and event.interface_name:
            cutoff = (event.event_time or utcnow()) - timedelta(seconds=120)
            admin_evt = session.scalar(
                select(NormalizedEvent).where(
                    and_(
                        NormalizedEvent.event_type == "interface_admin_down",
                        NormalizedEvent.source_ip == event.source_ip,
                        NormalizedEvent.interface_name == event.interface_name,
                        NormalizedEvent.event_time >= cutoff,
                    )
                ).limit(1)
            )
            if admin_evt is not None:
                return None, "no_match"

        incident = _latest_open_incident(session, event.correlation_key)
        was_existing = incident is not None
        if incident is None:
            # Check for recently-resolved — reopen instead of creating duplicate
            incident = _latest_recent_incident(session, event.correlation_key)
            if incident is not None:
                incident.status = "new"
                incident.closed_at = None
                incident.requires_attention = True
                _record_history(
                    session,
                    incident_id=incident.id,
                    action="reopened",
                    from_status="resolved",
                    to_status="new",
                    summary=f"Reopened: new {event.event_type} within {_REOPEN_WINDOW_SECONDS}s of resolution",
                )
                was_existing = True
        if incident is None:
            incident = Incident(
                title=event.summary,
                status="new",
                severity=SEVERITY_BY_EVENT_TYPE.get(event.event_type, event.severity or "warning"),
                source="syslog",
                event_type=event.event_type,
                correlation_key=event.correlation_key,
                primary_device_id=event.device_id,
                primary_source_ip=event.source_ip,
                summary=event.summary,
                event_count=0,
                last_event_time=event.event_time,
            )
            session.add(incident)
            session.flush()
            incident.incident_no = f"INC-{incident.id:05d}"
            _record_history(
                session,
                incident_id=incident.id,
                action="created",
                from_status=None,
                to_status="new",
                summary=f"Incident auto-created from {event.event_type}",
                payload={"event_id": event.id, "correlation_key": event.correlation_key},
            )
            _try_cluster_incident(session, incident)
            _try_cascade_cluster(session, incident)

        # Throttle high-frequency storm events: only refresh the incident record every
        # STORM_WINDOW_SECONDS to avoid thousands of identical writes during burst periods.
        if was_existing and event.event_type in STORM_THROTTLE_TYPES:
            last = incident.last_event_time
            if last is not None and (utcnow() - last).total_seconds() < STORM_WINDOW_SECONDS:
                incident.event_count += 1
                _link_event(session, incident.id, event.id)
                return incident, "issue"

        incident.summary = event.summary
        incident.last_event_time = event.event_time or utcnow()
        incident.event_count += 1
        incident.requires_attention = True
        if incident.status == "resolved":
            incident.closed_at = None
        if incident.status not in {"acknowledged", "in_progress", "monitoring"}:
            incident.status = "new"
        _link_event(session, incident.id, event.id)
        return incident, "issue"

    if event.event_type in RECOVERY_EVENT_TYPES:
        incident = _latest_open_incident(session, event.correlation_key)
        if incident is None:
            return None, "no_match"

        # Common bookkeeping
        incident.event_count += 1
        incident.last_event_time = event.event_time or utcnow()
        _link_event(session, incident.id, event.id)

        # Guard 1: Flap detection — 3+ resolutions in the last hour
        if _count_recent_resolutions(session, incident.id, window_secs=3600) >= 3:
            incident.requires_attention = True
            _record_history(
                session,
                incident_id=incident.id,
                action="flap_detected",
                from_status=incident.status,
                to_status=incident.status,
                summary=(
                    f"Flapping detected: recovery event {event.event_type} received "
                    f"but incident has resolved 3+ times in the past hour. "
                    f"Manual review required."
                ),
            )
            return incident, "flap_detected"

        # Guard 2: Escalated incident — defer resolution to AI SSH verification
        if _is_escalated(session, incident.id) and incident.primary_device_id is not None:
            _record_history(
                session,
                incident_id=incident.id,
                action="recovery_detected",
                from_status=incident.status,
                summary=(
                    f"Physical recovery syslog received ({event.event_type}). "
                    f"Pending AI SSH verification before closing."
                ),
            )
            return incident, "recovery_pending_verify"

        # Normal case: immediate resolve
        previous_status = incident.status
        incident.summary = event.summary
        incident.status = "resolved"
        incident.closed_at = event.event_time or utcnow()
        incident.requires_attention = False
        _record_history(
            session,
            incident_id=incident.id,
            action="auto_resolved",
            from_status=previous_status,
            to_status="resolved",
            summary=f"Incident auto-resolved from recovery event {event.event_type}",
            payload={"event_id": event.id, "correlation_key": event.correlation_key},
        )
        return incident, "auto_resolved"

    return None, "no_match"
