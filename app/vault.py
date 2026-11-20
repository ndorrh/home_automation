"""Windows DPAPI credential sealing for Gmail SMTP settings (decrypted only in memory)."""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import sys
from dataclasses import dataclass

from app.errors import AppError


@dataclass(frozen=True)
class GmailCredentials:
    user: str
    app_password: str
    notify_to: str


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_protect(raw: bytes) -> bytes:
    if sys.platform != "win32":
        raise AppError("DPAPI_UNSUPPORTED", "Windows DPAPI requires Windows OS.", 500)
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob = _DataBlob(len(raw), ctypes.create_string_buffer(raw, len(raw)))
    out_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob), "SmartHomeAV-Gmail", None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise AppError("DPAPI_ERROR", "Failed to protect Gmail credentials.", 500)
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(protected: bytes) -> bytes:
    if sys.platform != "win32":
        raise AppError("DPAPI_UNSUPPORTED", "Windows DPAPI requires Windows OS.", 500)
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob = _DataBlob(len(protected), ctypes.create_string_buffer(protected, len(protected)))
    out_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise AppError("DPAPI_ERROR", "Failed to decrypt Gmail credentials.", 500)
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def seal_gmail_credentials(creds: GmailCredentials) -> str:
    """Encrypt Gmail credentials using Windows DPAPI into an encoded .env token."""
    payload = json.dumps(
        {
            "user": creds.user.strip(),
            "app_password": creds.app_password.strip(),
            "notify_to": (creds.notify_to or creds.user).strip(),
        }
    ).encode("utf-8")
    protected = _dpapi_protect(payload)
    return "dpapi:" + base64.b64encode(protected).decode("ascii")


def unseal_gmail_credentials(token: str) -> GmailCredentials | None:
    """Decrypt DPAPI-sealed Gmail credentials strictly in memory."""
    cleaned = token.strip()
    if not cleaned:
        return None
    if not cleaned.startswith("dpapi:"):
        raise AppError("DPAPI_INVALID", "Invalid GMAIL_CREDENTIALS_ENC format.", 500)
    raw_blob = base64.b64decode(cleaned[6:].encode("ascii"))
    data = json.loads(_dpapi_unprotect(raw_blob).decode("utf-8"))
    user = str(data.get("user", "")).strip()
    app_password = str(data.get("app_password", "")).strip()
    notify_to = str(data.get("notify_to", "") or user).strip()
    if not user or not app_password or not notify_to:
        return None
    return GmailCredentials(user=user, app_password=app_password, notify_to=notify_to)
