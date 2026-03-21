"""Inventory synchronization and query helpers."""

from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from src.ops.models import Device, Incident, NormalizedEvent

_INVENTORY_PATH = Path(__file__).resolve().parent.parent.parent / "inventory" / "inventory.csv"
_OPEN_INCIDENT_STATUSES = ("new", "acknowledged", "in_progress", "monitoring", "open", "investigating")


def _guess_vendor(os_platform: str) -> str:
    if os_platform.startswith("cisco"):
        return "cisco"
    return os_platform.split("_", 1)[0]


def sync_inventory_from_csv(session: Session) -> dict[str, int]:
    """Upsert CSV inventory rows into the devices table."""
    created = 0
    updated = 0

    with open(_INVENTORY_PATH, newline="") as fh:
        rows = list(csv.DictReader(fh))

    for row in rows:
        existing = session.scalar(
            select(Device).where(Device.hostname == row["hostname"])
        )
        payload = {
            "hostname": row["hostname"],
            "mgmt_ip": row["ip_address"],
            "os_platform": row["os_platform"],
            "device_role": row["device_role"],
            "site": row["site"],
            "version": row.get("version", ""),
            "vendor": _guess_vendor(row["os_platform"]),
            "metadata_json": {"seed_source": "inventory.csv"},
        }
        if existing is None:
            session.add(Device(**payload))
            created += 1
        else:
            for key, value in payload.items():
                setattr(existing, key, value)
            updated += 1

    return {"created": created, "updated": updated, "total": len(rows)}


def _device_stats_query():
    open_incidents_sq = (
        select(
            Incident.primary_device_id.label("device_id"),
            func.count().label("open_incident_count"),
        )
        .where(Incident.status.in_(_OPEN_INCIDENT_STATUSES))
        .group_by(Incident.primary_device_id)
        .subquery()
    )

    latest_event_ranked_sq = (
        select(
            NormalizedEvent.device_id.label("device_id"),
            NormalizedEvent.summary.label("last_event_summary"),
            NormalizedEvent.event_time.label("last_event_time"),
            func.row_number().over(
                partition_by=NormalizedEvent.device_id,
                order_by=(NormalizedEvent.event_time.desc().nullslast(), NormalizedEvent.id.desc()),
            ).label("row_number"),
        )
        .where(NormalizedEvent.device_id.is_not(None))
        .subquery()
    )

    latest_event_sq = (
        select(
            latest_event_ranked_sq.c.device_id,
            latest_event_ranked_sq.c.last_event_summary,
            latest_event_ranked_sq.c.last_event_time,
        )
        .where(latest_event_ranked_sq.c.row_number == 1)
        .subquery()
    )

    return (
        select(
            Device.id,
            Device.hostname,
            Device.mgmt_ip,
            Device.os_platform,
            Device.device_role,
            Device.site,
            Device.version,
            Device.vendor,
            Device.enabled,
            func.coalesce(open_incidents_sq.c.open_incident_count, 0).label("open_incident_count"),
            latest_event_sq.c.last_event_summary,
            latest_event_sq.c.last_event_time,
        )
        .select_from(Device)
        .outerjoin(open_incidents_sq, open_incidents_sq.c.device_id == Device.id)
        .outerjoin(latest_event_sq, latest_event_sq.c.device_id == Device.id)
    )


def list_devices_with_stats(
    session: Session,
    *,
    q: str | None = None,
    site: str | None = None,
    role: str | None = None,
    has_open_incidents: bool = False,
    sort_by: str = "hostname",
    sort_dir: str = "asc",
    page: int = 1,
    page_size: int = 25,
) -> dict:
    """Return paginated device inventory with incident and event stats."""
    query = _device_stats_query()

    conditions = []
    if q:
        like_value = f"%{q.strip()}%"
        conditions.append(or_(
            Device.hostname.ilike(like_value),
            Device.mgmt_ip.ilike(like_value),
            Device.site.ilike(like_value),
            Device.device_role.ilike(like_value),
            Device.vendor.ilike(like_value),
            Device.os_platform.ilike(like_value),
        ))
    if site:
        conditions.append(Device.site == site)
    if role:
        conditions.append(Device.device_role == role)
    if has_open_incidents:
        conditions.append(func.coalesce(query.selected_columns.open_incident_count, 0) > 0)

    if conditions:
        query = query.where(and_(*conditions))

    sort_columns = {
        "hostname": Device.hostname,
        "site": Device.site,
        "role": Device.device_role,
        "open_incident_count": query.selected_columns.open_incident_count,
        "last_event_time": query.selected_columns.last_event_time,
    }
    order_column = sort_columns.get(sort_by, Device.hostname)
    order_expr = order_column.desc().nullslast() if sort_dir == "desc" else order_column.asc().nullsfirst()
    query = query.order_by(order_expr, Device.hostname.asc())

    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    offset = max(page - 1, 0) * page_size
    rows = session.execute(query.offset(offset).limit(page_size)).all()

    items = [{
        "id": row.id,
        "hostname": row.hostname,
        "mgmt_ip": row.mgmt_ip,
        "os_platform": row.os_platform,
        "device_role": row.device_role,
        "site": row.site,
        "version": row.version,
        "vendor": row.vendor,
        "enabled": row.enabled,
        "open_incident_count": int(row.open_incident_count or 0),
        "last_event_summary": row.last_event_summary,
        "last_event_time": row.last_event_time.isoformat() if row.last_event_time else None,
    } for row in rows]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max((total + page_size - 1) // page_size, 1),
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "facets": {
            "sites": [
                value
                for value in session.scalars(
                    select(Device.site).where(Device.site != "").distinct().order_by(Device.site.asc())
                ).all()
                if value
            ],
            "roles": [
                value
                for value in session.scalars(
                    select(Device.device_role)
                    .where(Device.device_role != "")
                    .distinct()
                    .order_by(Device.device_role.asc())
                ).all()
                if value
            ],
        },
    }
