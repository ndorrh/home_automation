"""FastAPI application factory, lifecycle management, and Uvicorn entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.bootstrap import run_bootstrap
from app.config import Settings, get_settings
from app.crypto import SessionKeyStore
from app.devices.power import PowerManager
from app.errors import register_exception_handlers
from app.logging_setup import clear_log_file, setup_logging
from app.media.audio_manager import AudioManager
from app.media.camera_manager import CameraManager
from app.media.screen_manager import ScreenManager
from app.routes import auth_routes, media_routes, schedule_routes, system_routes, ws_routes
from app.services.hotspot import HotspotManager
from app.services.network import get_lan_urls
from app.services.notifier import EmailNotifier
from app.services.scheduler import ScheduleItem, SchedulerService
from app.services.tunnel import CloudflaredTunnel
from app.state import AppState

logger = logging.getLogger("smart_home_av.server")


async def _run_scheduled_job(
    item: ScheduleItem,
    audio: AudioManager,
    camera: CameraManager,
    screen: ScreenManager,
) -> str:
    if item.target == "camera_snapshot":
        return await asyncio.to_thread(camera.take_snapshot)
    if item.target == "screen_snapshot":
        return await asyncio.to_thread(screen.take_screenshot)

    start_stop = {
        "audio_record": (audio.start_recording, audio.stop_recording),
        "camera_record": (camera.start_video_recording, camera.stop_video_recording),
        "screen_record": (screen.start_recording, screen.stop_recording),
    }
    start_fn, stop_fn = start_stop[item.target]
    await asyncio.to_thread(start_fn)
    try:
        await asyncio.sleep(item.duration_seconds)
    finally:
        saved = await asyncio.to_thread(stop_fn)
    return saved or "completed"


async def _log_cleanup_loop(ctx: AppState) -> None:
    interval = max(60, ctx.settings.log_clear_minutes * 60)
    while True:
        await asyncio.sleep(interval)
        if clear_log_file(ctx.settings.logs_dir):
            logger.info("Auto-cleared server logs (%d min retention).", ctx.settings.log_clear_minutes)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ctx: AppState = app.state.ctx
    loop = asyncio.get_running_loop()

    def broadcast_status() -> None:
        asyncio.create_task(
            ctx.ws_hub.broadcast_json({"type": "status", "data": ctx.build_status_payload()})
        )

    ctx.audio.bind_loop(loop, status_cb=broadcast_status)
    ctx.power.enable_keep_awake()
    ctx.notifier.start_watchdog()
    ctx.hotspot.start_watchdog()
    ctx.scheduler.start()
    log_cleaner = asyncio.create_task(_log_cleanup_loop(ctx))

    for url in get_lan_urls(ctx.settings.app_port):
        logger.info("Local Wi-Fi dashboard URL: %s", url)

    if ctx.tunnel is not None:
        ctx.tunnel.start()
    else:
        asyncio.create_task(ctx.notifier.notify_urls(None))

    try:
        yield
    finally:
        logger.info("Shutting down Smart Home AV server...")
        log_cleaner.cancel()
        try:
            await log_cleaner
        except asyncio.CancelledError:
            pass
        await ctx.scheduler.stop()
        await ctx.hotspot.stop_watchdog()
        await ctx.notifier.stop()
        if ctx.tunnel is not None:
            await ctx.tunnel.stop()
        ctx.audio.shutdown()
        ctx.camera.shutdown()
        ctx.screen.shutdown()
        ctx.power.restore_normal_sleep()


def create_app(settings: Settings | None = None, start_tunnel: bool = True) -> FastAPI:
    """Create configured FastAPI application instance."""
    run_bootstrap()
    cfg = settings or get_settings()
    setup_logging(cfg.logs_dir, cfg.log_level)

    audio = AudioManager(cfg.recordings_dir, cfg.audio_sample_rate, cfg.enc_public_key)
    camera = CameraManager(cfg.recordings_dir, cfg.camera_index, cfg.enc_public_key)
    screen = ScreenManager(
        cfg.recordings_dir, cfg.screen_monitor_index, cfg.screen_fps, cfg.enc_public_key
    )
    power = PowerManager(cfg.prevent_system_sleep)
    notifier = EmailNotifier(cfg)
    hotspot = HotspotManager(cfg)
    key_store = SessionKeyStore(cfg.session_ttl_seconds)

    async def execute_job(item: ScheduleItem) -> str:
        res = await _run_scheduled_job(item, audio, camera, screen)
        await ctx.ws_hub.broadcast_json({"type": "schedule_ran", "name": item.name, "result": res})
        return res

    scheduler = SchedulerService(cfg.data_dir / "schedules.json", execute_job)

    ctx = AppState(
        settings=cfg,
        audio=audio,
        camera=camera,
        screen=screen,
        power=power,
        notifier=notifier,
        hotspot=hotspot,
        key_store=key_store,
        scheduler=scheduler,
    )

    if start_tunnel and cfg.enable_cloudflared:
        async def on_tunnel_url(url: str) -> None:
            await ctx.ws_hub.broadcast_json({"type": "status", "data": ctx.build_status_payload()})
            await notifier.notify_urls(url)

        ctx.tunnel = CloudflaredTunnel(cfg.app_port, cfg.cloudflared_bin, on_url=on_tunnel_url)
        notifier.set_tunnel_url_provider(lambda: ctx.tunnel.tunnel_url if ctx.tunnel else None)

    app = FastAPI(title="Smart Home AV Server", lifespan=lifespan)
    app.state.ctx = ctx
    register_exception_handlers(app)

    app.include_router(auth_routes.router)
    app.include_router(media_routes.router)
    app.include_router(schedule_routes.router)
    app.include_router(system_routes.router)
    app.include_router(ws_routes.router)

    @app.get("/", include_in_schema=False)
    async def serve_index() -> FileResponse:
        return FileResponse(cfg.web_dir / "index.html")

    app.mount("/static", StaticFiles(directory=str(cfg.web_dir)), name="static")
    return app


def _ensure_windowless_streams(log_dir) -> None:
    """Prevent uvicorn/click crashes when launched via pythonw.exe (stdout/stderr are None)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(log_dir / "startup_stderr.log", "a", encoding="utf-8")


def main() -> None:
    run_bootstrap()
    cfg = get_settings()
    _ensure_windowless_streams(cfg.logs_dir)
    app = create_app(cfg, start_tunnel=True)
    uvicorn.run(
        app,
        host=cfg.app_host,
        port=cfg.app_port,
        log_level=cfg.log_level.lower(),
        use_colors=False,
    )


if __name__ == "__main__":
    main()
