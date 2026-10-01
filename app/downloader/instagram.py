"""
Instagram downloader using yt-dlp.
Supports reels, video posts, single photo posts, carousels, and stories.

Photo posts are NOT downloadable via yt-dlp directly — the Instagram
extractor raises "There is no video in this post" when a post contains
only images. For that case we fall back to fetching the public image(s)
via oEmbed + embed page scraping (no login required for public posts).
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


class InstagramDownloader(BaseDownloader):
    PLATFORM = "instagram"
    URL_PATTERNS = [
        # /p/<id>, /reel/<id>, /reels/<id>, /tv/<id> — with or without username prefix
        # e.g. /p/xxxx/, /reel/xxxx/, /username/reel/xxxx/, /username/p/xxxx/
        r"(?:https?://)?(?:www\.)?instagram\.com/(?:[\w.\-]+/)?(?:p|reel|reels|tv)/[\w\-]+",
        r"(?:https?://)?(?:www\.)?instagram\.com/(?:stories|share)/",
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

    # ── URL normalization ──────────────────────────────────────────────

    @classmethod
    def canonical_url(cls, url: str) -> str:
        """Strip tracking query params (?stkn=, ?igsh=, ?utm_*, ...) and fragments.

        yt-dlp only needs the path; extra params (e.g. share tokens) can
        break extraction, so we reduce to https://www.instagram.com/<path>/.
        /share/ links are redirect wrappers — resolve them first.
        """
        raw = url.strip()
        if "/share/" in raw:
            try:
                req = urllib.request.Request(raw, headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = resp.geturl()
            except Exception as e:
                logger.debug("Instagram share resolve failed: %s", e)
        try:
            parsed = urllib.parse.urlparse(raw)
            path = parsed.path or "/"
            if not path.endswith("/"):
                path += "/"
            return f"https://www.instagram.com{path}"
        except Exception:
            return raw.split("?")[0].split("#")[0]

    @classmethod
    def _shortcode(cls, canonical: str) -> str | None:
        """Extract the post shortcode (last path segment)."""
        try:
            parts = [p for p in urllib.parse.urlparse(canonical).path.split("/") if p]
            if not parts:
                return None
            # /stories/<user>/<id> → last part; /p/<id> → last part; etc.
            return parts[-1]
        except Exception:
            return None

    # ── Entry point (override) ─────────────────────────────────────────

    @classmethod
    def download(cls, url: str, output_dir: str | None = None) -> DownloadResult:
        canonical = cls.canonical_url(url)
        result = super().download(canonical, output_dir=output_dir)
        if result.success:
            return result

        err = f"{result.raw_error or ''}\n{result.error or ''}".lower()
        photo_signals = (
            "there is no video in this post",
            "no video formats",
            "no formats found",
            "no media found",
            "berupa foto",  # already-cleaned signal (belt & braces)
        )
        if any(s in err for s in photo_signals):
            logger.info("yt-dlp found no video for %s — trying photo fallback", canonical)
            try:
                return cls._download_photo_fallback(canonical, output_dir)
            except Exception as e:
                logger.warning("Photo fallback failed for %s: %s", canonical, e)
                return DownloadResult(
                    success=False,
                    error=(
                        f"Postingan ini berupa foto dan gagal diambil gambarnya ({e}). "
                        "Jika postingan privat, isi YTDLP_COOKIES_FILE dengan cookies "
                        "dari browser yang sudah login."
                    ),
                )
        return result

    # ── Photo fallback ─────────────────────────────────────────────────

    @classmethod
    def _download_photo_fallback(cls, canonical: str, output_dir: str | None = None) -> DownloadResult:
        out_dir = output_dir or settings.DOWNLOAD_DIR
        os.makedirs(out_dir, exist_ok=True)
        job_dir = os.path.join(out_dir, f"job-{uuid.uuid4().hex[:12]}")
        os.makedirs(job_dir, exist_ok=True)

        shortcode = cls._shortcode(canonical) or "ig"
        timeout = settings.REQUEST_TIMEOUT or 30

        title: str | None = None
        image_urls: list[str] = []

        # 1) oEmbed — fast, official, usually works without login.
        try:
            oembed = cls._fetch_oembed(canonical, timeout)
            if oembed:
                title = oembed.get("title") or oembed.get("author_name")
                thumb = oembed.get("thumbnail_url")
                if thumb:
                    image_urls.append(thumb)
        except Exception as e:
            logger.debug("oEmbed failed for %s: %s", canonical, e)

        # 2) Embed page — may contain more images (carousel) + higher res.
        try:
            embed_urls, embed_title = cls._fetch_embed_images(canonical, timeout)
            if embed_title and not title:
                title = embed_title
            for u in embed_urls:
                if u not in image_urls:
                    image_urls.append(u)
        except Exception as e:
            logger.debug("Embed scrape failed for %s: %s", canonical, e)

        # Deduplicate keeping order (strip query-size variants).
        seen: set[str] = set()
        unique: list[str] = []
        for u in image_urls:
            key = u.split("?")[0]
            if key not in seen:
                seen.add(key)
                unique.append(u)
        image_urls = unique[:10]  # Telegram media-group limit

        if not image_urls:
            raise RuntimeError(
                "tidak ada gambar yang bisa diambil "
                "(postingan mungkin privat atau memerlukan login)"
            )

        files: list[str] = []
        for i, img_url in enumerate(image_urls):
            ext = ".jpg"
            path_part = urllib.parse.urlparse(img_url).path.lower()
            if path_part.endswith(".png"):
                ext = ".png"
            elif path_part.endswith(".webp"):
                ext = ".webp"
            safe_code = re.sub(r"[^\w\-]+", "_", shortcode)[:40]
            dest = os.path.join(job_dir, f"{safe_code}-{i + 1}{ext}" if len(image_urls) > 1 else f"{safe_code}{ext}")
            cls._download_file(img_url, dest, timeout, referer="https://www.instagram.com/")
            if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                files.append(os.path.abspath(dest))

        if not files:
            raise RuntimeError("gagal mengunduh file gambar (respons kosong)")

        files.sort()
        media_type = "photo" if len(files) >= 1 else "photo"
        return DownloadResult(
            success=True,
            file_path=files[0],
            files=files,
            title=title or f"Instagram photo {shortcode}",
            duration=None,
            media_type=media_type,
            dir_path=job_dir,
        )

    @staticmethod
    def _http_get(url: str, timeout: int, referer: str | None = None) -> tuple[bytes, str]:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": settings.YTDLP_USER_AGENT or _UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
                **({"Referer": referer} if referer else {}),
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return data, ctype

    @classmethod
    def _fetch_oembed(cls, canonical: str, timeout: int) -> dict | None:
        api_url = "https://www.instagram.com/api/v1/oembed/?url=" + urllib.parse.quote(canonical, safe="")
        try:
            data, _ = cls._http_get(api_url, timeout)
            obj = json.loads(data.decode("utf-8", errors="replace"))
            if isinstance(obj, dict) and obj.get("thumbnail_url"):
                return obj
        except Exception as e:
            logger.debug("oEmbed request failed: %s", e)
        return None

    @classmethod
    def _fetch_embed_images(cls, canonical: str, timeout: int) -> tuple[list[str], str | None]:
        # Captioned embed renders the post's images as plain <img> tags.
        embed_url = canonical.rstrip("/") + "/embed/captioned/"
        try:
            data, _ = cls._http_get(embed_url, timeout, referer="https://www.instagram.com/")
        except Exception:
            # Fallback to the post page itself (often login-walled, but try anyway).
            data, _ = cls._http_get(canonical, timeout, referer="https://www.instagram.com/")
        html = data.decode("utf-8", errors="replace")

        # og:title for caption fallback.
        title = None
        m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', html, re.IGNORECASE)
        if m:
            title = m.group(1).strip() or None

        urls: list[str] = []
        # og:image (primary) — usually highest priority.
        for m in re.finditer(r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)', html, re.IGNORECASE):
            u = m.group(1).replace("&amp;", "&")
            if "scontent" in u or "instagram" in u or u.startswith("https://"):
                urls.append(u)
        # display_url JSON embedded in page (carousel items).
        for m in re.finditer(r'"display_url"\s*:\s*"([^"]+)"', html):
            u = m.group(1).replace("\\u0026", "&").replace("\\/", "/")
            if u.startswith("https://"):
                urls.append(u)
        # Plain <img> from scontent CDN.
        for m in re.finditer(r'<img[^>]+src=["\']([^"\']*scontent[^"\']*)["\']', html, re.IGNORECASE):
            u = m.group(1).replace("&amp;", "&")
            urls.append(u)

        # Prefer larger variants: drop low-res thumbs when a bigger twin exists.
        # (Instagram serves s150x150 etc.; keep the largest per base path.)
        best: dict[str, str] = {}
        for u in urls:
            base = re.sub(r"/s\d+x\d+/", "/", u.split("?")[0])
            prev = best.get(base)
            if prev is None or len(u) > len(prev):
                best[base] = u
        return list(best.values()), title

    @staticmethod
    def _download_file(url: str, dest: str, timeout: int, referer: str | None = None) -> None:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": settings.YTDLP_USER_AGENT or _UA,
                "Accept": "image/*,*/*;q=0.8",
                "Referer": referer or "https://www.instagram.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)

