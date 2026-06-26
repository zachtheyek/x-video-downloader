# Multi-arch (amd64 + arm64) — same image runs on an x86 Mac, ARM Oracle, or a Pi.
FROM python:3.12-slim

# ffmpeg is required for HLS fragment merge / MP4 remux.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# All state lives under /data so it can be a single mounted volume (local now, NAS later).
ENV XDL_DB=/data/ledger.db \
    XDL_ARCHIVE=/data/archive.txt \
    XDL_DOWNLOAD_DIR=/data/downloads \
    XDL_STAGING_DIR=/data/staging \
    XDL_HUEY_DB=/data/huey.db \
    XDL_HOST=0.0.0.0 \
    XDL_PORT=8080

EXPOSE 8080
VOLUME ["/data"]

# `xdl serve` runs uvicorn + an in-process Huey consumer (one container, both roles).
# To scale the worker out later, split into two services (see docker-compose.yml).
CMD ["xdl", "serve"]
