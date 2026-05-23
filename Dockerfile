FROM python:3.11-slim

# System deps:
#   - ffmpeg / ffprobe (video tools)
#   - tesseract-ocr + poppler-utils (Phase 2 OCR; bake now to skip rebuilds later)
#   - libgl1 / libglib2.0-0 (some imaging libs need them)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching)
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Default port; can be overridden via UAP_PORT
EXPOSE 3260

# Healthcheck — relies on /healthz route in __main__.py
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:${UAP_PORT:-3260}/healthz || exit 1

ENV PYTHONUNBUFFERED=1
ENV UAP_DATA_DIR=/srv/uap-data
ENV UAP_CACHE_DIR=/srv/uap-data/.cache
# Point faster-whisper's HuggingFace cache into the bind-mounted cache dir so
# model weights (base.en ~75MB, small.en ~250MB, etc.) persist across rebuilds.
ENV HF_HOME=/srv/uap-data/.cache/hf
ENV HF_HUB_DISABLE_TELEMETRY=1

CMD ["python", "-m", "uap_analyzer"]
