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

## Serverless: minefoundation.org + local helper (no server at all)

The public site is static. `https://minefoundation.org` serves the same dashboard (`webapp/static/index.html`), and the page
talks straight to a helper on the visitor's own Mac at `http://127.0.0.1:8765`. Videos, platform sign-ins and posting
never touch any server, so hosting is just static files.

- **Helper** (`helper/local_app.py`): the single-user engine (`webapp/engine.py`) plus `/api/auth/me` (always the local
  owner), `/api/connect?platform=` (shows the automation Chrome on-screen at that platform so the user signs in directly)
  and `/api/connect/done` (sends it back off-screen). Data lives in `~/.shorts-everywhere` (`SHORTS_DATA_DIR`), including
  `chrome_profile/`. Optional settings in `~/.shorts-everywhere/config.json`: `support_url`, `yt_no_link_channels`,
  `meta_asset_id`, `meta_business_id`.
- **Access guard**: Host must be `127.0.0.1:8765`/`localhost:8765` (blocks DNS rebinding); any `Origin` must be
  minefoundation.org or the helper itself (`SHORTS_EXTRA_ORIGINS` adds more, for testing); writes need an Origin plus
  `X-Shorts-Request: 1`, which forces a CORS preflight; preflights answer Chrome's Private Network Access check
  (`Access-Control-Allow-Private-Network`). WebSockets need an allowed Origin.
- **Dashboard**: on `*.minefoundation.org` (or with `?helper` in the URL) every `/api`, `/uploads`, `/thumbs`, `/ws` URL is
  rewritten to the helper. If the helper can't be reached it shows an install card and polls every 5s, reloading once the
  helper answers. Served by the helper or the hosted server, URLs stay same-origin as before. Safari won't let an https page
  reach `http://127.0.0.1`, so Safari users open `http://127.0.0.1:8765` directly.
- **Install** (`site/install.sh`, served as `https://minefoundation.org/install.sh`):
  `curl -fsSL https://minefoundation.org/install.sh | bash`. It needs macOS and Google Chrome, but no admin password or
  Apple developer signing. It installs `uv` into `~/.shorts-everywhere/bin`, which fetches Python 3.12 into
  `~/.shorts-everywhere/venv`, then downloads this repo's `master` from GitHub into `~/.shorts-everywhere/app` and
  registers the LaunchAgent `org.minefoundation.shorts-helper` (starts at login, restarts if it exits, logs to
  `~/.shorts-everywhere/helper.log`). Re-running it updates the helper; new code replaces the old only after packages
  install. `site/uninstall.sh` removes it and keeps sign-ins unless given `--all`. `SHORTS_REF=<branch>` and
  `SHORTS_SOURCE_DIR=<checkout>` install something other than `master`, for testing.
- **Build/deploy**: `bash site/build.sh` writes `site/dist/` (`index.html`, `static/design.css`, `static/connections.js`,
  `install.sh`, `uninstall.sh`, `.htaccess` serving `.sh` as `text/plain`, uncached). Upload `site/dist/` to the web root.
- **Per-user description link**: `bot.py`'s Ko-fi line is now `SHORTS_SUPPORT_URL` (empty means no link), and the
  no-link YouTube channels are `SHORTS_YT_NO_LINK_CHANNELS`. A public install no longer adds the owner's Ko-fi link to other
  people's posts. The owner's own server launcher must set both to keep the old behaviour.
- The video maker isn't installed by default (it needs ffmpeg and faster-whisper); it reports that it's unavailable.

### Windows support (2026-10-07)

The helper now runs on Windows too. Install it in PowerShell with `irm https://minefoundation.org/install.ps1 | iex`;
remove it with `uninstall.ps1` (set `$env:SHORTS_UNINSTALL_ALL='1'` first to also delete sign-ins).

- **`site/install.ps1`** does what `install.sh` does:
  - checks that Chrome is installed (Program Files, Program Files (x86), or the per-user LocalAppData install)
  - installs `uv` into `%USERPROFILE%\.shorts-everywhere\bin`, which fetches Python 3.12 into `venv\`
  - downloads the repo zip from codeload into `app\`; new code replaces the old only after packages install
  - stops any running `helper.local_app` process
  - adds a **Startup-folder shortcut** (`Shorts Everywhere helper.lnk`, runs `pythonw.exe -u -m helper.local_app`, no
    console window) and starts the helper
  - waits for `/api/helper/info`
  - needs no admin rights
  - honours the same `SHORTS_REF`, `SHORTS_SOURCE_DIR` and `SHORTS_NO_OPEN` testing variables as `install.sh`
- **`bot.py`**
  - `chrome_binary()` finds Chrome per OS (`SHORTS_CHROME_BINARY` overrides).
  - On Windows and Linux, `start_chrome` runs Chrome directly, detached (`DETACHED_PROCESS`); macOS keeps its
    `open -n -g -j` launch.
  - Both share `_chrome_flags()`, which also disables Windows' own occlusion throttling
    (`CalculateNativeWinOcclusion`).
  - The three select-all shortcuts went from `Meta+A` (the Windows key on a PC) to `ControlOrMeta+A`, so Meta and
    TikTok captions replace the field's text on both systems.
- **`helper/local_app.py`:** under `pythonw` there's no stdout or stderr, so output goes to `helper.log`.
- **Dashboard:** the install card shows the PowerShell steps and command to Windows visitors, and hides the Safari note.
- **`site/build.sh`:** publishes `install.ps1` and `uninstall.ps1`, served as `text/plain; charset=utf-8`, uncached.
- **CI** (`.github/workflows/windows-helper.yml`, `windows-latest`) installs from the checkout with `install.ps1`, then:
  - `/api/helper/info` returns 200
  - `/api/accounts` without a token returns 401
  - a foreign Origin gets 403
  - the startup shortcut exists
  - `bot.start_chrome()` launches Chrome, and cookies and windows can be read over CDP
  - `uninstall.ps1` stops the helper and removes the shortcut

  It runs on pull requests and pushes to `master` that touch the helper.

Verified on macOS:
- `chrome_binary()` still returns `/Applications/Google Chrome.app/...`.
- `ControlOrMeta+A` replaced a textarea's text in headless Chrome.
- The owner's helper reinstalled from this branch and answered.
- Unit tests: 57/59, with the same 2 pre-existing `test_videomaker` failures.

Not verified: a real post from Windows. The owner has no Windows PC, so the first Windows users from the invite list act
as beta testers.

### YouTube "upload finished" check (2026-10-05)

A YouTube post of video 10 to amoura looked stuck. A read-only capture of the Studio tab showed the upload already
finished: the progress bar read "Checks complete. No issues found." and the video was "Saved as private". `bot.youtube`
waited only for the text "Upload complete", which Studio had already replaced. After 10 minutes the step failed, and
the retry uploaded the video again; the log showed `thumbnail inputs found` twice.

The wait now counts any of "Upload complete", "Processing", "Checking" or "Checks complete" as finished, provided the
text no longer says "Uploading". I checked the rule against seven status texts (still uploading, uploading with
"Processing will begin", upload complete, processing, checking, checks complete, empty), and all came out right. This
change is only in `bot.py`; the site and installer are unchanged.

### Minimized posting browser, Post now, channel safety (2026-10-05)

The owner reported three problems on the live site.
- Posts failed with `Page.screenshot: Timeout 30000ms exceeded` after they minimized the posting browser.
- The live view stayed on "Loading the posting page…".
- A "Post now" seemed to schedule.

The helper's pre-publish screenshots (`~/.shorts-everywhere/shots/`) showed that both YouTube runs of video 10 were set to
**Public / Publish**. They also showed the 08:41 run uploading to **Cold Case Chronicles** (`UCg85UGSCLnoTM48RggRJmkQ`)
instead of **amoura** (`UC4gsfYjRlgp36oWy54juMHw`): the Google account owns both, and with no channel given, Studio uses
the one picked last. The helper log had none of the bot's `print` lines, because launchd ran Python with buffered output.

- **Minimized window:** `bot.unminimize_windows()` (plain CDP) restores every minimized automation window and parks it
  off-screen. `engine.run_job` runs it every 2s on a watchdog thread for the whole job. The live view calls it when a
  frame fails. The six diagnostic screenshots in `bot.py` now go through `bot.save_shot()`, which un-minimizes first,
  times out after 10s, and never fails or stalls the posting step.
- **Post now:**
  - Meta clicks the **Share now** mode explicitly. Right before the final click it stops with a clear error (and a
    `*_still_scheduled.png` shot) if the schedule time fields are still showing.
  - TikTok switches its radio away from `schedule` when that's selected, and stops if it stays selected.
  - YouTube already selected Public explicitly.
- **Default YouTube channel:** the `youtube_channel` setting in `config.json` (env `SHORTS_YOUTUBE_CHANNEL`) is used when
  a job doesn't name a channel. `bot.youtube` already refuses a mismatched channel, and its message now explains how to
  switch.
- **Helper log:** the LaunchAgent now runs `python -u`, so the bot's progress lines reach `helper.log`.
- **Owner's settings:** the owner's `~/.shorts-everywhere/config.json` didn't exist, so the minefoundation helper had been
  running without the Meta Page pin (Amoura), the Ko-fi link and the Cold Case no-link rule that `start_app.sh` sets for
  the old app. It now has all of those plus `youtube_channel` = amoura.

Verified on the owner's idle helper Chrome:
- I minimized its window over CDP. `unminimize_windows()` restored 1 window to `normal`, parked at left -1132 (macOS
  won't place a window fully off-screen), and a screenshot then took 0.7s.
- `save_shot()` on a re-minimized window saved the file in 1.3s.
- `configure()` mapped `config.json` to `SHORTS_YOUTUBE_CHANNEL`, `SHORTS_META_ASSET_ID`, `SHORTS_META_BUSINESS_ID`,
  `SHORTS_SUPPORT_URL` and `SHORTS_YT_NO_LINK_CHANNELS`.
- Unit tests: 57/59, with the same 2 pre-existing `test_videomaker` failures.
- Not exercised live: the Meta and TikTok "Post now" paths (they need real posts).

### Live view and account status speed (2026-10-05)

The owner reported a long wait before the live view appeared. Timing printouts in a local debug copy (never committed)
showed why. Each live view started its own Playwright driver: 2.4–3.6s to start, plus 1–1.6s to connect over CDP. Each
`/api/accounts` call did the same through `sync_playwright` and took 4–6s. The page asks for accounts every 6s, so
those starts ran almost back to back and competed for the CPU with the live view.

- **Live view:** `engine._live_browser()` keeps one Playwright driver and one CDP connection for every `/ws`. It
  reconnects only after Chrome restarts and never closes the shared connection per view. `bot.chrome_running()` (a
  blocking HTTP call) now runs off the event loop.
- **Account status:** `bot.browser_cookie_names()` reads cookies with `Storage.getCookies` over one plain WebSocket
  (`bot.cdp_browser_calls`, from the `websockets` package, which sends no Origin header so Chrome accepts it). If no
  window is open, it first reopens a hidden blank one, keeping `ensure_window`'s fix. No Playwright involved.

Measured on the owner's Mac, page served on another port with `?helper`, this branch's helper:

| | Before | After |
|---|---|---|
| `/api/accounts` | 4–6s | 0.47s first, then 0.05–0.08s |
| Live view "Ready" after page load | 9.5–10.9s | 1.2s first load, 0.7s next load |
| Account status shown after page load | about 5.7s | 0.7–1.2s |

The live https site showed the same 9.5–10.9s before the fix. Account results were unchanged (YouTube, Meta, TikTok
all true).

### App accounts (added 2026-10-05)

Only signed-in members can use the app. Accounts live in their **own** Supabase project, **shorts-everywhere**
(ref `xzbsyxvovxryisqppbbr`, free plan, `us-east-1`), deliberately separate from the Deswits project so shorts users
never touch the Deswits database. Schema and auth settings live in `supabase/` in this repo (`supabase/migrations/*.sql`
plus `supabase/config.toml`), applied with `supabase db push --linked` and `supabase config push` from this repo, which is
linked to that project only. The database password is in the owner's macOS Keychain (service `supabase-db-password`,
account `shorts-everywhere`).

- **Sign-in**: email + password through Supabase Auth (`supabase-js` 2.117.2 from jsDelivr), using the dialog on the
  dashboard. **Email confirmation is off**, because the free built-in mailer only delivers to the Supabase team's own
  addresses, so a confirmation email would never reach a public user. Consequence: emails aren't verified and there's no
  "forgot password" email yet. Both need a custom SMTP sender in `[auth.email.smtp]`. Passwords need at least 8 characters.
- **`public.shorts_users`**: one row per user (`email`, `first_login_at`, `last_login_at`, `login_count`). RLS lets a
  user read only their own row. Clients have no insert/update/delete privileges. The only write path is the
  `SECURITY DEFINER` function `shorts_record_login()`, which the dashboard calls after every sign-in.
  `shorts_ping()` (callable by anyone, returns `now()`) exists only for `.github/workflows/supabase-keepalive.yml`, which
  calls it daily so the free project never auto-pauses.
- **The helper enforces it.** Every `/api/*` route except `/api/helper/info`, and `/ws`, needs
  `Authorization: Bearer <Supabase access token>` (the WebSocket passes `?token=`). The helper checks the token with
  Supabase's `GET /auth/v1/user` and caches the answer for 60s. It **fails closed**: if Supabase can't be reached, it
  returns 503 and doesn't let anyone in. The page itself, `/static`, `/uploads` and `/thumbs` stay open
  (`<img>`/`<video>` can't send a token, and media names are random). The preflight allow-list now includes
  `Authorization`.
- **Dashboard** (`SUPA` mode = on minefoundation.org, or the page served by the helper on port 8765): the header shows
  **Sign in** and **Sign up** while signed out, and each opens the dialog in that mode (`openShortsAuth(create)`). Once
  signed in, the account menu says "My Account" and never shows the email (the owner's request, 2026-10-05). Sign out
  calls `sb.auth.signOut()`. Every `/api/` fetch gets the current access token, and a 401 brings back the sign-in dialog
  and both header buttons. The hosted multi-user server keeps its own `/login` and has no Sign up button.
  Verified 2026-10-05 in headless Chrome with this branch's helper:
  - Signed out, the header showed Sign in and Sign up.
  - Sign up opened "Create your account", and Sign in opened "Sign in to Shorts Everywhere".
  - After creating an account, the header showed only "My Account", and the email appeared nowhere on the page.
  - The test account was deleted afterwards.
- **Upgrading**: a helper installed before this change doesn't allow the `Authorization` header in its preflight, so it
  stops working with the new site until `curl -fsSL https://minefoundation.org/install.sh | bash` is run again.

Verified 2026-10-05, all against the new project only:
- **Database:** with a throwaway account, sign-up gave a token and `shorts_record_login()` created the row and counted
  2 logins on the second call. A user read only their own row. Direct insert and update as that user both got `42501`.
  Anonymous read and anonymous RPC both got `42501`. `/auth/v1/user` returned 200 for a good token and 403 for a bad one.
  `shorts_ping` returned the time.
- **Helper (curl):** `/api/helper/info` and `/` gave 200 without a token. `/api/accounts` without a token, with a bad
  token, and `POST /api/schedule` without a token all gave 401 "Please sign in."
- **Headless Chrome** against `site/dist` with `?helper` and this branch's helper:
  - Signed out, the page showed "Sign in to start".
  - The dialog refused a too-short password.
  - Create account signed the user in, showed their email, and connected the `/ws` live view with `?token=`.
  - Sign out returned to signed out. A wrong password showed "Wrong email or password." Signing in again worked.
  - `shorts_users.login_count` was 2.
  - Account status filled in about 5.7s after load (sign-in check plus the account read), showing YouTube connected
    and TikTok not.
- **Cleanup:** every test account was deleted afterwards, leaving 0 users and 0 rows. The Deswits project was not
  touched; its checkout stays linked to `hsdsqakknknkmiklwtky`.

Verified 2026-10-04: the installer ran end to end into a scratch `HOME` (about 90s, LaunchAgent up, `/api/helper/info`
answering), then was unloaded. curl checks of the guard: minefoundation.org Origin allowed; another Origin, a rebound Host,
a POST without the header and a POST without an Origin all got 403; the PNA preflight returned
`access-control-allow-private-network: true`. Headless Chrome against `site/dist` served on another port with `?helper`:
the install card showed while the helper was down; after the helper started, the page reloaded itself into the dashboard
(Connect buttons, "Browser is off", cross-origin POST and `/api/jobs` worked, no console errors); the same page served
by the helper behaved the same. Not tested: a real https page on minefoundation.org reaching the helper (needs deployment),
and a real platform sign-in through `/api/connect`.

Live, 2026-10-04 (after deploying through waypoint's workflow; the old Wanderly build was backed up on the server to
`~/backups/minefoundation-wanderly`). `curl -fsSL https://minefoundation.org/install.sh | bash` installed and started the
helper on the owner's Mac. Headless Chrome on https://minefoundation.org then found three bugs, fixed here:
- **Live view 404:** uvicorn in the helper's venv had no WebSocket library ("No supported WebSocket library detected"),
  so every `/ws` upgrade got a 404. `websockets` is now in `helper/requirements.txt`.
- **Accounts stuck on "Checking…":** macOS Chrome keeps running after its last window is closed, and then
  `Storage.getCookies` fails with "Browser context management is not supported" (posting would fail the same way).
  `bot.ensure_window()` reopens one blank window off-screen through `Target.createTarget`; `_accounts()` and
  `run_job()` call it.
- **Re-running the installer (updating) left the helper stopped:** `launchctl bootout` returns before launchd finishes,
  so the immediate `bootstrap` failed with "Bootstrap failed: 5: Input/output error". The installer now waits for the
  old service to go away and retries the bootstrap up to 5 times.

Verified after the fixes: the installer ran twice in a row over the existing install and the helper came back both
times. With every helper Chrome window closed, `/api/accounts` answered in about 2.5s with `chrome: true`. The live https
page, with Chrome's local-network permission granted, showed the dashboard: no install card, "Connect the missing
platforms", Connect buttons, and the `/ws` live view connected and showing "Ready". The only failed requests were
GoDaddy's injected `csp.secureserver.net` tracker. Without that permission, headless Chrome (which can't show the prompt)
is refused by CORS ("Permission was denied") and the page shows the install card, which tells people to choose Allow.
