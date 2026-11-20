"""Tests for Windows DPAPI Gmail credential sealing and .env migration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.bootstrap import _seal_plaintext_gmail_lines
from app.vault import GmailCredentials, seal_gmail_credentials, unseal_gmail_credentials


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI requires Windows")
def test_dpapi_seal_and_unseal_roundtrip() -> None:
    creds = GmailCredentials(
        user="sender@gmail.com",
        app_password="abcd efgh ijkl mnop",
        notify_to="receiver@gmail.com",
    )
    token = seal_gmail_credentials(creds)
    assert token.startswith("dpapi:")
    assert "sender@gmail.com" not in token
    assert "abcd efgh" not in token

    unsealed = unseal_gmail_credentials(token)
    assert unsealed == creds


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI requires Windows")
def test_plaintext_gmail_migration_scrubs_secrets(tmp_path: Path) -> None:
    raw_env = (
        "APP_PORT=8000\n"
        "GMAIL_USER=demo@gmail.com\n"
        "GMAIL_APP_PASSWORD=secretapppassword\n"
        "NOTIFY_EMAIL_TO=dest@gmail.com\n"
        "GMAIL_CREDENTIALS_ENC=\n"
    )
    migrated = _seal_plaintext_gmail_lines(raw_env)
    assert "GMAIL_USER=" not in migrated
    assert "GMAIL_APP_PASSWORD=" not in migrated
    assert "NOTIFY_EMAIL_TO=" not in migrated
    assert "secretapppassword" not in migrated
    assert "GMAIL_CREDENTIALS_ENC=dpapi:" in migrated
