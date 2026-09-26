# Hosted accounts and private browser workers

Run `python3 webapp/server.py` or `uvicorn webapp.server:app --workers 1`.
The home page is public. Upload attempts prompt for sign-in; private APIs, media and browser views require an app account. `webapp/engine.py` is an internal automation engine; do not expose it as an ASGI application.

For local testing on this computer:

```sh
SHORTS_COOKIE_SECURE=0 SHORTS_ALLOW_REGISTRATION=1 SHORTS_BROWSER_WORKERS=1 python3 webapp/server.py
```

Open http://localhost:8000 to browse the home page. Click Upload Video and sign in or create an account, then use another browser profile to create a second account. Sign-out invalidates the session on the server. App accounts and platform accounts are separate.

For an HTTPS deployment, leave secure cookies enabled and set `SHORTS_PUBLIC_ORIGIN` to the exact public origin, such as `https://shorts.example.com`. Keep registration closed (the default) unless intentionally accepting new accounts. Use `SHORTS_DATA_DIR` for a persistent private volume accessible only to the service account. It contains the SQLite account database and user workspaces. Back it up securely; never serve this directory or commit it. Terminate TLS at the reverse proxy and trust forwarded headers only from that proxy.

Implemented:

- Email/password registration and sign-in; scrypt password hashes with individual salts.
- Opaque server-side sessions, hashed session tokens, seven-day expiry, logout revocation, HttpOnly/Secure/SameSite cookies.
- Origin checks and a required custom header on mutations; persistent login/registration attempt limits.
- Authenticated routing to separate user media directories, jobs, automation module state, and saved browser profiles.
- No access to the pre-existing desktop Chrome profile. Existing desktop files and sessions are not migrated.

## Connect platforms without upload APIs

Enable workers with `SHORTS_BROWSER_WORKERS=1`. Each app user receives a separate regular Chrome process, profile directory, dynamic loopback-only debugging port, and serialized posting executor. The existing browser automation performs uploads; no platform upload APIs or developer credentials are used.

Select **Connect YouTube / Instagram / Facebook / TikTok**. An interactive dialog shows that user's browser. Click and type directly in the page, or use the masked text field and Send text on mobile. Complete verification, then select Done / Close. Instagram and Facebook currently both connect through Meta Business Suite, matching the existing uploader. Session detection is based on cookie presence, not proof of publishing permissions or the selected account identity.

Closing the dialog destroys its sign-in tabs and clears the displayed image/input, while preserving the private Chrome profile. Browsers stop after 30 minutes without sign-in or posting activity. Persistent platform cookies survive a browser restart; platforms can still expire or revoke sessions. App logout revokes access to browser control/view but keeps the private platform profile for the user's next app login. Queued jobs continue after app logout. Use Connect again when a platform requires reauthentication.

Only one sign-in dialog is allowed per user. Sign-in is rejected while that user's jobs are queued/running; posting is rejected while sign-in is open. Live Automation is read-only and hides sign-in pages. WebSockets require the app session and matching Origin, recheck revocation/expiry, and limit sign-in to 15 minutes. Input does not accept URLs, arbitrary commands, file uploads, or browser shortcuts. Input, screenshots, passwords and verification codes are not written to application logs.

## Server setup

Use Python 3.10+ with FastAPI, uvicorn, python-multipart, Pillow and Playwright **1.60+**. Install regular Google Chrome and Xvfb on a Linux host. Run the service as an unprivileged account; Chrome's sandbox stays enabled. Each worker starts a separate Xvfb display (not headless Chrome). `SHORTS_CHROME_BINARY` can select the Chrome executable. The macOS development launcher uses `open -g -j` and offscreen positioning; macOS window placement/focus is best-effort.

Set `SHORTS_MAX_BROWSERS` to cap simultaneously running browsers (default 4). Excess starts return a retry message. Keep one ASGI process; the profile lock prevents a second process from controlling an already-owned profile. Normal app shutdown waits for posting executors, then closes Chrome to flush profiles. After a force-killed server, a surviving Chrome instance is refused rather than adopted by port number; the administrator must stop that orphan before retrying. No personal desktop browser or its profile is reused.

For hosting, keep CDP ports private; proxy only the authenticated HTTP/WebSocket application over HTTPS. Treat browser profiles as credentials: restrict and encrypt the persistent volume and backups. Separate profiles are application-level isolation, **not a hardened OS/container boundary**. Before opening registration to untrusted users, isolate workers at the OS/container and network layer, deny access to internal services/cloud metadata, and enforce storage/resource quotas. This implementation is suitable for controlled testing, not unrestricted public multi-tenant deployment.

Remaining work: durable job history/queue and restart recovery, tenant time zones, platform-specific reconnection/disconnect and identity checks, email verification, password recovery, account deletion and deployment monitoring. Jobs still live in memory and scheduling uses the server time zone and existing platform UI limitations. Platform passkeys, native browser/OS dialogs, device-bound verification and some interactive challenges may require additional support; they have not been validated against real accounts.

## Verification

- `python3 -m unittest discover -s tests -p 'test_*.py'`: auth, tenant isolation, request validation, view-only enforcement, posting/sign-in exclusion and logout revocation.
- `python3 tests/check_ui.py`: existing dashboard regression fixtures.
- `python3 tests/check_private_workers.py`: two real headed Chrome instances, isolated cookies, input/screenshots, shutdown/restart with a saved cookie. Temporary profiles only.
- `python3 tests/check_connections_ui.py`: complete app sign-up and remote input flow against a local page in a real headed worker. The UI test driver itself is headless.

No test signs into real platforms or publishes content. macOS worker lifecycle has been exercised locally; Linux/Xvfb deployment still needs a host smoke test.

Implementation references: [Playwright CDP attachment](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp) and [Chrome's dedicated profile requirement for remote debugging](https://developer.chrome.com/blog/remote-debugging-port).

## Local direct sign-in validation (macOS)

If Google rejects the streamed sign-in view, enable `SHORTS_LOCAL_MANUAL_LOGIN=1` **only for local development on the Mac**. After signing into the app, choose **Sign in to YouTube in Chrome** in Account Status. Close any in-app sign-in dialog first and wait for queued posts to finish.

The app closes the automated browser and opens the same private profile in a visible, ordinary Chrome process **without remote-debugging flags or a Playwright connection**. Complete sign-in directly in that window. Quit that Chrome instance with Command-Q, then select **Continue after quitting Chrome** in the app. The app refuses to resume while the manual browser is still running. Resuming reopens the same saved profile for browser automation; it does not automatically upload anything.

During direct sign-in, account cookie inspection, both browser WebSockets, and posting are blocked. A page reload still shows the pending sign-in state. App shutdown closes this test browser; normal profile persistence is retained. The endpoint requires authentication, same-origin mutation headers, explicit configuration, a loopback client, and a localhost Host. Do not enable it behind a hosted reverse proxy or on a shared server.

`python3 tests/check_manual_signin.py` verifies the no-CDP process, rejects early resume, and checks that a cookie set by a local fixture survives into the resumed automation profile. It does **not** test Google account acceptance. A user must complete that validation manually; there is no guarantee this resolves Google's block. Hosted users still need an isolated remote-desktop sign-in transport; this local option is the validation step before building it.

## Helper mode ("your browser, our dashboard")

Set `SHORTS_HELPER_MODE=1`. The server then never runs Chrome for users. Posting happens in a small helper program on each
user's own computer, using their own signed-in Chrome; platform logins never reach the server.

- Website: **Accounts Status → Pair a helper** shows a one-time code (10 minutes). Uploads are refused while no helper is online.
- Helper (macOS today): `python3 helper/shorts_helper.py pair --server URL --code CODE`, then `login` once, then `run`.
- The helper polls `/api/helper-agent/poll` with a per-helper bearer token (stored hashed, revocable via `/api/helper/revoke/{id}`), downloads
  the video, runs the same posting engine, and syncs progress to `/api/helper-agent/jobs/{id}/sync`.
- Jobs are persisted in the accounts database (`job_history`) and survive server restarts. A claimed job that stops reporting for 5 minutes is
  handed to the next poll (only platforms not already `done`).
- Agent routes are exempt from the browser Origin/CSRF check because they use bearer tokens, never cookies.

Not done yet: Windows/Linux Chrome launching (`bot.start_chrome` uses macOS `open`), a packaged installer with auto-update, prompts for
platform verification codes, and hosting/billing.
