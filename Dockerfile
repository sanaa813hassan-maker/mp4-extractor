FROM python:3.12-slim-bookworm

# Install system dependencies needed by Playwright Chromium
# Use playwright install-deps (official method - handles package names correctly)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget gnupg ca-certificates \
    fonts-liberation \
    libasound2 libatk-bridge2.0-0 libatk1.0-0 libcairo2 libcups2 \
    libdbus-1-3 libexpat1 libfontconfig1 libgbm1 libgcc-s1 \
    libglib2.0-0 libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 \
    libpangocairo-1.0-0 libstdc++6 libx11-6 libx11-xcb1 libxcb1 \
    libxcomposite1 libxcursor1 libxdamage1 libxext6 libxfixes3 \
    libxi6 libxrandr2 libxrender1 libxss1 libxtst6 lsb-release xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir aiohttp playwright

# Install Playwright Chromium browser WITH system dependencies
RUN playwright install --with-deps chromium

# Copy the app
COPY index.py /app/index.py

WORKDIR /app

# Fly.io sets PORT env var (usually 8080)
ENV PORT=8080
EXPOSE 8080

# Health check - verify the app is responding
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

CMD ["python3", "index.py"]
