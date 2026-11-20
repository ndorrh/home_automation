"""Standardized domain errors and centralized FastAPI error handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("smart_home_av.errors")


class AppError(Exception):
    """Base structured domain error."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Authentication required.") -> None:
        super().__init__("UNAUTHORIZED", message, status_code=401)


class RateLimitedError(AppError):
    def __init__(self, message: str = "Too many failed login attempts.") -> None:
        super().__init__("RATE_LIMITED", message, status_code=429)


class NotFoundError(AppError):
    def __init__(self, message: str = "Requested resource not found.") -> None:
        super().__init__("NOT_FOUND", message, status_code=404)


class DeviceUnavailableError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("DEVICE_UNAVAILABLE", message, status_code=409)


def error_envelope(code: str, message: str) -> dict:
    """Build standard JSON error response payload."""
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Attach centralized domain, validation, and fallback handlers."""

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        logger.warning("Request validation error: %s", exc.errors())
        return JSONResponse(
            status_code=400,
            content=error_envelope("VALIDATION_ERROR", "Invalid request payload."),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled server exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_envelope("INTERNAL_ERROR", "An unexpected server error occurred."),
        )
