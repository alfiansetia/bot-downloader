"""
Facebook downloader using yt-dlp.
Supports public video and reel URLs.
"""

from __future__ import annotations

from app.downloader.base import BaseDownloader
from app.config import settings


class FacebookDownloader(BaseDownloader):
    PLATFORM = "facebook"
    URL_PATTERNS = [
        r"(?:https?://)?(?:www\.)?facebook\.com/[\w.]+/videos/",
        r"(?:https?://)?(?:www\.)?facebook\.com/watch/",
        r"(?:https?://)?(?:www\.)?fb\.watch/",
        r"(?:https?://)?(?:www\.)?facebook\.com/reel/",
        r"(?:https?://)?(?:www\.)?facebook\.com/share/",
    ]

    @classmethod
    def _get_ydl_opts(cls, output_dir: str) -> dict:
        opts = {
            "format": "bestvideo[height<=1080]/bestvideo[width<=1080]/bestvideo+bestaudio/best[height<=1080]/best[width<=1080]/best",
            "outtmpl": f"{output_dir}/%(title).150s.%(ext)s",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
        }
        if settings.YTDLP_COOKIES_FILE:
            opts["cookiefile"] = settings.YTDLP_COOKIES_FILE
        return opts
