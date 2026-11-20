"""Periodic & change-triggered Nodemailer notifier with in-memory DPAPI decryption."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from datetime import datetime
from typing import Callable

from app.config import Settings
from app.services.network import get_lan_urls, has_active_internet
from app.vault import GmailCredentials, unseal_gmail_credentials

logger = logging.getLogger("smart_home_av.notifier")


class EmailNotifier:
    """Sends access URLs when internet connects, IPs/tunnel change, or resend interval elapses."""

    def __init__(
        self,
        settings: Settings,
        internet_check: Callable[[], bool] = has_active_internet,
        poll_interval: float = 15.0,
        tunnel_url_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._settings = settings
        self._internet_check = internet_check
        self._poll_interval = poll_interval
        self._tunnel_url_provider = tunnel_url_provider
        self._lock = asyncio.Lock()
        self._last_sent_key: tuple[str | None, tuple[str, ...]] | None = None
        self._last_sent_at: float | None = None
        self._latest_tunnel_url: str | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._stopping = False

    def set_tunnel_url_provider(self, provider: Callable[[], str | None]) -> None:
        self._tunnel_url_provider = provider

    def start_watchdog(self) -> None:
        """Start continuous background scanner for URL changes and periodic reminders."""
        if self._watchdog_task is not None and not self._watchdog_task.done():
            return
        self._stopping = False
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def notify_urls(self, tunnel_url: str | None, force: bool = False) -> bool:
        """Send URLs if changed, periodic interval elapsed, or forced."""
        if tunnel_url is not None:
            self._latest_tunnel_url = tunnel_url
        self.start_watchdog()
        return await self.evaluate_once(force=force)

    async def send_now(self) -> bool:
        """Trigger an immediate email notification using latest active URLs."""
        url = self._current_tunnel_url()
        return await self.evaluate_once(force=True, override_tunnel=url)

    async def stop(self) -> None:
        self._stopping = True
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None

    def _current_tunnel_url(self) -> str | None:
        if self._tunnel_url_provider is not None:
            live = self._tunnel_url_provider()
            if live:
                self._latest_tunnel_url = live
        return self._latest_tunnel_url

    def _should_send(self, key: tuple[str | None, tuple[str, ...]], force: bool) -> bool:
        if force or self._last_sent_key is None or self._last_sent_at is None:
            return True
        if key != self._last_sent_key:
            return True
        elapsed = time.monotonic() - self._last_sent_at
        return elapsed >= float(self._settings.email_resend_minutes * 60)

    async def _watchdog_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self._poll_interval)
            await self.evaluate_once(force=False)

    async def evaluate_once(
        self,
        force: bool = False,
        override_tunnel: str | None = None,
    ) -> bool:
        """Check if notification is due and internet is active, then deliver via Nodemailer."""
        creds = self._decrypt_credentials_in_memory()
        if creds is None:
            return False

        tunnel_url = override_tunnel if override_tunnel is not None else self._current_tunnel_url()
        if self._settings.enable_cloudflared and tunnel_url is None and not force:
            return False

        lan_urls = get_lan_urls(self._settings.app_port)
        key = (tunnel_url, tuple(lan_urls))

        async with self._lock:
            if not self._should_send(key, force):
                return True

            online = await asyncio.to_thread(self._internet_check)
            if not online:
                logger.info("Internet offline; email notification queued until connected.")
                return False

            sent = await self._invoke_nodemailer(creds, lan_urls, tunnel_url)
            if sent:
                self._last_sent_key = key
                self._last_sent_at = time.monotonic()
                return True
            return False

    def _decrypt_credentials_in_memory(self) -> GmailCredentials | None:
        if self._settings.gmail_credentials_enc.strip():
            return unseal_gmail_credentials(self._settings.gmail_credentials_enc)
        user = self._settings.gmail_user.strip()
        pw = self._settings.gmail_app_password.strip()
        to = (self._settings.notify_email_to or user).strip()
        if user and pw and to:
            return GmailCredentials(user=user, app_password=pw, notify_to=to)
        return None

    async def _invoke_nodemailer(
        self,
        creds: GmailCredentials,
        lan_urls: list[str],
        tunnel_url: str | None,
    ) -> bool:
        node_bin = shutil.which("node")
        script = self._settings.mailer_script
        if not node_bin or not script.exists():
            logger.error("Node.js or mailer script missing (%s).", script)
            return False

        payload = json.dumps(
            {
                "lanUrls": lan_urls,
                "tunnelUrl": tunnel_url,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        ).encode("utf-8")

        env = os.environ.copy()
        env["GMAIL_USER"] = creds.user
        env["GMAIL_APP_PASSWORD"] = creds.app_password
        env["NOTIFY_EMAIL_TO"] = creds.notify_to

        proc = await asyncio.create_subprocess_exec(
            node_bin,
            str(script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate(input=payload)
        if proc.returncode != 0:
            logger.error("Nodemailer failed: %s", stderr.decode("utf-8", errors="replace").strip())
            return False

        logger.info("Sent access URLs via Gmail (%s).", stdout.decode("utf-8", errors="replace").strip())
        return True
