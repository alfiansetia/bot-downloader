"""
Entry point — run with: python -m app [bot|api|all]
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import settings

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Bot Downloader")
    parser.add_argument(
        "mode",
        nargs="?",
        default="all",
        choices=["bot", "api", "all"],
        help="Run mode: bot, api, or all (default: all)",
    )
    args = parser.parse_args()

    if args.mode == "bot":
        from app.bot import run_bot
        run_bot()
    elif args.mode == "api":
        _start_api()
    elif args.mode == "all":
        # In "all" mode:
        # If APP_URL is set, we use webhook (API runs and configures it), so no polling thread is needed.
        # If APP_URL is not set, we run the polling bot in a background thread and the API on the main thread.
        if not settings.APP_URL:
            _start_bot()
        _start_api()


def _start_api():
    """Start FastAPI via uvicorn."""
    import uvicorn

    logger.info("Starting REST API on %s:%s", settings.API_HOST, settings.API_PORT)
    uvicorn.run(
        "app.api:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        workers=settings.API_WORKERS,
        log_level="debug" if settings.DEBUG else "info",
    )


def _start_bot():
    """Start Telegram bot in a background thread."""
    from threading import Thread
    from app.bot import run_bot

    t = Thread(target=run_bot, daemon=True)
    t.start()
    logger.info("Telegram bot started in background thread")


if __name__ == "__main__":
    main()
