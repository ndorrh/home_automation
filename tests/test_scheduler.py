"""Tests for persistent AV/screen recording scheduler."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from app.services.scheduler import ScheduleCreate, SchedulerService, compute_next_run


def test_compute_next_run_modes() -> None:
    now = datetime(2026, 11, 20, 8, 0, 0).astimezone()
    daily_later = compute_next_run("daily", now, time_of_day="09:30")
    assert daily_later == now.replace(hour=9, minute=30)

    daily_tomorrow = compute_next_run("daily", now, time_of_day="07:00")
    assert daily_tomorrow == (now + timedelta(days=1)).replace(hour=7, minute=0)

    interval_next = compute_next_run("interval", now, interval_minutes=15)
    assert interval_next == now + timedelta(minutes=15)


def test_scheduler_persistence_and_execution(tmp_path: Path) -> None:
    store = tmp_path / "schedules.json"
    ran: list[str] = []

    async def fake_exec(item) -> str:
        ran.append(item.id)
        return "ok"

    async def scenario() -> None:
        svc = SchedulerService(store, fake_exec)
        created = await svc.create_item(
            ScheduleCreate(
                name="Snap Test",
                target="screen_snapshot",
                mode="interval",
                interval_minutes=1,
            )
        )
        assert store.exists()

        reloaded = SchedulerService(store, fake_exec)
        assert len(reloaded.list_items()) == 1

        future_now = datetime.now().astimezone() + timedelta(minutes=2)
        await reloaded.tick(now=future_now)
        await asyncio.sleep(0.05)
        assert created["id"] in ran

        await reloaded.delete_item(created["id"])
        assert reloaded.list_items() == []

    asyncio.run(scenario())
