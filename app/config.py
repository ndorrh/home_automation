"""Immutable application configuration validated from environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings loaded from .env."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000, ge=1024, le=65535)
    log_level: str = Field(default="INFO")
    log_clear_minutes: int = Field(default=30, ge=1, le=1440)

    app_password_hash: str = Field(default="")
    session_secret: str = Field(default="")
    session_ttl_seconds: int = Field(default=86400, ge=300)

    enc_public_key: str = Field(default="")
    enc_private_key_wrapped: str = Field(default="")

    prevent_system_sleep: bool = Field(default=True)
    auto_hotspot_on_offline: bool = Field(default=True)
    enable_cloudflared: bool = Field(default=True)
    cloudflared_bin: str = Field(default="cloudflared")

    gmail_credentials_enc: str = Field(default="")
    gmail_user: str = Field(default="")
    gmail_app_password: str = Field(default="")
    notify_email_to: str = Field(default="")
    email_resend_minutes: int = Field(default=60, ge=1, le=1440)

    camera_index: int = Field(default=0, ge=0)
    audio_sample_rate: int = Field(default=16000, ge=8000, le=48000)
    screen_fps: int = Field(default=6, ge=1, le=30)
    screen_monitor_index: int = Field(default=1, ge=0)

    @property
    def recordings_dir(self) -> Path:
        return ROOT_DIR / "recordings"

    @property
    def logs_dir(self) -> Path:
        return ROOT_DIR / "logs"

    @property
    def data_dir(self) -> Path:
        return ROOT_DIR / "data"

    @property
    def bin_dir(self) -> Path:
        return ROOT_DIR / "bin"

    @property
    def mailer_script(self) -> Path:
        return ROOT_DIR / "mailer" / "send_url.mjs"

    @property
    def web_dir(self) -> Path:
        return ROOT_DIR / "web"

    @property
    def email_configured(self) -> bool:
        if self.gmail_credentials_enc.strip():
            return True
        recipient = self.notify_email_to or self.gmail_user
        return bool(self.gmail_user.strip() and self.gmail_app_password.strip() and recipient.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached immutable Settings singleton."""
    return Settings()
