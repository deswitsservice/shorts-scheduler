"""Real regular-Chrome lifecycle fixture. Temporary profiles only; no platform login or posting."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import threading
import time
from playwright.sync_api import sync_playwright
from webapp.browser_worker import BrowserWorker

with tempfile.TemporaryDirectory(prefix='shorts-worker-test-') as root:
    capacity = threading.BoundedSemaphore(2)
    ports = {}
    a = BrowserWorker(Path(root) / 'a' / 'profile', capacity, lambda port: ports.update(a=port))
    b = BrowserWorker(Path(root) / 'b' / 'profile', capacity, lambda port: ports.update(b=port))
    try:
        a.start(); b.start()
        assert ports['a'] != ports['b']
        with sync_playwright() as p:
            ba = p.chromium.connect_over_cdp(a.endpoint, no_defaults=True)
            bb = p.chromium.connect_over_cdp(b.endpoint, no_defaults=True)
            ca, cb = ba.contexts[0], bb.contexts[0]
            ca.add_cookies([{'name': 'fixture_session', 'value': 'user-a-only', 'domain': 'example.test',
                             'path': '/', 'expires': time.time() + 86400, 'secure': True, 'httpOnly': True}])
            assert not any(c['name'] == 'fixture_session' for c in cb.cookies())
            page = ca.pages[0]
            page.set_content('<label>Name<input id="name"></label><p id="result"></p>')
            page.locator('#name').fill('private input')
            assert page.locator('#name').input_value() == 'private input'
            assert len(page.screenshot(type='jpeg')) > 100
        a.stop()
        a.start()
        with sync_playwright() as p:
            ba = p.chromium.connect_over_cdp(a.endpoint, no_defaults=True)
            assert any(c['name'] == 'fixture_session' and c['value'] == 'user-a-only'
                       for c in ba.contexts[0].cookies()), 'Saved session did not survive restart'
        print('Passed: separate headed Chrome profiles and ports, isolated cookies, input, screenshots, saved session after restart.')
    finally:
        a.stop(); b.stop()
    assert capacity.acquire(False) and capacity.acquire(False), 'Browser capacity leaked'
