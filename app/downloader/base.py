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
        self.error = error


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
        from app.downloader.base import DownloadResult

        out_dir = output_dir or settings.DOWNLOAD_DIR
        os.makedirs(out_dir, exist_ok=True)

        opts = cls._get_ydl_opts(out_dir)

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
            logger.exception("Download failed for %s", url)
            return DownloadResult(success=False, error=str(e))


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

    if matches:
        return max(matches, key=os.path.getmtime)

    # Last resort: any video file in output dir
    videos = glob.glob(os.path.join(output_dir, "*.mp4")) + \
             glob.glob(os.path.join(output_dir, "*.webm")) + \
             glob.glob(os.path.join(output_dir, "*.mkv"))
    if videos:
        return max(videos, key=os.path.getmtime)

    return None

