from contextlib import asynccontextmanager
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from webapp.browser_view import valid_input, allowed_navigation
from webapp.browser_worker import BrowserWorker
from webapp.server import create_app


class InputTests(unittest.TestCase):
    def test_rejects_navigation_shortcuts_and_bad_coordinates(self):
        for value in ({'type': 'key', 'key': 'Control+L'}, {'type': 'navigate', 'url': 'file:///etc/passwd'},
                      {'type': 'click', 'x': float('nan'), 'y': .5}, {'type': 'text', 'text': 'x' * 4097},
                      {'type': 'key', 'key': []}, [], {'type': 'scroll', 'dx': 99999, 'dy': 0}):
            self.assertFalse(valid_input(value), value)
        self.assertTrue(valid_input({'type': 'text', 'text': 'example@example.com'}))
        self.assertTrue(valid_input({'type': 'click', 'x': .5, 'y': .2}))
        for url in ('http://127.0.0.1/', 'file:///etc/passwd', 'https://google.com.evil.test/', 'chrome://settings'):
            self.assertFalse(allowed_navigation(url))
        self.assertTrue(allowed_navigation('https://accounts.google.com/signin'))

    def test_failed_start_releases_profile_and_capacity(self):
        with tempfile.TemporaryDirectory() as root:
            capacity = threading.BoundedSemaphore(1)
            worker = BrowserWorker(Path(root) / 'profile', capacity, Mock())
            with patch.dict(os.environ, {'SHORTS_CHROME_BINARY': '/nonexistent/chrome'}):
                with self.assertRaisesRegex(RuntimeError, 'not installed'):
                    worker.start()
            self.assertIsNone(worker.profile_lock)
            self.assertTrue(capacity.acquire(False))
            capacity.release()
            worker.stop()


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'SHORTS_ALLOW_REGISTRATION': '1', 'SHORTS_BROWSER_WORKERS': '1'})
        env.start(); self.addCleanup(env.stop)
        self.app = create_app(self.tmp.name, secure_cookie=False)
        self.client = TestClient(self.app)
        self.headers = {'Origin': 'http://testserver', 'X-Shorts-Request': '1'}
        response = self.client.post('/api/auth/register', headers=self.headers,
                                    json={'email': 'a@example.com', 'password': 'a long test password'})
        self.workspace = self.app.state.tenants.workspace(response.json()['id'])
        self.workspace.worker = Mock(endpoint='http://private-worker', manual_mode=False)
        self.workspace.worker.running.return_value = True
        self.page = Mock(viewport_size={'width': 1200, 'height': 780})
        self.page.is_closed.return_value = False
        for method in ('set_viewport_size', 'goto', 'close'):
            setattr(self.page, method, AsyncMock())
        self.page.screenshot = AsyncMock(return_value=b'test jpeg')
        self.page.keyboard = Mock(insert_text=AsyncMock(), press=AsyncMock())
        self.context = Mock(route=AsyncMock(), unroute=AsyncMock())
        def on(event, callback):
            self.callback = callback
        self.context.on.side_effect = on
        async def new_page():
            self.callback(self.page)
            return self.page
        self.context.new_page = new_page
        session = Mock(send=AsyncMock(), detach=AsyncMock())
        browser = Mock(contexts=[self.context], new_browser_cdp_session=AsyncMock(return_value=session))
        self.connect = AsyncMock(return_value=browser)
        @asynccontextmanager
        async def playwright():
            yield Mock(chromium=Mock(connect_over_cdp=self.connect))
        patcher = patch('webapp.browser_view.async_playwright', playwright)
        patcher.start(); self.addCleanup(patcher.stop)

    def socket(self, path='/ws/connect/youtube', origin='http://testserver'):
        return self.client.websocket_connect(path, headers={'Origin': origin})

    def test_live_view_rejects_input_and_uses_only_own_worker(self):
        self.workspace.engine.find_automation_page = AsyncMock(return_value=self.page)
        with self.socket('/ws') as ws:
            self.assertEqual(ws.receive_json()['t'], 'frame')
            ws.send_json({'type': 'text', 'text': 'must not type'})
            with self.assertRaises(WebSocketDisconnect):
                while True:
                    ws.receive_json()
        self.page.keyboard.insert_text.assert_not_awaited()
        self.assertEqual(self.connect.call_args.args[0], 'http://private-worker')

    def test_signin_locks_posting_and_logout_revokes_live_control(self):
        with self.socket() as ws:
            self.assertEqual(ws.receive_json()['t'], 'frame')
            ws.send_json({'type': 'text', 'text': 'private test input'})
            # The next frame ensures the receiver has had a chance to process the input.
            ws.receive_json()
            self.page.keyboard.insert_text.assert_awaited_with('private test input')
            self.assertEqual(self.client.post('/api/schedule', headers=self.headers).status_code, 409)
            with self.assertRaises(WebSocketDisconnect):
                with self.socket('/ws/connect/tiktok'):
                    pass
            self.client.post('/api/auth/logout', headers=self.headers)
            with self.assertRaises(WebSocketDisconnect):
                while True:
                    ws.receive_json()
        self.assertFalse(self.workspace.gate.locked())
        self.page.close.assert_awaited()

    def test_second_user_routes_to_their_own_browser(self):
        other = TestClient(self.app)
        response = other.post('/api/auth/register', headers=self.headers,
                              json={'email': 'b@example.com', 'password': 'a long test password'})
        workspace = self.app.state.tenants.workspace(response.json()['id'])
        workspace.worker = Mock(endpoint='http://other-private-worker', manual_mode=False)
        workspace.worker.running.return_value = True
        workspace.engine.find_automation_page = AsyncMock(return_value=self.page)
        with other.websocket_connect('/ws', headers={'Origin': 'http://testserver'}) as ws:
            self.assertEqual(ws.receive_json()['t'], 'frame')
        self.assertEqual(self.connect.call_args.args[0], 'http://other-private-worker')
        self.assertFalse(self.workspace.gate.locked())

    def test_origin_platform_and_busy_checks(self):
        for path, origin in (('/ws/connect/youtube', 'https://evil.test'),
                             ('/ws/connect/unknown', 'http://testserver')):
            with self.assertRaises(WebSocketDisconnect):
                with self.socket(path, origin):
                    pass
        self.workspace.engine.jobs['queued'] = {'finished': None}
        with self.assertRaises(WebSocketDisconnect):
            with self.socket():
                pass
        self.workspace.worker.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
