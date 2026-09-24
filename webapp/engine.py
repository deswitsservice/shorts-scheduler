#!/usr/bin/env python3
"""Internal single-workspace automation engine. Serve through authenticated server.py only."""
import asyncio, datetime, os, re, sys, time, uuid
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import bot  # noqa: E402  (browser flows: youtube / meta / tiktok)

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

UPLOADS = os.path.join(HERE, "uploads")
THUMBS = os.path.join(bot.HERE, "shots", "thumbs")
os.makedirs(UPLOADS, exist_ok=True)
CDP = f"http://127.0.0.1:{bot.PORT}"
app = FastAPI()
executor = ThreadPoolExecutor(max_workers=1)  # one browser -> one job at a time
jobs = {}

MAX_VIDEO_BYTES = 500 * 1024 * 1024  # 500MB: generous for a <=60s 1080x1920 clip, guards against filling the disk
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# Real per-platform caption limits (title box on YouTube is separate and browser-capped at 100 already).
CAPTION_LIMITS = {"youtube": 5000, "instagram": 2200, "tiktok": 4000}  # facebook: no practical cap enforced here
INSTAGRAM_MAX_HASHTAGS = 30


def count_hashtags(text):
    return len(re.findall(r"(?<!\w)#\w+", text))


def validate_job(plats, title, desc, when, publish_now):
    """Return an error string, or None if the job looks postable. Catches the checks a user would otherwise only
    discover after waiting through a multi-minute browser run that fails deep inside one platform's flow."""
    if "youtube" in plats and len(title) > 100:
        return f"Title is {len(title)} characters; YouTube's limit is 100."
    applicable = [CAPTION_LIMITS[p] for p in plats if p in CAPTION_LIMITS]
    if applicable and len(desc) > min(applicable):
        tightest = min(applicable, key=lambda n: n)
        which = [p for p in plats if CAPTION_LIMITS.get(p) == tightest]
        return (f"Description + hashtags is {len(desc)} characters; {'/'.join(which)}'s limit is {tightest}. "
                f"Shorten the description or remove some tags.")
    if "instagram" in plats:
        n = count_hashtags(desc)
        if n > INSTAGRAM_MAX_HASHTAGS:
            return f"{n} hashtags in the description; Instagram allows at most {INSTAGRAM_MAX_HASHTAGS}."
    if not publish_now:
        now = datetime.datetime.now()
        if when <= now + datetime.timedelta(minutes=2):
            return "That publish time is in the past (or too soon) — pick a later time, or choose Post now."
        if {"instagram", "facebook"} & set(plats) and when.date() != now.date():
            return ("Instagram/Facebook scheduling here only supports a time later today — this tool hasn't been "
                    "extended to future-dated Reels yet. Pick a time today, or use Post now.")
        if "tiktok" in plats:
            if when.minute % 5:
                return "TikTok only accepts scheduled times on a 5-minute mark (e.g. 5:00, 5:05, 5:10)."
            if when < now + datetime.timedelta(minutes=20):
                return "TikTok requires scheduling at least 20 minutes from now."
            if when > now + datetime.timedelta(days=10):
                return "TikTok only accepts scheduling up to 10 days ahead."
    return None


def friendly_error(exc):
    """Playwright/network failures are usually cryptic (raw Timeout/selector text). Map the common ones to
    something the user can actually act on; fall back to a trimmed version of the original otherwise."""
    msg = str(exc)
    checks = [
        (r"connect ECONNREFUSED|Connection closed while reading", "Lost connection to the browser. It may have been closed — reopen it and try again."),
        (r"TimeoutError.*set_input_files|TimeoutError.*input\[type=file\]", "Couldn't find the upload button on the page — the site's layout may have changed, or you're not logged in there. Check the Chrome window."),
        (r"TimeoutError.*expect_navigation|TimeoutError.*wait_for_url", "The page didn't move on to the next step in time — it may still be uploading, or the site changed. Check the Chrome window and the post lists directly."),
        (r"TimeoutError", "A step on the page took too long and timed out — the site may be slow, logged out, or its layout changed. Check the Chrome window."),
        (r"No scheduled video starting with", None),  # already a clear message from bot.py, leave as-is
    ]
    for pattern, friendly in checks:
        if re.search(pattern, msg):
            return friendly if friendly else msg[:300]
    return f"{type(exc).__name__}: {msg[:300]}"


# ---------- jobs ----------
PLATFORM_HOST = {"youtube": "studio.youtube.com", "instagram": "business.facebook.com", "facebook": "business.facebook.com", "tiktok": "tiktok.com"}


def run_job(job):
    vid = job["vid"]
    if not bot.chrome_running():
        for step in job["steps"].values():
            step["state"], step["error"] = "failed", "The browser isn't running, so nothing was posted. Open it (python3 bot.py login) and try again."
        job["finished"] = time.time()
        return
    bot.ACTIVE_TARGET_ID = None
    job["started"] = time.time()
    for plat in job["platforms"]:
        step = job["steps"][plat]
        step["state"] = "running"
        step["log"] = []
        job["current_platform"] = plat

        def on_step(msg, step=step):
            step["detail"] = msg
            step["log"].append({"t": time.time(), "msg": msg})

        try:
            commit = not job["dry"]
            if plat == "youtube":
                bot.youtube(vid, commit=commit, on_step=on_step)
            elif plat == "instagram":
                bot.meta(vid, "instagram", commit=commit, on_step=on_step)
            elif plat == "facebook":
                bot.meta(vid, "facebook", commit=commit, on_step=on_step)
            elif plat == "tiktok":
                bot.tiktok(vid, commit=commit, on_step=on_step)
            step["state"] = "done" if commit else "checked (dry run)"
        except SystemExit as e:
            step["state"], step["error"] = "failed", str(e)
        except Exception as e:  # noqa: BLE001
            step["state"], step["error"] = "failed", friendly_error(e)
    bot.ACTIVE_TARGET_ID = None
    job["current_platform"] = None
    job["finished"] = time.time()
    try:
        bot.show_idle_screen()
    except Exception:  # noqa: BLE001
        pass


@app.post("/api/schedule")
async def schedule(
    file: UploadFile = File(...),
    thumbnail: UploadFile | None = File(None),
    title: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    platforms: str = Form(...),
    when: str = Form(""),  # "YYYY-MM-DDTHH:MM" from <input type=datetime-local>; ignored when mode is "now"
    mode: str = Form("schedule"),  # "now" or "schedule"
    made_for_kids: str = Form(""),  # "true" / "false", required when youtube is a selected platform
    dry: str = Form("false"),
    story: str = Form("false"),
    thumb_title: str = Form("true"),
    youtube_channel: str = Form(""),
    instagram_account: str = Form(""),
    facebook_account: str = Form(""),
):
    jid = uuid.uuid4().hex[:8]

    if not title.strip():
        return JSONResponse({"error": "Give the video a title."}, status_code=400)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in VIDEO_EXTS:
        return JSONResponse({"error": f"'{ext or 'no extension'}' doesn't look like a video file. Use .mp4, .mov, .m4v or .webm."}, status_code=400)
    if thumbnail is not None and thumbnail.filename:
        text_ext = os.path.splitext(thumbnail.filename)[1].lower()
        if text_ext not in IMAGE_EXTS:
            return JSONResponse({"error": f"Thumbnail '{text_ext or 'no extension'}' isn't a supported image type. Use .png or .jpg."}, status_code=400)

    if mode not in ("now", "schedule"):
        return JSONResponse({"error": "Choose Post now or Schedule for later."}, status_code=400)
    plats = list(dict.fromkeys(p.strip() for p in platforms.split(",") if p.strip() in PLATFORM_HOST))
    if not plats:
        return JSONResponse({"error": "Pick at least one platform."}, status_code=400)
    publish_now = mode == "now"
    when_dt = None
    if not publish_now:
        if not when:
            return JSONResponse({"error": "Pick a publish time, or choose Post now."}, status_code=400)
        try:
            when_dt = datetime.datetime.strptime(when, "%Y-%m-%dT%H:%M")
        except ValueError:
            return JSONResponse({"error": "That publish time doesn't look valid."}, status_code=400)
    if "youtube" in plats and made_for_kids not in ("true", "false"):
        return JSONResponse({"error": "Answer whether this video is made for kids (required for YouTube)."}, status_code=400)

    hashtags = " ".join("#" + re.sub(r"\s+", "", t.strip().lstrip("#")) for t in tags.split(",") if t.strip())
    desc = (description.strip() + ("\n\n" + hashtags if hashtags else "")).strip()

    err = validate_job(plats, title.strip(), desc, when_dt, publish_now)
    if err:
        return JSONResponse({"error": err}, status_code=400)

    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "video.mp4")
    path = os.path.join(UPLOADS, f"{jid}_{safe}")
    size = 0
    with open(path, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_VIDEO_BYTES:
                f.close()
                os.remove(path)
                return JSONResponse({"error": f"Video is over {MAX_VIDEO_BYTES // (1024*1024)}MB — that's unusually large for a short clip; double-check the file."}, status_code=400)
            f.write(chunk)
    thumb_path = None
    if thumbnail is not None and thumbnail.filename:
        thumb_ext = os.path.splitext(thumbnail.filename)[1].lower() or ".png"
        thumb_path = os.path.join(UPLOADS, f"{jid}_thumb{thumb_ext}")
        with open(thumb_path, "wb") as f:
            f.write(await thumbnail.read())

    vid = {"id": jid, "file": path, "title": title.strip(), "description": desc,
           "schedule": None if publish_now else when.replace("T", " ")[:16], "publish_now": publish_now,
           "made_for_kids": made_for_kids == "true", "story": story == "true", "thumbnail": thumb_path,
           "thumb_title": thumb_title == "true", "youtube_channel": youtube_channel.strip(),
           "instagram_account": instagram_account.strip(), "facebook_account": facebook_account.strip(), "accounts": {}}
    job = {"id": jid, "title": vid["title"], "when": "Now" if publish_now else vid["schedule"], "platforms": plats,
           "dry": dry == "true", "vid": vid, "accounts": vid["accounts"], "steps": {p: {"state": "queued", "log": []} for p in plats},
           "created": time.time(), "started": None, "finished": None, "current_platform": None, "thumbnail_url": None}
    try:
        await asyncio.get_running_loop().run_in_executor(None, bot.prepare_thumbnail, vid)
    except Exception:
        for uploaded in (path, thumb_path):
            if uploaded and os.path.exists(uploaded):
                os.remove(uploaded)
        return JSONResponse({"error": "Couldn't prepare the thumbnail. Choose a valid image and try again."}, status_code=400)
    if vid.get("thumbnail"):
        tdir = THUMBS
        job["thumbnail_url"] = f"/thumbs/{os.path.basename(vid['thumbnail'])}" if vid["thumbnail"].startswith(tdir) \
            else f"/uploads/{os.path.basename(vid['thumbnail'])}"
    jobs[jid] = job
    executor.submit(run_job, job)
    return {"id": jid}


@app.get("/api/jobs")
def list_jobs():
    out = []
    keys = ("id", "title", "when", "platforms", "dry", "steps", "started", "finished", "current_platform", "thumbnail_url", "accounts")
    for j in sorted(jobs.values(), key=lambda x: -x["created"]):
        out.append({k: j[k] for k in keys})
    return out


def _accounts():
    if not bot.chrome_running():
        return {"chrome": False, "youtube": False, "meta": False, "tiktok": False}
    res = {"chrome": True, "youtube": False, "meta": False, "tiktok": False}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP, no_defaults=True)
        names = {(c["domain"].lstrip("."), c["name"]) for c in b.contexts[0].cookies()}
        has = lambda dom, name: any(d.endswith(dom) and n == name for d, n in names)  # noqa: E731
        res["youtube"] = has("youtube.com", "SAPISID") or has("google.com", "SAPISID")
        res["meta"] = has("facebook.com", "c_user")
        res["tiktok"] = has("tiktok.com", "sessionid")
    return res


@app.get("/api/accounts")
async def accounts():
    try:
        return await asyncio.get_running_loop().run_in_executor(None, _accounts)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"chrome": False, "error": str(e)[:200]}, status_code=200)


@app.post("/api/start")
async def start_browser():
    """Explicitly launches the app's Chrome (with saved logins already on disk). Never called automatically —
    only in response to the user clicking Start Browser — so just opening/viewing the page can't pop a window.
    Lands on a neutral YouTube tab rather than Chrome's own New Tab page (which shows the real profile's
    personal shortcuts/history) — startup URLs passed on the command line aren't reliable across Chrome restarts."""
    def _start():
        bot.start_chrome()
        if not bot.chrome_running():
            raise RuntimeError("Chrome did not become ready. Check that Chrome is installed and try again.")
    try:
        await asyncio.get_running_loop().run_in_executor(None, _start)
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:300]}, status_code=503)
    return {"ok": True}


# ---------- live browser view (read-only: pixels only, no input is ever sent to the page) ----------
async def find_automation_page(ctx, target_id):
    """Resolve only the explicitly selected automation target, never another visible tab."""
    if not target_id:
        return None
    for page in list(ctx.pages):
        session = None
        try:
            session = await ctx.new_cdp_session(page)
            info = await session.send("Target.getTargetInfo")
            if info["targetInfo"]["targetId"] == target_id:
                return page
        except Exception:
            continue
        finally:
            if session:
                try:
                    await session.detach()
                except Exception:
                    pass
    return None


@app.websocket("/ws")
async def browser_ws(ws: WebSocket):
    """Read-only snapshots of the posting tab, independent of OS focus or tab visibility."""
    import base64
    await ws.accept()
    if not bot.chrome_running():
        await ws.send_json({"t": "no-browser"})
        await ws.close()
        return
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(CDP, no_defaults=True)
            ctx = browser.contexts[0]
        except Exception:
            await ws.close()
            return

        async def stream():
            page, target = None, None
            while True:
                wanted = bot.ACTIVE_TARGET_ID
                if wanted != target or page is None or page.is_closed():
                    page = await find_automation_page(ctx, wanted)
                    target = wanted
                if page is None:
                    await ws.send_json({"t": "idle"})
                else:
                    try:
                        # Screenshots work independently of the foreground tab; do not activate it.
                        frame = await page.screenshot(type="jpeg", quality=65, timeout=4000)
                        if wanted == bot.ACTIVE_TARGET_ID:
                            await ws.send_json({"t": "frame", "d": base64.b64encode(frame).decode()})
                    except Exception:
                        page = None
                        await ws.send_json({"t": "waiting"})
                await asyncio.sleep(0.8)

        sender = asyncio.create_task(stream())
        receiver = asyncio.create_task(ws.receive_text())
        try:
            await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
        finally:
            sender.cancel()
            receiver.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


os.makedirs(THUMBS, exist_ok=True)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")
app.mount("/thumbs", StaticFiles(directory=THUMBS), name="thumbs")

if __name__ == "__main__":
    raise SystemExit("Internal engine only. Run python3 webapp/server.py for authenticated access.")
