"""Windows Mobile Hotspot & offline SoftAP controller with auto-failover watchdog."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Callable

from app.config import ROOT_DIR, Settings
from app.services.network import has_active_internet

logger = logging.getLogger("smart_home_av.hotspot")


class HotspotManager:
    """Monitors internet connectivity and manages Windows Hotspot for offline access."""

    def __init__(
        self,
        settings: Settings,
        internet_check: Callable[[], bool] = has_active_internet,
        poll_interval: float = 15.0,
        root_dir: Path = ROOT_DIR,
    ) -> None:
        self._settings = settings
        self._internet_check = internet_check
        self._poll_interval = poll_interval
        self._script = root_dir / "scripts" / "hotspot_control.ps1"
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._offline_streak = 0
        self._manual_off_override = False
        self._last_status: dict = {
            "active": False,
            "mode": "none",
            "ssid": None,
            "hotspot_ip": None,
            "auto_enabled": settings.auto_hotspot_on_offline,
        }

    def cached_status(self) -> dict:
        return dict(self._last_status)

    def start_watchdog(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._watchdog_loop())

    async def stop_watchdog(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def refresh_status(self) -> dict:
        res = await self._run_ps("status")
        if res is not None:
            res["auto_enabled"] = self._settings.auto_hotspot_on_offline
            self._last_status = res
        return self.cached_status()

    async def start_hotspot(self, manual: bool = True) -> dict:
        if manual:
            self._manual_off_override = False
        res = await self._run_ps("start")
        if res is not None:
            res["auto_enabled"] = self._settings.auto_hotspot_on_offline
            self._last_status = res
            logger.info("Hotspot started: mode=%s ssid=%s", res.get("mode"), res.get("ssid"))
        return self.cached_status()

    async def stop_hotspot(self, manual: bool = True) -> dict:
        if manual:
            self._manual_off_override = True
        res = await self._run_ps("stop")
        if res is not None:
            res["auto_enabled"] = self._settings.auto_hotspot_on_offline
            self._last_status = res
            logger.info("Hotspot stopped.")
        return self.cached_status()

    async def _watchdog_loop(self) -> None:
        await self.refresh_status()
        while not self._stopping:
            await asyncio.sleep(self._poll_interval)
            await self.evaluate_once()

    async def evaluate_once(self) -> None:
        """Check connectivity once and auto-enable hotspot when offline."""
        if not self._settings.auto_hotspot_on_offline:
            return
        online = await asyncio.to_thread(self._internet_check)
        if online:
            self._offline_streak = 0
            self._manual_off_override = False
            return

        self._offline_streak += 1
        if self._offline_streak < 2 or self._manual_off_override:
            return

        current = await self.refresh_status()
        if not current.get("active"):
            logger.info("Internet offline for 2 checks; enabling PC hotspot for local access.")
            await self.start_hotspot(manual=False)

    async def _run_ps(self, action: str) -> dict | None:
        if sys.platform != "win32" or not self._script.exists():
            return None
        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self._script),
            "-Action",
            action,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "Hotspot script (%s) failed: %s",
                action,
                stderr.decode("utf-8", errors="replace").strip(),
            )
            return None
        text = stdout.decode("utf-8", errors="replace").strip()
        try:
            return json.loads(text) if text else None
        except json.JSONDecodeError:
            logger.warning("Unexpected hotspot output: %s", text)
            return None
