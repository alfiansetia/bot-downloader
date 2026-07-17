# Bot Downloader

Bot Telegram & REST API untuk download video dari **YouTube, TikTok, Instagram, Facebook, dan Twitter/X**.

Dibangun dengan **Python**, **yt-dlp**, **FastAPI**, dan **python-telegram-bot**.

---

## 📋 Fitur

- ✅ Download video dari **YouTube** (termasuk Shorts)
- ✅ Download video dari **TikTok**
- ✅ Download video dari **Instagram** (Reel, Post video, Story)
- ✅ Download video dari **Facebook** (Video, Reel)
- ✅ Download video dari **Twitter/X**
- ✅ **Telegram Bot** — kirim link, dapat video
- ✅ **REST API** — endpoint HTTP untuk download
- ✅ **Docker support** — siap deploy

---

## 🚀 Instalasi & Cara Pakai

### 1. Clone & Setup

```bash
git clone <repo-url>
cd bot-downloader

# Copy environment file
cp .env.example .env
```

### 2. Konfigurasi `.env`

Edit file `.env` dan isi minimal:

```env
# Wajib: Token dari @BotFather
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklmNOPqrstUVwxyz

# Wajib: URL publik bot (untuk webhook Telegram)
APP_URL=https://bot.example.com

# Opsional: batasi user (pisahkan koma)
TELEGRAM_ALLOWED_USERS=123456789,987654321
```

### 3. Jalankan

#### 🖥️ Local (tanpa Docker)

```bash
# Buat & aktifkan virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy & isi .env
cp .env.example .env
# lalu edit .env, isi TELEGRAM_BOT_TOKEN dan APP_URL

# Jalankan server (API + Bot via webhook)
python -m app
```

#### 🐳 Docker

```bash
# Copy & edit .env
cp .env.example .env
# lalu edit .env, isi TELEGRAM_BOT_TOKEN dan APP_URL

# Build & jalankan
docker compose up -d --build

# Lihat log
docker compose logs -f

# Lihat log container tertentu
docker compose logs bot-downloader -f

# Lihat log realtime dengan timestamp
docker compose logs -f --tail=50
```

---

## 🤖 Webhook Telegram Bot

Bot menggunakan **webhook** — server menerima update dari Telegram secara otomatis.

**Cara kerja:**

1. Set `APP_URL` di `.env` dengan domain publik (misal `https://bot.example.com`)
2. Jalankan server
3. Server otomatis set webhook ke `https://api.telegram.org/bot<TOKEN>/setWebhook` saat startup
4. Telegram kirim update ke `https://bot.example.com/webhook`

> **Catatan:** Pastikan domain sudah指向 ke server dan pakai **HTTPS** (Telegram mewajibkan HTTPS untuk webhook).

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

# Download video via API
curl -X POST http://localhost:8000/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.tiktok.com/@user/video/123456"}' \
  -o video.mp4
```

---

## ⚙️ Konfigurasi

Semua konfigurasi via file `.env`:

| Variable                 | Default  | Description                                   |
| ------------------------ | -------- | --------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`     | -        | Token dari @BotFather **(wajib untuk bot)**   |
| `TELEGRAM_ALLOWED_USERS` | (kosong) | Batasi user Telegram (pisahkan koma)          |
| `API_PORT`               | `8000`   | Port REST API                                 |
| `MAX_FILE_SIZE_MB`       | `50`     | Maksimal ukuran file (MB)                     |
| `DEBUG`                  | `false`  | Mode debug                                    |
| `YTDLP_COOKIES_FILE`     | -        | Path file cookies untuk akses konten terbatas |

---

## 🐳 Deploy Production

Contoh dengan reverse proxy (Caddy/Traefik/Nginx) + domain + SSL:

```yaml
# docker-compose.yml tambahan untuk production
services:
  caddy:
    image: caddy:latest
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
    depends_on:
      - bot-downloader

volumes:
  caddy_data:
```

```
# Caddyfile
yourdomain.com {
    reverse_proxy bot-downloader:8000
}
```

---

## 📦 API Documentation

| Method | Endpoint    | Description                 |
| ------ | ----------- | --------------------------- |
| `GET`  | `/`         | Info aplikasi               |
| `GET`  | `/health`   | Health check                |
| `POST` | `/download` | Download video              |
| `POST` | `/webhook`  | Telegram webhook (internal) |

**Contoh API call:**

```bash
curl -X POST http://localhost:8000/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.instagram.com/reel/xxxxx/"}' \
  -o video.mp4
```

---

## 🛠 Tech Stack

- **Python 3.12**
- **yt-dlp** — engine download video
- **FastAPI** — REST API
- **python-telegram-bot** — Telegram Bot
- **Docker** — containerization
