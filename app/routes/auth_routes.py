"""Authentication routes (login, logout, session check, key unwrapping)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.crypto import unwrap_private_key
from app.errors import UnauthorizedError
from app.security import (
    SESSION_COOKIE_NAME,
    SessionManager,
    get_session_manager,
    require_auth,
    verify_password,
)
from app.state import AppState

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _get_state(request: Request) -> AppState:
    return request.app.state.ctx


def _is_https_request(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.lower() == "https"


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    sessions: SessionManager = Depends(get_session_manager),
) -> dict:
    ctx = _get_state(request)
    settings = ctx.settings
    client_ip = request.client.host if request.client else "unknown"
    ctx.rate_limiter.check(client_ip)

    if not verify_password(body.password, settings.app_password_hash):
        ctx.rate_limiter.record_failure(client_ip)
        raise UnauthorizedError("Invalid password.")

    priv_raw = unwrap_private_key(body.password, settings.enc_private_key_wrapped)
    sid = secrets.token_urlsafe(24)
    ctx.key_store.put(sid, priv_raw)
    ctx.rate_limiter.reset(client_ip)

    token = sessions.create_token(sid)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=_is_https_request(request),
        path="/",
    )
    return {"success": True, "authenticated": True}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    sessions: SessionManager = Depends(get_session_manager),
) -> dict:
    sid = sessions.get_session_id(request.cookies.get(SESSION_COOKIE_NAME))
    _get_state(request).key_store.remove(sid)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"success": True, "authenticated": False}


@router.get("/auth/me")
async def auth_me(_: str = Depends(require_auth)) -> dict:
    return {"success": True, "authenticated": True}
