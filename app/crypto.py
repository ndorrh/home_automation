"""X25519 + AES-256-GCM hybrid encryption for password-protected recordings."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import struct
import threading
import time
from pathlib import Path
from typing import Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.errors import AppError, UnauthorizedError

FILE_MAGIC = b"SHAV1"
CHUNK_SIZE = 64 * 1024
WRAP_ITERATIONS = 600_000


def _derive_wrap_key(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def generate_keypair_bundle(
    password: str, iterations: int = WRAP_ITERATIONS
) -> tuple[str, str]:
    """Generate an X25519 keypair and wrap the private key with password."""
    priv = x25519.X25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_b64 = base64.urlsafe_b64encode(pub_raw).decode("ascii")
    wrapped = wrap_private_key(priv_raw, password, iterations)
    return pub_b64, wrapped


def wrap_private_key(
    priv_raw: bytes, password: str, iterations: int = WRAP_ITERATIONS
) -> str:
    """Encrypt raw 32-byte X25519 private key with password-derived AES-256-GCM."""
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    kek = _derive_wrap_key(password, salt, iterations)
    ct = AESGCM(kek).encrypt(nonce, priv_raw, b"shav-key-wrap-v1")
    return f"v1${iterations}${salt.hex()}${nonce.hex()}${ct.hex()}"


def unwrap_private_key(password: str, wrapped: str) -> bytes:
    """Decrypt raw 32-byte X25519 private key using the user's password."""
    parts = wrapped.strip().split("$")
    if len(parts) != 5 or parts[0] != "v1":
        raise AppError("KEY_CONFIG_INVALID", "Encrypted key format is invalid.", 500)
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        nonce = bytes.fromhex(parts[3])
        ct = bytes.fromhex(parts[4])
    except ValueError as exc:
        raise AppError("KEY_CONFIG_INVALID", "Corrupted encrypted key data.", 500) from exc

    kek = _derive_wrap_key(password, salt, iterations)
    try:
        return AESGCM(kek).decrypt(nonce, ct, b"shav-key-wrap-v1")
    except InvalidTag as exc:
        raise UnauthorizedError("Invalid password for recording decryption.") from exc


def rewrap_private_key(old_password: str, new_password: str, wrapped: str) -> str:
    """Re-encrypt existing private key under a new password."""
    priv_raw = unwrap_private_key(old_password, wrapped)
    return wrap_private_key(priv_raw, new_password)


def _derive_file_aes_key(shared_secret: bytes, ephem_pub: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=ephem_pub,
        info=b"shav-recording-v1",
    ).derive(shared_secret)


def encrypt_file(src_path: Path, enc_path: Path, public_key_b64: str) -> None:
    """Encrypt a plaintext media file to .enc using X25519 + chunked AES-256-GCM."""
    ephem_pub, aes = _init_recipient_cipher(public_key_b64)
    enc_path.parent.mkdir(parents=True, exist_ok=True)
    with src_path.open("rb") as fin, enc_path.open("wb") as fout:
        fout.write(FILE_MAGIC + ephem_pub)
        _write_encrypted_chunks(fin, fout, aes)


def encrypt_bytes(data: bytes, enc_path: Path, public_key_b64: str) -> None:
    """Encrypt in-memory bytes directly to .enc without writing plaintext to disk."""
    ephem_pub, aes = _init_recipient_cipher(public_key_b64)
    enc_path.parent.mkdir(parents=True, exist_ok=True)
    with enc_path.open("wb") as fout:
        fout.write(FILE_MAGIC + ephem_pub)
        offset = 0
        idx = 0
        total = len(data)
        while True:
            chunk = data[offset : offset + CHUNK_SIZE]
            offset += len(chunk)
            is_last = offset >= total
            nonce = os.urandom(12)
            aad = struct.pack(">Q?", idx, is_last)
            ct = aes.encrypt(nonce, chunk, aad)
            fout.write(struct.pack(">I", len(ct)) + nonce + ct)
            if is_last:
                break
            idx += 1


def _init_recipient_cipher(public_key_b64: str) -> tuple[bytes, AESGCM]:
    pub_raw = base64.urlsafe_b64decode(public_key_b64.encode("ascii"))
    recipient_pub = x25519.X25519PublicKey.from_public_bytes(pub_raw)
    ephem_priv = x25519.X25519PrivateKey.generate()
    ephem_pub = ephem_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    aes_key = _derive_file_aes_key(ephem_priv.exchange(recipient_pub), ephem_pub)
    return ephem_pub, AESGCM(aes_key)


def _write_encrypted_chunks(fin, fout, aes: AESGCM) -> None:
    idx = 0
    chunk = fin.read(CHUNK_SIZE)
    while True:
        next_chunk = fin.read(CHUNK_SIZE)
        is_last = len(next_chunk) == 0
        nonce = os.urandom(12)
        aad = struct.pack(">Q?", idx, is_last)
        ct = aes.encrypt(nonce, chunk, aad)
        fout.write(struct.pack(">I", len(ct)) + nonce + ct)
        if is_last:
            break
        chunk = next_chunk
        idx += 1


def decrypt_file_iter(enc_path: Path, priv_raw: bytes) -> Iterator[bytes]:
    """Stream decrypted plaintext chunks from an .enc recording."""
    recipient_priv = x25519.X25519PrivateKey.from_private_bytes(priv_raw)
    with enc_path.open("rb") as fin:
        header = fin.read(len(FILE_MAGIC) + 32)
        if len(header) != len(FILE_MAGIC) + 32 or not header.startswith(FILE_MAGIC):
            raise AppError("DECRYPT_FAILED", "Unrecognized encrypted file header.", 500)
        ephem_pub_raw = header[len(FILE_MAGIC) :]
        ephem_pub = x25519.X25519PublicKey.from_public_bytes(ephem_pub_raw)
        aes = AESGCM(_derive_file_aes_key(recipient_priv.exchange(ephem_pub), ephem_pub_raw))

        idx = 0
        while True:
            len_bytes = fin.read(4)
            if not len_bytes:
                raise AppError("DECRYPT_FAILED", "Truncated encrypted file.", 500)
            (ct_len,) = struct.unpack(">I", len_bytes)
            nonce = fin.read(12)
            ct = fin.read(ct_len)
            if len(nonce) != 12 or len(ct) != ct_len:
                raise AppError("DECRYPT_FAILED", "Corrupted encrypted chunk.", 500)
            is_last = len(fin.peek(1)) == 0
            aad = struct.pack(">Q?", idx, is_last)
            try:
                yield aes.decrypt(nonce, ct, aad)
            except InvalidTag as exc:
                raise UnauthorizedError("Decryption failed for this recording.") from exc
            if is_last:
                break
            idx += 1


def decrypt_file_bytes(enc_path: Path, priv_raw: bytes) -> bytes:
    """Decrypt an entire .enc recording into memory."""
    return b"".join(decrypt_file_iter(enc_path, priv_raw))


class SessionKeyStore:
    """In-memory store holding unwrapped X25519 private keys for active sessions."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[bytes, float]] = {}

    def put(self, session_id: str, priv_raw: bytes) -> None:
        with self._lock:
            self._entries[session_id] = (priv_raw, time.monotonic() + self._ttl)

    def get(self, session_id: str | None) -> bytes | None:
        if not session_id:
            return None
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                return None
            priv_raw, expires_at = entry
            if time.monotonic() > expires_at:
                self._entries.pop(session_id, None)
                return None
            return priv_raw

    def remove(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._entries.pop(session_id, None)
