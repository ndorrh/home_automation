"""REST endpoints for AV, screen capture, decrypted recordings, and device release."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.crypto import decrypt_file_iter
from app.devices.preemptor import release_conflicting_av_processes
from app.errors import NotFoundError
from app.media.recordings import (
    decrypted_download_name,
    delete_recording,
    list_recordings,
    media_type_for,
    resolve_recording_path,
)
from app.security import require_auth, require_session_key
from app.state import AppState

router = APIRouter(prefix="/api", tags=["media"], dependencies=[Depends(require_auth)])


class MonitorPayload(BaseModel):
    monitor_index: int = Field(default=1, ge=0, le=16)


def _get_state(request: Request) -> AppState:
    return request.app.state.ctx


@router.get("/status")
async def get_status(request: Request) -> dict:
    ctx = _get_state(request)
    return await asyncio.to_thread(ctx.build_status_payload)


@router.get("/camera/stream")
async def camera_stream(request: Request) -> StreamingResponse:
    ctx = _get_state(request)
    return StreamingResponse(
        ctx.camera.mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.post("/camera/snapshot")
async def take_snapshot(request: Request) -> dict:
    ctx = _get_state(request)
    filename = await asyncio.to_thread(ctx.camera.take_snapshot)
    return {"success": True, "filename": filename, "url": f"/api/recordings/snapshots/{filename}"}


@router.post("/camera/record/start")
async def start_camera_record(request: Request) -> dict:
    ctx = _get_state(request)
    filename = await asyncio.to_thread(ctx.camera.start_video_recording)
    await ctx.ws_hub.broadcast_json({"type": "status", "data": ctx.build_status_payload()})
    return {"success": True, "filename": filename}


@router.post("/camera/record/stop")
async def stop_camera_record(request: Request) -> dict:
    ctx = _get_state(request)
    filename = await asyncio.to_thread(ctx.camera.stop_video_recording)
    await ctx.ws_hub.broadcast_json({"type": "status", "data": ctx.build_status_payload()})
    return {"success": True, "filename": filename}


@router.get("/screen/monitors")
async def screen_monitors(request: Request) -> dict:
    ctx = _get_state(request)
    monitors = await asyncio.to_thread(ctx.screen.list_monitors)
    return {"success": True, "monitors": monitors}


@router.get("/screen/stream")
async def screen_stream(
    request: Request,
    monitor: int | None = Query(default=None, ge=0, le=16),
) -> StreamingResponse:
    ctx = _get_state(request)
    return StreamingResponse(
        ctx.screen.mjpeg_generator(monitor),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.post("/screen/snapshot")
async def take_screenshot(
    request: Request,
    body: MonitorPayload | None = None,
) -> dict:
    ctx = _get_state(request)
    idx = body.monitor_index if body else None
    filename = await asyncio.to_thread(ctx.screen.take_screenshot, idx)
    return {"success": True, "filename": filename, "url": f"/api/recordings/screen/{filename}"}


@router.post("/screen/record/start")
async def start_screen_record(
    request: Request,
    body: MonitorPayload | None = None,
) -> dict:
    ctx = _get_state(request)
    idx = body.monitor_index if body else None
    filename = await asyncio.to_thread(ctx.screen.start_recording, idx)
    await ctx.ws_hub.broadcast_json({"type": "status", "data": ctx.build_status_payload()})
    return {"success": True, "filename": filename}


@router.post("/screen/record/stop")
async def stop_screen_record(request: Request) -> dict:
    ctx = _get_state(request)
    filename = await asyncio.to_thread(ctx.screen.stop_recording)
    await ctx.ws_hub.broadcast_json({"type": "status", "data": ctx.build_status_payload()})
    return {"success": True, "filename": filename}


@router.post("/mic/record/start")
async def start_mic_record(request: Request) -> dict:
    ctx = _get_state(request)
    filename = await asyncio.to_thread(ctx.audio.start_recording)
    return {"success": True, "filename": filename}


@router.post("/mic/record/stop")
async def stop_mic_record(request: Request) -> dict:
    ctx = _get_state(request)
    filename = await asyncio.to_thread(ctx.audio.stop_recording)
    return {"success": True, "filename": filename}


@router.get("/recordings")
async def get_recordings(request: Request, kind: str | None = Query(default=None)) -> dict:
    ctx = _get_state(request)
    items = await asyncio.to_thread(list_recordings, ctx.settings.recordings_dir, kind)
    return {"success": True, "items": items}


@router.get("/recordings/{kind}/{filename}")
async def download_recording(
    kind: str,
    filename: str,
    request: Request,
    download: bool = Query(default=False),
    priv_raw: bytes = Depends(require_session_key),
) -> StreamingResponse:
    ctx = _get_state(request)
    target = resolve_recording_path(ctx.settings.recordings_dir, kind, filename)
    if not target.exists() or not target.is_file():
        raise NotFoundError(f"Recording '{filename}' not found.")

    plain_name = decrypted_download_name(target.name)
    disposition = "attachment" if download else "inline"
    return StreamingResponse(
        decrypt_file_iter(target, priv_raw),
        media_type=media_type_for(target.name),
        headers={
            "Content-Disposition": f'{disposition}; filename="{plain_name}"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/recordings/{kind}/{filename}")
async def remove_recording(kind: str, filename: str, request: Request) -> dict:
    ctx = _get_state(request)
    await asyncio.to_thread(delete_recording, ctx.settings.recordings_dir, kind, filename)
    return {"success": True, "deleted": filename}


@router.post("/devices/release")
async def release_devices(request: Request) -> dict:
    ctx = _get_state(request)
    report = await asyncio.to_thread(release_conflicting_av_processes)
    await ctx.ws_hub.broadcast_json({"type": "status", "data": ctx.build_status_payload()})
    return {
        "success": True,
        "terminated": report.terminated,
        "skipped": report.skipped,
        "failed": report.failed,
    }
