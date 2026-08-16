FROM python:3.12-slim-bookworm

# Set environment variables for optimization
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ✨ لا Playwright ولا Chromium — proxy فقط بـ aiohttp
# الـ image دي صغيرة جداً (~150MB) وبتشتغل على 50MB RAM
RUN pip install --no-cache-dir aiohttp==3.10.5

WORKDIR /app
COPY index.py .

ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python3", "-u", "index.py"]
