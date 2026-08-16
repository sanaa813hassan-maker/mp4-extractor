FROM python:3.12-slim-bookworm

# Set environment variables for faster startup and Python optimization
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies needed by Playwright Chromium (Debian 12 bookworm)
# Using explicit package names to avoid conflicts
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc-s1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (better caching)
RUN pip install --no-cache-dir aiohttp==3.10.5 playwright==1.47.0

# Install Playwright Chromium browser binaries (NO --with-deps, we did apt-get above)
RUN playwright install chromium

# Copy the app
COPY index.py /app/index.py

WORKDIR /app

# Fly.io sets PORT env var
ENV PORT=8080
EXPOSE 8080

# Health check with longer start-period (Playwright needs time to init)
HEALTHCHECK --interval=30s --timeout=15s --start-period=90s --retries=5 \
  CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python3", "-u", "index.py"]
