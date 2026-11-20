"""Tests for Windows .env Hidden/ReadOnly protection and safe updates."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.env_guard import is_env_protected, protect_env, unprotect_env, write_env_safely


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file attributes required")
def test_protect_unprotect_and_safe_write(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_PORT=8000\n", encoding="utf-8")
    assert is_env_protected(env_file) is False

    assert protect_env(env_file, apply_acl=False) is True
    assert is_env_protected(env_file) is True

    # Updating protected .env via write_env_safely succeeds and preserves protection
    write_env_safely(env_file, "APP_PORT=8001\n")
    assert is_env_protected(env_file) is True
    assert "APP_PORT=8001" in env_file.read_text(encoding="utf-8")

    assert unprotect_env(env_file) is True
    assert is_env_protected(env_file) is False
