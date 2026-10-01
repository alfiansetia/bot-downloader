# Bot Downloader

Bot Telegram & REST API untuk download **video, foto, dan carousel/album** dari
**YouTube, TikTok, Instagram, Facebook, dan Twitter/X**.

Dibangun dengan **Python**, **yt-dlp**, **FastAPI**, dan **python-telegram-bot**.

---

## 📋 Fitur

- ✅ Download video dari **YouTube** (termasuk Shorts)
- ✅ Download **video & foto slide** dari **TikTok**
- ✅ Download dari **Instagram** (Reel, Post foto, Carousel/album, Story)
- ✅ Download dari **Facebook** (Video, Reel, Post foto)
- ✅ Download **video & foto** dari **Twitter/X**
- ✅ **Telegram Bot** — kirim link, terima video / foto / album sekaligus (media group, maks 10 per grup)
- ✅ **REST API** — 1 file dikembalikan langsung, album/carousel dikembalikan sebagai `.zip`
- ✅ Batas ukuran per file (`MAX_FILE_SIZE_MB`, default 50 MB mengikuti limit Telegram)
- ✅ **Docker support** — tanpa publish port, siap di-tunnel via Cloudflare
- ✅ **Auto-deploy** via GitHub Actions (push ke `main` → deploy ke server)

---

## 🚀 Instalasi & Cara Pakai

### 1. Clone & Setup

```bash
git clone https://github.com/alfiansetia/bot-downloader.git
cd bot-downloader

# Linux/macOS
cp .env.example .env
# Windows
copy .env.example .env
```

### 2. Konfigurasi `.env`

Minimal isi:

```env
# Wajib: Token dari @BotFather
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklmNOPqrstUVwxyz

# Wajib untuk production/webhook: URL publik bot (tanpa path)
APP_URL=https://bot.example.com

# Opsional: batasi user (pisahkan koma, kosong = semua boleh)
TELEGRAM_ALLOWED_USERS=123456789,987654321
```

Daftar lengkap variabel ada di [tabel konfigurasi](#️-konfigurasi).

### 3. Jalankan

#### 🖥️ Local — Windows (venv Laragon / Python 3.10+)

```powershell
C:\laragon\bin\python\python-3.10\python.exe -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python -m app api    # API saja
python -m app all    # API + bot polling (untuk dev tanpa webhook)
```

#### 🖥️ Local — Linux/macOS

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m app api
```

Mode run: `python -m app [bot|api|all]` (default: `all`).
Tanpa `APP_URL` → bot jalan mode polling di background thread.
Dengan `APP_URL` → bot jalan via webhook (lihat bawah).

#### 🐳 Docker (server)

```bash
cp .env.example .env   # lalu isi .env
docker compose up -d --build
docker compose logs -f
```

> Container **tidak mempublish port** ke host. Akses hanya lewat network
> internal `app-network` (tunnel ke `http://bot-downloader:8000`).
> `DOWNLOAD_DIR`/`LOGS_DIR` otomatis dioverride ke path container
> (`/tmp/downloads`, `/app/logs`) via `docker-compose.yml`.

---

## 🤖 Webhook Telegram Bot

Bot menggunakan **webhook** di production — server menerima update dari Telegram otomatis.

**Cara kerja:**

1. Set `APP_URL` di `.env` dengan domain publik (misal `https://bot.example.com`, wajib HTTPS)
2. Jalankan server
3. Server otomatis set webhook ke `<APP_URL>/webhook` saat startup
4. Telegram kirim update ke endpoint tersebut

> **Catatan:** Pastikan domain sudah mengarah ke server dan memakai **HTTPS**
> (Telegram mewajibkan HTTPS untuk webhook). `API_WORKERS` disarankan `1`
> agar webhook tidak di-set ganda oleh tiap worker.

### Cek status webhook

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo"
```

### Hapus webhook (jika perlu)

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/deleteWebhook"
```

---

## 🧪 Testing

```bash
# Cek health API
curl http://localhost:8000/health

# Download video/foto tunggal (dikembalikan sebagai file)
curl -X POST http://localhost:8000/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.tiktok.com/@user/video/123456"}' \
  -o media.mp4

# Download carousel/album (dikembalikan sebagai .zip)
curl -X POST http://localhost:8000/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.instagram.com/p/xxxx/"}' \
  -o album.zip
```

---

## ⚙️ Konfigurasi

Semua konfigurasi via file `.env`:

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `Bot Downloader` | Nama aplikasi (tampil di response API) |
| `APP_VERSION` | `1.0.0` | Versi aplikasi |
| `DEBUG` | `false` | Mode debug (logging lebih detail) |
| `API_HOST` | `0.0.0.0` | Host binding REST API |
| `API_PORT` | `8000` | Port REST API (run lokal; di Docker tidak dipublish) |
| `API_WORKERS` | `1` | Jumlah worker (pakai `1` untuk webhook) |
| `APP_URL` | - | URL publik untuk webhook, mis. `https://bot.example.com` **(wajib production)** |
| `TELEGRAM_BOT_TOKEN` | - | Token dari @BotFather **(wajib agar bot jalan)** |
| `TELEGRAM_ALLOWED_USERS` | (kosong) | Batasi user Telegram (pisahkan koma, kosong = semua boleh) |
| `DOWNLOAD_DIR` | `./downloads` | Direktori file sementara (di Docker dioverride ke `/tmp/downloads`) |
| `MAX_FILE_SIZE_MB` | `50` | Maksimal ukuran **per file** (MB, mengikuti limit Telegram) |
| `REQUEST_TIMEOUT` | `30` | Timeout request download (detik) |
| `LOGS_DIR` | `logs` | Direktori log interaksi per chat ID (di Docker dioverride ke `/app/logs`) |
| `YTDLP_COOKIES_FILE` | - | Path file cookies (untuk konten terbatas/private) |
| `YTDLP_USER_AGENT` | - | Custom User-Agent bila default diblokir |

---

## 📌 Batasan yang perlu diketahui

- **Ukuran file:** maksimal `MAX_FILE_SIZE_MB` per file. Bot melewati file yang
  kebesaran (dengan catatan); API menolak dengan `413` bila semua file kebesaran.
- **Album Telegram:** maksimal 10 media per grup — album lebih besar hanya
  10 pertama yang dikirim (ada pemberitahuan di chat).
- **Instagram:** sering membatasi akses tanpa login (`empty media response`).
  Solusinya: coba lagi nanti atau isi `YTDLP_COOKIES_FILE` dari browser yang login.
  Postingan foto & carousel publik didukung via fallback (oEmbed + embed page).
- **TikTok:** video didukung penuh; slideshow foto (`/photo/`) didukung via
  fallback (maks 10 gambar). Konten privat / akun privat butuh cookies.
- **Twitter/X:** video didukung penuh; tweet foto-only didukung via fallback
  (maks 4 foto, sesuai batas X). Akun protected butuh login.
- **Facebook:** video/reel publik didukung; postingan foto publik didukung via
  fallback `og:image`. Postingan teman-saja / grup tertutup butuh cookies.
- **YouTube:** video publik & unlisted-with-link didukung; video privat,
  khusus member, atau dibatasi umur butuh cookies akun yang berhak.
- **Konten privat/dihapus:** bot & API mengembalikan pesan error yang jelas.
  Tanpa terkecuali, konten privat TETAP tidak bisa diunduh tanpa
  `YTDLP_COOKIES_FILE` dari akun yang punya akses (bukan bug — batasan platform).

---

## 🐳 Deploy Production

Arsitektur: `bot-downloader` + `cloudflared` dalam satu Docker network
(`app-network`), tanpa port terpublish.

```yaml
# docker-compose.yml (sudah termasuk di repo)
services:
  bot-downloader:
    build: .
    container_name: bot-downloader
    restart: unless-stopped
    env_file: [.env]
    networks: [app-network]   # external, dibuat oleh stack lain

networks:
  app-network:
    name: app-network
    external: true
```

```yaml
# Ingress cloudflared (di stack cloudflare)
ingress:
  - hostname: bot.example.com
    service: http://bot-downloader:8000
```

### Auto-deploy via GitHub Actions

Setiap push ke `main` otomatis deploy (lihat `.github/workflows/deploy.yml`).
Isi secrets di **Settings → Secrets → Actions**:

| Secret | Isi |
|---|---|
| `SSH_HOST` | IP / domain server |
| `SSH_USER` | user SSH |
| `SSH_KEY` | private key (isi `id_*`, bukan `.pub`) |
| `SSH_PORT` | port SSH (default `22`) |
| `PROJECT_PATH` | path project di server, mis. `/opt/bot-downloader` |

Sekali saja di server:

```bash
git clone git@github.com:alfiansetia/bot-downloader.git /opt/bot-downloader
cd /opt/bot-downloader && cp .env.example .env  # isi TOKEN, APP_URL, dll
docker network ls | grep app-network            # pastikan sudah ada
```

`.env`, `downloads/`, `logs/` aman saat deploy karena masuk `.gitignore`.

---

## 📦 API Documentation

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Info aplikasi + platform & media yang didukung |
| `GET` | `/health` | Health check (`{"status":"ok"}`) |
| `POST` | `/download` | Download media. 1 file → file langsung; album → `.zip` |
| `POST` | `/webhook` | Telegram webhook (internal) |

**Contoh API call:**

```bash
curl -X POST http://localhost:8000/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.instagram.com/reel/xxxxx/"}' \
  -o hasil.mp4
```

**Response header tambahan:**

| Header | Arti |
|---|---|
| `X-Platform` | `youtube` / `tiktok` / `instagram` / `facebook` / `twitter` |
| `X-Title` | Judul konten |
| `X-Media-Type` | `video` / `photo` / `mixed` |
| `X-Media-Count` | Jumlah file |
| `X-Excluded-Too-Large` | Jumlah file yang dilewati karena melebihi batas (bila ada) |
| `X-Archive` | Nama file zip (bila hasil berupa album) |

**Error:**

| Status | Arti |
|---|---|
| `400` | URL tidak dikenali / download gagal (lihat `detail`) |
| `413` | File melebihi `MAX_FILE_SIZE_MB` |
| `500` | File tidak ditemukan setelah download |

---

## 🛠 Tech Stack

- **Python 3.12** (Docker) / 3.10+ (lokal)
- **yt-dlp** — engine download video & foto
- **FastAPI** — REST API
- **python-telegram-bot** — Telegram Bot
- **Docker** — containerization (tanpa publish port, via internal network)
- **GitHub Actions + Cloudflare Tunnel** — deploy & ekspos production
