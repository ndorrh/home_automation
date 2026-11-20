"""Tests for offline Windows hotspot failover watchdog."""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.services.hotspot import HotspotManager


def test_offline_hotspot_watchdog_and_manual_override() -> None:
    cfg = Settings(_env_file=None, auto_hotspot_on_offline=True)
    online_state = {"online": True}
    calls: list[str] = []

    async def fake_run_ps(action: str) -> dict:
        calls.append(action)
        if action == "start":
            return {"active": True, "mode": "wifi_direct_ap", "ssid": "NDORRH-PC 1977"}
        if action == "stop":
            return {"active": False, "mode": "stopped", "ssid": "NDORRH-PC 1977"}
        return {"active": "start" in calls and calls[-1] != "stop", "mode": "none"}

    async def scenario() -> None:
        mgr = HotspotManager(cfg, internet_check=lambda: online_state["online"])
        mgr._run_ps = fake_run_ps  # type: ignore[method-assign]

        # Online -> does not start hotspot
        await mgr.evaluate_once()
        assert calls == []

        # Offline 1st check -> waits for streak of 2
        online_state["online"] = False
        await mgr.evaluate_once()
        assert calls == []

        # Offline 2nd check -> starts hotspot
        await mgr.evaluate_once()
        assert "start" in calls

        # Manual stop while still offline -> respects manual override
        await mgr.stop_hotspot(manual=True)
        prev_len = len(calls)
        await mgr.evaluate_once()
        assert len(calls) == prev_len

    asyncio.run(scenario())
