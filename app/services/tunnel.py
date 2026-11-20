"""Managed Cloudflare Quick Tunnel subprocess with isolated config and auto-reconnect."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable

from app.bootstrap import ensure_cloudflared_quick_config, resolve_cloudflared_binary
from app.config import ROOT_DIR
from app.services.network import has_active_internet

logger = logging.getLogger("smart_home_av.tunnel")

TRYCLOUDFLARE_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def extract_tunnel_url(line: str) -> str | None:
    """Extract a *.trycloudflare.com URL from a cloudflared output line."""
    match = TRYCLOUDFLARE_URL_RE.search(line)
    return match.group(0) if match else None


def build_tunnel_args(config_path: Path, local_port: int) -> list[str]:
    """Build isolated quick-tunnel CLI arguments ignoring user ~/.cloudflared/config.yml."""
    return [
        "tunnel",
        "--config",
        str(config_path),
        "--edge-ip-version",
        "4",
        "--protocol",
        "http2",
        "--no-autoupdate",
        "--url",
        f"http://127.0.0.1:{local_port}",
    ]


class CloudflaredTunnel:
    """Supervises a free Cloudflare Quick Tunnel targeting the local server."""

    def __init__(
        self,
        local_port: int,
        binary_name: str = "cloudflared",
        on_url: Callable[[str], Awaitable[None]] | None = None,
        root_dir: Path = ROOT_DIR,
    ) -> None:
        self._port = local_port
        self._binary = binary_name
        self._on_url = on_url
        self._root = root_dir
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._stopping = False
        self._tunnel_url: str | None = None

    @property
    def tunnel_url(self) -> str | None:
        return self._tunnel_url

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stopping = True
        await self._terminate_proc()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._tunnel_url = None

    async def _run_loop(self) -> None:
        backoff = 3
        while not self._stopping:
            resolved = resolve_cloudflared_binary(self._binary, self._root)
            if not resolved:
                logger.warning("cloudflared not found; retrying check in 30s.")
                await asyncio.sleep(30)
                continue

            online = await asyncio.to_thread(has_active_internet)
            if not online:
                logger.info("Waiting for active internet before starting cloudflared...")
                await asyncio.sleep(5)
                continue

            await self._run_once(resolved)
            if self._stopping:
                break
            logger.info("Restarting cloudflared tunnel in %ss...", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _run_once(self, binary_path: str) -> None:
        self._tunnel_url = None
        self._pending_url: str | None = None
        self._registered = False
        cfg_path = ensure_cloudflared_quick_config(self._root)
        args = build_tunnel_args(cfg_path, self._port)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                binary_path,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info("Started isolated cloudflared quick tunnel (pid=%s).", self._proc.pid)
            await asyncio.gather(
                self._consume_stream(self._proc.stdout),
                self._consume_stream(self._proc.stderr),
            )
            await self._proc.wait()
        except asyncio.CancelledError:
            await self._terminate_proc()
            raise
        except Exception as exc:
            logger.error("cloudflared tunnel error: %s", exc)
        finally:
            self._proc = None

    async def _consume_stream(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if " ERR " in line:
                if "DNS local resolver" in line:
                    logger.debug("cloudflared: %s", line)
                else:
                    logger.warning("cloudflared: %s", line)
            url = extract_tunnel_url(line)
            if url:
                self._pending_url = url
            if "Registered tunnel connection" in line:
                self._registered = True
            if self._pending_url and self._registered and self._pending_url != self._tunnel_url:
                self._tunnel_url = self._pending_url
                await asyncio.sleep(5.0)
                logger.info("Cloudflare tunnel ready: %s", self._tunnel_url)
                if self._on_url:
                    await self._on_url(self._tunnel_url)

    async def _terminate_proc(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except Exception:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
