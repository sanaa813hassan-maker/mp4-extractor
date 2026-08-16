#!/usr/bin/env python3
"""
MP4 Extractor — Streaming Proxy (نسخة مبسطة مستقرة)

خدمة بث MP4 من CDNs (premilkyway.com, hakunaymatata.com, etc.)
عبر Fly.io proxy. الـ proxy بيضيف:
- Content-Disposition: attachment باسم ملف مخصص
- Accept-Ranges: bytes (لدعم seek)
- Cache-Control: no-store (منع الـ loop عند 100%)
- HEAD method support

✨ النسخة دي مفيهاش Playwright — عشان Fly.io machines كانت بتعمل crash
بسبب memory Chromium (500-800MB لكل instance). الـ proxy بيشغل على 50MB بس.

الاستخراج (extract + videasy) اتنقل لـ Vercel باستخدام @sparticuz/chromium.

Endpoints:
  GET  /proxy?url={mp4_url}&filename={custom}.mp4&download=1
  HEAD /proxy?url={mp4_url}&filename={custom}.mp4
  GET  /health
"""

import os
import re
from urllib.parse import unquote, quote as url_quote
import aiohttp
from aiohttp import web, web_request

PORT = int(os.environ.get('PORT', '8080'))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

# Request type (compatible with all aiohttp versions)
try:
    from aiohttp.web_request import Request as Request
except ImportError:
    try:
        from aiohttp import web_request
        Request = web_request.Request
    except ImportError:
        from aiohttp.web_request import BaseRequest as Request


def sanitize_filename(name: str) -> str:
    """نظّف اسم الملف من الرموز غير المسموحة"""
    from urllib.parse import unquote
    name = unquote(name).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'\s+', '.', name)
    if len(name) > 200:
        name = name[:200]
    if not name.lower().endswith('.mp4'):
        name = name + '.mp4'
    return name


async def handle_proxy(request):
    """
    GET /proxy?url={mp4_url}&filename={custom_name}&download=1
    HEAD /proxy?url={mp4_url}&filename={custom_name}

    يبث ملف MP4 من الـ CDN عبر Fly.io (الـ token مرتبط بـ IP).
    """
    target_url = request.query.get('url')
    if not target_url:
        return web.json_response({'ok': False, 'error': 'Missing "url" parameter'}, status=400)

    # اسم الملف: من filename parameter فقط، ولا video.mp4
    custom_filename = request.query.get('filename')
    if custom_filename:
        filename = sanitize_filename(custom_filename)
    else:
        filename = 'video.mp4'

    # HEAD method — المتصفح بيتحقق من الـ metadata قبل التحميل
    if request.method == 'HEAD':
        ascii_fn = filename.encode('ascii', 'replace').decode('ascii').replace('?', '_')
        return web.Response(
            status=200,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
                'Access-Control-Allow-Headers': 'Range',
                'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Content-Type, Content-Disposition',
                'Accept-Ranges': 'bytes',
                'Content-Type': 'video/mp4',
                'Content-Disposition': f"attachment; filename=\"{ascii_fn}\"; filename*=UTF-8''{url_quote(filename)}",
                'Cache-Control': 'no-cache',
            }
        )

    try:
        session = aiohttp.ClientSession()
        try:
            headers = {'User-Agent': UA}

            # أضف Referer/Origin تلقائياً حسب الـ CDN
            if 'hakunaymatata.com' in target_url or 'bcdnxw' in target_url:
                headers['Referer'] = 'https://videodownloader.site/'
                headers['Origin'] = 'https://videodownloader.site'
            elif 'premilkyway.com' in target_url or 'cdn-centaurus.com' in target_url:
                headers['Referer'] = 'https://audinifer.com/'
                headers['Origin'] = 'https://audinifer.com'

            custom_ref = request.query.get('ref')
            if custom_ref:
                headers['Referer'] = custom_ref

            range_header = request.headers.get('Range')
            if range_header:
                headers['Range'] = range_header

            upstream = await session.get(target_url, headers=headers, timeout=aiohttp.ClientTimeout(total=300))

            if upstream.status != 200 and upstream.status != 206:
                body = await upstream.text()
                await upstream.release()
                await session.close()
                return web.json_response(
                    {'ok': False, 'error': f'Upstream returned {upstream.status}', 'body': body[:200]},
                    status=502,
                    headers={'Access-Control-Allow-Origin': '*'}
                )

            # Response headers
            ascii_filename = filename.encode('ascii', 'replace').decode('ascii').replace('?', '_')
            resp_headers = {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
                'Access-Control-Allow-Headers': 'Range',
                'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Content-Type, Content-Disposition',
                'Accept-Ranges': 'bytes',
                'Content-Disposition': f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{url_quote(filename)}",
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Content-Type': 'video/mp4',
            }

            for h in ['content-length', 'content-range']:
                v = upstream.headers.get(h)
                if v:
                    resp_headers[h] = v

            stream_response = web.StreamResponse(
                status=upstream.status,
                headers=resp_headers
            )
            await stream_response.prepare(request)

            async for chunk in upstream.content.iter_chunked(256 * 1024):
                await stream_response.write(chunk)

            await stream_response.write_eof()
            await upstream.release()
            await session.close()
            return stream_response

        except Exception as e:
            await session.close()
            raise e
    except Exception as e:
        return web.json_response(
            {'ok': False, 'error': str(e)},
            status=502,
            headers={'Access-Control-Allow-Origin': '*'}
        )


async def handle_health(request):
    return web.json_response({
        'ok': True,
        'service': 'mp4-extractor',
        'version': '2.0.0',
        'mode': 'proxy-only (no playwright)',
    })


async def handle_options(request):
    return web.Response(status=204, headers={
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
        'Access-Control-Allow-Headers': 'Range, Content-Type',
    })


def main():
    app = web.Application()
    # ✨ proxy فقط — بدون /extract, /extract-all, /videasy, /qualities
    app.router.add_get('/proxy', handle_proxy)
    app.router.add_head('/proxy', handle_proxy)
    app.router.add_get('/health', handle_health)
    app.router.add_route('OPTIONS', '/{tail:.*}', handle_options)
    print(f'MP4 extractor (proxy-only v2.0) listening on :{PORT}')
    web.run_app(app, host='0.0.0.0', port=PORT, access_log=None)


if __name__ == '__main__':
    main()
