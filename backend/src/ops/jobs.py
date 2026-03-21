"""Job lifecycle helpers for platform workflows."""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.ops.db import utcnow
from src.ops.models import Job


def create_job(
    session: Session,
    *,
    job_type: str,
    title: str,
    requested_by: str = "manager",
    target_type: str | None = None,
    target_ref: str | None = None,
    payload_json: dict | None = None,
) -> Job:
    job = Job(
        job_type=job_type,
        title=title,
        status="queued",
        requested_by=requested_by,
        target_type=target_type,
        target_ref=target_ref,
        payload_json=payload_json or {},
    )
    session.add(job)
    session.flush()
    return job


def start_job(job: Job) -> None:
    job.status = "running"
    job.started_at = utcnow()


def complete_job(job: Job, *, summary: str | None = None, result_json: dict | None = None) -> None:
    job.status = "succeeded"
    job.completed_at = utcnow()
    job.summary = summary
    job.result_json = result_json or {}


def fail_job(job: Job, error_text: str) -> None:
    job.status = "failed"
    job.error_text = error_text
    job.completed_at = utcnow()

