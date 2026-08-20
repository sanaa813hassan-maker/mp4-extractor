# MP4 Extractor + Vidfast Extractor

Microservice for extracting video sources from multiple sites.

## Endpoints

### GET /health
Health check.

### GET /proxy?url={mp4_url}&filename={custom_name}&ref={referer}
Proxy MP4/m3u8 from CDNs with proper Referer/Origin headers.

### POST /vidfast
body: `{"url": "https://vidfast.vc/movie/1265609"}`

Extracts m3u8/mp4 URLs from vidfast.vc using Playwright + stealth.
Tries Chromium first, then Firefox as fallback.

### POST /vidfast-cookies
body: `{"url": "https://vidfast.vc/movie/1265609"}`

Lighter extraction using curl_cffi (Chrome impersonation).
Bypasses Cloudflare but cannot execute JavaScript.
Returns the page's RSC data (en token, title, etc.).

## Deployment on Fly.io

1. `fly deploy` (or push to GitHub and connect the repo)
2. The Dockerfile uses `mcr.microsoft.com/playwright/python` which has Chromium pre-installed
3. Needs at least 1GB RAM for Chromium
4. `auto_stop_machines = 'stop'` — saves cost when idle
