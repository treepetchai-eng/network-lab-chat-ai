"""Background runtime helpers for inventory, syslog, and incident analysis."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from src.ops.db import session_scope
from src.ops.service import run_incident_scan, run_inventory_sync, run_syslog_sync

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TaskSnapshot:
    enabled: bool
    running: bool = False
    last_status: str = "idle"
    last_detail: str | None = None
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    next_run_at: datetime | None = None

    def serialize(self) -> dict[str, str | bool | None]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "last_status": self.last_status,
            "last_detail": self.last_detail,
            "last_started_at": self.last_started_at.isoformat() if self.last_started_at else None,
            "last_completed_at": self.last_completed_at.isoformat() if self.last_completed_at else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
        }


class OpsEmbeddedScheduler:
    """Run inventory/syslog/analyzer loops without blocking API startup."""

    def __init__(self) -> None:
        self.inventory_initial = os.getenv("INVENTORY_INITIAL_SYNC", "1") != "0"
        self.syslog_initial = os.getenv("SYSLOG_INITIAL_SYNC", "1") != "0"
        self.inventory_interval = int(os.getenv("INVENTORY_SYNC_INTERVAL_SECONDS", "0") or "0")
        self.syslog_interval = int(os.getenv("SYSLOG_SYNC_INTERVAL_SECONDS", "0") or "0")
        self.analysis_initial = os.getenv("INCIDENT_ANALYZER_INITIAL_RUN", "1") != "0"
        self.analysis_interval = int(os.getenv("INCIDENT_ANALYZER_INTERVAL_SECONDS", "30") or "30")
        self.initial_delay = max(int(os.getenv("OPS_INITIAL_SYNC_DELAY_SECONDS", "2") or "2"), 0)
        self._tasks: list[asyncio.Task] = []
        self._inventory_state = TaskSnapshot(enabled=self.inventory_initial or self.inventory_interval > 0)
        self._syslog_state = TaskSnapshot(enabled=bool(os.getenv("SYSLOG_HOST")) and (self.syslog_initial or self.syslog_interval > 0))
        self._analysis_state = TaskSnapshot(enabled=self.analysis_initial or self.analysis_interval > 0)
        # Ops loop: periodic auto-execute poll
        _ops_loop_interval = int(os.getenv("OPS_LOOP_POLL_INTERVAL", "15") or "15")
        _ops_loop_auto_execute = os.getenv("OPS_LOOP_AUTO_EXECUTE", "0") not in ("", "0")
        self._ops_loop_interval = _ops_loop_interval
        self._ops_loop_state = TaskSnapshot(enabled=_ops_loop_auto_execute)
        # Auto-close: resolve stale monitoring incidents
        _auto_close_enabled = os.getenv("OPS_LOOP_AUTO_CLOSE", "0") != "0"
        self._auto_close_state = TaskSnapshot(enabled=_auto_close_enabled)
        # AI health check tasks: recovery verification + quiet-period probing
        _ai_health_enabled = os.getenv("OPS_LOOP_AI_HEALTH_CHECK", "0") != "0"
        self._ai_recovery_state = TaskSnapshot(enabled=_ai_health_enabled)
        self._ai_health_state = TaskSnapshot(enabled=_ai_health_enabled)

    async def start(self) -> None:
        if self.inventory_initial:
            self._tasks.append(
                asyncio.create_task(
                    self._run_delayed_once("inventory", self.initial_delay, run_inventory_sync, self._inventory_state)
                )
            )
        if self._syslog_state.enabled and self.syslog_initial:
            self._tasks.append(
                asyncio.create_task(
                    self._run_delayed_once("syslog", self.initial_delay + 1, run_syslog_sync, self._syslog_state)
                )
            )
        if self._analysis_state.enabled and self.analysis_initial:
            self._tasks.append(
                asyncio.create_task(
                    self._run_delayed_once("incident_analyzer", self.initial_delay + 2, run_incident_scan, self._analysis_state)
                )
            )
        if self.inventory_interval > 0:
            self._tasks.append(
                asyncio.create_task(
                    self._run_periodic("inventory", self.inventory_interval, run_inventory_sync, self._inventory_state)
                )
            )
        if self._syslog_state.enabled and self.syslog_interval > 0:
            self._tasks.append(
                asyncio.create_task(
                    self._run_periodic("syslog", self.syslog_interval, run_syslog_sync, self._syslog_state)
                )
            )
        if self._analysis_state.enabled and self.analysis_interval > 0:
            self._tasks.append(
                asyncio.create_task(
                    self._run_periodic("incident_analyzer", self.analysis_interval, run_incident_scan, self._analysis_state)
                )
            )
        if self._ops_loop_state.enabled:
            self._tasks.append(
                asyncio.create_task(
                    self._run_periodic_ops_loop(self._ops_loop_interval, self._ops_loop_state)
                )
            )
        if self._auto_close_state.enabled:
            self._tasks.append(
                asyncio.create_task(
                    self._run_periodic("auto_close", 60, self._run_auto_close_sync, self._auto_close_state)
                )
            )
        if self._ai_recovery_state.enabled:
            self._tasks.append(
                asyncio.create_task(
                    self._run_periodic_async("ai_recovery_verify", 60, _ai_recovery_verify_coro, self._ai_recovery_state)
                )
            )
        if self._ai_health_state.enabled:
            self._tasks.append(
                asyncio.create_task(
                    self._run_periodic_async("ai_health_check_quiet", 300, _ai_health_check_quiet_coro, self._ai_health_state)
                )
            )

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    def snapshot(self) -> dict[str, dict[str, str | bool | None]]:
        return {
            "inventory": self._inventory_state.serialize(),
            "syslog": self._syslog_state.serialize(),
            "incident_analyzer": self._analysis_state.serialize(),
            "ops_loop": self._ops_loop_state.serialize(),
            "auto_close": self._auto_close_state.serialize(),
            "ai_recovery_verify": self._ai_recovery_state.serialize(),
            "ai_health_check_quiet": self._ai_health_state.serialize(),
        }

    async def _run_delayed_once(
        self,
        name: str,
        delay_seconds: int,
        func: Callable[..., dict],
        state: TaskSnapshot,
    ) -> None:
        if delay_seconds > 0:
            state.next_run_at = (_utcnow() + timedelta(seconds=delay_seconds)).replace(microsecond=0)
            await asyncio.sleep(delay_seconds)
        await self._run_once(name, func, state)
        state.next_run_at = None

    async def _run_periodic(
        self,
        name: str,
        interval_seconds: int,
        func: Callable[..., dict],
        state: TaskSnapshot,
    ) -> None:
        while True:
            state.next_run_at = (_utcnow() + timedelta(seconds=interval_seconds)).replace(microsecond=0)
            await asyncio.sleep(interval_seconds)
            await self._run_once(name, func, state)

    async def _run_once(
        self,
        name: str,
        func: Callable[..., dict],
        state: TaskSnapshot,
    ) -> None:
        if state.running:
            logger.info("Skipping overlapping %s sync iteration", name)
            return

        state.running = True
        state.last_started_at = _utcnow()
        state.last_status = "running"
        state.last_detail = None
        try:
            result = await asyncio.to_thread(self._run_sync_job, func)
            state.last_status = "ok"
            state.last_detail = self._format_result(result)
            # Trigger AI ops loop when incident scan creates a new incident
            if result.get("incidents_created", 0) > 0 and result.get("touched_incident_id"):
                await self._trigger_ops_loop(result["touched_incident_id"])
        except Exception as exc:  # pragma: no cover - exercised via live runtime
            logger.exception("Background %s sync failed", name)
            state.last_status = "error"
            state.last_detail = str(exc)
        finally:
            state.running = False
            state.last_completed_at = _utcnow()

    @staticmethod
    def _run_sync_job(func: Callable[..., dict]) -> dict:
        with session_scope() as session:
            return func(session, requested_by="system")

    @staticmethod
    async def _trigger_ops_loop(incident_id: int) -> None:
        """Trigger the AI ops loop when a new incident is detected."""
        try:
            from src.ops.ops_loop import on_incident_created
            await on_incident_created(incident_id)
        except Exception as exc:
            logger.error("Failed to trigger ops loop for incident %d: %s", incident_id, exc, exc_info=True)

    @staticmethod
    def _run_auto_close_sync(_session=None, requested_by: str = "system") -> dict:
        from src.ops.ops_loop import auto_close_stale_monitoring
        return auto_close_stale_monitoring()

    @staticmethod
    def _run_sync_ops_loop_job(main_loop: asyncio.AbstractEventLoop) -> dict:
        from src.ops.ops_loop import loop_config, poll_and_execute_approved
        return poll_and_execute_approved(loop_config(), main_loop=main_loop)

    async def _run_periodic_ops_loop(
        self,
        interval_seconds: int,
        state: TaskSnapshot,
    ) -> None:
        while True:
            state.next_run_at = (_utcnow() + timedelta(seconds=interval_seconds)).replace(microsecond=0)
            await asyncio.sleep(interval_seconds)
            if state.running:
                logger.info("Skipping overlapping ops_loop iteration")
                continue
            state.running = True
            state.last_started_at = _utcnow()
            state.last_status = "running"
            main_loop = asyncio.get_running_loop()
            try:
                result = await asyncio.to_thread(self._run_sync_ops_loop_job, main_loop)
                state.last_status = "ok"
                state.last_detail = self._format_result(result)
            except Exception as exc:
                logger.exception("Ops loop poll task failed")
                state.last_status = "error"
                state.last_detail = str(exc)
            finally:
                state.running = False
                state.last_completed_at = _utcnow()

    async def _run_periodic_async(
        self,
        name: str,
        interval_seconds: int,
        coro_func: Callable,
        state: TaskSnapshot,
    ) -> None:
        """Periodic loop for async task functions (no asyncio.to_thread wrapper)."""
        while True:
            state.next_run_at = (_utcnow() + timedelta(seconds=interval_seconds)).replace(microsecond=0)
            await asyncio.sleep(interval_seconds)
            if state.running:
                logger.info("Skipping overlapping %s iteration", name)
                continue
            state.running = True
            state.last_started_at = _utcnow()
            state.last_status = "running"
            state.last_detail = None
            try:
                result = await coro_func()
                state.last_status = "ok"
                state.last_detail = self._format_result(result)
            except Exception as exc:
                logger.exception("Async periodic task %s failed", name)
                state.last_status = "error"
                state.last_detail = str(exc)
            finally:
                state.running = False
                state.last_completed_at = _utcnow()

    @staticmethod
    def _format_result(result: dict) -> str:
        parts = []
        if "job_id" in result:
            parts.append(f"job #{result['job_id']}")
        for key in ("created", "updated", "total", "files", "raw_logs", "events", "incidents_touched", "logs_analyzed", "incidents_created", "incidents_updated", "no_issue_windows"):
            value = result.get(key)
            if value is not None:
                parts.append(f"{key}={value}")
        return ", ".join(parts) if parts else "completed"


def scheduler_enabled() -> bool:
    return os.getenv("OPS_EMBEDDED_SCHEDULER", "1") != "0"


# ---------------------------------------------------------------------------
# Coroutine wrappers for async periodic tasks (deferred imports)
# ---------------------------------------------------------------------------


async def _ai_recovery_verify_coro() -> dict:
    from src.ops.ops_loop import run_ai_recovery_verify
    return await run_ai_recovery_verify()


async def _ai_health_check_quiet_coro() -> dict:
    from src.ops.ops_loop import run_ai_health_check_quiet
    return await run_ai_health_check_quiet()
