"""
Twitter/X downloader using yt-dlp.
Supports video tweets natively; photo-only tweets (yt-dlp raises
"No video could be found in this tweet") fall back to the public
vxtwitter API which exposes direct pbs.twimg.com image URLs.
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


class TwitterDownloader(BaseDownloader):
    PLATFORM = "twitter"
    URL_PATTERNS = [
        r"(?:https?://)?(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/\w+/status/\d+",
        r"(?:https?://)?(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/\w+/status/",
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
        result = super().download(url, output_dir=output_dir)
        if result.success:
            return result

        err = (result.error or "").lower()
        photo_signals = (
            "no video could be found",
            "no video formats",
            "no formats",
            "is not a video",
        )
        if any(s in err for s in photo_signals):
            logger.info("yt-dlp found no video for %s — trying photo fallback", url)
            try:
                return cls._download_photo_fallback(url, output_dir)
            except Exception as e:
                logger.warning("Twitter photo fallback failed for %s: %s", url, e)
                return DownloadResult(
                    success=False,
                    error=(
                        f"Tweet ini berupa foto dan gambarnya gagal diambil ({e}). "
                        "Jika tweet dari akun privat/protected, isi YTDLP_COOKIES_FILE "
                        "dengan cookies dari browser yang sudah login dan follow akun tersebut."
                    ),
                )
        return result

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Canonicalize mobile hosts and strip query/fragment."""
        try:
            parsed = urllib.parse.urlparse(url.strip())
            host = parsed.netloc.lower().replace("mobile.", "www.")
            # Keep x.com as-is (yt-dlp handles both); drop query/fragment.
            path = parsed.path or "/"
            return urllib.parse.urlunparse((parsed.scheme or "https", host or parsed.netloc, path, "", "", ""))
        except Exception:
            return url.split("?")[0].split("#")[0]

    @staticmethod
    def _status_id(url: str) -> str | None:
        m = re.search(r"/status(?:es)?/(\d+)", url)
        return m.group(1) if m else None

    @classmethod
    def _download_photo_fallback(cls, url: str, output_dir: str | None = None) -> DownloadResult:
        """Fetch tweet images via the public vxtwitter API (no auth).

        GET https://api.vxtwitter.com/<screen_name>/status/<id>
        returns JSON with mediaURLs[] / media_extended[] (photos, incl.
        ?format=jpg&name=orig originals).
        """
        out_dir = output_dir or settings.DOWNLOAD_DIR
        os.makedirs(out_dir, exist_ok=True)
        job_dir = os.path.join(out_dir, f"job-{uuid.uuid4().hex[:12]}")
        os.makedirs(job_dir, exist_ok=True)
        timeout = settings.REQUEST_TIMEOUT or 30

        sid = cls._status_id(url)
        if not sid:
            raise RuntimeError("ID tweet tidak dikenali dari URL")

        # vxtwitter expects the full original path; try x.com canonical first,
        # then twitter.com form.
        candidates = [url]
        if "x.com" in url:
            candidates.append(url.replace("x.com", "twitter.com"))
        else:
            candidates.append(url.replace("twitter.com", "x.com"))

        title: str | None = None
        image_urls: list[str] = []
        last_err: str = "respons kosong"
        for cand in candidates:
            api_url = "https://api.vxtwitter.com/" + cand.split("://", 1)[-1]
            try:
                req = urllib.request.Request(api_url, headers={"User-Agent": _UA, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    obj = json.loads(resp.read().decode("utf-8", errors="replace"))
            except Exception as e:
                last_err = str(e)[:120]
                continue
            if not isinstance(obj, dict):
                continue
            title = obj.get("text") or title
            media = obj.get("mediaURLs") or obj.get("media_extended") or []
            for item in media:
                if isinstance(item, str) and item.startswith("http"):
                    image_urls.append(item)
                elif isinstance(item, dict):
                    u = item.get("url") or item.get("media_url_https")
                    if isinstance(u, str) and u.startswith("http"):
                        image_urls.append(u)
            if image_urls:
                break
            last_err = "tweet tidak berisi foto atau memerlukan login"

        # Request originals where possible (pbs.twimg.com supports ?name=orig).
        upgraded: list[str] = []
        for u in image_urls:
            if "pbs.twimg.com" in u and "name=" not in u:
                sep = "&" if "?" in u else "?"
                upgraded.append(f"{u}{sep}name=orig")
            else:
                upgraded.append(u)
        image_urls = upgraded

        seen: set[str] = set()
        unique: list[str] = []
        for u in image_urls:
            key = u.split("?")[0]
            if key not in seen:
                seen.add(key)
                unique.append(u)
        image_urls = unique[:4]  # X allows max 4 photos per tweet

        if not image_urls:
            raise RuntimeError(last_err)

        files: list[str] = []
        for i, img_url in enumerate(image_urls):
            ext = ".jpg"
            lp = urllib.parse.urlparse(img_url).path.lower()
            if lp.endswith(".png"):
                ext = ".png"
            elif lp.endswith(".webp"):
                ext = ".webp"
            dest = os.path.join(
                job_dir,
                f"{sid}-{i + 1}{ext}" if len(image_urls) > 1 else f"{sid}{ext}",
            )
            req = urllib.request.Request(img_url, headers={"User-Agent": _UA, "Referer": "https://x.com/"})
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
        clean_title = (title or f"X post {sid}")
        if len(clean_title) > 200:
            clean_title = clean_title[:200]
        return DownloadResult(
            success=True,
            file_path=files[0],
            files=files,
            title=clean_title,
            duration=None,
            media_type="photo",
            dir_path=job_dir,
        )
