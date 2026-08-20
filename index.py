#!/usr/bin/env python3
"""
MP4 Extractor + Vidfast Extractor — Fly.io Service

Endpoints:
  GET  /health                    — Health check
  GET  /proxy?url={mp4_url}       — Proxy MP4/m3u8 from CDNs (existing)
  HEAD /proxy?url={mp4_url}       — HEAD for proxy
  POST /vidfast                   — Extract m3u8/mp4 from vidfast.vc using Playwright + stealth
  POST /vidfast-cookies           — Extract using curl_cffi (Chrome impersonation, no Playwright)

  body for /vidfast: {"url": "https://vidfast.vc/movie/1265609"}
  body for /vidfast-cookies: {"url": "https://vidfast.vc/movie/1265609"}
"""

import os
import re
import asyncio
import json
from urllib.parse import unquote, quote as url_quote, urlencode
import aiohttp
from aiohttp import web

PORT = int(os.environ.get('PORT', '8080'))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

# ─── Playwright (lazy import — only when needed) ──────────────────────────
_async_playwright = None
_Stealth = None

def _get_playwright():
    global _async_playwright
    if _async_playwright is None:
        from playwright.async_api import async_playwright as _ap
        _async_playwright = _ap
    return _async_playwright

def _get_stealth():
    global _Stealth
    if _Stealth is None:
        from playwright_stealth import Stealth as _S
        _Stealth = _S
    return _Stealth


# ─── JS hooks to capture video source URLs ─────────────────────────────────
VIDFAST_INIT_JS = r"""
window.__captured = [];
window.__sources = [];

// Hook fetch
const origFetch = window.fetch;
window.fetch = function() {
  try {
    const a = arguments;
    let u = typeof a[0] === 'string' ? a[0] : (a[0] && a[0].url) || '';
    if (u && /m3u8|mp4|mpd|playlist|stream|video|source|media|playout|playback/i.test(u))
      window.__captured.push({type:'fetch', url:u, t:Date.now()});
  } catch(e) {}
  return origFetch.apply(this, arguments);
};

// Hook XHR
const origOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(m, u) {
  try {
    if (typeof u === 'string' && /m3u8|mp4|mpd|playlist|stream|video|source|media|playout|playback/i.test(u))
      window.__captured.push({type:'xhr', method:m, url:u, t:Date.now()});
  } catch(e) {}
  return origOpen.apply(this, arguments);
};

// Hook video src
const desc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src');
if (desc && desc.set) {
  Object.defineProperty(HTMLMediaElement.prototype, 'src', {
    set: function(v) {
      try {
        if (typeof v === 'string' && v) {
          window.__captured.push({type:'media-src', url:v, t:Date.now()});
          if (v.includes('.m3u8') || v.includes('.mp4'))
            window.__sources.push(v);
        }
      } catch(e) {}
      desc.set.call(this, v);
    },
    get: function() { return desc.get.call(this); },
    configurable: true,
  });
}

// Hook source element src
const origSetAttribute = Element.prototype.setAttribute;
Element.prototype.setAttribute = function(name, value) {
  try {
    if (name === 'src' && typeof value === 'string' && (value.includes('.m3u8') || value.includes('.mp4') || value.includes('.mpd'))) {
      window.__captured.push({type:'attr-src', url:value, t:Date.now()});
      window.__sources.push(value);
    }
  } catch(e) {}
  return origSetAttribute.apply(this, arguments);
};

// Hook innerHTML to catch src= patterns
const origInnerHTML = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
if (origInnerHTML && origInnerHTML.set) {
  Object.defineProperty(Element.prototype, 'innerHTML', {
    set: function(v) {
      try {
        if (typeof v === 'string') {
          const urls = v.match(/https?:\/\/[^\s"'<>]+\.(?:m3u8|mp4|mpd)[^\s"'<>]*/gi);
          if (urls) for (const u of urls) window.__sources.push(u);
        }
      } catch(e) {}
      return origInnerHTML.set.call(this, v);
    },
    get: function() { return origInnerHTML.get.call(this); },
    configurable: true,
  });
}

console.clear = function(){};
'vidfast hooks installed';
"""


async def extract_vidfast(url: str) -> dict:
    """Extract m3u8/mp4 URLs from vidfast.vc using Playwright + stealth."""
    pw = _get_playwright()
    
    async with pw() as p:
        # Try Chromium first, then Firefox as fallback
        for browser_type, launch_args in [
            ('chromium', ['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']),
            ('firefox', ['--no-sandbox']),
        ]:
            browser = None
            try:
                launcher = getattr(p, browser_type)
                browser = await launcher.launch(headless=True, args=launch_args)
                
                context = await browser.new_context(
                    user_agent=UA,
                    viewport={'width': 1280, 'height': 800},
                    locale='en-US',
                    timezone_id='UTC',
                    extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
                )
                
                # Apply stealth (only for Chromium)
                if browser_type == 'chromium':
                    try:
                        stealth = _get_stealth()()
                        await stealth.apply_stealth_async(context)
                    except Exception:
                        pass
                
                await context.add_init_script(VIDFAST_INIT_JS)
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); "
                    "window.chrome = { runtime: {} }; "
                    "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]}); "
                    "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
                )
                
                page = await context.new_page()
                
                # Capture network requests
                captured_urls = []
                def on_request(req):
                    u = req.url
                    ul = u.lower()
                    if any(k in ul for k in ['.m3u8', '.mp4', '.mpd', 'playlist', 'stream', '/source', 'video', 'media', 'playout', 'playback']):
                        captured_urls.append({'url': u, 'method': req.method, 'type': 'network'})
                
                page.on('request', on_request)
                
                # Navigate
                resp = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                if resp and resp.status == 403:
                    # Cloudflare block — wait for challenge
                    await page.wait_for_timeout(10000)
                    # Check if challenge passed
                    title = await page.title()
                    if 'blocked' in title.lower() or 'attention' in title.lower():
                        await browser.close()
                        continue  # Try next browser type
                
                # Wait for page to render
                await page.wait_for_timeout(8000)
                
                # Try clicking play button
                try:
                    await page.evaluate("""() => {
                        const selectors = [
                            'video', '.vjs-big-play-button',
                            '[class*="play"]', '[class*="Play"]',
                            'button', '[class*="player"]'
                        ];
                        for (const sel of selectors) {
                            const els = document.querySelectorAll(sel);
                            for (const el of els) {
                                if (el.offsetParent !== null) {
                                    el.click();
                                    return 'clicked: ' + sel;
                                }
                            }
                        }
                        const v = document.querySelector('video');
                        if (v) { v.play(); return 'video.play()'; }
                        return 'no play button';
                    }""")
                except:
                    pass
                
                # Wait for video to start loading
                await page.wait_for_timeout(12000)
                
                # Get captured URLs from JS context
                try:
                    js_captured = await page.evaluate("() => window.__captured || []")
                    js_sources = await page.evaluate("() => window.__sources || []")
                except:
                    js_captured = []
                    js_sources = []
                
                # Combine all captured URLs
                all_urls = []
                seen = set()
                
                # Priority: js_sources (video src) > js_captured (fetch/xhr) > network requests
                for s in js_sources:
                    if isinstance(s, str) and s not in seen and not s.startswith('blob:'):
                        seen.add(s)
                        all_urls.append(s)
                
                for c in js_captured:
                    u = c.get('url') if isinstance(c, dict) else None
                    if u and u not in seen and not u.startswith('blob:'):
                        seen.add(u)
                        all_urls.append(u)
                
                for c in captured_urls:
                    u = c['url']
                    if u not in seen and not u.startswith('blob:'):
                        seen.add(u)
                        all_urls.append(u)
                
                # Filter to m3u8/mp4 URLs
                m3u8_urls = [u for u in all_urls if '.m3u8' in u.lower()]
                mp4_urls = [u for u in all_urls if '.mp4' in u.lower()]
                mpd_urls = [u for u in all_urls if '.mpd' in u.lower()]
                
                await browser.close()
                
                return {
                    'ok': len(m3u8_urls) + len(mp4_urls) + len(mpd_urls) > 0,
                    'url': url,
                    'browser': browser_type,
                    'sources': {
                        'm3u8': m3u8_urls,
                        'mp4': mp4_urls,
                        'mpd': mpd_urls,
                        'all': all_urls,
                    },
                    'stats': {
                        'total_captured': len(all_urls),
                        'm3u8_count': len(m3u8_urls),
                        'mp4_count': len(mp4_urls),
                        'mpd_count': len(mpd_urls),
                    },
                }
                
            except Exception as e:
                if browser:
                    try:
                        await browser.close()
                    except:
                        pass
                continue
        
        # All browser types failed
        return {'ok': False, 'error': 'All browser types failed (Cloudflare block or timeout)'}


async def extract_vidfast_curl(url: str) -> dict:
    """Extract from vidfast.vc using curl_cffi (Chrome impersonation, no Playwright).
    
    This bypasses Cloudflare but can't execute JavaScript.
    Returns the page HTML and RSC data for analysis.
    """
    try:
        from curl_cffi import requests as cffi_requests
        
        session = cffi_requests.Session(impersonate="chrome")
        r = session.get(url, timeout=25, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        
        if r.status_code != 200:
            return {'ok': False, 'error': f'HTTP {r.status_code}', 'url': url}
        
        html = r.text
        
        # Extract RSC streaming data (self.__next_f.push calls)
        # The en token is inside these calls with unicode-escaped quotes
        import re
        pushes = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html, re.DOTALL)
        
        en_token = None
        title = None
        movie_id = None
        year = None
        host = None
        server = None
        
        for p in pushes:
            try:
                decoded = p.encode().decode('unicode_escape')
            except:
                continue
            
            # Only search in chunks that have "en" AND "host" (the player component props)
            if '"en"' in decoded and '"host"' in decoded:
                en_match = re.search(r'"en":"([^"]+)"', decoded)
                if en_match:
                    en_token = en_match.group(1)
                
                for field, var in [('host', 'host'), ('id', 'movie_id'), ('title', 'title'), ('year', 'year'), ('server', 'server')]:
                    m = re.search(rf'"{field}":"([^"]*)"', decoded)
                    if m:
                        val = m.group(1)
                        if var == 'host': host = val
                        elif var == 'movie_id': movie_id = val
                        elif var == 'title': title = val
                        elif var == 'year': year = val
                        elif var == 'server': server = val
        
        # Search for m3u8/mp4 URLs in the HTML (unlikely but check anyway)
        m3u8_urls = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html, re.I)
        mp4_urls = re.findall(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', html, re.I)
        
        return {
            'ok': en_token is not None,
            'url': url,
            'method': 'curl_cffi (Chrome impersonation)',
            'meta': {
                'title': title,
                'id': movie_id,
                'year': year,
                'en_token': en_token,
                'host': host,
                'server': server if server != '$undefined' else None,
            },
            'sources': {
                'm3u8': m3u8_urls,
                'mp4': mp4_urls,
            },
            'note': 'curl_cffi bypasses Cloudflare. The en token is extracted from RSC data. '
                    'To get actual m3u8 URLs, the JS player needs to execute (use /vidfast endpoint with Playwright).'
                    if en_token else 'No en token found.',
        }
    except ImportError:
        return {'ok': False, 'error': 'curl_cffi not installed'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


# ─── HTTP Handlers ──────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    name = unquote(name).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'\s+', '.', name)
    if len(name) > 200:
        name = name[:200]
    if not name.lower().endswith('.mp4'):
        name = name + '.mp4'
    return name


async def handle_proxy(request):
    """Proxy MP4/m3u8 from CDNs with proper Referer/Origin headers."""
    target_url = request.query.get('url')
    if not target_url:
        return web.json_response({'ok': False, 'error': 'Missing "url" parameter'}, status=400)
    
    custom_filename = request.query.get('filename')
    filename = sanitize_filename(custom_filename) if custom_filename else 'video.mp4'
    
    if request.method == 'HEAD':
        ascii_fn = filename.encode('ascii', 'replace').decode('ascii').replace('?', '_')
        return web.Response(status=200, headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
            'Access-Control-Allow-Headers': 'Range',
            'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Content-Type, Content-Disposition',
            'Accept-Ranges': 'bytes',
            'Content-Type': 'video/mp4',
            'Content-Disposition': f"attachment; filename=\"{ascii_fn}\"; filename*=UTF-8''{url_quote(filename)}",
            'Cache-Control': 'no-cache',
        })
    
    try:
        session = aiohttp.ClientSession()
        try:
            headers = {'User-Agent': UA}
            
            if 'hakunaymatata.com' in target_url or 'bcdnxw' in target_url:
                headers['Referer'] = 'https://videodownloader.site/'
                headers['Origin'] = 'https://videodownloader.site'
            elif 'premilkyway.com' in target_url or 'cdn-centaurus.com' in target_url:
                headers['Referer'] = 'https://audinifer.com/'
                headers['Origin'] = 'https://audinifer.com'
            elif 'peakstorm.top' in target_url or 'hypermaple.top' in target_url:
                headers['Referer'] = 'https://player.videasy.to/'
                headers['Origin'] = 'https://player.videasy.to'
            
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
                    status=502, headers={'Access-Control-Allow-Origin': '*'}
                )
            
            ascii_filename = filename.encode('ascii', 'replace').decode('ascii').replace('?', '_')
            resp_headers = {
                'Access-Control-Allow-Origin': '*',
                'Accept-Ranges': 'bytes',
                'Content-Disposition': f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{url_quote(filename)}",
                'Cache-Control': 'no-cache, no-store, must-revalidate',
            }
            
            ct = upstream.headers.get('content-type', '')
            if 'mpegurl' in ct or '.m3u8' in target_url:
                resp_headers['Content-Type'] = 'application/vnd.apple.mpegurl'
            else:
                resp_headers['Content-Type'] = 'video/mp4'
            
            for h in ['content-length', 'content-range']:
                v = upstream.headers.get(h)
                if v:
                    resp_headers[h] = v
            
            stream_response = web.StreamResponse(status=upstream.status, headers=resp_headers)
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
        return web.json_response({'ok': False, 'error': str(e)}, status=502, headers={'Access-Control-Allow-Origin': '*'})


async def handle_vidfast(request):
    """POST /vidfast — Extract m3u8/mp4 from vidfast.vc using Playwright + stealth."""
    try:
        body = await request.json()
        url = body.get('url')
        if not url:
            return web.json_response({'ok': False, 'error': 'Missing "url" in body'}, status=400)
    except Exception as e:
        return web.json_response({'ok': False, 'error': f'Invalid JSON: {e}'}, status=400)
    
    try:
        result = await extract_vidfast(url)
        return web.json_response(result, headers={'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=500, headers={'Access-Control-Allow-Origin': '*'})


async def handle_vidfast_curl(request):
    """POST /vidfast-cookies — Extract using curl_cffi (no Playwright, lighter)."""
    try:
        body = await request.json()
        url = body.get('url')
        if not url:
            return web.json_response({'ok': False, 'error': 'Missing "url" in body'}, status=400)
    except Exception as e:
        return web.json_response({'ok': False, 'error': f'Invalid JSON: {e}'}, status=400)
    
    try:
        result = await extract_vidfast_curl(url)
        return web.json_response(result, headers={'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=500, headers={'Access-Control-Allow-Origin': '*'})


async def handle_health(request):
    return web.json_response({
        'ok': True,
        'service': 'mp4-extractor + vidfast',
        'version': '3.0.0',
        'endpoints': ['/proxy', '/vidfast', '/vidfast-cookies', '/health'],
        'playwright': True,
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
    app.router.add_post('/vidfast', handle_vidfast)
    app.router.add_post('/vidfast-cookies', handle_vidfast_curl)
    app.router.add_get('/health', handle_health)
    app.router.add_route('OPTIONS', '/{tail:.*}', handle_options)
    print(f'mp4-extractor + vidfast v3.0 listening on :{PORT}')
    web.run_app(app, host='0.0.0.0', port=PORT, access_log=None)


if __name__ == '__main__':
    main()
