# MP4 Extractor

Microservice لاستخراج روابط MP4 المباشرة من hgcloud.to/vibuxer.com/hanerix.com
يدعم جودات: 4K, FullHD, HD, Normal

## Endpoints

### GET /health
Health check.

### GET /qualities?id={fileId}
يفحص الجودات المتاحة لملف معين (سريع، بدون Playwright).

### POST /extract
body: `{"url": "https://hgcloud.to/f/{id}_{quality}", "fallback": true}`
يستخرج رابط MP4 مباشر باستخدام Playwright (reCAPTCHA v3 تُحل تلقائياً).

## النشر على Fly.io

1. اذهب إلى https://fly.io/dashboard
2. New App → اختر هذا الـ repo
3. Fly.io سيكتشف Dockerfile تلقائياً
4. انتظر النشر (~2-3 دقائق)

## متغيرات البيئة

- `PORT` — البورت (Fly.io يضبطه تلقائياً، افتراضي 8080)
