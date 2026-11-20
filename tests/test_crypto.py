"""Tests for X25519 + AES-256-GCM hybrid encryption and key wrapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.crypto import (
    decrypt_file_bytes,
    encrypt_bytes,
    encrypt_file,
    generate_keypair_bundle,
    rewrap_private_key,
    unwrap_private_key,
)
from app.errors import UnauthorizedError


def test_keypair_wrap_unwrap_and_rewrap() -> None:
    pub, wrapped = generate_keypair_bundle("FullStack@2023", iterations=10_000)
    priv = unwrap_private_key("FullStack@2023", wrapped)
    assert len(priv) == 32

    with pytest.raises(UnauthorizedError):
        unwrap_private_key("WrongPassword", wrapped)

    new_wrapped = rewrap_private_key("FullStack@2023", "NewPass!99", wrapped)
    assert unwrap_private_key("NewPass!99", new_wrapped) == priv
    assert pub


def test_encrypt_file_and_bytes_roundtrip(tmp_path: Path) -> None:
    pub, wrapped = generate_keypair_bundle("FullStack@2023", iterations=10_000)
    priv = unwrap_private_key("FullStack@2023", wrapped)

    payload = b"RIFF" + b"ABCD1234" * 20_000  # > 64KB to exercise multi-chunk GCM
    src = tmp_path / "sample.wav"
    enc = tmp_path / "sample.wav.enc"
    src.write_bytes(payload)

    encrypt_file(src, enc, pub)
    disk_bytes = enc.read_bytes()
    assert disk_bytes.startswith(b"SHAV1")
    assert b"ABCD1234" not in disk_bytes
    assert decrypt_file_bytes(enc, priv) == payload

    enc2 = tmp_path / "mem.wav.enc"
    encrypt_bytes(b"RIFF-MEM-DATA", enc2, pub)
    assert decrypt_file_bytes(enc2, priv) == b"RIFF-MEM-DATA"
