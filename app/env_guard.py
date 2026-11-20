"""Windows .env attribute hiding (Hidden+System+ReadOnly) and safe atomic updates."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("smart_home_av.env_guard")

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def _get_attrs(path: Path) -> int:
    if sys.platform != "win32" or not path.exists():
        return 0
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    return 0 if attrs == INVALID_FILE_ATTRIBUTES else int(attrs)


def _set_attrs(path: Path, attrs: int) -> bool:
    if sys.platform != "win32" or not path.exists():
        return False
    return bool(ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs))


def is_env_protected(env_path: Path) -> bool:
    """Return True if .env has Hidden and Read-Only attributes applied."""
    attrs = _get_attrs(env_path)
    return bool((attrs & FILE_ATTRIBUTE_HIDDEN) and (attrs & FILE_ATTRIBUTE_READONLY))


def protect_env(env_path: Path, apply_acl: bool = True) -> bool:
    """Hide .env (+H +S +R) and restrict NTFS ACL to current user."""
    if not env_path.exists():
        return False
    ok = _set_attrs(
        env_path,
        FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM | FILE_ATTRIBUTE_READONLY,
    )
    if apply_acl and sys.platform == "win32":
        _apply_owner_only_acl(env_path)
    return ok


def unprotect_env(env_path: Path) -> bool:
    """Clear Hidden, System, and Read-Only attributes so .env can be viewed/edited."""
    if not env_path.exists():
        return False
    return _set_attrs(env_path, FILE_ATTRIBUTE_NORMAL)


def write_env_safely(env_path: Path, content: str, protect_after: bool | None = None) -> None:
    """Safely update .env even when protected (+H +S +R), preserving protection state."""
    was_protected = is_env_protected(env_path)
    should_protect = was_protected if protect_after is None else protect_after
    if env_path.exists():
        unprotect_env(env_path)
    env_path.write_text(content, encoding="utf-8")
    if should_protect:
        protect_env(env_path, apply_acl=not was_protected)


def _apply_owner_only_acl(env_path: Path) -> None:
    user = os.environ.get("USERNAME", "").strip()
    domain = os.environ.get("USERDOMAIN", "").strip()
    account = f"{domain}\\{user}" if domain and user else user
    if not account:
        return
    try:
        subprocess.run(
            ["icacls", str(env_path), "/inheritance:r", "/grant:r", f"{account}:(R,W)"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        logger.debug("icacls owner-only ACL skipped: %s", exc)
