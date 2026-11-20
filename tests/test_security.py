"""Security, authentication, and path-traversal protection tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.crypto import generate_keypair_bundle
from app.security import hash_password, verify_password
from app.server import create_app


def _test_settings(password: str = "FullStack@2023") -> Settings:
    pub, wrapped = generate_keypair_bundle(password, iterations=10_000)
    return Settings(
        _env_file=None,
        app_password_hash=hash_password(password, iterations=10_000),
        session_secret="test-secret-key-for-pytest",
        enc_public_key=pub,
        enc_private_key_wrapped=wrapped,
        prevent_system_sleep=False,
        enable_cloudflared=False,
    )


def test_password_hash_and_verify() -> None:
    hashed = hash_password("FullStack@2023", iterations=10_000)
    assert hashed.startswith("pbkdf2_sha256$10000$")
    assert verify_password("FullStack@2023", hashed) is True
    assert verify_password("wrong-password", hashed) is False
    assert verify_password("FullStack@2023", "invalid-format") is False


def test_http_and_websocket_auth_flow() -> None:
    cfg = _test_settings()
    app = create_app(settings=cfg, start_tunnel=False)

    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 401

        try:
            with client.websocket_connect("/ws"):
                raise AssertionError("Unauthenticated WebSocket should be rejected")
        except WebSocketDisconnect:
            pass

        bad_login = client.post("/api/login", json={"password": "wrong"})
        assert bad_login.status_code == 401
        assert bad_login.json()["success"] is False

        ok_login = client.post("/api/login", json={"password": "FullStack@2023"})
        assert ok_login.status_code == 200
        assert ok_login.json()["authenticated"] is True

        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["success"] is True

        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "status"
            assert msg["data"]["success"] is True


def test_recording_download_rejects_traversal() -> None:
    cfg = _test_settings()
    app = create_app(settings=cfg, start_tunnel=False)
    with TestClient(app) as client:
        assert client.post("/api/login", json={"password": "FullStack@2023"}).status_code == 200
        resp = client.get("/api/recordings/audio/..%5C..%5C.env")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_FILENAME"
