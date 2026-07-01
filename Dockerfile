FROM python:3.12-slim

# System deps for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
    fonts-liberation fonts-noto-cjk fonts-noto-color-emoji \
    wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium (deps already installed above via apt-get)
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install chromium

# Copy app (including static files)
COPY app/ ./app/

# Output + cache directories
RUN mkdir -p /tmp/pdf-output /var/cache/pdf

# Non-root user
RUN useradd -m -s /bin/bash pdfuser && \
    chown -R pdfuser:pdfuser /app /tmp/pdf-output /var/cache/pdf /opt/playwright
USER pdfuser

# Environment defaults
ENV BROWSER_POOL_SIZE=3
ENV MAX_CONCURRENT_TASKS=10
ENV MAX_PAGES_PER_BROWSER=200
ENV LOG_LEVEL=INFO
ENV OUTPUT_DIR=/tmp/pdf-output
ENV MAX_HTML_SIZE=5000000
ENV MAX_CSS_SIZE=500000
ENV MAX_JS_SIZE=100000
ENV USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
# Output cache (biggest cost saver) + security defaults
ENV CACHE_ENABLED=true
ENV CACHE_DIR=/var/cache/pdf
ENV CACHE_TTL_SECONDS=86400
ENV CACHE_MAX_BYTES=2000000000
# PDF_API_KEYS / ALLOWED_HOSTS / CORS_ORIGINS: set at runtime (compose/.env)

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
