"""Authentication, PBKDF2 password hashing, rate limiting, and session cookies."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import Depends, Request, WebSocket
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings, get_settings
from app.errors import RateLimitedError, UnauthorizedError

SESSION_COOKIE_NAME = "shav_session"


def hash_password(password: str, iterations: int = 600_000) -> str:
    """Hash a plaintext password using PBKDF2-HMAC-SHA256 with random 16-byte salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify plaintext password against stored PBKDF2-HMAC-SHA256 hash."""
    parts = stored_hash.strip().split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
    except ValueError:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


class LoginRateLimiter:
    """Simple sliding-window IP rate limiter for login attempts."""

    def __init__(self, max_attempts: int = 10, window_seconds: int = 300) -> None:
        self._max_attempts = max_attempts
        self._window = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._attempts[key]
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return bucket

    def check(self, client_ip: str) -> None:
        if len(self._prune(client_ip, time.monotonic())) >= self._max_attempts:
            raise RateLimitedError()

    def record_failure(self, client_ip: str) -> None:
        self._prune(client_ip, time.monotonic()).append(time.monotonic())

    def reset(self, client_ip: str) -> None:
        self._attempts.pop(client_ip, None)


class SessionManager:
    """Creates and verifies signed session cookies containing a session ID."""

    def __init__(self, secret: str, ttl_seconds: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt="shav-auth-v1")
        self._ttl = ttl_seconds

    def create_token(self, session_id: str) -> str:
        return self._serializer.dumps({"sub": "admin", "sid": session_id})

    def get_session_id(self, token: str | None) -> str | None:
        if not token:
            return None
        try:
            data = self._serializer.loads(token, max_age=self._ttl)
            if isinstance(data, dict) and data.get("sub") == "admin":
                sid = data.get("sid")
                return str(sid) if sid else None
            return None
        except (BadSignature, SignatureExpired):
            return None


def get_session_manager(request: Request) -> SessionManager:
    """FastAPI dependency returning SessionManager from active app settings."""
    ctx = getattr(request.app.state, "ctx", None)
    settings = ctx.settings if ctx is not None else get_settings()
    if not settings.session_secret:
        raise UnauthorizedError("Server session secret is not initialized.")
    return SessionManager(settings.session_secret, settings.session_ttl_seconds)


def require_auth(
    request: Request,
    sessions: SessionManager = Depends(get_session_manager),
) -> str:
    """Verify session cookie and unlocked in-memory key session."""
    sid = sessions.get_session_id(request.cookies.get(SESSION_COOKIE_NAME))
    ctx = getattr(request.app.state, "ctx", None)
    if not sid or ctx is None or ctx.key_store.get(sid) is None:
        raise UnauthorizedError()
    return sid


def require_session_key(
    request: Request,
    sid: str = Depends(require_auth),
) -> bytes:
    """Return unwrapped X25519 private key for the authenticated session."""
    priv_raw = request.app.state.ctx.key_store.get(sid)
    if priv_raw is None:
        raise UnauthorizedError("Session key expired; please sign in again.")
    return priv_raw


def verify_websocket_auth(websocket: WebSocket, settings: Settings) -> bool:
    """Validate signed session cookie and active session key for WebSocket handshake."""
    if not settings.session_secret:
        return False
    sessions = SessionManager(settings.session_secret, settings.session_ttl_seconds)
    sid = sessions.get_session_id(websocket.cookies.get(SESSION_COOKIE_NAME))
    ctx = getattr(websocket.app.state, "ctx", None)
    return bool(sid and ctx is not None and ctx.key_store.get(sid) is not None)
