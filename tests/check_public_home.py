"""Guest-to-upload sign-in flow against an isolated local app; no platform access."""
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
with tempfile.TemporaryDirectory() as data:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    env = dict(os.environ, SHORTS_DATA_DIR=data, SHORTS_COOKIE_SECURE='0', SHORTS_ALLOW_REGISTRATION='1')
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'webapp.server:app', '--host', '127.0.0.1', '--port', str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f'http://127.0.0.1:{port}'
        for _ in range(100):
            try: urllib.request.urlopen(url); break
            except OSError: time.sleep(.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page()
            errors, private_requests, sockets = [], [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: private_requests.append(request.url) if any(path in request.url for path in ['/api/jobs', '/api/accounts', '/api/start', '/api/schedule']) else None)
            page.on('websocket', lambda ws: sockets.append(ws.url))
            page.goto(url)
            page.get_by_role('link', name='Sign in', exact=True).wait_for()
            page.wait_for_timeout(1800)
            assert page.url == url + '/'
            assert not private_requests and not sockets
            assert not page.locator('#signOut').is_visible()
            page.locator('#file').click()
            page.get_by_role('heading', name='Sign in to upload your video').wait_for()
            page.get_by_role('button', name='Keep browsing').click()
            assert page.url == url + '/'
            page.locator('#file').set_input_files({'name': 'fixture.mp4', 'mimeType': 'video/mp4', 'buffer': b'fixture'})
            page.get_by_role('heading', name='Sign in to upload your video').wait_for()
            page.wait_for_function("document.getElementById('file').files.length === 0")
            assert not private_requests
            page.get_by_role('link', name='Sign in or create account').click()
            page.get_by_role('button', name='Create an account', exact=True).click()
            page.get_by_label('Email', exact=True).fill('guest@example.com')
            page.get_by_label('Password', exact=True).fill('a private test password')
            page.get_by_role('button', name='Create account', exact=True).click()
            page.wait_for_url(url + '/#videoCard')
            page.get_by_role('button', name='Sign out', exact=True).wait_for()
            with page.expect_file_chooser() as chooser:
                page.locator('#file').click()
            chooser.value.set_files([])
            page.get_by_role('button', name='Sign out', exact=True).click()
            page.wait_for_url(url + '/')
            page.get_by_role('link', name='Sign in', exact=True).wait_for()
            assert not errors, errors
            browser.close()
        print('Passed: public home, no guest private polling, upload sign-in prompt, cancel, rejected guest file, sign-up return to upload, signed-in file chooser, logout to home.')
    finally:
        proc.terminate(); proc.wait(timeout=15)
