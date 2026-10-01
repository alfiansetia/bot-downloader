"""
Facebook downloader using yt-dlp.
Supports public video/reel URLs; photo-only posts fall back to og:image
scraping (public posts only — no login).
"""

from __future__ import annotations

import logging
import os
import re
import uuid
import urllib.parse
import urllib.request

from app.downloader.base import BaseDownloader, DownloadResult
from app.config import settings

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class FacebookDownloader(BaseDownloader):
    PLATFORM = "facebook"
    URL_PATTERNS = [
        r"(?:https?://)?(?:www\.|m\.|web\.)?facebook\.com/[\w.\-]+/videos/",
        r"(?:https?://)?(?:www\.|m\.|web\.)?facebook\.com/watch/?",
        r"(?:https?://)?(?:www\.)?fb\.watch/",
        r"(?:https?://)?(?:www\.|m\.)?facebook\.com/reel/",
        r"(?:https?://)?(?:www\.|m\.)?facebook\.com/share/",
        r"(?:https?://)?(?:www\.|m\.)?facebook\.com/photo",
        r"(?:https?://)?(?:www\.|m\.)?facebook\.com/[^/]+/posts/",
        r"(?:https?://)?(?:www\.|m\.)?facebook\.com/(?:permalink|story)\.php",
        r"(?:https?://)?(?:www\.|m\.)?facebook\.com/share/[^/]+/\S+",
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

    # ── Entry point (override) ─────────────────────────────────────────

    @classmethod
    def download(cls, url: str, output_dir: str | None = None) -> DownloadResult:
        url = cls._normalize_url(url)
        # /share/ links are redirect wrappers — resolve to the real post first.
        if "/share/" in url:
            resolved = cls._resolve_share(url)
            if resolved and resolved != url:
                logger.info("Resolved Facebook share %s → %s", url, resolved)
                url = resolved
        result = super().download(url, output_dir=output_dir)
        if result.success:
            return result

        err = (result.error or "").lower()
        photo_signals = (
            "no video",
            "no formats",
            "unsupported url",
            "no media",
        )
        if any(s in err for s in photo_signals):
            logger.info("Facebook fallback (photo?) for %s", url)
            try:
                return cls._download_photo_fallback(url, output_dir)
            except Exception as e:
                logger.warning("Facebook photo fallback failed for %s: %s", url, e)
                return DownloadResult(
                    success=False,
                    error=(
                        f"Postingan Facebook ini tidak bisa diambil ({e}). "
                        "Jika postingan privat/teman-saja, isi YTDLP_COOKIES_FILE "
                        "dengan cookies dari browser yang sudah login dan berteman/follow."
                    ),
                )
        return result

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_url(url: str) -> str:
        """m./web. → www., strip tracking params (fbclid, etc.)."""
        try:
            parsed = urllib.parse.urlparse(url.strip())
            host = parsed.netloc.lower()
            if host.startswith("m.facebook.com") or host.startswith("web.facebook.com"):
                host = "www.facebook.com"
                parsed = parsed._replace(netloc=host)
            # Drop known tracking params, keep post identifiers (v, id, story_fbid).
            qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            keep = [(k, v) for k, v in qs if k.lower() in ("v", "id", "story_fbid", "video_id")]
            parsed = parsed._replace(query=urllib.parse.urlencode(keep), fragment="")
            return urllib.parse.urlunparse(parsed)
        except Exception:
            return url.split("#")[0]

    @staticmethod
    def _resolve_share(url: str) -> str:
        """Follow the /share/ redirect to the canonical post URL."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.geturl()
        except Exception as e:
            logger.debug("Facebook share resolve failed: %s", e)
            return url

    @classmethod
    def _download_photo_fallback(cls, url: str, output_dir: str | None = None) -> DownloadResult:
        """Scrape og:image / safe_image from a public Facebook post page."""
        out_dir = output_dir or settings.DOWNLOAD_DIR
        os.makedirs(out_dir, exist_ok=True)
        job_dir = os.path.join(out_dir, f"job-{uuid.uuid4().hex[:12]}")
        os.makedirs(job_dir, exist_ok=True)
        timeout = settings.REQUEST_TIMEOUT or 30

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Login wall detection — private post.
        if re.search(r'name=["\']login|/login\.php|Log (?:In|Masuk) to Facebook', html, re.IGNORECASE):
            raise RuntimeError("postingan privat atau memerlukan login")

        urls: list[str] = []
        for m in re.finditer(
            r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
            html, re.IGNORECASE,
        ):
            u = m.group(1).replace("&amp;", "&")
            if u.startswith("https://"):
                urls.append(u)
        # Embedded safe_image JSON (often higher res than og:image).
        for m in re.finditer(r'"safe_image"\s*:\s*\{\s*"uri"\s*:\s*"([^"]+)"', html):
            u = m.group(1).replace("\\/", "/")
            if u.startswith("https://"):
                urls.append(u)

        seen: set[str] = set()
        unique: list[str] = []
        for u in urls:
            key = u.split("?")[0]
            if key not in seen:
                seen.add(key)
                unique.append(u)
        urls = unique[:10]

        if not urls:
            raise RuntimeError("tidak ada gambar publik yang ditemukan (mungkin privat)")

        title = None
        m = re.search(
            r'<meta[^>]+property=["\']og:(?:title|description)["\'][^>]+content=["\']([^"\']+)',
            html, re.IGNORECASE,
        )
        if m:
            title = m.group(1).replace("&amp;", "&").strip()[:200] or None

        files: list[str] = []
        for i, img_url in enumerate(urls):
            dest = os.path.join(job_dir, f"fb-{i + 1}.jpg" if len(urls) > 1 else "fb.jpg")
            r2 = urllib.request.Request(img_url, headers={"User-Agent": _UA, "Referer": "https://www.facebook.com/"})
            with urllib.request.urlopen(r2, timeout=timeout) as resp, open(dest, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                files.append(os.path.abspath(dest))

        if not files:
            raise RuntimeError("gagal mengunduh file gambar (respons kosong)")

        files.sort()
        return DownloadResult(
            success=True,
            file_path=files[0],
            files=files,
            title=title or "Facebook photo",
            duration=None,
            media_type="photo",
            dir_path=job_dir,
        )
