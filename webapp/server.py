#!/usr/bin/env python3
"""Authenticated hosted entry point. Run with a single ASGI worker."""
from contextlib import contextmanager
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
COOKIE = 'shorts_session'
TTL = 7 * 24 * 3600


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'accounts.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                    salt TEXT NOT NULL, password TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    key TEXT PRIMARY KEY, count INTEGER NOT NULL, reset REAL NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def user(self, token):
        if not token:
            return None
        with self.connect() as db:
            row = db.execute('SELECT users.id, users.email FROM sessions JOIN users '
                             'ON users.id=sessions.user_id WHERE token=? AND expires>?',
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return dict(row) if row else None

    def allow_attempt(self, key):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE key=?', (key,)).fetchone()
            if row and row['reset'] > now and row['count'] >= 10:
                return False
            count = row['count'] + 1 if row and row['reset'] > now else 1
            reset = row['reset'] if row and row['reset'] > now else now + 900
            db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?)', (key, count, reset))
        return True


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


class Workspace:
    """Private engine namespace: no shared jobs, bot globals, profiles or media mounts."""
    def __init__(self, root, uid):
        self.root = root / 'users' / uid
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.engine = load_module('engine_' + uid, HERE / 'engine.py')
        engine = self.engine
        engine.bot = load_module('bot_' + uid, ROOT / 'bot.py')
        engine.bot.HERE = str(self.root)
        engine.bot.PROFILE = str(self.root / 'chrome_profile')
        # Hosted browser workers are intentionally unavailable until explicitly integrated.
        # Never fall back to the desktop CDP endpoint or another user's browser.
        engine.bot.chrome_running = lambda: False
        engine.bot.start_chrome = self.unavailable
        engine.UPLOADS = str(self.root / 'uploads')
        engine.THUMBS = str(self.root / 'shots' / 'thumbs')
        for directory in (engine.UPLOADS, engine.THUMBS):
            Path(directory).mkdir(parents=True, exist_ok=True, mode=0o700)
        engine.app.router.routes = [r for r in engine.app.routes
                                    if getattr(r, 'path', '') not in ('/uploads', '/thumbs')]
        engine.app.mount('/uploads', StaticFiles(directory=engine.UPLOADS))
        engine.app.mount('/thumbs', StaticFiles(directory=engine.THUMBS))

    @staticmethod
    def unavailable(*args, **kwargs):
        raise RuntimeError('Hosted platform connections are not configured yet.')


class TenantRouter:
    def __init__(self, store):
        self.store = store
        self.workspaces = {}
        self.lock = threading.Lock()

    def workspace(self, uid):
        with self.lock:
            if uid not in self.workspaces:
                self.workspaces[uid] = Workspace(self.store.root, uid)
            return self.workspaces[uid]

    async def __call__(self, scope, receive, send):
        from starlette.requests import HTTPConnection
        conn = HTTPConnection(scope)
        user = self.store.user(conn.cookies.get(COOKIE))
        if scope['type'] == 'websocket':
            # Live browser streams are disabled until a tenant-aware worker is connected.
            await send({'type': 'websocket.close', 'code': 1008})
            return
        if not user:
            response = (RedirectResponse('/login', status_code=303) if scope['path'] == '/'
                        else JSONResponse({'error': 'Please sign in.'}, status_code=401))
            await response(scope, receive, send)
            return
        if scope['path'] in ('/api/start', '/api/schedule'):
            await JSONResponse({'error': 'Hosted platform connections are not configured yet. '
                                'Your account is ready; publishing will be available after a hosted worker is connected.'},
                               status_code=503)(scope, receive, send)
            return
        if scope['path'] == '/api/accounts':
            await JSONResponse({'chrome': False, 'youtube': False, 'meta': False, 'tiktok': False,
                                'hosted_pending': True})(scope, receive, send)
            return
        workspace = self.workspace(user['id'])
        await workspace.engine.app(scope, receive, send)


def create_app(data_dir=None, secure_cookie=None):
    store = Store(data_dir or os.environ.get('SHORTS_DATA_DIR', ROOT / 'private_data'))
    secure = secure_cookie if secure_cookie is not None else os.environ.get('SHORTS_COOKIE_SECURE', '1') == '1'
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    tenants = TenantRouter(store)
    app.state.tenants = tenants

    @app.middleware('http')
    async def protect(request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            expected = os.environ.get('SHORTS_PUBLIC_ORIGIN') or str(request.base_url).rstrip('/')
            if origin != expected or request.headers.get('x-shorts-request') != '1':
                return JSONResponse({'error': 'Invalid request origin.'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.get('/login')
    def login_page():
        return FileResponse(HERE / 'static' / 'login.html')

    @app.get('/api/auth/me')
    def me(request: Request):
        user = store.user(request.cookies.get(COOKIE))
        return user if user else JSONResponse({'error': 'Please sign in.'}, status_code=401)

    @app.post('/api/auth/{action}')
    async def authenticate(action: str, request: Request):
        if action == 'logout':
            token = request.cookies.get(COOKIE, '')
            with store.connect() as db:
                db.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(token.encode()).hexdigest(),))
            response = JSONResponse({'ok': True})
            response.delete_cookie(COOKIE, path='/')
            return response
        if action not in ('login', 'register'):
            return JSONResponse({'error': 'Not found.'}, status_code=404)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                return JSONResponse({'error': 'Request too large.'}, status_code=413)
        try:
            data = json.loads(body)
            email, password = data['email'].strip().lower(), data['password']
            if not isinstance(password, str) or not 12 <= len(password) <= 128 or len(email) > 254 or '@' not in email:
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            return JSONResponse({'error': 'Enter an email and a password of 12–128 characters.'}, status_code=400)
        ip = request.client.host if request.client else 'unknown'
        if not store.allow_attempt('ip:' + ip) or not store.allow_attempt('email:' + email):
            return JSONResponse({'error': 'Too many attempts. Try again in 15 minutes.'}, status_code=429)
        with store.connect() as db:
            if action == 'register':
                if os.environ.get('SHORTS_ALLOW_REGISTRATION', '0') != '1':
                    return JSONResponse({'error': 'Registration is currently closed.'}, status_code=403)
                uid, salt = secrets.token_hex(16), secrets.token_hex(16)
                try:
                    db.execute('INSERT INTO users VALUES (?,?,?,?)', (uid, email, salt, password_hash(password, salt)))
                except sqlite3.IntegrityError:
                    return JSONResponse({'error': 'Unable to create this account. Try signing in.'}, status_code=400)
            else:
                user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
                candidate = password_hash(password, user['salt'] if user else '00' * 16)
                if not user or not hmac.compare_digest(candidate, user['password']):
                    return JSONResponse({'error': 'Email or password is incorrect.'}, status_code=401)
                uid = user['id']
            token = secrets.token_urlsafe(32)
            old = request.cookies.get(COOKIE, '')
            db.execute('DELETE FROM sessions WHERE expires<=? OR token=?',
                       (time.time(), hashlib.sha256(old.encode()).hexdigest()))
            db.execute('INSERT INTO sessions VALUES (?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), uid, time.time() + TTL))
        response = JSONResponse({'id': uid, 'email': email})
        response.set_cookie(COOKIE, token, max_age=TTL, httponly=True, secure=secure, samesite='strict', path='/')
        return response

    app.mount('/static', StaticFiles(directory=HERE / 'static'))
    app.mount('/', tenants)
    return app


app = create_app()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8000)
