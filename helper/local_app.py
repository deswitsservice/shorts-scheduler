#!/usr/bin/env python3
"""Shorts Everywhere local helper: the posting engine, running on YOUR computer with YOUR signed-in Chrome.

  python3 -m helper.local_app          serve on http://127.0.0.1:8765

The dashboard at https://minefoundation.org talks to this process directly from your browser; nothing about your
videos or logins goes through a server. The same dashboard is also served here, at http://127.0.0.1:8765.

Settings live in ~/.shorts-everywhere/config.json (all optional):
  {"support_url": "ko-fi.com/you", "yt_no_link_channels": ["UC..."], "meta_asset_id": "...", "meta_business_id": "...",
   "youtube_channel": "UC..."}
youtube_channel is the channel posts go to when a job doesn't name one; if YouTube Studio is on a different channel the
post stops instead of uploading to the wrong one.
"""
import json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

PORT = int(os.environ.get("SHORTS_HELPER_PORT", "8765"))
DATA = Path(os.environ.get("SHORTS_DATA_DIR") or Path.home() / ".shorts-everywhere")
# Only these pages may drive the helper. Everything else (any other website open in the same browser) is refused.
ORIGINS = {"https://minefoundation.org", "https://www.minefoundation.org",
           f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
ORIGINS |= {o.strip().rstrip("/") for o in os.environ.get("SHORTS_EXTRA_ORIGINS", "").split(",") if o.strip()}
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
SETTINGS = {"support_url": "SHORTS_SUPPORT_URL", "meta_asset_id": "SHORTS_META_ASSET_ID",
            "meta_business_id": "SHORTS_META_BUSINESS_ID", "youtube_channel": "SHORTS_YOUTUBE_CHANNEL"}
# App accounts live in the shorts-everywhere Supabase project (supabase/ in this repo). Both values are public by
# design (the same ones the dashboard ships); the helper only uses them to ask Supabase whether a token is valid.
SUPABASE_URL = os.environ.get("SHORTS_SUPABASE_URL", "https://xzbsyxvovxryisqppbbr.supabase.co")
SUPABASE_KEY = os.environ.get("SHORTS_SUPABASE_KEY", "sb_publishable_0vTD_B0vyvQdrnRvxt_dVA_hsJKZfYL")
TOKEN_CACHE_SECONDS = 60  # the dashboard polls every few seconds; don't ask Supabase each time


class SignInUnavailable(Exception):
    pass


_verified = {}


def verify_token(token):
    """The signed-in Supabase user ({id, email}) for an access token, or None if it's invalid or expired.
    Raises SignInUnavailable when Supabase can't be reached: fail closed, never treat that as signed in."""
    now = time.monotonic()
    hit = _verified.get(token)
    if hit and hit[1] > now:
        return hit[0]
    req = urllib.request.Request(SUPABASE_URL + "/auth/v1/user",
                                 headers={"apikey": SUPABASE_KEY, "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        user = {"id": data["id"], "email": data.get("email") or ""}
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            raise SignInUnavailable() from exc
        user = None  # 401/403: bad or expired token
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        raise SignInUnavailable() from exc
    for stale in [t for t, (_, exp) in _verified.items() if exp <= now]:
        del _verified[stale]
    if user:
        _verified[token] = (user, now + TOKEN_CACHE_SECONDS)
    return user


def needs_sign_in(path):
    """Everything that reads or changes jobs, accounts or the browser needs an app sign-in. The page itself, its
    static files and job media (random file names, loaded by <img>/<video>, which can't send a token) don't."""
    return (path.startswith("/api/") and path != "/api/helper/info") or path == "/ws"


def configure():
    """Point the engine at the user's data folder and apply config.json, before bot/engine are imported."""
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["SHORTS_DATA_DIR"] = str(DATA)
    os.environ.setdefault("BOT_PROFILE", str(DATA / "chrome_profile"))
    try:
        cfg = json.loads((DATA / "config.json").read_text())
    except (OSError, ValueError):
        cfg = {}
    for key, env in SETTINGS.items():
        if cfg.get(key):
            os.environ.setdefault(env, str(cfg[key]))
    if cfg.get("yt_no_link_channels"):
        os.environ.setdefault("SHORTS_YT_NO_LINK_CHANNELS", ",".join(cfg["yt_no_link_channels"]))
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root), str(root / "webapp")]


def guard(app):
    """Wrap the engine so only the allowed origins can use it from a browser.

    - Host must be 127.0.0.1/localhost: blocks DNS-rebinding pages that resolve their own name to 127.0.0.1.
    - A request carrying an Origin must come from ORIGINS; writes also need X-Shorts-Request, which forces a CORS
      preflight, so another site can't fire a plain form POST at the helper.
    - Preflights answer Chrome's Private Network Access check, which an https page needs to reach 127.0.0.1.
    - App endpoints need a valid Supabase sign-in (Authorization: Bearer <access token>; the WebSocket, which can't
      send headers, passes ?token=). The user is put on request.state.user.
    """
    import asyncio
    from urllib.parse import parse_qs
    from starlette.responses import JSONResponse, PlainTextResponse, Response

    def cors_headers(origin):
        return [(b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin")]

    async def guarded(scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        origin = headers.get("origin", "").rstrip("/")
        if headers.get("host", "") not in HOSTS or (origin and origin not in ORIGINS):
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)
        async def signed_in_user():
            """(user, None) or (None, error message)."""
            if scope["type"] == "websocket":
                token = (parse_qs(scope.get("query_string", b"").decode()).get("token") or [""])[0]
            else:
                auth = headers.get("authorization", "")
                token = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not token:
                return None, "Please sign in."
            try:
                user = await asyncio.to_thread(verify_token, token)
            except SignInUnavailable:
                return None, "Can't reach the sign-in service. Check your internet connection and try again."
            return (user, None) if user else (None, "Your sign-in has expired. Please sign in again.")

        if scope["type"] == "websocket":
            if not origin:  # browsers always send Origin on a WebSocket; refuse anything that doesn't
                return await send({"type": "websocket.close", "code": 1008})
            if needs_sign_in(scope["path"]):
                user, _ = await signed_in_user()
                if not user:
                    return await send({"type": "websocket.close", "code": 1008})
                scope.setdefault("state", {})["user"] = user
            return await app(scope, receive, send)
        method = scope["method"]
        if method == "OPTIONS":
            extra = [(b"access-control-allow-methods", b"GET, POST"),
                     (b"access-control-allow-headers", b"Authorization, X-Shorts-Request, Content-Type"),
                     (b"access-control-max-age", b"600")]
            if headers.get("access-control-request-private-network") == "true":
                extra.append((b"access-control-allow-private-network", b"true"))
            response = Response(status_code=204)
            response.raw_headers.extend(cors_headers(origin) + extra if origin else extra)
            return await response(scope, receive, send)

        async def send_with_cors(message):
            if message["type"] == "http.response.start" and origin:
                message = {**message, "headers": list(message.get("headers", [])) + cors_headers(origin)}
            await send(message)
        if method not in ("GET", "HEAD") and (not origin or headers.get("x-shorts-request") != "1"):
            return await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)
        if needs_sign_in(scope["path"]):
            user, error = await signed_in_user()
            if not user:
                status = 503 if error.startswith("Can't reach") else 401
                return await JSONResponse({"error": error}, status_code=status)(scope, receive, send_with_cors)
            scope.setdefault("state", {})["user"] = user
        return await app(scope, receive, send_with_cors)
    return guarded


def build_app():
    configure()
    import asyncio
    import bot
    import engine
    from fastapi import Request
    from fastapi.responses import JSONResponse

    sites = {"youtube": "https://studio.youtube.com/", "instagram": "https://business.facebook.com/",
             "facebook": "https://business.facebook.com/", "tiktok": "https://www.tiktok.com/tiktokstudio"}

    @engine.app.get("/api/auth/me")
    def me(request: Request):
        # Only reached with a valid Supabase sign-in (guard). `local` tells the dashboard it's talking to a helper.
        return {**request.state.user, "local": True}

    @engine.app.get("/api/helper/info")
    def info():
        return {"app": "shorts-everywhere-helper", "local": True}

    @engine.app.post("/api/connect")
    async def connect(platform: str):
        if platform not in sites:
            return JSONResponse({"error": "Unknown platform."}, status_code=400)
        try:
            await asyncio.get_running_loop().run_in_executor(None, bot.open_signin, sites[platform])
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)[:300]}, status_code=503)
        return {"ok": True}

    @engine.app.post("/api/connect/done")
    async def connect_done():
        try:
            await asyncio.get_running_loop().run_in_executor(None, bot.hide_signin)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)[:300]}, status_code=503)
        return {"ok": True}

    return guard(engine.app)


def main():
    import uvicorn
    if sys.stdout is None or sys.stderr is None:
        # Windows runs the helper with pythonw.exe (no console window), which leaves stdout/stderr as None; logging
        # would fail. Send everything to the same helper.log the macOS LaunchAgent writes.
        DATA.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(DATA / "helper.log", "a", buffering=1, encoding="utf-8")
    uvicorn.run(build_app(), host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
