# ── Builder stage ──
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


# ── Runtime stage ──
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies (yt-dlp needs ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local

# Copy app code
COPY app/ /app/app/

# Ensure scripts in .local are in PATH
ENV PATH=/root/.local/bin:$PATH

# Create download directory
RUN mkdir -p /tmp/downloads

EXPOSE 8000

ENTRYPOINT ["python", "-m", "app"]
CMD ["all"]
