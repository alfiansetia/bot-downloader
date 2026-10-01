"""
TikTok downloader using yt-dlp.
Supports video posts; photo slideshows (/photo/) are handled via fallback
because the bundled yt-dlp TikTok extractor only supports /video/ URLs.
"""

from __future__ import annotations

import json
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


class TikTokDownloader(BaseDownloader):
    PLATFORM = "tiktok"
    URL_PATTERNS = [
        r"(?:https?://)?(?:www\.|m\.)?tiktok\.com/",
        r"(?:https?://)?(?:www\.)?vm\.tiktok\.com/",
        r"(?:https?://)?(?:www\.)?vt\.tiktok\.com/",
        r"(?:https?://)?(?:www\.)?tiktok\.com/@[\w.\-]+/(?:video|photo)/\d+",
        r"(?:https?://)?(?:www\.)?tiktok\.com/t/\w+",
    ]

    @classmethod
    def _get_ydl_opts(cls, output_dir: str) -> dict:
        opts = {
            "format": "bestvideo[height<=1080]/bestvideo[width<=1080]/bestvideo+bestaudio/best[height<=1080]/best[width<=1080]/best",
            "outtmpl": f"{output_dir}/%(title).150s.%(ext)s",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }
        if settings.YTDLP_COOKIES_FILE:
            opts["cookiefile"] = settings.YTDLP_COOKIES_FILE
        if settings.YTDLP_USER_AGENT:
            opts["user_agent"] = settings.YTDLP_USER_AGENT
        return opts

    # ── Entry point (override) ─────────────────────────────────────────

    @classmethod
    def download(cls, url: str, output_dir: str | None = None) -> DownloadResult:
        # Normalize mobile host (m.tiktok.com → www.tiktok.com) so yt-dlp
        # always sees a canonical host.
        url = cls._normalize_host(url)
        result = super().download(url, output_dir=output_dir)
        if result.success:
            return result

        err = (result.error or "").lower()
        photo_signals = (
            "unsupported url",
            "no video",
            "no formats",
            "status code 10216",  # private post
            "status code 10222",  # private account
        )
        is_photo_url = "/photo/" in url.lower()
        if is_photo_url or any(s in err for s in photo_signals):
            # Private posts/accounts can never be fetched without login —
            # don't disguise that as a "photo" issue.
            if "10216" in err or "10222" in err or "permission" in err:
                return result
            logger.info("TikTok fallback for %s (photo/slideshow?)", url)
            try:
                return cls._download_photo_fallback(url, output_dir)
            except Exception as e:
                logger.warning("TikTok photo fallback failed for %s: %s", url, e)
                return DownloadResult(
                    success=False,
                    error=(
                        "Link TikTok ini tidak bisa diambil "
                        f"({e}). Jika ini slideshow foto privat, "
                        "isi YTDLP_COOKIES_FILE dengan cookies dari browser yang sudah login."
                    ),
                )
        return result

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_host(url: str) -> str:
        try:
            parsed = urllib.parse.urlparse(url.strip())
            host = parsed.netloc.lower()
            if host in ("m.tiktok.com", "tiktok.com"):
                parsed = parsed._replace(netloc="www.tiktok.com")
                return urllib.parse.urlunparse(parsed)
        except Exception:
            pass
        return url

    @classmethod
    def _download_photo_fallback(cls, url: str, output_dir: str | None = None) -> DownloadResult:
        """Fetch TikTok photo slideshow images.

        Strategy (best-effort, public posts only):
        1. TikWM public API (returns slideshow image URLs, no auth).
        2. TikTok oEmbed thumbnail (single cover image).
        """
        out_dir = output_dir or settings.DOWNLOAD_DIR
        os.makedirs(out_dir, exist_ok=True)
        job_dir = os.path.join(out_dir, f"job-{uuid.uuid4().hex[:12]}")
        os.makedirs(job_dir, exist_ok=True)
        timeout = settings.REQUEST_TIMEOUT or 30

        m = re.search(r"/(?:video|photo)/(\d+)", url)
        vid = m.group(1) if m else "tiktok"
        title: str | None = None
        image_urls: list[str] = []

        # 1) TikWM API — no key required for basic lookup.
        try:
            api_url = "https://www.tikwm.com/api/?url=" + urllib.parse.quote(url, safe="")
            req = urllib.request.Request(api_url, headers={"User-Agent": _UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                obj = json.loads(resp.read().decode("utf-8", errors="replace"))
            data = obj.get("data") if isinstance(obj, dict) else None
            if isinstance(data, dict):
                title = data.get("title")
                images = data.get("images") or []
                for im in images:
                    if isinstance(im, str) and im.startswith("http"):
                        image_urls.append(im)
        except Exception as e:
            logger.debug("TikWM lookup failed: %s", e)

        # 2) oEmbed cover as last resort (single image).
        if not image_urls:
            try:
                oembed_url = "https://www.tiktok.com/oembed?url=" + urllib.parse.quote(url, safe="")
                req = urllib.request.Request(oembed_url, headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    obj = json.loads(resp.read().decode("utf-8", errors="replace"))
                if isinstance(obj, dict):
                    if not title:
                        title = obj.get("title") or obj.get("author_name")
                    thumb = obj.get("thumbnail_url")
                    if thumb:
                        image_urls.append(thumb)
            except Exception as e:
                logger.debug("TikTok oEmbed failed: %s", e)

        # Deduplicate, cap at Telegram media-group limit.
        seen: set[str] = set()
        unique: list[str] = []
        for u in image_urls:
            key = u.split("?")[0]
            if key not in seen:
                seen.add(key)
                unique.append(u)
        image_urls = unique[:10]

        if not image_urls:
            raise RuntimeError(
                "tidak ada gambar yang bisa diambil "
                "(slideshow mungkin privat atau TikTok memblokir akses tanpa login)"
            )

        files: list[str] = []
        for i, img_url in enumerate(image_urls):
            dest = os.path.join(
                job_dir,
                f"{vid}-{i + 1}.jpg" if len(image_urls) > 1 else f"{vid}.jpg",
            )
            req = urllib.request.Request(
                img_url,
                headers={"User-Agent": _UA, "Referer": "https://www.tiktok.com/"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
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
            title=title or f"TikTok slideshow {vid}",
            duration=None,
            media_type="photo",
            dir_path=job_dir,
        )
