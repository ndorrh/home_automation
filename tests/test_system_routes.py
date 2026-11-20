"""Tests for dashboard system status, log viewer, and allowlisted script runner."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.crypto import generate_keypair_bundle
from app.security import hash_password
from app.server import create_app


def _make_app():
    pub, wrapped = generate_keypair_bundle("FullStack@2023", iterations=10_000)
    cfg = Settings(
        _env_file=None,
        app_password_hash=hash_password("FullStack@2023", iterations=10_000),
        session_secret="test-secret-system-routes",
        enc_public_key=pub,
        enc_private_key_wrapped=wrapped,
        prevent_system_sleep=False,
        enable_cloudflared=False,
    )
    return create_app(settings=cfg, start_tunnel=False)


def test_system_routes_auth_and_allowlist() -> None:
    app = _make_app()
    with TestClient(app) as client:
        assert client.get("/api/system/overview").status_code == 401
        assert client.get("/api/system/logs").status_code == 401
        assert client.post("/api/system/scripts/run", json={"script_id": "check_status"}).status_code == 401

        assert client.post("/api/login", json={"password": "FullStack@2023"}).status_code == 200

        ov = client.get("/api/system/overview")
        assert ov.status_code == 200
        assert "pid" in ov.json()

        logs = client.get("/api/system/logs?lines=20")
        assert logs.status_code == 200
        assert isinstance(logs.json()["lines"], list)

        bad = client.post("/api/system/scripts/run", json={"script_id": "rm_rf_hack"})
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "INVALID_SCRIPT"

        scripts = client.get("/api/system/scripts")
        assert scripts.status_code == 200
        ids = [item["id"] for item in scripts.json()["items"]]
        assert "hotspot_on" in ids
        assert "fix_firewall" in ids
        assert "env_hide" in ids
        assert "send_email" in ids
        assert "git_update" in ids

        cleared = client.post("/api/system/logs/clear")
        assert cleared.status_code == 200
        assert cleared.json()["success"] is True
