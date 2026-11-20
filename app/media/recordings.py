"""Safe listing, encrypted storage cleanup, resolution, and deletion of recordings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.crypto import encrypt_file
from app.errors import AppError, NotFoundError

KIND_DIRS: dict[str, str] = {
    "audio": "audio",
    "snapshots": "snapshots",
    "video": "video",
    "screen": "screen",
}

ALLOWED_EXTENSIONS: dict[str, frozenset[str]] = {
    "audio": frozenset({".wav.enc"}),
    "snapshots": frozenset({".jpg.enc", ".jpeg.enc", ".png.enc"}),
    "video": frozenset({".mp4.enc", ".avi.enc"}),
    "screen": frozenset({".jpg.enc", ".mp4.enc"}),
}

MEDIA_TYPES: dict[str, str] = {
    ".wav": "audio/wav",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".mp4": "video/mp4",
    ".avi": "video/x-msvideo",
}

SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class RecordingItem:
    kind: str
    filename: str
    display_name: str
    media_type: str
    size_bytes: int
    modified_at: str
    url: str


def validate_kind(kind: str) -> str:
    if kind not in KIND_DIRS:
        raise AppError("INVALID_KIND", f"Unsupported recording category: {kind}", 400)
    return KIND_DIRS[kind]


def _matching_suffix(filename: str, allowed: frozenset[str]) -> str | None:
    lower = filename.lower()
    for suffix in allowed:
        if lower.endswith(suffix):
            return suffix
    return None


def decrypted_download_name(filename: str) -> str:
    return filename[:-4] if filename.lower().endswith(".enc") else filename


def media_type_for(filename: str) -> str:
    plain_name = decrypted_download_name(filename)
    ext = Path(plain_name).suffix.lower()
    return MEDIA_TYPES.get(ext, "application/octet-stream")


def resolve_recording_path(base_dir: Path, kind: str, filename: str) -> Path:
    """Resolve and validate a recording file path safely inside its category folder."""
    subdir = validate_kind(kind)
    if not filename or not SAFE_FILENAME_RE.match(filename):
        raise AppError("INVALID_FILENAME", "Invalid recording filename.", 400)

    if _matching_suffix(filename, ALLOWED_EXTENSIONS[kind]) is None:
        raise AppError("INVALID_FILENAME", "Unsupported file extension.", 400)

    target_dir = (base_dir / subdir).resolve()
    target_file = (target_dir / filename).resolve()
    if target_file.parent != target_dir:
        raise AppError("INVALID_FILENAME", "Path traversal is not permitted.", 400)
    return target_file


def encrypt_and_cleanup(tmp_path: Path, enc_path: Path, public_key_b64: str) -> None:
    """Encrypt temporary plaintext recording to .enc and delete temp file safely."""
    try:
        encrypt_file(tmp_path, enc_path, public_key_b64)
    finally:
        tmp_path.unlink(missing_ok=True)


def list_recordings(base_dir: Path, kind: str | None = None) -> list[dict]:
    """List encrypted recordings sorted newest-first."""
    kinds = [kind] if kind else list(KIND_DIRS.keys())
    items: list[RecordingItem] = []

    for cat in kinds:
        subdir = validate_kind(cat)
        folder = base_dir / subdir
        if not folder.exists():
            continue
        _collect_folder_items(folder, cat, items)

    items.sort(key=lambda item: item.modified_at, reverse=True)
    return [item.__dict__ for item in items]


def _collect_folder_items(folder: Path, cat: str, out: list[RecordingItem]) -> None:
    allowed = ALLOWED_EXTENSIONS[cat]
    for path in folder.iterdir():
        if not path.is_file() or path.name.startswith(".tmp_"):
            continue
        if _matching_suffix(path.name, allowed) is None:
            continue
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        out.append(
            RecordingItem(
                kind=cat,
                filename=path.name,
                display_name=decrypted_download_name(path.name),
                media_type=media_type_for(path.name),
                size_bytes=stat.st_size,
                modified_at=mtime,
                url=f"/api/recordings/{cat}/{path.name}",
            )
        )


def delete_recording(base_dir: Path, kind: str, filename: str) -> None:
    """Delete a specific encrypted recording file."""
    target = resolve_recording_path(base_dir, kind, filename)
    if not target.exists() or not target.is_file():
        raise NotFoundError(f"Recording '{filename}' not found.")
    target.unlink()
