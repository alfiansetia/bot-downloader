"""
Telegram Bot — receives URLs and sends back downloaded media
(videos, photos, and carousel/album posts).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from telegram import InputMediaPhoto, InputMediaVideo, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.config import settings
from app.downloader import (
    YouTubeDownloader,
    TikTokDownloader,
    InstagramDownloader,
    FacebookDownloader,
    TwitterDownloader,
)
from app.downloader.base import cleanup_result, classify_media

logger = logging.getLogger(__name__)

# ── Registry of downloaders ──
DOWNLOADERS = [
    YouTubeDownloader,
    TikTokDownloader,
    InstagramDownloader,
    FacebookDownloader,
    TwitterDownloader,
]

# Telegram allows at most 10 items in a media group.
MEDIA_GROUP_LIMIT = 10
# Telegram caption limit for media is 1024 chars — stay safely below.
CAPTION_LIMIT = 900


def _get_downloader(url: str):
    """Find the first downloader that matches the URL."""
    for dl in DOWNLOADERS:
        if dl.matches(url):
            return dl
    return None


def log_chat_interaction(
    chat_id: int,
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    url: str,
    platform: Optional[str],
    success: bool,
    error: Optional[str] = None,
    media_type: Optional[str] = None,
    media_count: Optional[int] = None,
) -> None:
    """Log user chat interaction to a JSON file named after the chat ID."""
    import datetime
    import json
    from pathlib import Path

    try:
        logs_dir = Path(settings.LOGS_DIR)
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / f"{chat_id}.json"

        # Load existing logs
        if log_file.exists():
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
                    if not isinstance(history, list):
                        history = []
            except Exception:
                history = []
        else:
            history = []

        # Create new entry
        entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "url": url,
            "platform": platform,
            "success": success,
            "error": error,
            "media_type": media_type,
            "media_count": media_count,
        }

        history.append(entry)

        # Atomic write (tmp file + rename) so concurrent messages can't corrupt the log.
        tmp_file = log_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, log_file)

    except Exception as e:
        logger.error("Failed to write chat log for chat_id %d: %s", chat_id, e)


def _log(
    update: Update,
    url: str,
    platform: Optional[str],
    success: bool,
    error: Optional[str] = None,
    media_type: Optional[str] = None,
    media_count: Optional[int] = None,
) -> None:
    """Small wrapper so handlers don't repeat the user/chat plumbing."""
    log_chat_interaction(
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name,
        url=url,
        platform=platform,
        success=success,
        error=error,
        media_type=media_type,
        media_count=media_count,
    )


async def start(update: Update, context):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "🤖 *Bot Downloader*\n\n"
        f"🆔 *Chat ID Anda:* `{chat_id}`\n\n"
        "Kirim link dari:\n"
        "• YouTube / Shorts (video)\n"
        "• TikTok (video / foto slide)\n"
        "• Instagram (Reel / Post foto / Carousel / Story)\n"
        "• Facebook (Video / Reel / Post foto)\n"
        "• Twitter / X (video / foto)\n\n"
        "Postingan carousel/album berisi beberapa foto akan dikirim sekaligus!",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context):
    await start(update, context)


def _build_caption(platform: str, title: Optional[str], duration: Optional[int],
                   media_type: str, media_count: int) -> str:
    import html

    caption = f"✅ <b>{html.escape(platform.title())}</b>\n"
    if media_count > 1:
        label = "foto" if media_type == "photo" else "media"
        caption += f"🖼 {media_count} {label}\n"
    if title:
        caption += f"📹 {html.escape(title)}\n"
    if duration and media_count == 1:
        duration_secs = int(duration)
        caption += f"⏱ {duration_secs // 60}:{duration_secs % 60:02d}\n"
    return caption[:CAPTION_LIMIT]


async def _send_single(update: Update, file_path: str, caption: str):
    """Send one file using the right Telegram method for its type."""
    kind = classify_media(file_path)
    if kind == "photo":
        with open(file_path, "rb") as f:
            await update.message.reply_photo(
                photo=f, caption=caption, parse_mode="HTML",
                write_timeout=120, read_timeout=120,
            )
    elif kind == "video":
        with open(file_path, "rb") as f:
            await update.message.reply_video(
                video=f, caption=caption, parse_mode="HTML",
                supports_streaming=True, write_timeout=120, read_timeout=120,
            )
    else:
        with open(file_path, "rb") as f:
            await update.message.reply_document(
                document=f, caption=caption, parse_mode="HTML",
                write_timeout=120, read_timeout=120,
            )


async def _send_multiple(update: Update, files: list[str], caption: str):
    """Send several files as a media group (photos/videos), rest as documents."""
    group = [f for f in files if classify_media(f) in ("photo", "video")][:MEDIA_GROUP_LIMIT]
    rest = [f for f in files if f not in group]

    handles = []
    try:
        media = []
        for i, path in enumerate(group):
            fh = open(path, "rb")
            handles.append(fh)
            if classify_media(path) == "photo":
                media.append(InputMediaPhoto(fh, caption=caption if i == 0 else None,
                                             parse_mode="HTML" if i == 0 else None))
            else:
                media.append(InputMediaVideo(fh, caption=caption if i == 0 else None,
                                             parse_mode="HTML" if i == 0 else None,
                                             supports_streaming=True))
        await update.message.reply_media_group(media=media)
    finally:
        for fh in handles:
            try:
                fh.close()
            except Exception:
                pass

    for path in rest:
        await _send_single(update, path, caption="")


async def handle_message(update: Update, context):
    """Handle incoming messages — detect URL and download."""
    if not update.message or not update.message.text:
        return

    # ── Auth check ──
    user_id = update.effective_user.id
    if settings.TELEGRAM_ALLOWED_USERS and user_id not in settings.TELEGRAM_ALLOWED_USERS:
        await update.message.reply_text("⛔ Kamu tidak diizinkan menggunakan bot ini.")
        return

    url = update.message.text.strip()

    # ── Resolve redirects (e.g. vt.tiktok.com) ──
    from app.downloader.base import resolve_redirects
    url = resolve_redirects(url)

    # ── Find matching downloader ──
    downloader = _get_downloader(url)
    if not downloader:
        # No link or unrecognized link — ignore silently
        return

    # ── React with 👀 and send processing message ──
    try:
        await update.message.set_reaction(reaction="👀")
    except Exception as e:
        logger.debug("set_reaction failed (non-fatal): %s", e)
    msg = await update.message.reply_text("⏬ Memproses...")

    try:
        # yt-dlp blocks — run off the event loop so the bot stays responsive.
        result = await asyncio.to_thread(downloader.download, url)

        if not result.success:
            await msg.edit_text(f"❌ Gagal mendownload: {result.error}")
            _log(update, url, downloader.PLATFORM, False, error=result.error)
            return

        files = [f for f in result.files if f and os.path.exists(f)]
        if not files:
            await msg.edit_text("❌ File tidak ditemukan setelah download.")
            _log(update, url, downloader.PLATFORM, False,
                 error="File not found after download")
            cleanup_result(result)
            return

        # Per-file size check (Telegram bots cap uploads at ~50 MB per file).
        ok_files: list[str] = []
        too_large: list[str] = []
        for f in files:
            size_mb = os.path.getsize(f) / (1024 * 1024)
            if size_mb > settings.MAX_FILE_SIZE_MB:
                too_large.append(f"{os.path.basename(f)} ({size_mb:.1f} MB)")
            else:
                ok_files.append(f)

        if not ok_files:
            await msg.edit_text(
                f"⚠️ File terlalu besar (maksimal {settings.MAX_FILE_SIZE_MB} MB):\n"
                + "\n".join(f"• {name}" for name in too_large[:10])
            )
            _log(update, url, downloader.PLATFORM, False,
                 error=f"File too large: {'; '.join(too_large[:3])}",
                 media_type=result.media_type, media_count=len(files))
            cleanup_result(result)
            return

        caption = _build_caption(downloader.PLATFORM, result.title,
                                 result.duration, result.media_type, len(ok_files))
        if too_large:
            caption += f"\n⚠️ {len(too_large)} file dilewati (melebihi {settings.MAX_FILE_SIZE_MB} MB)."
            caption = caption[:CAPTION_LIMIT]

        if len(ok_files) == 1:
            await _send_single(update, ok_files[0], caption)
        else:
            if len(ok_files) > MEDIA_GROUP_LIMIT:
                await update.message.reply_text(
                    f"ℹ️ {len(ok_files)} media ditemukan, "
                    f"hanya {MEDIA_GROUP_LIMIT} pertama yang dikirim (limit Telegram)."
                )
            await _send_multiple(update, ok_files, caption)

        _log(update, url, downloader.PLATFORM, True,
             media_type=result.media_type, media_count=len(ok_files))

        # Cleanup
        cleanup_result(result)

    except Exception as e:
        logger.exception("Error processing message")
        try:
            await msg.edit_text(f"❌ Error: {str(e)}")
        except Exception:
            pass
        _log(update, url, downloader.PLATFORM if downloader else None,
             False, error=str(e))


def build_application() -> Application:
    """Build and return the Telegram Application (ready for webhook)."""
    from telegram.request import HTTPXRequest

    # Use higher timeouts for uploading large media files (up to 50MB)
    request = HTTPXRequest(write_timeout=120, read_timeout=120, connect_timeout=15)
    app = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .request(request)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app


def run_bot() -> None:
    """Run the bot in polling mode (blocking)."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set — cannot run bot in polling mode.")
        return
    app = build_application()
    logger.info("Starting Telegram bot in polling mode...")
    app.run_polling()
