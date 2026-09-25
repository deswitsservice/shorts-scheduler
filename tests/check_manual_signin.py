"""Local fixture: Chrome without CDP receives a cookie, then automation reuses its profile."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from webapp.browser_worker import BrowserWorker

if sys.platform != 'darwin':
    raise SystemExit('This fixture tests the local macOS manual sign-in option.')
visited = threading.Event()
class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<h1>Local direct sign-in fixture</h1><p>No account or credentials are used in this test.</p>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Set-Cookie', 'manual_fixture=preserved; Max-Age=3600; Path=/; HttpOnly')
        self.end_headers()
        self.wfile.write(body)
        visited.set()
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix='shorts-manual-test-') as directory:
        worker = BrowserWorker(Path(directory) / 'profile', threading.BoundedSemaphore(1), lambda port: None)
        try:
            worker.start()
            worker.start_manual(f'http://127.0.0.1:{server.server_port}/')
            assert worker.manual_mode and not worker.running()
            assert worker.endpoint is None and worker.port is None
            assert not any('remote-debugging' in arg for arg in worker.process.args)
            assert not (worker.profile / 'DevToolsActivePort').exists()
            assert visited.wait(15), 'The normal browser did not reach the local fixture'
            try:
                worker.finish_manual()
                raise AssertionError('Allowed automation before the manual browser quit')
            except RuntimeError as error:
                assert 'Command-Q' in str(error)
            worker.process.terminate()  # Test-owned process only; simulate ending manual sign-in.
            worker.process.wait(timeout=15)
            worker.finish_manual()
            assert worker.running() and not worker.manual_mode
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(worker.endpoint, no_defaults=True)
                assert any(cookie['name'] == 'manual_fixture' and cookie['value'] == 'preserved'
                           for cookie in browser.contexts[0].cookies())
            print('Passed: automation closed, manual Chrome has no CDP, early resume rejected, cookie preserved on automation resume.')
        finally:
            worker.stop()
finally:
    server.shutdown(); server.server_close()
