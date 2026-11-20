"""Tests for encrypted recording listing, password-gated decryption, and deletion."""

from __future__ import annotations

import io
import wave

from fastapi.testclient import TestClient

from app.config import Settings
from app.crypto import encrypt_bytes, generate_keypair_bundle
from app.security import hash_password
from app.server import create_app


def _dummy_wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 160)
    return buf.getvalue()


def test_encrypted_recordings_list_download_delete() -> None:
    pub, wrapped = generate_keypair_bundle("FullStack@2023", iterations=10_000)
    cfg = Settings(
        _env_file=None,
        app_password_hash=hash_password("FullStack@2023", iterations=10_000),
        session_secret="test-secret-recordings",
        enc_public_key=pub,
        enc_private_key_wrapped=wrapped,
        prevent_system_sleep=False,
        enable_cloudflared=False,
    )
    app = create_app(settings=cfg, start_tunnel=False)
    enc_file = cfg.recordings_dir / "audio" / "audio_test_clip.wav.enc"
    wav_bytes = _dummy_wav_bytes()
    encrypt_bytes(wav_bytes, enc_file, pub)

    try:
        # Verify on-disk file cannot be read directly as WAV
        assert not enc_file.read_bytes().startswith(b"RIFF")

        with TestClient(app) as client:
            assert client.get("/api/recordings/audio/audio_test_clip.wav.enc").status_code == 401
            assert client.post("/api/login", json={"password": "FullStack@2023"}).status_code == 200

            listing = client.get("/api/recordings?kind=audio").json()
            names = [item["filename"] for item in listing["items"]]
            assert "audio_test_clip.wav.enc" in names

            dl = client.get("/api/recordings/audio/audio_test_clip.wav.enc?download=1")
            assert dl.status_code == 200
            assert dl.content == wav_bytes

            deleted = client.delete("/api/recordings/audio/audio_test_clip.wav.enc")
            assert deleted.status_code == 200
            assert not enc_file.exists()
    finally:
        if enc_file.exists():
            enc_file.unlink()
