"""Tests for isolated tunnel args, connectivity watchdog, and DPAPI email delivery."""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.services.network import filter_physical_lan_ips
from app.services.notifier import EmailNotifier
from app.services.tunnel import build_tunnel_args, extract_tunnel_url
from app.vault import GmailCredentials, seal_gmail_credentials


def test_filter_physical_lan_ips_excludes_virtual_switches() -> None:
    fake_addrs = {
        "vEthernet (Default Switch)": [SimpleNamespace(family=socket.AF_INET, address="172.17.48.1")],
        "vEthernet (WSL (Hyper-V firewall))": [SimpleNamespace(family=socket.AF_INET, address="192.168.224.1")],
        "Wi-Fi": [SimpleNamespace(family=socket.AF_INET, address="192.168.10.247")],
        "Local Area Connection* 10": [SimpleNamespace(family=socket.AF_INET, address="192.168.137.1")],
    }
    fake_stats = {k: SimpleNamespace(isup=True) for k in fake_addrs}
    ips = filter_physical_lan_ips(fake_addrs, fake_stats)
    assert ips == ["192.168.137.1", "192.168.10.247"]


def test_extract_tunnel_url_and_isolated_args(tmp_path: Path) -> None:
    line = "2026-11-20T07:00:00Z INF |  https://quiet-river-42.trycloudflare.com  |"
    assert extract_tunnel_url(line) == "https://quiet-river-42.trycloudflare.com"
    assert extract_tunnel_url("INF Starting metrics server on 127.0.0.1:20241") is None

    cfg_path = tmp_path / "cloudflared_quick.yml"
    args = build_tunnel_args(cfg_path, 8000)
    assert "--config" in args
    assert "--protocol" in args
    assert "http2" in args
    assert str(cfg_path) in args
    assert "http://127.0.0.1:8000" in args


def test_watchdog_retries_offline_then_sends_with_dpapi() -> None:
    sealed = (
        seal_gmail_credentials(
            GmailCredentials("sender@gmail.com", "abcd efgh ijkl mnop", "dest@gmail.com")
        )
        if sys.platform == "win32"
        else ""
    )
    cfg = Settings(
        _env_file=None,
        gmail_credentials_enc=sealed,
        gmail_user="" if sealed else "sender@gmail.com",
        gmail_app_password="" if sealed else "abcd efgh ijkl mnop",
        notify_email_to="" if sealed else "dest@gmail.com",
        enable_cloudflared=False,
    )
    online_flag = {"online": False}
    sent_calls: list[tuple[str, str | None]] = []

    async def fake_invoke(creds, lan_urls: list[str], tunnel_url: str | None) -> bool:
        sent_calls.append((creds.user, tunnel_url))
        return True

    async def scenario() -> None:
        notifier = EmailNotifier(
            cfg, internet_check=lambda: online_flag["online"], poll_interval=0.05
        )
        notifier._invoke_nodemailer = fake_invoke  # type: ignore[method-assign]

        assert await notifier.notify_urls("https://demo-1.trycloudflare.com") is False
        assert len(sent_calls) == 0

        online_flag["online"] = True
        await asyncio.sleep(0.15)
        assert len(sent_calls) == 1
        assert sent_calls[0][0] == "sender@gmail.com"
        assert sent_calls[0][1] == "https://demo-1.trycloudflare.com"

        # Force periodic / manual resend even when URL unchanged
        assert await notifier.evaluate_once(force=True, override_tunnel="https://demo-1.trycloudflare.com") is True
        assert len(sent_calls) == 2

        # Different tunnel URL -> resends immediately
        assert await notifier.notify_urls("https://demo-2.trycloudflare.com") is True
        assert len(sent_calls) == 3

        await notifier.stop()

    asyncio.run(scenario())
