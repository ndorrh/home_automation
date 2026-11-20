"""WebSocket endpoint for real-time microphone audio streaming and AV control."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from app.devices.preemptor import release_conflicting_av_processes
from app.errors import AppError
from app.security import verify_websocket_auth
from app.state import AppState

logger = logging.getLogger("smart_home_av.ws")

router = APIRouter(tags=["websocket"])


class WsCommand(BaseModel):
    action: str


async def _pump_audio(ws: WebSocket, queue: asyncio.Queue[bytes]) -> None:
    """Forward raw int16 PCM audio chunks to the connected WebSocket client."""
    while True:
        pcm = await queue.get()
        await ws.send_bytes(pcm)


async def _dispatch_command(action: str, ctx: AppState, ws: WebSocket, state_bag: dict) -> None:
    handlers = {
        "mic:record_start": lambda: _run_sync(ctx.audio.start_recording, "mic_recording", ws),
        "mic:record_stop": lambda: _run_sync(ctx.audio.stop_recording, "mic_saved", ws),
        "cam:record_start": lambda: _run_video(ctx.camera.start_video_recording, "cam_recording", ctx, ws),
        "cam:record_stop": lambda: _run_video(ctx.camera.stop_video_recording, "cam_saved", ctx, ws),
        "screen:record_start": lambda: _run_video(ctx.screen.start_recording, "screen_recording", ctx, ws),
        "screen:record_stop": lambda: _run_video(ctx.screen.stop_recording, "screen_saved", ctx, ws),
    }

    if action == "status:get":
        await ws.send_text(json.dumps({"type": "status", "data": ctx.build_status_payload()}))
        return
    if action == "mic:listen_start":
        _start_listen(ctx, ws, state_bag)
        return
    if action == "mic:listen_stop":
        await _stop_listen(ctx, state_bag)
        return
    if action == "devices:release":
        report = await asyncio.to_thread(release_conflicting_av_processes)
        await ws.send_text(json.dumps({"type": "release_report", "terminated": report.terminated, "failed": report.failed}))
        return
    if action in handlers:
        await handlers[action]()
        return
    await ws.send_text(json.dumps({"type": "error", "message": f"Unknown action: {action}"}))


async def _run_sync(fn, event_type: str, ws: WebSocket) -> None:
    filename = await asyncio.to_thread(fn)
    await ws.send_text(json.dumps({"type": event_type, "filename": filename}))


async def _run_video(fn, event_type: str, ctx: AppState, ws: WebSocket) -> None:
    filename = await asyncio.to_thread(fn)
    await ctx.ws_hub.broadcast_json({"type": "status", "data": ctx.build_status_payload()})
    await ws.send_text(json.dumps({"type": event_type, "filename": filename}))


def _start_listen(ctx: AppState, ws: WebSocket, state_bag: dict) -> None:
    if state_bag.get("audio_queue") is not None:
        return
    queue = ctx.audio.add_listener()
    state_bag["audio_queue"] = queue
    state_bag["pump_task"] = asyncio.create_task(_pump_audio(ws, queue))


async def _stop_listen(ctx: AppState, state_bag: dict) -> None:
    pump_task: asyncio.Task | None = state_bag.pop("pump_task", None)
    if pump_task is not None:
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass
    queue: asyncio.Queue[bytes] | None = state_bag.pop("audio_queue", None)
    if queue is not None:
        ctx.audio.remove_listener(queue)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    ctx: AppState = ws.app.state.ctx
    if not verify_websocket_auth(ws, ctx.settings):
        await ws.close(code=1008, reason="Unauthorized")
        return

    await ws.accept()
    ctx.ws_hub.register(ws)
    state_bag: dict = {}
    await ws.send_text(json.dumps({"type": "status", "data": ctx.build_status_payload()}))

    try:
        while True:
            raw_text = await ws.receive_text()
            try:
                cmd = WsCommand.model_validate_json(raw_text)
                await _dispatch_command(cmd.action, ctx, ws, state_bag)
            except ValidationError:
                await ws.send_text(json.dumps({"type": "error", "message": "Invalid command."}))
            except AppError as exc:
                await ws.send_text(json.dumps({"type": "error", "message": exc.message}))
    except WebSocketDisconnect:
        pass
    finally:
        await _stop_listen(ctx, state_bag)
        ctx.ws_hub.unregister(ws)
