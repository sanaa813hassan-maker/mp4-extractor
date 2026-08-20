#!/usr/bin/env python3
"""
mp4-extractor v5.0 — Fly.io Service (curl_cffi Chrome TLS)

The CDN (bcdnxw.hakunaymatata.com) checks TLS fingerprint.
Only Chrome TLS works (206). Node.js/Vercel → 427, Cloudflare Worker → 427.
curl_cffi with impersonate="chrome" → 206 ✅

Endpoints:
  GET  /health
  GET  /proxy?url=<url>&ref=<referer>&filename=<name>
"""

import os
import re
from urllib.parse import unquote, quote as url_quote
from aiohttp import web

PORT = int(os.environ.get('PORT', '8080'))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

_cffi_session = None

def get_cffi():
    global _cffi_session
    if _cffi_session is None:
        from curl_cffi import requests as cffi_requests
        _cffi_session = cffi_requests.Session(impersonate="chrome")
    return _cffi_session


async def handle_proxy(request):
    """Proxy CDN URL with Chrome TLS + Referer. Streams response."""
    target_url = request.query.get('url')
    if not target_url:
        return web.json_response({'ok': False, 'error': 'Missing "url"'}, status=400)

    custom_ref = request.query.get('ref', 'https://videodownloader.site/')
    custom_filename = request.query.get('filename', 'video.mp4')
    range_header = request.headers.get('Range')

    filename = unquote(custom_filename).strip()
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', filename)
    filename = re.sub(r'\s+', '.', filename)
    if len(filename) > 200:
        filename = filename[:200]
    if not filename.lower().endswith('.mp4'):
        filename = filename + '.mp4'

    cdn_headers = {
        'User-Agent': UA,
        'Referer': custom_ref,
        'Origin': custom_ref.rstrip('/'),
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    if range_header:
        cdn_headers['Range'] = range_header

    try:
        cffi = get_cffi()
        resp = cffi.get(target_url, headers=cdn_headers, timeout=300, stream=True)

        if resp.status_code != 200 and resp.status_code != 206:
            body = resp.text[:200] if hasattr(resp, 'text') else 'unknown'
            return web.json_response(
                {'ok': False, 'error': f'CDN returned {resp.status_code}', 'body': body},
                status=502, headers={'Access-Control-Allow-Origin': '*'}
            )

        ascii_fn = filename.encode('ascii', 'replace').decode('ascii').replace('?', '_')
        encoded_fn = url_quote(filename)

        resp_headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
            'Access-Control-Allow-Headers': 'Range',
            'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Content-Disposition',
            'Accept-Ranges': 'bytes',
            'Content-Disposition': f"attachment; filename=\"{ascii_fn}\"; filename*=UTF-8''{encoded_fn}",
            'Cache-Control': 'no-store',
        }

        ct = resp.headers.get('content-type', '')
        resp_headers['Content-Type'] = ct if ('video' in ct or 'mpegurl' in ct) else 'video/mp4'

        cl = resp.headers.get('content-length')
        if cl:
            resp_headers['Content-Length'] = cl
        cr = resp.headers.get('content-range')
        if cr:
            resp_headers['Content-Range'] = cr

        stream_response = web.StreamResponse(status=resp.status_code, headers=resp_headers)
        await stream_response.prepare(request)

        for chunk in resp.iter_content(chunk_size=256 * 1024):
            await stream_response.write(chunk)

        await stream_response.write_eof()
        return stream_response

    except Exception as e:
        return web.json_response(
            {'ok': False, 'error': str(e)},
            status=502, headers={'Access-Control-Allow-Origin': '*'}
        )


async def handle_health(request):
    return web.json_response({
        'ok': True, 'service': 'mp4-extractor', 'version': '5.0.0',
        'mode': 'curl_cffi Chrome TLS', 'endpoints': ['/proxy', '/health'],
    })


async def handle_options(request):
    return web.Response(status=204, headers={
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, HEAD, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Range, Content-Type',
    })


def main():
    app = web.Application()
    app.router.add_get('/proxy', handle_proxy)
    app.router.add_head('/proxy', handle_proxy)
    app.router.add_get('/health', handle_health)
    app.router.add_route('OPTIONS', '/{tail:.*}', handle_options)
    print(f'mp4-extractor v5.0 (curl_cffi Chrome TLS) listening on :{PORT}')
    web.run_app(app, host='0.0.0.0', port=PORT, access_log=None)


if __name__ == '__main__':
    main()
