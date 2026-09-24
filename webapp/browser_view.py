"""Authenticated remote sign-in and read-only automation views; no credential logging."""
import asyncio
import base64
import json
import math
from urllib.parse import urlparse

from playwright.async_api import async_playwright
from starlette.websockets import WebSocket, WebSocketDisconnect

SITES = {'youtube': 'https://studio.youtube.com/',
         'instagram': 'https://business.facebook.com/',
         'facebook': 'https://business.facebook.com/',
         'tiktok': 'https://www.tiktok.com/tiktokstudio'}
LOGIN_DOMAINS = ('youtube.com', 'google.com', 'facebook.com', 'instagram.com', 'tiktok.com')
KEYS = {'Enter', 'Tab', 'Shift+Tab', 'Backspace', 'Delete', 'Escape',
        'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'ControlOrMeta+A'}


def allowed_navigation(url):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    host = parsed.hostname or ''
    return url == 'about:blank' or (parsed.scheme == 'https' and port in (None, 443)
        and any(host == d or host.endswith('.' + d) for d in LOGIN_DOMAINS))


def valid_input(data):
    if not isinstance(data, dict):
        return False
    kind = data.get('type')
    if not isinstance(kind, str):
        return False
    if kind == 'text':
        return isinstance(data.get('text'), str) and 0 < len(data['text']) <= 4096
    if kind == 'key':
        return isinstance(data.get('key'), str) and data['key'] in KEYS
    if kind in ('click', 'down', 'up', 'move'):
        return all(isinstance(data.get(k), (float, int)) and not isinstance(data[k], bool)
                   and math.isfinite(data[k]) and 0 <= data[k] <= 1 for k in ('x', 'y'))
    if kind == 'scroll':
        return all(isinstance(data.get(k), (float, int)) and not isinstance(data[k], bool)
                   and math.isfinite(data[k]) and abs(data[k]) <= 1500 for k in ('dx', 'dy'))
    return False


async def apply_input(page, data):
    kind = data['type']
    if kind == 'text':
        await page.keyboard.insert_text(data['text'])
    elif kind == 'key':
        await page.keyboard.press(data['key'])
    elif kind == 'scroll':
        await page.mouse.wheel(data['dx'], data['dy'])
    else:
        size = page.viewport_size or {'width': 1200, 'height': 780}
        x, y = data['x'] * (size['width'] - 1), data['y'] * (size['height'] - 1)
        await page.mouse.move(x, y)
        if kind == 'click':
            await page.mouse.click(x, y)
        elif kind == 'down':
            await page.mouse.down()
        elif kind == 'up':
            await page.mouse.up()


async def serve_view(ws: WebSocket, workspace, authorized, platform=None):
    interactive = platform is not None
    if interactive and (platform not in SITES or workspace.gate.locked() or workspace.busy()):
        await ws.close(code=1008, reason='Finish the current sign-in or posting job first.')
        return
    if interactive:
        await workspace.gate.acquire()
    pages = []
    tasks = []
    try:
        await ws.accept()
        if interactive:
            await asyncio.to_thread(workspace.worker.start)
        if not authorized():
            await ws.close(code=1008)
            return
        if not workspace.worker.running():
            await ws.send_json({'t': 'no-browser'})
            return
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(workspace.worker.endpoint, no_defaults=True, timeout=10000)
            ctx = browser.contexts[0]
            active = None
            route_handler = None
            page_handler = None
            try:
                if interactive:
                    workspace.engine.bot.ACTIVE_TARGET_ID = None
                    async def restrict_navigation(route):
                        if route.request.is_navigation_request() and not allowed_navigation(route.request.url):
                            await route.abort()
                        else:
                            await route.continue_()
                    route_handler = restrict_navigation
                    await ctx.route('**/*', route_handler)
                    session = await browser.new_browser_cdp_session()
                    await session.send('Browser.setDownloadBehavior', {'behavior': 'deny'})
                    await session.detach()
                    def track(page):
                        pages.append(page)
                    page_handler = track
                    ctx.on('page', page_handler)
                    active = await ctx.new_page()
                    await active.set_viewport_size({'width': 1200, 'height': 780})
                    # Start streaming before navigation so even a slow sign-in page can be closed.
                    async def navigate():
                        try:
                            await active.goto(SITES[platform], wait_until='domcontentloaded', timeout=45000)
                        except Exception:
                            if authorized():
                                await ws.send_json({'t': 'notice', 'message': 'The sign-in page is slow or unavailable. Close and reconnect if it does not load.'})
                    tasks.append(asyncio.create_task(navigate()))

                def current_page():
                    return next((page for page in reversed(pages) if not page.is_closed()), None)

                async def screenshots():
                    while authorized():
                        page = current_page() if interactive else None
                        if not interactive and not workspace.gate.locked():
                            page = await workspace.engine.find_automation_page(ctx, workspace.engine.bot.ACTIVE_TARGET_ID)
                        if page is None:
                            await ws.send_json({'t': 'idle'})
                        else:
                            try:
                                if interactive:
                                    await page.set_viewport_size({'width': 1200, 'height': 780})
                                frame = await page.screenshot(type='jpeg', quality=65, timeout=4000)
                                if authorized() and (interactive or not workspace.gate.locked()):
                                    await ws.send_json({'t': 'frame', 'd': base64.b64encode(frame).decode(),
                                                        'w': 1200, 'h': 780})
                            except Exception:
                                if authorized():
                                    await ws.send_json({'t': 'waiting'})
                        if interactive or workspace.busy():
                            workspace.worker.touch()
                        await asyncio.sleep(.5)

                async def receive_input():
                    window, count = asyncio.get_running_loop().time(), 0
                    while authorized():
                        raw = await ws.receive_text()
                        if not authorized():
                            return
                        now = asyncio.get_running_loop().time()
                        if now - window >= 1:
                            window, count = now, 0
                        count += 1
                        if len(raw) > 20000 or count > 100 or not interactive:
                            return
                        data = json.loads(raw)
                        if not valid_input(data):
                            return
                        page = current_page()
                        if page:
                            try:
                                await apply_input(page, data)
                            except Exception:
                                pass  # Page navigation can interrupt a keystroke; never log its payload.

                async def check_session():
                    deadline = asyncio.get_running_loop().time() + 900
                    while authorized() and (not interactive or asyncio.get_running_loop().time() < deadline):
                        await asyncio.sleep(.5)

                running = [asyncio.create_task(screenshots()), asyncio.create_task(receive_input()),
                           asyncio.create_task(check_session())]
                tasks.extend(running)
                await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if page_handler:
                    ctx.remove_listener('page', page_handler)
                for page in pages:
                    try:
                        await page.close()
                    except Exception:
                        pass
                if route_handler:
                    await ctx.unroute('**/*', route_handler)
    except WebSocketDisconnect:
        pass
    except Exception:
        if authorized():
            try:
                await ws.send_json({'t': 'error', 'message': 'Could not open your private browser. Check the browser setup and reconnect.'})
            except Exception:
                pass
    finally:
        if interactive:
            workspace.gate.release()
        try:
            await ws.close()
        except Exception:
            pass
