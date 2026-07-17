"""
Base downloader — all platform downloaders inherit from this.
Uses yt-dlp as the primary engine, with platform-specific overrides.
"""

from __future__ import annotations

import glob
import logging
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yt_dlp

from app.config import settings

logger = logging.getLogger(__name__)


def clean_error_message(error: Optional[str]) -> Optional[str]:
    if not error:
        return error

    error_lower = error.lower()

    # Instagram-specific friendly messages
    if "instagram" in error_lower and "empty media response" in error_lower:
        return "Instagram membatasi akses tanpa login. Silakan coba sesaat lagi, atau gunakan cookies jika ini postingan privat."

    if "private" in error_lower or "login" in error_lower or "sign in" in error_lower:
        return "Video ini bersifat privat, memerlukan login, atau tidak dapat diakses secara publik."

    # Simplify other errors
    lines = error.split('\n')
    for line in lines:
        if "error:" in line.lower():
            clean_line = line.strip()
            # Strip out common yt-dlp trailing instructions
            lower_line = clean_line.lower()
            for pattern in [
                "; please report",
                "; check if",
                "; see http",
                ". see http",
                ". confirm you",
                "confirm you are",
            ]:
                if pattern in lower_line:
                    idx = lower_line.index(pattern)
                    clean_line = clean_line[:idx].strip()
                    lower_line = clean_line.lower()
            return clean_line

    return error[:200]


class DownloadResult:
    """Result of a download operation."""

    def __init__(
        self,
        success: bool,
        file_path: Optional[str] = None,
        title: Optional[str] = None,
        duration: Optional[int] = None,
        error: Optional[str] = None,
    ):
        self.success = success
        self.file_path = file_path
        self.title = title
        self.duration = duration
        self.error = clean_error_message(error)


class BaseDownloader(ABC):
    """
    Abstract base for all platform downloaders.
    Each subclass must provide:
      - PLATFORM name
      - URL_PATTERNS (list of regex strings)
      - _get_ydl_opts() returning yt-dlp options
    """

    PLATFORM: str = ""
    URL_PATTERNS: list[str] = []

    @classmethod
    @abstractmethod
    def _get_ydl_opts(cls, output_dir: str) -> dict:
        """Return yt-dlp options for this platform."""
        ...

    @classmethod
    def matches(cls, url: str) -> bool:
        """Check if a URL matches this downloader's platform."""
        for pattern in cls.URL_PATTERNS:
            if re.search(pattern, url, re.IGNORECASE):
                return True
        return False

    @classmethod
    def download(cls, url: str, output_dir: Optional[str] = None) -> DownloadResult:
        """
        Download video from the given URL.
        Returns a DownloadResult with the file path or error.
        """
        import time
        from app.downloader.base import DownloadResult

        out_dir = output_dir or settings.DOWNLOAD_DIR
        os.makedirs(out_dir, exist_ok=True)

        opts = cls._get_ydl_opts(out_dir)

        max_retries = 3
        last_exception = None

        for attempt in range(1, max_retries + 1):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)

                    # Try to get the actual downloaded file
                    filename = ydl.prepare_filename(info)
                    # yt-dlp may append extensions; find the actual file
                    file_path = _resolve_file_path(out_dir, info)

                    return DownloadResult(
                        success=True,
                        file_path=file_path,
                        title=info.get("title"),
                        duration=info.get("duration"),
                    )
            except Exception as e:
                last_exception = e
                logger.warning(
                    "Download attempt %d/%d failed for %s: %s",
                    attempt,
                    max_retries,
                    url,
                    str(e),
                )
                if attempt < max_retries:
                    # Wait slightly before retrying
                    time.sleep(attempt * 1.5)

        logger.exception("All %d download attempts failed for %s", max_retries, url)
        return DownloadResult(success=False, error=str(last_exception))


def _resolve_file_path(output_dir: str, info: dict) -> Optional[str]:
    """
    Resolve the actual downloaded file path from yt-dlp info dict.
    Handles various extensions yt-dlp might append.
    """
    # Try to get the file from requested_downloads
    requested = info.get("requested_downloads")
    if requested:
        path = requested[0].get("filepath")
        if path and os.path.exists(path):
            return path

    # Fallback 1: check by ID
    vid_id = info.get("id")
    if vid_id:
        pattern = os.path.join(output_dir, f"*{vid_id}*")
        matches = glob.glob(pattern)
        if matches:
            return max(matches, key=os.path.getmtime)

    # Fallback 2: guess from title
    title = info.get("title")
    if title:
        # Sanitize filename and slice to match the truncated template
        safe = re.sub(r'[^\w\-_. ]', "", title)[:150]
        pattern = os.path.join(output_dir, f"{safe}*")
        matches = glob.glob(pattern)
        if matches:
            return max(matches, key=os.path.getmtime)

    # Last resort: any video file in output dir
    videos = glob.glob(os.path.join(output_dir, "*.mp4")) + \
             glob.glob(os.path.join(output_dir, "*.webm")) + \
             glob.glob(os.path.join(output_dir, "*.mkv"))
    if videos:
        return max(videos, key=os.path.getmtime)

    return None


def resolve_redirects(url: str) -> str:
    """
    Follow HTTP redirects to find the final URL.
    This helps bypass shortener-related blocks or extraction failures.
    """
    import urllib.request
    import urllib.parse

    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lower()
    except Exception:
        return url

    # Run redirect resolution for known redirect domains to keep things fast
    redirect_domains = [
        "vt.tiktok.com",
        "vm.tiktok.com",
        "youtu.be",
        "fb.watch",
        "fb.gg",
        "t.co",
        "bit.ly",
        "tinyurl.com",
    ]

    if not any(d in domain for d in redirect_domains):
        return url

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            final_url = response.geturl()
            logger.info("Resolved redirect from %s to %s", url, final_url)
            return final_url
    except Exception as e:
        logger.warning("Failed to resolve redirects for %s: %s", url, e)
        return url

