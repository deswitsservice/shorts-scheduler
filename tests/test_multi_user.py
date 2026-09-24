import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from webapp.server import create_app, COOKIE


class MultiUserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'SHORTS_ALLOW_REGISTRATION': '1'})
        env.start()
        self.addCleanup(env.stop)
        self.app = create_app(self.tmp.name, secure_cookie=False)
        self.a = TestClient(self.app)
        self.b = TestClient(self.app)
        self.headers = {'Origin': 'http://testserver', 'X-Shorts-Request': '1'}

    def register(self, client, email):
        response = client.post('/api/auth/register', headers=self.headers,
                               json={'email': email, 'password': 'a long test password'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def test_private_jobs_and_media(self):
        aid = self.register(self.a, 'a@example.com')
        bid = self.register(self.b, 'b@example.com')
        wa = self.app.state.tenants.workspace(aid)
        wb = self.app.state.tenants.workspace(bid)
        self.assertNotEqual(wa.engine.bot.PROFILE, wb.engine.bot.PROFILE)
        self.assertIsNot(wa.engine.jobs, wb.engine.jobs)
        wa.engine.jobs['private'] = dict(id='private', title='Private title', when='Now',
            platforms=['youtube'], dry=True, steps={}, started=None, finished=None,
            current_platform=None, thumbnail_url=None, accounts={}, created=1)
        self.assertEqual(self.a.get('/api/jobs').json()[0]['title'], 'Private title')
        file = wa.root / 'uploads' / 'private.jpg'
        file.write_bytes(b'private image')
        self.assertEqual(self.a.get('/uploads/private.jpg').content, b'private image')
        self.assertEqual(self.b.get('/uploads/private.jpg').status_code, 404)
        self.assertEqual(TestClient(self.app).get('/uploads/private.jpg').status_code, 401)
        self.assertEqual(self.b.get('/api/jobs').json(), [])
        self.assertFalse(wa.engine.bot.chrome_running())
        self.assertEqual(self.a.post('/api/start', headers=self.headers).status_code, 503)

    def test_logout_revokes_session_and_login_survives_restart(self):
        uid = self.register(self.a, 'a@example.com')
        token = self.a.cookies.get(COOKIE)
        restarted = TestClient(create_app(self.tmp.name, secure_cookie=False))
        restarted.cookies.set(COOKIE, token)
        self.assertEqual(restarted.get('/api/auth/me').json()['id'], uid)
        self.assertEqual(self.a.post('/api/auth/logout', headers=self.headers).status_code, 200)
        self.assertEqual(restarted.get('/api/auth/me').status_code, 401)
        response = self.a.post('/api/auth/login', headers=self.headers,
                               json={'email': 'A@example.com', 'password': 'a long test password'})
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(self.a.cookies.get(COOKIE), token)

    def test_csrf_and_anonymous_access(self):
        self.assertEqual(self.a.get('/api/jobs').status_code, 401)
        self.assertEqual(self.a.post('/api/auth/register', json={}).status_code, 403)
        self.assertEqual(self.a.post('/api/auth/login', headers={'Origin': 'https://evil.example',
                         'X-Shorts-Request': '1'}, json={}).status_code, 403)
        self.assertEqual(self.a.get('/', follow_redirects=False).status_code, 303)
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with self.a.websocket_connect('/ws'):
                pass

    def test_expired_session_cannot_read_private_data(self):
        self.register(self.a, 'expiry@example.com')
        with self.app.state.store.connect() as db:
            db.execute('UPDATE sessions SET expires=0')
        self.assertEqual(self.a.get('/api/jobs').status_code, 401)
        self.assertEqual(self.a.post('/api/schedule', headers=self.headers).status_code, 401)

    def test_credentials_are_hashed_and_secure_cookie_default(self):
        self.register(self.a, 'a@example.com')
        with self.app.state.store.connect() as db:
            row = db.execute('SELECT * FROM users').fetchone()
            session = db.execute('SELECT * FROM sessions').fetchone()
        self.assertNotEqual(row['password'], 'a long test password')
        self.assertNotEqual(session['token'], self.a.cookies.get(COOKIE))
        client = TestClient(create_app(self.tmp.name, secure_cookie=True))
        response = client.post('/api/auth/login', headers=self.headers,
                               json={'email': 'a@example.com', 'password': 'a long test password'})
        cookie = response.headers['set-cookie'].lower()
        for flag in ('secure', 'httponly', 'samesite=strict'):
            self.assertIn(flag, cookie)


if __name__ == '__main__':
    unittest.main()
