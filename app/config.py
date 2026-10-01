"""
Base configuration — reads from environment variables with sensible defaults.
All settings are centralized here so any module can import from config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


@dataclass
class Settings:
    # ── App ──
    APP_NAME: str = "Bot Downloader"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── Server (REST API) ──
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_WORKERS: int = 4
    APP_URL: Optional[str] = None  # public URL for webhook, e.g. https://example.com

    # ── Telegram Bot ──
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_ALLOWED_USERS: list[int] = None  # empty = allow all

    # ── Download ──
    DOWNLOAD_DIR: str = "/tmp/downloads"
    MAX_FILE_SIZE_MB: int = 50
    REQUEST_TIMEOUT: int = 30

    # ── yt-dlp ──
    YTDLP_COOKIES_FILE: Optional[str] = None
    YTDLP_USER_AGENT: Optional[str] = None

    # ── Logs ──
    LOGS_DIR: str = "logs"

    @classmethod
    def from_env(cls) -> "Settings":
        import os

        allowed_users = os.getenv("TELEGRAM_ALLOWED_USERS", "")
        parsed_users = []
        if allowed_users:
            try:
                parsed_users = [int(u.strip()) for u in allowed_users.split(",") if u.strip()]
            except ValueError:
                parsed_users = []

        return cls(
            APP_NAME=os.getenv("APP_NAME", "Bot Downloader"),
            APP_VERSION=os.getenv("APP_VERSION", "1.0.0"),
            DEBUG=os.getenv("DEBUG", "false").lower() in ("true", "1", "yes"),
            API_HOST=os.getenv("API_HOST", "0.0.0.0"),
            API_PORT=int(os.getenv("API_PORT", "8000")),
            API_WORKERS=int(os.getenv("API_WORKERS", "4")),
            APP_URL=os.getenv("APP_URL"),
            TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN"),
            TELEGRAM_ALLOWED_USERS=parsed_users,
            DOWNLOAD_DIR=os.getenv("DOWNLOAD_DIR", "/tmp/downloads"),
            MAX_FILE_SIZE_MB=int(os.getenv("MAX_FILE_SIZE_MB", "50")),
            REQUEST_TIMEOUT=int(os.getenv("REQUEST_TIMEOUT", "30")),
            YTDLP_COOKIES_FILE=os.getenv("YTDLP_COOKIES_FILE"),
            YTDLP_USER_AGENT=os.getenv("YTDLP_USER_AGENT"),
            LOGS_DIR=os.getenv("LOGS_DIR", "logs"),
        )


settings = Settings.from_env()
