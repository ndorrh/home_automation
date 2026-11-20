"""First-run bootstrap: directories, .env secrets, DPAPI Gmail sealing, cloudflared & mailer."""

from __future__ import annotations

import logging
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import ROOT_DIR
from app.env_guard import protect_env, write_env_safely
from app.vault import GmailCredentials, seal_gmail_credentials

logger = logging.getLogger("smart_home_av.bootstrap")


def ensure_directories(root: Path = ROOT_DIR) -> None:
    """Create recordings, logs, data, and bin directories if missing."""
    for rel in (
        "recordings/audio",
        "recordings/snapshots",
        "recordings/video",
        "recordings/screen",
        "logs",
        "data",
        "bin",
    ):
        (root / rel).mkdir(parents=True, exist_ok=True)


def ensure_env_file(root: Path = ROOT_DIR) -> Path:
    """Create .env from .env.example on first run, populate SESSION_SECRET, and seal Gmail."""
    env_path = root / ".env"
    example_path = root / ".env.example"
    newly_created = not env_path.exists()

    if newly_created:
        content = example_path.read_text(encoding="utf-8") if example_path.exists() else ""
        env_path.write_text(content, encoding="utf-8")
        logger.info("Created initial .env configuration from .env.example")

    text = env_path.read_text(encoding="utf-8")
    updated = _fill_session_secret(text)
    updated = _seal_plaintext_gmail_lines(updated)
    if updated != text:
        write_env_safely(env_path, updated, protect_after=True if newly_created else None)
    elif newly_created:
        protect_env(env_path)

    return env_path


def _fill_session_secret(content: str) -> str:
    lines = content.splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith("SESSION_SECRET="):
            found = True
            if not line.split("=", 1)[1].strip():
                out.append(f"SESSION_SECRET={secrets.token_urlsafe(48)}")
                continue
        out.append(line)
    if not found:
        out.append(f"SESSION_SECRET={secrets.token_urlsafe(48)}")
    return "\n".join(out) + "\n"


def _seal_plaintext_gmail_lines(content: str) -> str:
    """Migrate plaintext GMAIL_* keys in .env into DPAPI-encrypted GMAIL_CREDENTIALS_ENC."""
    if sys.platform != "win32":
        return content
    values = _extract_kv(content)
    user = values.get("GMAIL_USER", "").strip()
    pw = values.get("GMAIL_APP_PASSWORD", "").strip()
    to = values.get("NOTIFY_EMAIL_TO", "").strip() or user
    if not (user and pw):
        return content

    sealed = seal_gmail_credentials(GmailCredentials(user=user, app_password=pw, notify_to=to))
    filtered: list[str] = []
    wrote_enc = False
    for line in content.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in {"GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL_TO"}:
            continue
        if key == "GMAIL_CREDENTIALS_ENC":
            filtered.append(f"GMAIL_CREDENTIALS_ENC={sealed}")
            wrote_enc = True
            continue
        filtered.append(line)

    if not wrote_enc:
        filtered.append(f"GMAIL_CREDENTIALS_ENC={sealed}")
    logger.info("Sealed plaintext Gmail credentials into GMAIL_CREDENTIALS_ENC.")
    return "\n".join(filtered) + "\n"


def _extract_kv(content: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, val = stripped.split("=", 1)
        parsed[key.strip()] = val.strip()
    return parsed


def ensure_cloudflared_quick_config(root: Path = ROOT_DIR) -> Path:
    """Create isolated quick-tunnel config so user ~/.cloudflared/config.yml is ignored."""
    cfg_path = root / "logs" / "cloudflared_quick.yml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text("# Isolated config for Quick Tunnel\nno-autoupdate: true\n", encoding="utf-8")
    return cfg_path


def resolve_cloudflared_binary(binary_name: str = "cloudflared", root: Path = ROOT_DIR) -> str | None:
    """Prefer self-contained bin/cloudflared.exe, falling back to PATH."""
    local_bin = root / "bin" / "cloudflared.exe"
    if local_bin.exists():
        return str(local_bin)
    resolved = shutil.which(binary_name)
    if resolved:
        return resolved
    logger.warning("cloudflared binary not found in bin/ or PATH.")
    return None


def ensure_mailer_dependencies(root: Path = ROOT_DIR) -> bool:
    """Install Node.js dependencies for nodemailer if node_modules is missing."""
    mailer_dir = root / "mailer"
    if not (mailer_dir / "package.json").exists():
        return False
    if (mailer_dir / "node_modules" / "nodemailer").exists():
        return True

    npm_bin = shutil.which("npm")
    if not npm_bin:
        logger.warning("npm not found on PATH; email notifications unavailable.")
        return False

    result = subprocess.run(
        [npm_bin, "install", "--omit=dev"],
        cwd=str(mailer_dir),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        logger.error("npm install failed: %s", result.stderr.strip())
        return False
    logger.info("Nodemailer installed successfully.")
    return True


def run_bootstrap(root: Path = ROOT_DIR, cloudflared_bin: str = "cloudflared") -> None:
    """Execute idempotent first-run initialization."""
    ensure_directories(root)
    ensure_env_file(root)
    ensure_cloudflared_quick_config(root)
    ensure_mailer_dependencies(root)
    resolve_cloudflared_binary(cloudflared_bin, root)
