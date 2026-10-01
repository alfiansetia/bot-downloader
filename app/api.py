"""
REST API — download media (videos, photos, carousels) via HTTP endpoints.
Built with FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import shutil
import uuid
import zipfile
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
from app.downloader.base import cleanup_result

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
    url: str = Field(..., description="Media URL to download (video, photo, or carousel/album)")


class DownloadResponse(BaseModel):
    success: bool
    platform: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[int] = None
    file_url: Optional[str] = None
    error: Optional[str] = None


# ── Helpers ──

def _cleanup_paths(*paths: str) -> None:
    """Background-task cleanup for files/directories (ignores missing paths)."""
    for p in paths:
        try:
            if not p:
                continue
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.isfile(p):
                os.remove(p)
        except Exception as e:
            logger.warning("Cleanup failed for %s: %s", p, e)


def _zip_files(files: list[str], dest: str) -> str:
    """Pack multiple media files into a zip (dedupes colliding basenames)."""
    seen: set[str] = set()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, path in enumerate(files):
            name = os.path.basename(path)
            if name in seen:
                stem, ext = os.path.splitext(name)
                name = f"{stem}-{i}{ext}"
            seen.add(name)
            zf.write(path, arcname=name)
    return dest


# ── Endpoints ──

@app.get("/")
def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "platforms": ["youtube", "tiktok", "instagram", "facebook", "twitter"],
        "media": ["video", "photo", "carousel"],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/download")
async def download_media(req: DownloadRequest, background_tasks: BackgroundTasks):
    """
    Download media from a supported platform.

    - Single video/photo → returned directly as a file download.
    - Carousel/album (multiple files) → returned as a `.zip` archive.
    """
    url = req.url.strip()

    # ── Resolve redirects (e.g. vt.tiktok.com) ──
    from app.downloader.base import resolve_redirects
    url = resolve_redirects(url)

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

    # yt-dlp blocks — offload from the event loop.
    result = await asyncio.to_thread(downloader.download, url)

    if not result.success:
        raise HTTPException(status_code=400, detail=f"Download failed: {result.error}")

    files = [f for f in result.files if f and os.path.exists(f)]
    if not files:
        cleanup_result(result)
        raise HTTPException(status_code=500, detail="File not found after download")

    # Enforce per-file size limit (same rule as the Telegram bot).
    ok_files: list[str] = []
    for f in files:
        size_mb = os.path.getsize(f) / (1024 * 1024)
        if size_mb <= settings.MAX_FILE_SIZE_MB:
            ok_files.append(f)
    if not ok_files:
        cleanup_result(result)
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {settings.MAX_FILE_SIZE_MB} MB per file)",
        )

    headers = {
        "X-Platform": downloader.PLATFORM,
        "X-Title": result.title or "",
        "X-Media-Type": result.media_type,
        "X-Media-Count": str(len(ok_files)),
    }
    if len(ok_files) < len(files):
        headers["X-Excluded-Too-Large"] = str(len(files) - len(ok_files))

    if len(ok_files) == 1:
        # Single file → stream it directly with its real content type.
        path = ok_files[0]
        media_type, _ = mimetypes.guess_type(path)
        background_tasks.add_task(_cleanup_paths, result.dir_path)
        return FileResponse(
            path=path,
            filename=os.path.basename(path),
            media_type=media_type or "application/octet-stream",
            headers=headers,
        )

    # Multiple files (carousel/album) → zip them.
    zip_name = f"{downloader.PLATFORM}-album-{uuid.uuid4().hex[:8]}.zip"
    zip_path = os.path.join(settings.DOWNLOAD_DIR, zip_name)
    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    await asyncio.to_thread(_zip_files, ok_files, zip_path)
    background_tasks.add_task(_cleanup_paths, zip_path, result.dir_path)
    headers["X-Archive"] = zip_name
    return FileResponse(
        path=zip_path,
        filename=zip_name,
        media_type="application/zip",
        headers=headers,
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
