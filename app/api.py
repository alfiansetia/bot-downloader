"""
REST API — download videos via HTTP endpoints.
Built with FastAPI.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from telegram import Update

from app.config import settings
from app.downloader import (
    YouTubeDownloader,
    TikTokDownloader,
    InstagramDownloader,
    FacebookDownloader,
    TwitterDownloader,
)

logger = logging.getLogger(__name__)

DOWNLOADERS = [
    YouTubeDownloader,
    TikTokDownloader,
    InstagramDownloader,
    FacebookDownloader,
    TwitterDownloader,
]

# ── Telegram bot application (lazy init) ──
_telegram_app = None


def _get_telegram_app():
    global _telegram_app
    if _telegram_app is None and settings.TELEGRAM_BOT_TOKEN:
        from app.bot import build_application
        _telegram_app = build_application()
    return _telegram_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Set up Telegram webhook on startup."""
    tg_app = _get_telegram_app()
    if tg_app and settings.TELEGRAM_BOT_TOKEN:
        await tg_app.initialize()
        await tg_app.start()
        if settings.APP_URL:
            webhook_url = f"{settings.APP_URL}/webhook"
            try:
                # Check webhook first to avoid redundant set_webhook calls
                webhook_info = await tg_app.bot.get_webhook_info()
                if webhook_info.url != webhook_url:
                    await tg_app.bot.set_webhook(url=webhook_url)
                    logger.info("Telegram webhook set to %s", webhook_url)
                else:
                    logger.info("Telegram webhook is already set to %s", webhook_url)
            except Exception as e:
                logger.warning(
                    "Failed to check or set Telegram webhook: %s. "
                    "This is expected if multiple workers are starting up simultaneously.",
                    e
                )
        else:
            logger.warning("APP_URL not set — webhook not configured. Set APP_URL in .env")
    yield
    if tg_app:
        try:
            await tg_app.stop()
            await tg_app.shutdown()
        except Exception as e:
            logger.warning("Error during Telegram application shutdown: %s", e)


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)


# ── Models ──

class DownloadRequest(BaseModel):
    url: str = Field(..., description="Video URL to download")


class DownloadResponse(BaseModel):
    success: bool
    platform: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[int] = None
    file_url: Optional[str] = None
    error: Optional[str] = None


# ── Endpoints ──

@app.get("/")
def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "platforms": ["youtube", "tiktok", "instagram", "facebook", "twitter"],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/download", response_model=DownloadResponse)
def download_video(req: DownloadRequest, background_tasks: BackgroundTasks):
    """
    Download a video from a supported platform.
    Returns the video file as a download response.
    """
    from app.downloader.base import DownloadResult

    url = req.url.strip()

    # Find matching downloader
    downloader = None
    for dl_cls in DOWNLOADERS:
        if dl_cls.matches(url):
            downloader = dl_cls
            break

    if not downloader:
        raise HTTPException(
            status_code=400,
            detail="URL not recognized. Supported: YouTube, TikTok, Instagram, Facebook, Twitter/X",
        )

    result = downloader.download(url)

    if not result.success:
        raise HTTPException(status_code=400, detail=f"Download failed: {result.error}")

    if not result.file_path or not os.path.exists(result.file_path):
        raise HTTPException(status_code=500, detail="File not found after download")

    # Return file
    filename = os.path.basename(result.file_path)
    background_tasks.add_task(os.remove, result.file_path)
    return FileResponse(
        path=result.file_path,
        filename=filename,
        media_type="video/mp4",
        headers={
            "X-Platform": downloader.PLATFORM,
            "X-Title": result.title or "",
        },
    )


@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Receive Telegram updates via webhook.
    Telegram sends POST requests here when users interact with the bot.
    """
    tg_app = _get_telegram_app()
    if not tg_app:
        return JSONResponse(status_code=503, content={"error": "Telegram bot not configured"})

    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}
