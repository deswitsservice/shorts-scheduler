#!/usr/bin/env python3
"""Shorts Everywhere helper: posts your videos from YOUR computer, using YOUR signed-in Chrome.

  python3 helper/shorts_helper.py pair --server https://your-site --code ABCD-EFGH
  python3 helper/shorts_helper.py login     # sign in to YouTube / Instagram / Facebook / TikTok once
  python3 helper/shorts_helper.py run       # keep this running while you use the website

Your platform logins stay in ~/.shorts-everywhere/chrome_profile and are never sent anywhere. The helper only
asks the website for jobs and reports progress back.
"""
import argparse, json, os, shutil, socket, sys, threading, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webapp"))
HOME = Path(os.environ.get("SHORTS_HELPER_HOME", Path.home() / ".shorts-everywhere"))
CONFIG = HOME / "config.json"
POLL_SECONDS = 3
SESSION_CHECK_SECONDS = 30


class ServerError(Exception):
    pass


def load_config():
    if not CONFIG.exists():
        sys.exit("Not paired yet. Get a code from the website, then run: helper pair --server URL --code CODE")
    return json.loads(CONFIG.read_text())


def request(cfg, method, path, body=None, raw=False, auth=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(cfg["server"].rstrip("/") + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", "Bearer " + cfg["token"])
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get("error", "")
        except ValueError:
            message = ""
        raise ServerError(f"{exc.code}: {message or exc.reason}") from exc
    except (urllib.error.URLError, socket.timeout, ConnectionError) as exc:
        raise ServerError(f"cannot reach the website ({exc})") from exc
    return payload if raw else json.loads(payload or b"{}")


def pair(server, code, name):
    cfg = {"server": server, "token": ""}
    result = request(cfg, "POST", "/api/helper/pair", {"code": code, "name": name}, auth=False)
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    CONFIG.write_text(json.dumps({"server": server, "token": result["token"]}))
    CONFIG.chmod(0o600)
    print("Paired. Next: 'login' to sign in to your platforms, then 'run'.")


def prepare_bot():
    import bot
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    bot.HERE = str(HOME)
    bot.PROFILE = str(HOME / "chrome_profile")
    return bot


def download(cfg, path, dest):
    Path(dest).write_bytes(request(cfg, "GET", path, raw=True))


def snapshot(job):
    return {"steps": {p: {"state": s["state"], "error": s.get("error"), "detail": s.get("detail", ""), "attempt": s.get("attempt", 0),
                          "log": s.get("log", [])[-300:]} for p, s in job["steps"].items()},
            "current_platform": job.get("current_platform"), "accounts": dict(job["vid"].get("accounts", {}))}


def run_job(cfg, payload, bot, engine):
    jid = payload["id"]
    tmp = HOME / "jobs" / jid
    tmp.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        video = tmp / "video.mp4"
        download(cfg, f"/api/helper-agent/jobs/{jid}/video", video)
        vid = dict(payload["vid"], id=jid, file=str(video), thumbnail=None, accounts={})
        if payload.get("has_thumbnail"):
            vid["thumbnail"] = str(tmp / "thumbnail.png")
            download(cfg, f"/api/helper-agent/jobs/{jid}/thumbnail", vid["thumbnail"])
        job = {"id": jid, "title": vid["title"], "platforms": payload["platforms"], "dry": payload["dry"], "vid": vid,
               "accounts": vid["accounts"], "steps": {p: {"state": "queued", "log": []} for p in payload["platforms"]},
               "started": None, "finished": None, "current_platform": None}
        if not bot.chrome_running():
            bot.start_chrome()
        worker = threading.Thread(target=engine.run_job, args=(job,), daemon=True)
        worker.start()
        while worker.is_alive():
            sync(cfg, jid, job, finished=False)
            time.sleep(2)
        sync(cfg, jid, job, finished=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sync(cfg, jid, job, finished):
    body = snapshot(job)
    body["finished"] = finished
    for attempt in range(5 if finished else 1):
        try:
            request(cfg, "POST", f"/api/helper-agent/jobs/{jid}/sync", body)
            return
        except ServerError as exc:
            print("sync failed:", exc)
            time.sleep(2 + attempt * 3)


def run():
    cfg = load_config()
    bot = prepare_bot()
    import engine
    engine.bot = bot
    sessions, checked = {}, 0.0
    print("Helper running. Keep this window open while you post from the website. Ctrl+C to stop.")
    while True:
        try:
            if time.time() - checked > SESSION_CHECK_SECONDS:
                try:
                    accounts = engine._accounts() if bot.chrome_running() else {}
                except Exception:  # noqa: BLE001
                    accounts = {}
                sessions = {k: bool(accounts.get(k)) for k in ("chrome", "youtube", "meta", "tiktok")}
                checked = time.time()
            reply = request(cfg, "POST", "/api/helper-agent/poll", {"sessions": sessions})
            if reply.get("job"):
                print("Posting:", reply["job"]["vid"]["title"], "->", ", ".join(reply["job"]["platforms"]))
                run_job(cfg, reply["job"], bot, engine)
                checked = 0.0
                print("Finished.")
                continue
        except ServerError as exc:
            print("waiting:", exc)
            time.sleep(10)
        time.sleep(POLL_SECONDS)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair"); p.add_argument("--server", required=True); p.add_argument("--code", required=True)
    p.add_argument("--name", default=socket.gethostname())
    sub.add_parser("login"); sub.add_parser("run")
    args = ap.parse_args()
    if args.cmd == "pair":
        pair(args.server, args.code, args.name)
    elif args.cmd == "login":
        prepare_bot().login()
    else:
        try:
            run()
        except KeyboardInterrupt:
            print("Stopped.")


if __name__ == "__main__":
    main()
