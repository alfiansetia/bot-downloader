"""
Instagram downloader using yt-dlp.
Supports reels, posts with video, and stories.
"""

from __future__ import annotations

from app.downloader.base import BaseDownloader
from app.config import settings


class InstagramDownloader(BaseDownloader):
    PLATFORM = "instagram"
    URL_PATTERNS = [
        r"(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|reels|tv|stories)/",
        r"(?:https?://)?(?:www\.)?instagram\.com/reel/",
        r"(?:https?://)?(?:www\.)?instagram\.com/share/",
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
