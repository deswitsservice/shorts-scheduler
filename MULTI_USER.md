# Hosted account foundation

Run `python3 webapp/server.py` or `uvicorn webapp.server:app --workers 1`.
The public entry point now requires an app account. `webapp/engine.py` is an internal automation engine; do not expose it as an ASGI application.

For local testing on this computer:

```sh
SHORTS_COOKIE_SECURE=0 SHORTS_ALLOW_REGISTRATION=1 python3 webapp/server.py
```

Open http://localhost:8000, create an account, then use another browser profile to create a second account. Sign-out invalidates the session on the server. App accounts and platform accounts are separate.

For an HTTPS deployment, leave secure cookies enabled and set `SHORTS_PUBLIC_ORIGIN` to the exact public origin, such as `https://shorts.example.com`. Keep registration closed (the default) unless intentionally accepting new accounts. Use `SHORTS_DATA_DIR` for a persistent private volume accessible only to the service account. It contains the SQLite account database and user workspaces. Back it up securely; never serve this directory or commit it. Terminate TLS at the reverse proxy and trust forwarded headers only from that proxy.

Implemented:

- Email/password registration and sign-in; scrypt password hashes with individual salts.
- Opaque server-side sessions, hashed session tokens, seven-day expiry, logout revocation, HttpOnly/Secure/SameSite cookies.
- Origin checks and a required custom header on mutations; persistent login/registration attempt limits.
- Authenticated routing to separate user media directories, jobs, automation module state, and reserved browser profile paths.
- No access to the pre-existing desktop Chrome profile. Existing desktop files and sessions are not migrated.

This is an account foundation, **not a production-ready hosted publisher**. Browser start, publishing and live screenshots are explicitly unavailable until a tenant-aware hosted connection provider is integrated. The macOS desktop launcher cannot connect remote users safely. The UI identifies this limitation; publishing requests return 503 before accepting media or starting automation.

Remaining work before hosted publishing: choose official platform authorization or private regular-Chrome workers, implement platform connection/sign-in and per-user credential storage, integrate durable job execution and live progress, handle token/session expiry, and add operational limits. Before public account launch, add email verification, password recovery, account deletion and deployment monitoring. SQLite and in-memory runtime objects currently require a single application process; multiple replicas need shared storage and a worker queue.

Tests: `python3 -m unittest discover -s tests -p 'test_*.py'` and `python3 tests/check_ui.py`. These use local fixtures/mocks; they do not publish to real platforms.
