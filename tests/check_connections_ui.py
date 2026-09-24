"""End-to-end remote sign-in UI with real headed worker and local page only."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='shorts-connection-ui-') as data:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    env = dict(os.environ, SHORTS_DATA_DIR=data, SHORTS_COOKIE_SECURE='0',
               SHORTS_ALLOW_REGISTRATION='1', SHORTS_BROWSER_WORKERS='1')
    code = ("from webapp.server import app; from webapp.browser_view import SITES; "
            "SITES['youtube']='about:blank#signin-fixture'; import uvicorn; "
            f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')")
    proc = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        url = f'http://127.0.0.1:{port}'
        for _ in range(100):
            try:
                urllib.request.urlopen(url + '/login'); break
            except OSError:
                time.sleep(.1)
        with sync_playwright() as p:
            # The test driver is headless; the application's private worker uses regular Chrome.
            driver = p.chromium.launch(channel='chrome', headless=True)
            page = driver.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.get_by_role('link', name='Sign in', exact=True).click()
            page.get_by_role('button', name='Create an account', exact=True).click()
            page.get_by_label('Email', exact=True).fill('fixture@example.com')
            page.get_by_label('Password', exact=True).fill('a private test password')
            page.get_by_role('button', name='Create account', exact=True).click()
            page.wait_for_url(url + '/')
            page.get_by_role('button', name='Connect YouTube', exact=True).click()
            page.get_by_text('Click the page to sign in.', exact=False).wait_for(timeout=45000)
            marker = next(Path(data).glob('users/*/chrome_profile/DevToolsActivePort'))
            endpoint = 'http://127.0.0.1:' + marker.read_text().splitlines()[0]
            worker = p.chromium.connect_over_cdp(endpoint, no_defaults=True)
            remote = next(p for p in worker.contexts[0].pages if p.url == 'about:blank#signin-fixture')
            remote.set_content('<label>Fixture name<input id="field" style="margin:40px;width:300px;height:40px"></label>')
            remote_box = remote.locator('#field').bounding_box()
            canvas = page.locator('#connectionScreen')
            box = canvas.bounding_box()
            page.mouse.click(box['x'] + (remote_box['x'] + 20) / 1200 * box['width'],
                             box['y'] + (remote_box['y'] + 20) / 780 * box['height'])
            page.keyboard.type('remote typing')
            remote.wait_for_function("document.querySelector('#field').value === 'remote typing'", timeout=10000)
            page.get_by_label('Type into selected field', exact=True).fill(' plus mobile')
            page.get_by_role('button', name='Send text', exact=True).click()
            remote.wait_for_function("document.querySelector('#field').value === 'remote typing plus mobile'")
            page.get_by_role('button', name='Done / Close', exact=True).click()
            page.wait_for_function("!document.querySelector('#connectionDialog').open")
            assert page.locator('#connectionText').input_value() == ''
            page.get_by_role('button', name='Sign out', exact=True).click()
            page.wait_for_url(url + '/')
            assert not errors, errors
            driver.close()
        print('Passed: app sign-up, private Chrome connection, real remote pointer/keyboard input, mobile text entry, close cleanup, logout; no JS errors.')
    finally:
        proc.terminate()
        try:
            _, stderr = proc.communicate(timeout=35)
        except subprocess.TimeoutExpired:
            proc.kill(); _, stderr = proc.communicate()
        if proc.returncode not in (0, -15):
            print(stderr.decode()[-2000:])
