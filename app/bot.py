"""
Telegram Bot — receives URLs and sends back downloaded videos.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.config import settings
from app.downloader import (
    YouTubeDownloader,
    TikTokDownloader,
    InstagramDownloader,
    FacebookDownloader,
    TwitterDownloader,
)

logger = logging.getLogger(__name__)

# ── Registry of downloaders ──
DOWNLOADERS = [
    YouTubeDownloader,
    TikTokDownloader,
    InstagramDownloader,
    FacebookDownloader,
    TwitterDownloader,
]


def _get_downloader(url: str):
    """Find the first downloader that matches the URL."""
    for dl in DOWNLOADERS:
        if dl.matches(url):
            return dl
    return None


async def start(update: Update, context):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "🤖 *Bot Downloader*\n\n"
        f"🆔 *Chat ID Anda:* `{chat_id}`\n\n"
        "Kirim link video dari:\n"
        "• YouTube / Shorts\n"
        "• TikTok\n"
        "• Instagram (Reel / Post / Story)\n"
        "• Facebook (Video / Reel)\n"
        "• Twitter / X\n\n"
        "Lalu saya akan download dan kirim videonya!",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context):
    await start(update, context)


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
    await update.message.set_reaction(reaction="👀")
    msg = await update.message.reply_text("⏬ Memproses video...")

    try:
        result = downloader.download(url)

        if not result.success:
            await msg.edit_text(f"❌ Gagal mendownload: {result.error}")
            return

        if not result.file_path or not os.path.exists(result.file_path):
            await msg.edit_text("❌ File tidak ditemukan setelah download.")
            return

        # Check file size
        file_size_mb = os.path.getsize(result.file_path) / (1024 * 1024)
        if file_size_mb > settings.MAX_FILE_SIZE_MB:
            await msg.edit_text(
                f"⚠️ File terlalu besar ({file_size_mb:.1f} MB). "
                f"Maksimal {settings.MAX_FILE_SIZE_MB} MB."
            )
            os.remove(result.file_path)
            return

        import html

        caption = f"✅ <b>{html.escape(downloader.PLATFORM.title())}</b>\n"
        if result.title:
            caption += f"📹 {html.escape(result.title)}\n"
        if result.duration:
            duration_secs = int(result.duration)
            caption += f"⏱ {duration_secs // 60}:{duration_secs % 60:02d}\n"

        with open(result.file_path, "rb") as f:
            await update.message.reply_video(
                video=f,
                caption=caption,
                parse_mode="HTML",
                supports_streaming=True,
                write_timeout=120,
                read_timeout=120,
            )

        # Cleanup
        os.remove(result.file_path)

    except Exception as e:
        logger.exception("Error processing message")
        await msg.edit_text(f"❌ Error: {str(e)}")


def build_application() -> Application:
    """Build and return the Telegram Application (ready for webhook)."""
    from telegram.request import HTTPXRequest

    # Use higher timeouts for uploading large video files (up to 50MB)
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
