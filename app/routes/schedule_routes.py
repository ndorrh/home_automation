"""REST endpoints for persistent recording schedules."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.security import require_auth
from app.services.scheduler import ScheduleCreate
from app.state import AppState

router = APIRouter(prefix="/api/schedules", tags=["schedules"], dependencies=[Depends(require_auth)])


class ScheduleToggle(BaseModel):
    enabled: bool


def _get_state(request: Request) -> AppState:
    return request.app.state.ctx


@router.get("")
async def list_schedules(request: Request) -> dict:
    return {"success": True, "items": _get_state(request).scheduler.list_items()}


@router.post("")
async def create_schedule(body: ScheduleCreate, request: Request) -> dict:
    item = await _get_state(request).scheduler.create_item(body)
    return {"success": True, "item": item}


@router.patch("/{schedule_id}")
async def toggle_schedule(schedule_id: str, body: ScheduleToggle, request: Request) -> dict:
    item = await _get_state(request).scheduler.toggle_item(schedule_id, body.enabled)
    return {"success": True, "item": item}


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, request: Request) -> dict:
    await _get_state(request).scheduler.delete_item(schedule_id)
    return {"success": True, "deleted": schedule_id}
