"""Sync syslog-ng archived logs from the remote collector into PostgreSQL."""

from __future__ import annotations

import os
from pathlib import PurePosixPath

import paramiko
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ops.models import SyslogCheckpoint
from src.ops.syslog_ingest import SyslogIngressRecord, ingest_syslog_records


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def _open_sftp():
    host = _require_env("SYSLOG_HOST")
    user = _require_env("SYSLOG_USER")
    password = _require_env("SYSLOG_PASS")
    port = int(os.getenv("SYSLOG_PORT", "22"))

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username=user, password=password, timeout=15)
    return client, client.open_sftp()


def _iter_source_dirs(sftp: paramiko.SFTPClient, root: str) -> list[str]:
    entries = []
    for attr in sftp.listdir_attr(root):
        if attr.filename.startswith("."):
            continue
        mode = attr.st_mode
        if mode & 0o040000:
            entries.append(attr.filename)
    return sorted(entries)


def _iter_log_files(sftp: paramiko.SFTPClient, root: str) -> list[tuple[str, str]]:
    paths: list[tuple[str, str]] = []
    for source_dir in _iter_source_dirs(sftp, root):
        remote_dir = str(PurePosixPath(root) / source_dir)
        try:
            files = sorted(name for name in sftp.listdir(remote_dir) if name.endswith(".log"))
        except OSError:
            continue
        for name in files:
            paths.append((source_dir, str(PurePosixPath(remote_dir) / name)))
    return paths


def sync_syslog_from_remote(session: Session) -> dict[str, int]:
    """Sync all new lines from the remote syslog archive."""
    root = os.getenv("SYSLOG_ROOT", "/data/syslog")
    client, sftp = _open_sftp()
    collector_name = os.getenv("SYSLOG_HOST", "remote_collector")
    counts = {"files": 0, "received": 0, "duplicates": 0, "raw_logs": 0, "events": 0, "incidents_touched": 0}

    try:
        for source_ip, path in _iter_log_files(sftp, root):
            counts["files"] += 1
            checkpoint = session.scalar(
                select(SyslogCheckpoint).where(SyslogCheckpoint.file_path == path)
            )
            remote_file = sftp.open(path, "r")
            try:
                size = remote_file.stat().st_size
                start_offset = checkpoint.offset if checkpoint else 0
                if start_offset > size:
                    start_offset = 0
                remote_file.seek(start_offset)
                pending_records: list[SyslogIngressRecord] = []

                while True:
                    line_start = remote_file.tell()
                    raw_line = remote_file.readline()
                    if not raw_line:
                        break
                    line_end = remote_file.tell()
                    message = raw_line.rstrip("\r\n")
                    if not message:
                        continue

                    pending_records.append(
                        SyslogIngressRecord(
                            source_ip=source_ip,
                            raw_message=message,
                            file_path=path,
                            offset_start=line_start,
                            offset_end=line_end,
                            collector_name=collector_name,
                            ingest_source="remote_file",
                        )
                    )

                if pending_records:
                    result = ingest_syslog_records(session, pending_records)
                    for key in ("received", "duplicates", "raw_logs", "events", "incidents_touched"):
                        counts[key] += result.get(key, 0)

                if checkpoint is None:
                    checkpoint = SyslogCheckpoint(file_path=path, source_ip=source_ip, offset=remote_file.tell())
                    session.add(checkpoint)
                else:
                    checkpoint.source_ip = source_ip
                    checkpoint.offset = remote_file.tell()
            finally:
                remote_file.close()
    finally:
        sftp.close()
        client.close()

    return counts
