#!/usr/bin/env python3
"""
MP4 Extractor Microservice

يستخرج رابط MP4 مباشر من hgcloud.to/vibuxer.com/hanerix.com
يدعم اختيار الجودة: 4K (_x), FullHD (_h), HD (_n)

المسار:
  hgcloud.to/f/{id}_{quality} → Playwright → click button → wait 5s → extract MP4 URL

الـ endpoint:
  POST /extract
  body: {"url": "https://hgcloud.to/f/tmk5xynqwvvw_x"}
  → {"ok": true, "mp4Url": "https://...premilkyway.com/vp/.../file.mp4?t=...&s=...&e=129600...", "expiresAt": "..."}
"""

import asyncio
import json
import os
import re
from aiohttp import web, web_request
from playwright.async_api import async_playwright

PORT = int(os.environ.get('PORT', '3040'))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

# Pool of player domains to try (hgcloud.to redirects to one of these)
PLAYER_DOMAINS = ['hanerix.com', 'audinifer.com', 'dohaxe.com', 'vibuxer.com']


# الجودات المدعومة بالترتيب من الأعلى للأقل
QUALITIES = ['x', 'h', 'n', 'l']  # 4K, FullHD, HD, Normal
QUALITY_LABELS = {'x': '4K ed', 'h': 'FullHD ed', 'n': 'HD ed', 'l': 'Normal ed'}


def extract_file_id_and_quality(url: str):
    """استخرج fileId والجودة من الرابط

    يدعم عدة صيغ:
      /f/{id}_x, /f/{id}, /{id}_x, /{id}
      /f/{id}_x.html, /{id}_x.html
      vibuxer.com/{id}, vibuxer.com/f/{id}
    """
    # Quality suffixes: x=4K, h=FullHD, n=HD, l=Normal
    # Try multiple patterns
    patterns = [
        r'/f?/([A-Za-z0-9]{6,20})(?:_([xhnl]))?(?:\.html)?(?:[/?#]|$)',
        r'/([A-Za-z0-9]{6,20})(?:_([xhnl]))?(?:\.html)?(?:[/?#]|$)',
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1), m.group(2) or 'x'
    return None, None


async def check_quality_available(file_id: str, quality: str, domain: str = 'hanerix.com'):
    """تحقق إذا كانت الجودة متاحة بسرعة (بدون Playwright)"""
    import aiohttp
    url = f'https://{domain}/f/{file_id}_{quality}'
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return False
                html = await resp.text()
                # Available: has the form/button with download
                # Unavailable: shows "nobtn" instead of the button
                return 'submit-btn' in html or 'g-recaptcha btn' in html
    except:
        return False


async def get_available_qualities(file_id: str, domain: str = 'hanerix.com'):
    """تحقق من كل الجودات المتاحة لملف معين"""
    import aiohttp
    available = []
    url_base = f'https://{domain}/f/{file_id}'
    try:
        async with aiohttp.ClientSession() as session:
            for q in QUALITIES:
                url = f'{url_base}_{q}'
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            if 'submit-btn' in html or 'g-recaptcha btn' in html:
                                # Extract file size if present
                                size_match = re.search(r'([0-9.]+\s*[KMGT]B)', html)
                                size = size_match.group(1) if size_match else None
                                available.append({
                                    'quality': q,
                                    'label': QUALITY_LABELS.get(q, q),
                                    'size': size,
                                    'url': url
                                })
                except:
                    continue
    except:
        pass
    return available


def parse_mp4_expiry(mp4_url: str):
    """احسب وقت انتهاء الصلاحية من الـ query params"""
    try:
        from urllib.parse import urlparse, parse_qs
        u = urlparse(mp4_url)
        params = parse_qs(u.query)
        s = int(params.get('s', ['0'])[0])
        e = int(params.get('e', ['129600'])[0])
        if s > 0:
            from datetime import datetime, timezone
            return datetime.fromtimestamp(s + e, tz=timezone.utc).isoformat()
    except:
        pass
    return None


async def extract_mp4_playwright(target_url: str, quality: str = 'x'):
    """شغّل Playwright لاستخراج رابط MP4"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
        )
        context = await browser.new_context(
            user_agent=UA,
            viewport={'width': 1280, 'height': 800},
            locale='en-US',
        )
        page = await context.new_page()

        try:
            await page.goto(target_url, wait_until='networkidle', timeout=20000)
            await page.wait_for_timeout(1500)

            # Click the download button (g-recaptcha)
            clicked = False
            try:
                await page.click('button.g-recaptcha', timeout=5000)
                clicked = True
            except:
                # Fallback: find button with .mp4 text
                try:
                    btn = page.locator('button:has-text(".mp4")')
                    await btn.first.click(timeout=5000)
                    clicked = True
                except:
                    pass

            if not clicked:
                raise Exception('Could not find/click the download button')

            # Wait for countdown (5 seconds) + MP4 link to appear
            await page.wait_for_timeout(7000)

            # Extract MP4 URL
            mp4_url = await page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a')).map(a => a.href).filter(h => h.includes('.mp4'));
                const mp4InDom = (document.body.innerHTML.match(/https?:\\/\\/[^\\s"'<>]+\\.mp4[^\\s"'<>]*/gi) || []);
                return links[0] || mp4InDom[0] || null;
            }""")

            if not mp4_url:
                # Debug: get page content
                content = await page.content()
                raise Exception(f'No MP4 URL found after countdown. Page size: {len(content)}')

            return mp4_url

        finally:
            await browser.close()


async def handle_qualities(request: web_request.Request):
    """GET /qualities?id={fileId} — تحقق من كل الجودات المتاحة"""
    try:
        file_id = request.query.get('id')
        if not file_id:
            return web.json_response({'ok': False, 'error': 'Missing "id" parameter'}, status=400)

        # Try each player domain until one works
        available = []
        for domain in PLAYER_DOMAINS:
            available = await get_available_qualities(file_id, domain)
            if available:
                break

        return web.json_response({
            'ok': True,
            'fileId': file_id,
            'qualities': available,
            'count': len(available),
            'qualitiesOrder': QUALITIES,
            'labels': QUALITY_LABELS,
        }, headers={'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        return web.json_response(
            {'ok': False, 'error': str(e)},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'}
        )


async def handle_extract(request: web_request.Request):
    """POST /extract — body: {"url": "https://hgcloud.to/f/{id}_{quality}"}"""
    try:
        body = await request.json()
        url = body.get('url')
        fallback = body.get('fallback', True)  # default: try lower qualities if requested one unavailable
        if not url:
            return web.json_response({'ok': False, 'error': 'Missing "url" in body'}, status=400)

        # Extract fileId and quality
        file_id, quality = extract_file_id_and_quality(url)
        if not file_id:
            return web.json_response({'ok': False, 'error': f'Could not extract fileId from: {url}'}, status=400)

        # Build list of qualities to try (requested first, then fallback to lower)
        qualities_to_try = [quality]
        if fallback:
            # Add remaining qualities in order (skip the requested one)
            for q in QUALITIES:
                if q != quality:
                    qualities_to_try.append(q)

        # Try each quality
        for q in qualities_to_try:
            target_url = f'https://hgcloud.to/f/{file_id}_{q}'

            # Quick check: is this quality available?
            is_available = await check_quality_available(file_id, q)
            if not is_available:
                continue  # skip to next quality

            # Extract MP4 via Playwright
            mp4_url = None
            tried = []
            for domain in ['hgcloud.to'] + PLAYER_DOMAINS:
                try_url = f'https://{domain}/f/{file_id}_{q}'
                tried.append(domain)
                try:
                    mp4_url = await extract_mp4_playwright(try_url, q)
                    if mp4_url:
                        break
                except:
                    continue

            if mp4_url:
                expires_at = parse_mp4_expiry(mp4_url)
                return web.json_response({
                    'ok': True,
                    'mp4Url': mp4_url,
                    'fileId': file_id,
                    'quality': q,
                    'qualityLabel': QUALITY_LABELS.get(q, q),
                    'requestedQuality': quality,
                    'fellBack': q != quality,
                    'expiresAt': expires_at,
                }, headers={'Access-Control-Allow-Origin': '*'})

        # No quality worked
        return web.json_response({
            'ok': False,
            'error': f'No available quality found for fileId={file_id}. Tried: {", ".join(qualities_to_try)}',
            'triedQualities': qualities_to_try,
        }, status=502, headers={'Access-Control-Allow-Origin': '*'})

    except Exception as e:
        return web.json_response(
            {'ok': False, 'error': str(e)},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'}
        )


async def handle_health(request):
    return web.json_response({'ok': True, 'service': 'mp4-extractor', 'version': '1.0.0'})


async def handle_options(request):
    return web.Response(status=204, headers={
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
    })


async def handle_proxy(request: web_request.Request):
    """
    GET /proxy?url={mp4_url}

    يبث ملف MP4 من premilkyway.com عبر Fly.io.
    ضروري لأن premilkyway.com يربط الـ token بالـ IP:
    - الـ token وُلّد على Fly.io → يعمل فقط من Fly.io IP
    - هذا الـ proxy يجلب الـ MP4 من Fly.io ويعيد بثه للمتصفح
    """
    import aiohttp
    target_url = request.query.get('url')
    if not target_url:
        return web.json_response({'ok': False, 'error': 'Missing "url" parameter'}, status=400)

    try:
        # لا نستخدم 'async with' للـ session حتى نتمكن من بث الـ response
        session = aiohttp.ClientSession()
        try:
            # مرّر Range header إذا موجود (لدعم seek)
            headers = {'User-Agent': UA}

            # أضف Referer/Origin تلقائياً حسب الـ CDN
            if 'hakunaymatata.com' in target_url or 'bcdnxw' in target_url:
                headers['Referer'] = 'https://videodownloader.site/'
                headers['Origin'] = 'https://videodownloader.site'
            elif 'premilkyway.com' in target_url or 'cdn-centaurus.com' in target_url:
                headers['Referer'] = 'https://audinifer.com/'
                headers['Origin'] = 'https://audinifer.com'

            # اسمح بتمرير Referer مخصص عبر query param
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

            # مرّر الـ headers المهمة
            resp_headers = {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
                'Access-Control-Allow-Headers': 'Range',
                'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Content-Type',
                'Cache-Control': 'public, max-age=86400',
            }

            # وضع التحميل: أضف Content-Disposition: attachment
            download_mode = request.query.get('download')
            if download_mode:
                # استخرج اسم الملف من الـ URL
                import os
                url_path = target_url.split('?')[0]
                filename = os.path.basename(url_path) or 'video.mp4'
                # فكّ الترميز إذا كان URL-encoded
                from urllib.parse import unquote
                filename = unquote(filename)
                resp_headers['Content-Disposition'] = f'attachment; filename="{filename}"'
            for h in ['content-type', 'content-length', 'content-range', 'accept-ranges']:
                v = upstream.headers.get(h)
                if v:
                    resp_headers[h] = v

            # استخدم StreamResponse لبث الـ body chunk-by-chunk
            stream_response = web.StreamResponse(
                status=upstream.status,
                headers=resp_headers
            )
            await stream_response.prepare(request)

            # اقرأ الـ upstream chunk-by-chunk وابثه
            async for chunk in upstream.content.iter_chunked(256 * 1024):  # 256KB chunks for speed
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


async def handle_extract_all(request: web_request.Request):
    """
    POST /extract-all
    body: { "url": "https://hgcloud.to/f/{id}" }  (بدون suffix جودة)

    يستخرج MP4 لكل الجودات المتاحة بالتوازي:
      1. يفحص الجودات المتاحة عبر /qualities
      2. يستخرج MP4 لكل جودة بالتوازي (Playwright)
      3. يُعيد كل الروابط مع الحجم والصلاحية

    الاستجابة:
      { ok, fileId, qualities: [{quality, label, mp4Url, size, expiresAt, ok}], count }
    """
    try:
        body = await request.json()
        url = body.get('url')
        if not url:
            return web.json_response(
                {'ok': False, 'error': 'Missing "url" in body'},
                status=400,
                headers={'Access-Control-Allow-Origin': '*'}
            )

        # استخرج fileId
        file_id, _ = extract_file_id_and_quality(url)
        if not file_id:
            return web.json_response(
                {'ok': False, 'error': f'Could not extract fileId from: {url}'},
                status=400,
                headers={'Access-Control-Allow-Origin': '*'}
            )

        # 1. افحص الجودات المتاحة
        available = await get_available_qualities(file_id)

        if not available:
            return web.json_response(
                {'ok': False, 'error': f'No available qualities for fileId={file_id}'},
                status=404,
                headers={'Access-Control-Allow-Origin': '*'}
            )

        # 2. استخرج MP4 لكل جودة بالتوازي
        async def extract_one(quality_info):
            q = quality_info['quality']
            target_url = f'https://hgcloud.to/f/{file_id}_{q}'
            try:
                mp4_url = None
                for domain in ['hgcloud.to'] + PLAYER_DOMAINS:
                    try_url = f'https://{domain}/f/{file_id}_{q}'
                    try:
                        mp4_url = await extract_mp4_playwright(try_url, q)
                        if mp4_url:
                            break
                    except:
                        continue

                if mp4_url:
                    return {
                        'quality': q,
                        'label': QUALITY_LABELS.get(q, q),
                        'mp4Url': mp4_url,
                        'size': quality_info.get('size'),
                        'expiresAt': parse_mp4_expiry(mp4_url),
                        'ok': True,
                    }
                else:
                    return {
                        'quality': q,
                        'label': QUALITY_LABELS.get(q, q),
                        'mp4Url': None,
                        'size': quality_info.get('size'),
                        'expiresAt': None,
                        'ok': False,
                        'error': 'extraction failed',
                    }
            except Exception as e:
                return {
                    'quality': q,
                    'label': QUALITY_LABELS.get(q, q),
                    'mp4Url': None,
                    'size': quality_info.get('size'),
                    'expiresAt': None,
                    'ok': False,
                    'error': str(e),
                }

        # شغّل كل الاستخراجات بالتوازي
        tasks = [extract_one(qi) for qi in available]
        results = await asyncio.gather(*tasks)

        # 3. فلتر الناجحة
        valid = [r for r in results if r['ok']]

        return web.json_response({
            'ok': True,
            'fileId': file_id,
            'qualities': results,
            'validCount': len(valid),
            'totalCount': len(results),
        }, headers={'Access-Control-Allow-Origin': '*'})

    except Exception as e:
        return web.json_response(
            {'ok': False, 'error': str(e)},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'}
        )


def main():
    app = web.Application()
    app.router.add_post('/extract', handle_extract)
    app.router.add_post('/extract-all', handle_extract_all)
    app.router.add_get('/qualities', handle_qualities)
    app.router.add_get('/proxy', handle_proxy)
    app.router.add_get('/health', handle_health)
    app.router.add_route('OPTIONS', '/{tail:.*}', handle_options)
    print(f'MP4 extractor listening on :{PORT}')
    web.run_app(app, host='0.0.0.0', port=PORT, access_log=None)


if __name__ == '__main__':
    main()
