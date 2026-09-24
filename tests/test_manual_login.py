import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from webapp.server import create_app


class ManualLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'SHORTS_ALLOW_REGISTRATION': '1', 'SHORTS_BROWSER_WORKERS': '1',
                                     'SHORTS_LOCAL_MANUAL_LOGIN': '1'})
        env.start(); self.addCleanup(env.stop)
        platform = patch('webapp.server.sys.platform', 'darwin')
        platform.start(); self.addCleanup(platform.stop)
        self.app = create_app(self.tmp.name, secure_cookie=False)
        self.client = TestClient(self.app, base_url='http://localhost', client=('127.0.0.1', 50000))
        self.headers = {'Origin': 'http://localhost', 'X-Shorts-Request': '1'}
        response = self.client.post('/api/auth/register', headers=self.headers,
                                    json={'email': 'local@example.com', 'password': 'password-fixture'})
        self.workspace = self.app.state.tenants.workspace(response.json()['id'])
        self.workspace.worker = Mock(manual_mode=False)

    def test_start_only_accepts_fixed_platform_and_blocks_busy_jobs(self):
        self.assertEqual(self.client.post('/api/manual-login/start?platform=file:///etc/passwd', headers=self.headers).status_code, 400)
        self.workspace.worker.start_manual.assert_not_called()
        self.workspace.engine.jobs['job'] = {'finished': None}
        self.assertEqual(self.client.post('/api/manual-login/start', headers=self.headers).status_code, 409)
        self.workspace.worker.start_manual.assert_not_called()
        self.workspace.engine.jobs.clear()
        self.assertEqual(self.client.post('/api/manual-login/start', headers=self.headers).status_code, 200)
        self.workspace.worker.start_manual.assert_called_once_with('https://studio.youtube.com/')

    def test_manual_mode_never_reads_cookies_streams_or_posts(self):
        self.workspace.worker.manual_mode = True
        self.workspace.engine._accounts = Mock(side_effect=AssertionError('Must not attach Playwright'))
        result = self.client.get('/api/accounts')
        self.assertTrue(result.json()['manual_active'])
        self.assertTrue(result.json()['manual_available'])
        self.workspace.engine._accounts.assert_not_called()
        for path in ('/api/start', '/api/schedule'):
            self.assertEqual(self.client.post(path, headers=self.headers).status_code, 409)
        for path in ('/ws', '/ws/connect/youtube'):
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect(path, headers={'Origin': 'http://localhost'}):
                    pass
        self.workspace.worker.start.assert_not_called()

    def test_remote_or_disabled_manual_access_rejected(self):
        remote = TestClient(self.app, base_url='http://localhost', client=('198.51.100.5', 50000))
        remote.cookies.update(self.client.cookies)
        self.assertEqual(remote.post('/api/manual-login/start', headers=self.headers).status_code, 403)
        with patch.dict(os.environ, {'SHORTS_LOCAL_MANUAL_LOGIN': '0'}):
            self.assertEqual(self.client.post('/api/manual-login/start', headers=self.headers).status_code, 403)
        self.workspace.worker.start_manual.assert_not_called()

    def test_resume_requires_browser_to_be_closed(self):
        self.workspace.worker.finish_manual.side_effect = RuntimeError('Quit Chrome first.')
        self.assertEqual(self.client.post('/api/manual-login/finish', headers=self.headers).status_code, 409)
        self.workspace.worker.finish_manual.side_effect = None
        self.assertEqual(self.client.post('/api/manual-login/finish', headers=self.headers).status_code, 200)


if __name__ == '__main__':
    unittest.main()
