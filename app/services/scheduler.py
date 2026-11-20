"""Persistent recording scheduler for microphone, camera, and screen capture."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from app.errors import AppError, NotFoundError

logger = logging.getLogger("smart_home_av.scheduler")

ScheduleTarget = Literal[
    "audio_record",
    "camera_snapshot",
    "camera_record",
    "screen_snapshot",
    "screen_record",
]
ScheduleMode = Literal["once", "daily", "interval"]


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    target: ScheduleTarget
    mode: ScheduleMode
    time_of_day: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    run_at: datetime | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    duration_seconds: int = Field(default=30, ge=5, le=3600)
    enabled: bool = True


class ScheduleItem(BaseModel):
    id: str
    name: str
    target: ScheduleTarget
    mode: ScheduleMode
    time_of_day: str | None = None
    run_at: datetime | None = None
    interval_minutes: int | None = None
    duration_seconds: int = 30
    enabled: bool = True
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str = "pending"


def compute_next_run(
    mode: ScheduleMode,
    now: datetime,
    time_of_day: str | None = None,
    run_at: datetime | None = None,
    interval_minutes: int | None = None,
) -> datetime | None:
    """Compute next scheduled UTC-aware run time."""
    if mode == "once":
        if run_at is None:
            raise AppError("INVALID_SCHEDULE", "'run_at' is required for once schedules.", 400)
        aware = run_at if run_at.tzinfo else run_at.replace(tzinfo=now.tzinfo)
        return aware if aware > now else None

    if mode == "interval":
        mins = interval_minutes or 60
        return now + timedelta(minutes=mins)

    if not time_of_day:
        raise AppError("INVALID_SCHEDULE", "'time_of_day' (HH:MM) is required for daily schedules.", 400)
    hour, minute = (int(p) for p in time_of_day.split(":"))
    if hour > 23 or minute > 59:
        raise AppError("INVALID_SCHEDULE", "Invalid HH:MM time_of_day.", 400)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


class SchedulerService:
    """Executes persistent audio, camera, and screen schedules without device overlap."""

    def __init__(
        self,
        store_path: Path,
        executor: Callable[[ScheduleItem], Awaitable[str]],
    ) -> None:
        self._store_path = store_path
        self._executor = executor
        self._lock = asyncio.Lock()
        self._items: list[ScheduleItem] = []
        self._running_targets: set[str] = set()
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._load()

    def list_items(self) -> list[dict]:
        return [item.model_dump(mode="json") for item in self._items]

    async def create_item(self, payload: ScheduleCreate) -> dict:
        now = datetime.now().astimezone()
        next_run = compute_next_run(
            payload.mode, now, payload.time_of_day, payload.run_at, payload.interval_minutes
        )
        if next_run is None:
            raise AppError("INVALID_SCHEDULE", "Scheduled time must be in the future.", 400)

        item = ScheduleItem(
            id=secrets.token_hex(6),
            name=payload.name.strip(),
            target=payload.target,
            mode=payload.mode,
            time_of_day=payload.time_of_day,
            run_at=payload.run_at,
            interval_minutes=payload.interval_minutes,
            duration_seconds=payload.duration_seconds,
            enabled=payload.enabled,
            next_run_at=next_run if payload.enabled else None,
        )
        async with self._lock:
            self._items.append(item)
            self._save()
        return item.model_dump(mode="json")

    async def toggle_item(self, schedule_id: str, enabled: bool) -> dict:
        now = datetime.now().astimezone()
        async with self._lock:
            item = self._find(schedule_id)
            item.enabled = enabled
            item.next_run_at = (
                compute_next_run(
                    item.mode, now, item.time_of_day, item.run_at, item.interval_minutes
                )
                if enabled
                else None
            )
            self._save()
            return item.model_dump(mode="json")

    async def delete_item(self, schedule_id: str) -> None:
        async with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item.id != schedule_id]
            if len(self._items) == before:
                raise NotFoundError("Schedule not found.")
            self._save()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping:
            await self.tick()
            await asyncio.sleep(5)

    async def tick(self, now: datetime | None = None) -> None:
        current = now or datetime.now().astimezone()
        due: list[ScheduleItem] = []
        async with self._lock:
            for item in self._items:
                if not item.enabled or item.next_run_at is None:
                    continue
                target_dt = (
                    item.next_run_at
                    if item.next_run_at.tzinfo
                    else item.next_run_at.replace(tzinfo=current.tzinfo)
                )
                if target_dt <= current and item.target not in self._running_targets:
                    self._running_targets.add(item.target)
                    item.next_run_at = compute_next_run(
                        item.mode, current, item.time_of_day, item.run_at, item.interval_minutes
                    )
                    if item.mode == "once":
                        item.enabled = False
                    due.append(item)
            if due:
                self._save()

        for item in due:
            asyncio.create_task(self._run_job(item, current))

    async def _run_job(self, item: ScheduleItem, started_at: datetime) -> None:
        try:
            result = await self._executor(item)
            status = f"ok: {result}"
        except Exception as exc:
            logger.error("Scheduled job '%s' failed: %s", item.name, exc)
            status = f"error: {exc}"
        finally:
            async with self._lock:
                self._running_targets.discard(item.target)
                item.last_run_at = started_at
                item.last_status = status
                self._save()

    def _find(self, schedule_id: str) -> ScheduleItem:
        for item in self._items:
            if item.id == schedule_id:
                return item
        raise NotFoundError("Schedule not found.")

    def _load(self) -> None:
        if not self._store_path.exists():
            return
        try:
            raw = json.loads(self._store_path.read_text(encoding="utf-8"))
            self._items = [ScheduleItem.model_validate(row) for row in raw]
        except Exception as exc:
            logger.warning("Could not load schedules.json: %s", exc)
            self._items = []

    def _save(self) -> None:
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        data = [item.model_dump(mode="json") for item in self._items]
        self._store_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
