"""
YouTube downloader using yt-dlp.
"""

from __future__ import annotations

from app.downloader.base import BaseDownloader
from app.config import settings


class YouTubeDownloader(BaseDownloader):
    PLATFORM = "youtube"
    URL_PATTERNS = [
        r"(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be)/",
        r"(?:https?://)?(?:www\.)?youtube\.com/shorts/",
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
        if settings.YTDLP_USER_AGENT:
            opts["user_agent"] = settings.YTDLP_USER_AGENT
        return opts
