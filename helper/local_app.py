#!/usr/bin/env python3
"""Shorts Everywhere local helper: the posting engine, running on YOUR computer with YOUR signed-in Chrome.

  python3 -m helper.local_app          serve on http://127.0.0.1:8765

The dashboard at https://minefoundation.org talks to this process directly from your browser; nothing about your
videos or logins goes through a server. The same dashboard is also served here, at http://127.0.0.1:8765.

Settings live in ~/.shorts-everywhere/config.json (all optional):
  {"support_url": "ko-fi.com/you", "yt_no_link_channels": ["UC..."], "meta_asset_id": "...", "meta_business_id": "..."}
"""
import json, os, sys
from pathlib import Path

PORT = int(os.environ.get("SHORTS_HELPER_PORT", "8765"))
DATA = Path(os.environ.get("SHORTS_DATA_DIR") or Path.home() / ".shorts-everywhere")
# Only these pages may drive the helper. Everything else (any other website open in the same browser) is refused.
ORIGINS = {"https://minefoundation.org", "https://www.minefoundation.org",
           f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
ORIGINS |= {o.strip().rstrip("/") for o in os.environ.get("SHORTS_EXTRA_ORIGINS", "").split(",") if o.strip()}
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
SETTINGS = {"support_url": "SHORTS_SUPPORT_URL", "meta_asset_id": "SHORTS_META_ASSET_ID",
            "meta_business_id": "SHORTS_META_BUSINESS_ID"}


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
    """
    from starlette.responses import PlainTextResponse, Response

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
        if scope["type"] == "websocket":
            if not origin:  # browsers always send Origin on a WebSocket; refuse anything that doesn't
                return await send({"type": "websocket.close", "code": 1008})
            return await app(scope, receive, send)
        method = scope["method"]
        if method == "OPTIONS":
            extra = [(b"access-control-allow-methods", b"GET, POST"),
                     (b"access-control-allow-headers", b"X-Shorts-Request, Content-Type"),
                     (b"access-control-max-age", b"600")]
            if headers.get("access-control-request-private-network") == "true":
                extra.append((b"access-control-allow-private-network", b"true"))
            response = Response(status_code=204)
            response.raw_headers.extend(cors_headers(origin) + extra if origin else extra)
            return await response(scope, receive, send)
        if method not in ("GET", "HEAD") and (not origin or headers.get("x-shorts-request") != "1"):
            return await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)

        async def send_with_cors(message):
            if message["type"] == "http.response.start" and origin:
                message = {**message, "headers": list(message.get("headers", [])) + cors_headers(origin)}
            await send(message)
        return await app(scope, receive, send_with_cors)
    return guarded


def build_app():
    configure()
    import asyncio
    import bot
    import engine
    from fastapi.responses import JSONResponse

    sites = {"youtube": "https://studio.youtube.com/", "instagram": "https://business.facebook.com/",
             "facebook": "https://business.facebook.com/", "tiktok": "https://www.tiktok.com/tiktokstudio"}

    @engine.app.get("/api/auth/me")
    def me():
        # No app accounts: whoever can reach 127.0.0.1 on this computer is the owner.
        return {"id": "local", "email": "", "local": True}

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
    uvicorn.run(build_app(), host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
