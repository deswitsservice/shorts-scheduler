#!/usr/bin/env python3
"""Authenticated hosted entry point. Run with a single ASGI worker."""
from contextlib import contextmanager, asynccontextmanager
import asyncio
import hashlib
import hmac
import ipaddress
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
from starlette.websockets import WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from webapp.browser_worker import BrowserWorker
from webapp.browser_view import serve_view, SITES
HELPER_MODE = os.environ.get('SHORTS_HELPER_MODE', '0') == '1'
HELPER_ONLINE_SECONDS = 20
CLAIM_STALE_SECONDS = 300
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
                CREATE TABLE IF NOT EXISTS helpers (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                    created REAL NOT NULL, last_seen REAL, sessions TEXT NOT NULL DEFAULT '{}', revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pair_codes (
                    code TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS job_history (
                    user_id TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY (user_id, id));
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

    PAIR_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

    def create_pair_code(self, uid):
        raw = ''.join(secrets.choice(self.PAIR_ALPHABET) for _ in range(8))
        with self.connect() as db:
            db.execute('DELETE FROM pair_codes WHERE user_id=? OR expires<=?', (uid, time.time()))
            db.execute('INSERT INTO pair_codes VALUES (?,?,?)', (hashlib.sha256(raw.encode()).hexdigest(), uid, time.time() + 600))
        return raw[:4] + '-' + raw[4:]

    def redeem_pair_code(self, code, name):
        raw = ''.join(ch for ch in code.upper() if ch.isalnum())
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT user_id FROM pair_codes WHERE code=? AND expires>?', (digest, time.time())).fetchone()
            if not row:
                return None
            db.execute('DELETE FROM pair_codes WHERE code=?', (digest,))
            token, hid = secrets.token_urlsafe(32), secrets.token_hex(8)
            db.execute('INSERT INTO helpers (id,user_id,token,name,created) VALUES (?,?,?,?,?)',
                       (hid, row['user_id'], hashlib.sha256(token.encode()).hexdigest(), name[:60] or 'My computer', time.time()))
        return token

    def helper_for_token(self, token):
        if not token:
            return None
        with self.connect() as db:
            row = db.execute('SELECT * FROM helpers WHERE token=? AND revoked=0', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return dict(row) if row else None

    def touch_helper(self, hid, sessions=None):
        with self.connect() as db:
            if sessions is None:
                db.execute('UPDATE helpers SET last_seen=? WHERE id=?', (time.time(), hid))
            else:
                db.execute('UPDATE helpers SET last_seen=?, sessions=? WHERE id=?', (time.time(), json.dumps(sessions), hid))

    def helpers(self, uid):
        with self.connect() as db:
            rows = db.execute('SELECT id,name,created,last_seen,sessions FROM helpers WHERE user_id=? AND revoked=0 ORDER BY created', (uid,)).fetchall()
        return [dict(r) for r in rows]

    def revoke_helper(self, uid, hid):
        with self.connect() as db:
            return db.execute('UPDATE helpers SET revoked=1 WHERE id=? AND user_id=?', (hid, uid)).rowcount > 0

    def save_job(self, uid, job):
        body = {k: job.get(k) for k in ('id', 'title', 'when', 'platforms', 'dry', 'steps', 'accounts', 'created', 'started', 'finished',
                                        'current_platform', 'thumbnail_url', 'vid', 'helper_claim', 'cancel')}
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO job_history VALUES (?,?,?,?)', (uid, job['id'], json.dumps(body, default=str), time.time()))

    def load_jobs(self, uid):
        with self.connect() as db:
            rows = db.execute('SELECT body FROM job_history WHERE user_id=? ORDER BY updated DESC LIMIT 200', (uid,)).fetchall()
        return [json.loads(r['body']) for r in rows]

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


def helper_accounts(store, uid):
    helpers = store.helpers(uid)
    newest = max(helpers, key=lambda h: h['last_seen'] or 0, default=None)
    online = bool(newest and newest['last_seen'] and time.time() - newest['last_seen'] < HELPER_ONLINE_SECONDS)
    sessions = json.loads(newest['sessions']) if newest else {}
    return {'chrome': online, 'youtube': bool(online and sessions.get('youtube')), 'meta': bool(online and sessions.get('meta')),
            'tiktok': bool(online and sessions.get('tiktok')), 'helper_mode': True,
            'helper': {'paired': bool(helpers), 'online': online, 'name': newest['name'] if newest else None}}


class Workspace:
    """Private engine namespace: no shared jobs, bot globals, profiles or media mounts."""
    def __init__(self, root, uid, capacity, store=None):
        self.uid, self.store = uid, store
        self.root = root / 'users' / uid
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.engine = load_module('engine_' + uid, HERE / 'engine.py')
        engine = self.engine
        engine.bot = load_module('bot_' + uid, ROOT / 'bot.py')
        engine.bot.HERE = str(self.root)
        engine.bot.PROFILE = str(self.root / 'chrome_profile')
        self.gate = asyncio.Lock()
        self.posting = threading.Event()
        run_job = engine.run_job
        def posting_job(job):
            self.posting.set()
            try:
                run_job(job)
            finally:
                self.posting.clear()
                self.worker.touch()
        engine.run_job = posting_job
        if HELPER_MODE and store:
            def queue_for_helper(job):
                job['helper_claim'] = None
                store.save_job(uid, job)
            engine.run_job = queue_for_helper
            for body in reversed(store.load_jobs(uid)):
                body.setdefault('created', time.time())
                body.setdefault('vid', {})
                engine.jobs[body['id']] = body
        def ready(port):
            engine.bot.PORT = port
            engine.CDP = f'http://127.0.0.1:{port}'
        self.worker = BrowserWorker(engine.bot.PROFILE, capacity, ready)
        engine.bot.chrome_running = self.worker.running
        engine.bot.start_chrome = self.worker.start
        engine.UPLOADS = str(self.root / 'uploads')
        engine.THUMBS = str(self.root / 'shots' / 'thumbs')
        for directory in (engine.UPLOADS, engine.THUMBS):
            Path(directory).mkdir(parents=True, exist_ok=True, mode=0o700)
        engine.app.router.routes = [r for r in engine.app.routes
                                    if getattr(r, 'path', '') not in ('/uploads', '/thumbs')]
        engine.app.mount('/uploads', StaticFiles(directory=engine.UPLOADS))
        engine.app.mount('/thumbs', StaticFiles(directory=engine.THUMBS))

    def busy(self):
        return self.posting.is_set() or any(not job.get('finished') for job in self.engine.jobs.values())


class TenantRouter:
    def __init__(self, store):
        self.store = store
        self.workspaces = {}
        self.enabled = os.environ.get('SHORTS_BROWSER_WORKERS', '0') == '1'
        self.capacity = threading.BoundedSemaphore(int(os.environ.get('SHORTS_MAX_BROWSERS', '4')))
        self.lock = threading.Lock()

    def workspace(self, uid):
        with self.lock:
            if uid not in self.workspaces:
                self.workspaces[uid] = Workspace(self.store.root, uid, self.capacity, self.store)
            return self.workspaces[uid]

    def local_manual_allowed(self, conn):
        if not self.enabled or sys.platform != 'darwin' or os.environ.get('SHORTS_LOCAL_MANUAL_LOGIN') != '1':
            return False
        try:
            return (ipaddress.ip_address(conn.client.host).is_loopback
                    and conn.url.hostname in ('localhost', '127.0.0.1', '::1'))
        except (ValueError, AttributeError):
            return False

    async def __call__(self, scope, receive, send):
        from starlette.requests import HTTPConnection
        conn = HTTPConnection(scope)
        user = self.store.user(conn.cookies.get(COOKIE))
        if scope['type'] == 'websocket':
            expected = os.environ.get('SHORTS_PUBLIC_ORIGIN') or str(conn.base_url).rstrip('/').replace('ws:', 'http:', 1).replace('wss:', 'https:', 1)
            path = scope['path']
            platform = path.removeprefix('/ws/connect/') if path.startswith('/ws/connect/') else None
            if (not user or not self.enabled or conn.headers.get('origin') != expected
                    or (path != '/ws' and platform not in SITES)):
                await send({'type': 'websocket.close', 'code': 1008})
                return
            token = conn.cookies.get(COOKIE)
            workspace = self.workspace(user['id'])
            if workspace.worker.manual_mode:
                await send({'type': 'websocket.close', 'code': 1008})
                return
            await serve_view(WebSocket(scope, receive, send), workspace,
                             lambda: self.store.user(token) is not None, platform)
            return
        if not user:
            response = (FileResponse(HERE / 'static' / 'index.html') if scope['path'] == '/' and scope['method'] == 'GET'
                        else JSONResponse({'error': 'Please sign in.'}, status_code=401))
            await response(scope, receive, send)
            return
        if HELPER_MODE:
            path = scope['path']
            if path == '/api/start':
                await JSONResponse({'ok': True})(scope, receive, send)
                return
            if path == '/api/accounts':
                await JSONResponse(helper_accounts(self.store, user['id']))(scope, receive, send)
                return
            if path == '/api/schedule' and not helper_accounts(self.store, user['id'])['helper']['online']:
                await JSONResponse({'error': 'Your helper is offline. Open the Shorts Everywhere helper on your computer, then try again.'},
                                   status_code=409)(scope, receive, send)
                return
            await self.workspace(user['id']).engine.app(scope, receive, send)
            return
        if not self.enabled and scope['path'] in ('/api/start', '/api/schedule'):
            await JSONResponse({'error': 'Hosted platform connections are not configured yet. '
                                'Your account is ready; publishing will be available after a hosted worker is connected.'},
                               status_code=503)(scope, receive, send)
            return
        if not self.enabled and scope['path'] == '/api/accounts':
            await JSONResponse({'chrome': False, 'youtube': False, 'meta': False, 'tiktok': False,
                                'hosted_pending': True})(scope, receive, send)
            return
        workspace = self.workspace(user['id'])
        path = scope['path']
        if path in ('/api/manual-login/start', '/api/manual-login/finish'):
            if scope['method'] != 'POST':
                await JSONResponse({'error': 'Use POST.'}, status_code=405)(scope, receive, send)
                return
            if not self.local_manual_allowed(conn):
                await JSONResponse({'error': 'Direct sign-in is only available on the local Mac.'}, status_code=403)(scope, receive, send)
                return
            if workspace.gate.locked() or workspace.busy():
                await JSONResponse({'error': 'Close the in-app sign-in view and finish posting before direct sign-in.'}, status_code=409)(scope, receive, send)
                return
            async with workspace.gate:
                try:
                    if path.endswith('/start'):
                        # A fixed platform URL only; callers cannot navigate Chrome to arbitrary files/hosts.
                        platform = conn.query_params.get('platform', 'youtube')
                        if platform not in SITES:
                            await JSONResponse({'error': 'Unknown platform.'}, status_code=400)(scope, receive, send)
                            return
                        await asyncio.to_thread(workspace.worker.start_manual, SITES[platform])
                    else:
                        await asyncio.to_thread(workspace.worker.finish_manual)
                    await JSONResponse({'ok': True})(scope, receive, send)
                except RuntimeError as exc:
                    await JSONResponse({'error': str(exc)}, status_code=409)(scope, receive, send)
            return
        if path == '/api/accounts' and self.enabled:
            if workspace.worker.manual_mode:
                result = {'chrome': False, 'youtube': False, 'meta': False, 'tiktok': False,
                          'manual_active': True}
            else:
                try:
                    result = await asyncio.to_thread(workspace.engine._accounts)
                except Exception:
                    result = {'chrome': False, 'youtube': False, 'meta': False, 'tiktok': False}
            result['manual_available'] = self.local_manual_allowed(conn)
            await JSONResponse(result)(scope, receive, send)
            return
        if path in ('/api/start', '/api/schedule'):
            if workspace.gate.locked() or workspace.worker.manual_mode:
                await JSONResponse({'error': 'Finish connecting your account before posting.'}, status_code=409)(scope, receive, send)
                return
            async with workspace.gate:
                try:
                    await asyncio.to_thread(workspace.worker.start)
                except Exception as exc:
                    await JSONResponse({'error': str(exc)}, status_code=503)(scope, receive, send)
                    return
                await workspace.engine.app(scope, receive, send)
            return
        await workspace.engine.app(scope, receive, send)


def create_app(data_dir=None, secure_cookie=None):
    store = Store(data_dir or os.environ.get('SHORTS_DATA_DIR', ROOT / 'private_data'))
    secure = secure_cookie if secure_cookie is not None else os.environ.get('SHORTS_COOKIE_SECURE', '1') == '1'
    tenants = TenantRouter(store)

    @asynccontextmanager
    async def lifespan(app):
        async def reap_idle():
            while True:
                await asyncio.sleep(30)
                for workspace in list(tenants.workspaces.values()):
                    if (not workspace.gate.locked() and not workspace.busy()
                            and time.monotonic() - workspace.worker.last_used > 1800):
                        async with workspace.gate:
                            await asyncio.to_thread(workspace.worker.stop)
        reaper = asyncio.create_task(reap_idle())
        try:
            yield
        finally:
            reaper.cancel()
            await asyncio.gather(reaper, return_exceptions=True)
            for workspace in tenants.workspaces.values():
                await asyncio.to_thread(workspace.engine.executor.shutdown, wait=True)
                await asyncio.to_thread(workspace.worker.stop)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.tenants = tenants

    @app.middleware('http')
    async def protect(request, call_next):
        agent = request.url.path.startswith('/api/helper-agent/') or request.url.path == '/api/helper/pair'
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not agent:
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
            if not isinstance(password, str) or not 8 <= len(password) <= 128 or len(email) > 254 or '@' not in email:
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            return JSONResponse({'error': 'Enter an email and a password of 8–128 characters.'}, status_code=400)
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


    def session_user(request):
        return store.user(request.cookies.get(COOKIE))

    @app.post('/api/helper/pair-code')
    def helper_pair_code(request: Request):
        user = session_user(request)
        if not user:
            return JSONResponse({'error': 'Please sign in.'}, status_code=401)
        return {'code': store.create_pair_code(user['id']), 'expires_in': 600}

    @app.get('/api/helper/list')
    def helper_list(request: Request):
        user = session_user(request)
        if not user:
            return JSONResponse({'error': 'Please sign in.'}, status_code=401)
        return [{'id': h['id'], 'name': h['name'], 'last_seen': h['last_seen']} for h in store.helpers(user['id'])]

    @app.post('/api/helper/revoke/{hid}')
    def helper_revoke(hid: str, request: Request):
        user = session_user(request)
        if not user:
            return JSONResponse({'error': 'Please sign in.'}, status_code=401)
        return {'ok': store.revoke_helper(user['id'], hid)}

    @app.post('/api/helper/pair')
    async def helper_pair(request: Request):
        ip = request.client.host if request.client else 'unknown'
        if not store.allow_attempt('pair:' + ip):
            return JSONResponse({'error': 'Too many attempts. Try again in 15 minutes.'}, status_code=429)
        try:
            data = await request.json()
            code, name = str(data['code']), str(data.get('name', 'My computer'))
        except (ValueError, KeyError, TypeError):
            return JSONResponse({'error': 'Send the pairing code.'}, status_code=400)
        token = store.redeem_pair_code(code, name)
        if not token:
            return JSONResponse({'error': 'That code is wrong or has expired. Get a new one from the website.'}, status_code=400)
        return {'token': token}

    def agent(request: Request):
        header = request.headers.get('authorization', '')
        helper = store.helper_for_token(header[7:] if header.lower().startswith('bearer ') else '')
        return helper

    def job_payload(job):
        vid = job['vid']
        keep = ('title', 'description', 'schedule', 'publish_now', 'made_for_kids', 'story', 'thumb_title',
                'youtube_channel', 'instagram_account', 'facebook_account')
        return {'id': job['id'], 'dry': job['dry'], 'platforms': [p for p in job['platforms']
                if not (job['steps'][p]['state'] == 'done' or job['steps'][p]['state'].startswith('checked'))],
                'vid': {k: vid.get(k) for k in keep}, 'has_thumbnail': bool(vid.get('thumbnail'))}

    @app.post('/api/helper-agent/poll')
    async def agent_poll(request: Request):
        helper = agent(request)
        if not helper:
            return JSONResponse({'error': 'Helper not recognised. Pair it again.'}, status_code=401)
        try:
            sessions = (await request.json()).get('sessions')
        except ValueError:
            sessions = None
        clean = {k: bool(sessions.get(k)) for k in ('chrome', 'youtube', 'meta', 'tiktok')} if isinstance(sessions, dict) else None
        store.touch_helper(helper['id'], clean)
        engine = tenants.workspace(helper['user_id']).engine
        now = time.time()
        for job in sorted(engine.jobs.values(), key=lambda j: j.get('created') or 0):
            claim = job.get('helper_claim')
            if job.get('finished') or (claim and now - claim['t'] < CLAIM_STALE_SECONDS):
                continue
            if not job_payload(job)['platforms']:
                continue
            job['helper_claim'] = {'id': helper['id'], 't': now}
            store.save_job(helper['user_id'], job)
            return {'job': job_payload(job)}
        return {'job': None}

    def claimed_job(request, jid):
        helper = agent(request)
        if not helper:
            return None, None, JSONResponse({'error': 'Helper not recognised. Pair it again.'}, status_code=401)
        job = tenants.workspace(helper['user_id']).engine.jobs.get(jid)
        claim = job.get('helper_claim') if job else None
        if not job or not claim or claim['id'] != helper['id']:
            return None, None, JSONResponse({'error': 'This job is not assigned to your helper.'}, status_code=404)
        return helper, job, None

    @app.get('/api/helper-agent/jobs/{jid}/video')
    def agent_video(jid: str, request: Request):
        helper, job, err = claimed_job(request, jid)
        return err or FileResponse(job['vid']['file'], media_type='video/mp4')

    @app.get('/api/helper-agent/jobs/{jid}/thumbnail')
    def agent_thumbnail(jid: str, request: Request):
        helper, job, err = claimed_job(request, jid)
        if err:
            return err
        path = job['vid'].get('thumbnail')
        return FileResponse(path) if path and os.path.exists(path) else JSONResponse({'error': 'No thumbnail.'}, status_code=404)

    @app.post('/api/helper-agent/jobs/{jid}/sync')
    async def agent_sync(jid: str, request: Request):
        helper, job, err = claimed_job(request, jid)
        if err:
            return err
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({'error': 'Bad request.'}, status_code=400)
        allowed = {'queued', 'running', 'done', 'failed', 'checked (dry run)'}
        for plat, step in (data.get('steps') or {}).items():
            if plat not in job['steps'] or not isinstance(step, dict) or step.get('state') not in allowed:
                continue
            target = job['steps'][plat]
            target['state'] = step['state']
            target['error'] = str(step['error'])[:400] if step.get('error') else None
            target['detail'] = str(step.get('detail', ''))[:300]
            target['attempt'] = int(step.get('attempt') or 0)
            target['log'] = [{'t': float(e.get('t', now_ts())), 'msg': str(e.get('msg', ''))[:500]}
                             for e in (step.get('log') or [])[-300:] if isinstance(e, dict)]
        cur = data.get('current_platform')
        job['current_platform'] = cur if cur in job['steps'] else None
        job['started'] = job.get('started') or time.time()
        if isinstance(data.get('accounts'), dict):
            job['accounts'].update({str(k)[:20]: str(v)[:120] for k, v in data['accounts'].items()})
        job['finished'] = time.time() if data.get('finished') else None
        job['helper_claim']['t'] = time.time()
        store.save_job(helper['user_id'], job)
        return {'ok': True, 'cancel': bool(job.get('cancel'))}

    def now_ts():
        return time.time()

    app.mount('/static', StaticFiles(directory=HERE / 'static'))
    app.mount('/', tenants)
    return app


app = create_app()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8000)
