"""Shared syslog ingestion helpers for both file polling and HTTP push."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha1
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ops.db import utcnow
from src.ops.models import Device, DeviceInterface, NormalizedEvent, RawLog
from src.ops.syslog_parser import ParsedEvent, parse_syslog_line


@dataclass(frozen=True)
class SyslogIngressRecord:
    source_ip: str
    raw_message: str
    file_path: str | None = None
    offset_start: int = 0
    offset_end: int | None = None
    collector_name: str | None = None
    ingest_source: str = "http_push"
    event_uid: str | None = None
    reference_time: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _device_lookup(session: Session) -> dict[str, Device]:
    devices = session.scalars(select(Device)).all()
    result: dict[str, Device] = {}
    for device in devices:
        result[device.mgmt_ip] = device
        result[device.hostname.upper()] = device
    return result


def _synthetic_file_path(record: SyslogIngressRecord, event_uid: str, reference_time: datetime) -> str:
    collector = (record.collector_name or "collector").replace("/", "-")
    source_ip = (record.source_ip or "unknown").replace("/", "-")
    date_part = reference_time.strftime("%Y%m%d")
    return str(PurePosixPath("/ingest") / record.ingest_source / collector / source_ip / f"{date_part}-{event_uid}.log")


def _stable_event_uid(record: SyslogIngressRecord, file_path: str, reference_time: datetime) -> str:
    digest = sha1()
    digest.update((record.source_ip or "").encode("utf-8"))
    digest.update(b"\x00")
    digest.update((record.raw_message or "").encode("utf-8"))
    digest.update(b"\x00")
    digest.update((file_path or "").encode("utf-8"))
    digest.update(b"\x00")
    digest.update(reference_time.isoformat().encode("utf-8"))
    return digest.hexdigest()


def _resolved_offsets(record: SyslogIngressRecord, event_uid: str) -> tuple[int, int]:
    if record.ingest_source != "remote_file" and record.offset_start == 0 and record.offset_end is None:
        offset_start = int(event_uid[:15], 16)
    else:
        offset_start = record.offset_start
    offset_end = record.offset_end if record.offset_end is not None else offset_start + len(record.raw_message.encode("utf-8"))
    return offset_start, offset_end


def _parser_input_message(record: SyslogIngressRecord, reference_time: datetime) -> tuple[str, bool]:
    raw_message = record.raw_message.strip()
    if len(raw_message) >= 16 and raw_message[:3].isalpha() and raw_message[3] == " ":
        return raw_message, False
    marker_index = raw_message.find("%")
    if marker_index > 0 and record.source_ip:
        raw_message = raw_message[marker_index:]
    if raw_message.startswith("%") and record.source_ip:
        reconstructed = (
            f"{reference_time.strftime('%b')} {reference_time.day} "
            f"{reference_time.strftime('%H:%M:%S')} {record.source_ip} {raw_message}"
        )
        return reconstructed, True
    return raw_message, False


def _enrich_event_details(parsed: ParsedEvent, record: SyslogIngressRecord, event_uid: str) -> dict[str, Any]:
    details = dict(parsed.details)
    details["ingest_source"] = record.ingest_source
    details["event_uid"] = event_uid
    if record.collector_name:
        details["collector_name"] = record.collector_name
    if record.metadata:
        details["collector_metadata"] = record.metadata
    return details


def _upsert_device_interface(
    session: Session,
    *,
    device: Device | None,
    event: NormalizedEvent,
) -> None:
    interface_name = (event.interface_name or "").strip()
    if device is None or not interface_name:
        return

    interface = session.scalar(
        select(DeviceInterface).where(
            DeviceInterface.device_id == device.id,
            DeviceInterface.name == interface_name,
        )
    )
    if interface is None:
        interface = DeviceInterface(
            device_id=device.id,
            name=interface_name,
            protocol=event.protocol,
            last_state=event.state,
            last_event_id=event.id,
            last_event_time=event.event_time,
            event_count=1,
            metadata_json={
                "source": "normalized_event",
                "source_ip": event.source_ip,
            },
        )
        session.add(interface)
        return

    interface.protocol = event.protocol or interface.protocol
    interface.last_state = event.state or interface.last_state
    interface.last_event_id = event.id
    interface.last_event_time = event.event_time or interface.last_event_time
    interface.event_count += 1
    metadata = dict(interface.metadata_json or {})
    metadata["source"] = "normalized_event"
    metadata["source_ip"] = event.source_ip
    interface.metadata_json = metadata


def ingest_syslog_record(
    session: Session,
    record: SyslogIngressRecord,
    *,
    devices: dict[str, Device] | None = None,
) -> dict[str, Any]:
    """Persist one raw syslog record and derived normalized event if parse succeeds."""
    if devices is None:
        devices = _device_lookup(session)

    ingested_at = utcnow()
    reference_time = record.reference_time or ingested_at
    provisional_uid = record.event_uid or _stable_event_uid(record, record.file_path or "", reference_time)
    file_path = record.file_path or _synthetic_file_path(record, provisional_uid, reference_time)
    event_uid = record.event_uid or _stable_event_uid(record, file_path, reference_time)
    offset_start, offset_end = _resolved_offsets(record, event_uid)

    if session.scalar(select(RawLog.id).where(RawLog.event_uid == event_uid)) is not None:
        return {"received": 1, "duplicates": 1, "raw_logs": 0, "events": 0, "incidents_touched": 0, "touched_incident_ids": set()}

    parser_message, reconstructed_header = _parser_input_message(record, reference_time)
    parsed = parse_syslog_line(parser_message, file_path, reference_time=reference_time)
    raw_log = RawLog(
        source_ip=(record.source_ip or (parsed.source_ip if parsed else "")).strip(),
        file_path=file_path,
        offset_start=offset_start,
        offset_end=offset_end,
        log_time=parsed.event_time if parsed else None,
        raw_message=record.raw_message,
        ingested_at=ingested_at,
        ingest_source=record.ingest_source,
        collector_name=record.collector_name,
        event_uid=event_uid,
    )
    session.add(raw_log)
    session.flush()

    counts = {"received": 1, "duplicates": 0, "raw_logs": 1, "events": 0, "incidents_touched": 0, "touched_incident_ids": set()}
    if parsed is None:
        return counts

    resolved_source_ip = (parsed.source_ip or "").strip()
    if not resolved_source_ip or resolved_source_ip == "unknown":
        resolved_source_ip = (record.source_ip or "").strip()
    device = devices.get(resolved_source_ip)
    event_details = _enrich_event_details(parsed, record, event_uid)
    if reconstructed_header:
        event_details["collector_reconstructed_header"] = True

    event = NormalizedEvent(
        raw_log_id=raw_log.id,
        event_time=parsed.event_time,
        source_ip=resolved_source_ip,
        device_id=device.id if device else None,
        hostname=device.hostname if device else None,
        severity_num=parsed.severity_num,
        severity=parsed.severity,
        facility=parsed.facility,
        mnemonic=parsed.mnemonic,
        event_code=parsed.event_code,
        event_type=parsed.event_type,
        protocol=parsed.protocol,
        interface_name=parsed.interface_name,
        neighbor=parsed.neighbor,
        state=parsed.state,
        correlation_key=parsed.correlation_key,
        summary=parsed.summary,
        details_json=event_details,
    )
    session.add(event)
    session.flush()
    _upsert_device_interface(session, device=device, event=event)
    session.flush()
    counts["events"] = 1
    return counts


def ingest_syslog_records(session: Session, records: list[SyslogIngressRecord]) -> dict[str, Any]:
    """Persist a batch of syslog records with shared device lookup."""
    devices = _device_lookup(session)
    counts = {"received": 0, "duplicates": 0, "raw_logs": 0, "events": 0, "incidents_touched": 0, "touched_incident_ids": set()}
    for record in records:
        result = ingest_syslog_record(session, record, devices=devices)
        for key, value in result.items():
            if key == "touched_incident_ids":
                counts[key].update(value)
            else:
                counts[key] += value
    counts["touched_incident_ids"] = list(counts["touched_incident_ids"])
    return counts
