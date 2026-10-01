"""
Base downloader — all platform downloaders inherit from this.
Uses yt-dlp as the primary engine, with platform-specific overrides.

Supports single videos, single photos, and carousels/albums
(multi-entry results such as Instagram carousel posts or
TikTok photo slideshows).
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yt_dlp

from app.config import settings

logger = logging.getLogger(__name__)

# ── Media classification ──

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
# Everything else (gif, mp3, ...) is treated as a generic document.
MEDIA_EXTS = VIDEO_EXTS | PHOTO_EXTS | {".gif"}


def classify_media(file_path: str) -> str:
    """Return 'video', 'photo', or 'other' based on file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in PHOTO_EXTS:
        return "photo"
    return "other"


def classify_files(files: list[str]) -> str:
    """Return 'video', 'photo', or 'mixed' for a list of files."""
    kinds = {classify_media(f) for f in files}
    kinds.discard("other")
    if not kinds:
        return "other" if files else "video"
    if len(kinds) == 1:
        return kinds.pop()
    return "mixed"


def clean_error_message(error: Optional[str]) -> Optional[str]:
    if not error:
        return error

    error_lower = error.lower()

    # Instagram-specific friendly messages
    if "instagram" in error_lower and "empty media response" in error_lower:
        return "Instagram membatasi akses tanpa login. Silakan coba sesaat lagi, atau gunakan cookies jika ini postingan privat."

    if "there is no video in this post" in error_lower:
        return (
            "Postingan ini berupa foto (bukan video) dan gambarnya gagal diambil otomatis. "
            "Jika postingan privat, isi YTDLP_COOKIES_FILE dengan cookies dari browser yang sudah login."
        )

    if "no video could be found in this tweet" in error_lower:
        return (
            "Tweet ini berupa foto (bukan video) dan gambarnya gagal diambil otomatis. "
            "Jika tweet dari akun privat/protected, diperlukan login."
        )

    if "private" in error_lower or "login" in error_lower or "sign in" in error_lower:
        if "youtube" in error_lower:
            return "Video YouTube ini privat, unlisted tanpa akses, khusus member, atau memerlukan login/cookies."
        if "tiktok" in error_lower:
            return "Video TikTok ini privat atau akunnya privat. Diperlukan login/cookies yang punya akses."
        if "facebook" in error_lower:
            return "Postingan Facebook ini privat (teman-saja/grup tertutup) atau memerlukan login."
        if "twitter" in error_lower or "x.com" in error_lower:
            return "Tweet ini dari akun privat/protected, sudah dihapus, atau memerlukan login."
        return "Konten ini bersifat privat, memerlukan login, atau tidak dapat diakses secara publik."

    if (
        "unsupported url" in error_lower
        and ("tiktok" in error_lower or "/photo/" in error_lower)
    ):
        return "Link TikTok slideshow foto (/photo/) tidak didukung yt-dlp versi ini. Coba kirim link video, atau pastikan postingan bersifat publik."

    if "age" in error_lower and "confirm" in error_lower:
        return "Video ini dibatasi umur. Isi YTDLP_COOKIES_FILE dengan cookies dari akun yang sudah login dan terverifikasi umur."

    if "no media" in error_lower or "no video formats" in error_lower:
        return "Tidak ditemukan foto/video pada link tersebut. Mungkin postingan teks atau sudah dihapus."

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
    """Result of a download operation (single file or carousel/album)."""

    def __init__(
        self,
        success: bool,
        file_path: Optional[str] = None,
        title: Optional[str] = None,
        duration: Optional[int] = None,
        error: Optional[str] = None,
        files: Optional[list[str]] = None,
        media_type: str = "video",
        dir_path: Optional[str] = None,
    ):
        self.success = success
        # Primary file (first of `files`) — kept for backward compatibility.
        self.file_path = file_path
        # All downloaded files (photos for carousel posts, videos, ...).
        self.files: list[str] = files or ([file_path] if file_path else [])
        if file_path and file_path not in self.files:
            self.files.insert(0, file_path)
        self.title = title
        self.duration = duration
        self.media_type = media_type  # "video" | "photo" | "mixed" | "other"
        # Per-request working directory holding `files` (safe to delete).
        self.dir_path = dir_path
        self.error = clean_error_message(error)

    @property
    def media_count(self) -> int:
        return len(self.files)

    @property
    def is_multiple(self) -> bool:
        return len(self.files) > 1


def cleanup_result(result: "DownloadResult") -> None:
    """Delete the per-request working directory of a download result.

    Only removes directories that live inside DOWNLOAD_DIR (safety check).
    """
    try:
        if not result or not result.dir_path:
            return
        base = os.path.abspath(settings.DOWNLOAD_DIR)
        target = os.path.abspath(result.dir_path)
        if target == base or not target.startswith(base + os.sep):
            logger.warning("Refusing to delete directory outside DOWNLOAD_DIR: %s", target)
            return
        shutil.rmtree(target, ignore_errors=True)
    except Exception as e:
        logger.warning("Failed to cleanup download dir: %s", e)


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
        Download media (video, photo, or carousel/album) from the given URL.
        Returns a DownloadResult with one or more file paths, or an error.

        Each call gets an isolated sub-directory so concurrent downloads
        never pick up each other's files.
        """
        import time

        out_dir = output_dir or settings.DOWNLOAD_DIR
        os.makedirs(out_dir, exist_ok=True)

        # Isolated working directory per request.
        job_dir = os.path.join(out_dir, f"job-{uuid.uuid4().hex[:12]}")
        os.makedirs(job_dir, exist_ok=True)

        opts = cls._get_ydl_opts(job_dir)
        # Unique template per entry: avoids collisions between carousel items
        # (or unrelated posts with identical titles).
        opts["outtmpl"] = os.path.join(job_dir, "%(id)s-%(title).100s.%(ext)s")

        max_retries = 3
        last_exception = None

        for attempt in range(1, max_retries + 1):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)

                    if not info:
                        raise RuntimeError("yt-dlp returned no info")

                    entries = info.get("entries") if isinstance(info, dict) else None
                    if entries:
                        # Carousel / album / playlist: collect every entry.
                        files = _collect_entry_files(job_dir, entries)
                        title = info.get("title")
                        duration = info.get("duration")
                    else:
                        files = _collect_entry_files(job_dir, [info])
                        title = info.get("title")
                        duration = info.get("duration")

                    if not files:
                        raise RuntimeError(
                            "tidak ada file media yang terdownload "
                            "(konten mungkin privat atau tanpa media)"
                        )

                    return DownloadResult(
                        success=True,
                        file_path=files[0],
                        files=files,
                        title=title,
                        duration=duration,
                        media_type=classify_files(files),
                        dir_path=job_dir,
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
        shutil.rmtree(job_dir, ignore_errors=True)
        return DownloadResult(success=False, error=str(last_exception))


def _collect_entry_files(job_dir: str, entries: list[dict]) -> list[str]:
    """
    Collect downloaded media files for one or more yt-dlp entries.

    Prefers each entry's own `requested_downloads` filepath(s), falls back
    to matching by entry id inside the isolated job directory.
    Only the job directory is scanned, so concurrent downloads can't clash.
    """
    files: list[str] = []
    seen: set[str] = set()

    def _add(path: Optional[str]):
        if path and os.path.isfile(path):
            full = os.path.abspath(path)
            if full not in seen:
                seen.add(full)
                files.append(full)

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        requested = entry.get("requested_downloads") or []
        for req in requested:
            if isinstance(req, dict):
                _add(req.get("filepath"))
        # Fallback: match by entry id within the job dir.
        entry_id = entry.get("id")
        if entry_id:
            for match in glob.glob(os.path.join(job_dir, f"*{entry_id}*")):
                if os.path.isfile(match):
                    ext = os.path.splitext(match)[1].lower()
                    if not ext or ext in MEDIA_EXTS:
                        _add(match)

    # Last resort: any media file in the job dir (yt-dlp may merge/rename).
    if not files:
        for match in glob.glob(os.path.join(job_dir, "*")):
            if os.path.isfile(match) and os.path.splitext(match)[1].lower() in MEDIA_EXTS:
                _add(match)

    # Stable order: by modification time (download order).
    files.sort(key=os.path.getmtime)
    return files


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
        "tiktok.com/t/",
        "youtu.be",
        "youtube.com/redirect",
        "fb.watch",
        "fb.gg",
        "facebook.com/share",
        "m.facebook.com",
        "t.co",
        "bit.ly",
        "tinyurl.com",
        "ig.me",
        "instagr.am",
        "instagram.com/share",
        "l.instagram.com",
    ]

    lowered = url.lower()
    if not any(d in domain or d in lowered for d in redirect_domains):
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
