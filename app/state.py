"""Application runtime state container and WebSocket broadcast hub."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket

from app.config import Settings
from app.crypto import SessionKeyStore
from app.devices.power import PowerManager
from app.media.audio_manager import AudioManager
from app.media.camera_manager import CameraManager
from app.media.screen_manager import ScreenManager
from app.security import LoginRateLimiter
from app.services.hotspot import HotspotManager
from app.services.network import get_lan_urls, has_active_internet
from app.services.notifier import EmailNotifier
from app.services.scheduler import SchedulerService
from app.services.tunnel import CloudflaredTunnel

logger = logging.getLogger("smart_home_av.state")


class WebSocketHub:
    """Tracks connected authenticated dashboard WebSocket clients."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    def register(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def unregister(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast_json(self, payload: dict) -> None:
        if not self._clients:
            return
        text = json.dumps(payload)
        stale: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(text)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._clients.discard(ws)


@dataclass
class AppState:
    """Shared application services bound to FastAPI app.state."""

    settings: Settings
    audio: AudioManager
    camera: CameraManager
    screen: ScreenManager
    power: PowerManager
    notifier: EmailNotifier
    hotspot: HotspotManager
    key_store: SessionKeyStore
    scheduler: SchedulerService
    tunnel: CloudflaredTunnel | None = None
    rate_limiter: LoginRateLimiter = field(default_factory=LoginRateLimiter)
    ws_hub: WebSocketHub = field(default_factory=WebSocketHub)

    def build_status_payload(self) -> dict:
        return {
            "success": True,
            "audio": self.audio.status(),
            "camera": self.camera.status(),
            "screen": self.screen.status(),
            "power": {"keep_awake": self.power.is_active},
            "hotspot": self.hotspot.cached_status(),
            "network": {
                "lan_urls": get_lan_urls(self.settings.app_port),
                "tunnel_url": self.tunnel.tunnel_url if self.tunnel else None,
                "internet_online": has_active_internet(timeout=0.8),
            },
        }
