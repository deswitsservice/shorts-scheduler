#!/usr/bin/env python3
"""Runs once a day (via cron) to post that day's videos to all four platforms.
Reads schedule_plan.json, finds today's entries, posts each one same-day (required for
Instagram/Facebook), and records what it did so a second cron fire the same day is a no-op.

Usage: python3 daily_post.py [--dry-run] [--date YYYY-MM-DD]
"""
import argparse, datetime, json, os, sys, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bot  # noqa: E402
import thumb  # noqa: E402

PLAN = os.path.join(HERE, "schedule_plan.json")
STATE = os.path.join(HERE, "daily_post_state.json")
LOG = os.path.join(HERE, "daily_post.log")


def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def load_state():
    return json.load(open(STATE)) if os.path.exists(STATE) else {"posted": []}


def save_state(state):
    json.dump(state, open(STATE, "w"), indent=2)


def make_thumbnail(post):
    out = os.path.join(HERE, "shots", "thumbs", f"post{post['num']}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    thumb.compose_marker(os.path.join(post["folder"], "line1.png"), post["title"], out, style="caveat")
    return out


def post_one(post, dry_run):
    vid = {
        "id": f"post{post['num']}",
        "file": os.path.join(post["folder"], "final.mp4"),
        "title": post["title"],
        "description": post["description"],
        "schedule": f"{post['date']} {post['time']}",
        "publish_now": False,
        "made_for_kids": False,
        "story": False,
        "thumbnail": make_thumbnail(post),
    }
    commit = not dry_run
    results = {}
    for step_name, fn in (
        ("youtube", lambda: bot.youtube(vid, commit=commit, on_step=lambda m: log(f"  [yt] {m}"))),
        ("instagram", lambda: bot.meta(vid, "instagram", commit=commit, on_step=lambda m: log(f"  [ig] {m}"))),
        ("facebook", lambda: bot.meta(vid, "facebook", commit=commit, on_step=lambda m: log(f"  [fb] {m}"))),
        ("tiktok", lambda: bot.tiktok(vid, commit=commit, on_step=lambda m: log(f"  [tt] {m}"))),
    ):
        try:
            fn()
            results[step_name] = "ok"
        except SystemExit as e:
            results[step_name] = f"failed: {e}"
            log(f"  {step_name} FAILED: {e}")
        except Exception as e:  # noqa: BLE001
            results[step_name] = f"failed: {type(e).__name__}: {e}"
            log(f"  {step_name} FAILED: {type(e).__name__}: {e}")
            traceback.print_exc()
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=None, help="Override today's date (YYYY-MM-DD), for testing")
    args = ap.parse_args()

    today = args.date or datetime.date.today().isoformat()
    plan = json.load(open(PLAN))["posts"]
    state = load_state()
    todays = [p for p in plan if p["date"] == today and p["num"] not in state["posted"]]

    log(f"=== daily_post run for {today}{' (DRY RUN)' if args.dry_run else ''}: {len(todays)} post(s) due ===")
    if not todays:
        log("Nothing due today (already posted, or no entries for this date). Exiting.")
        return

    if not bot.chrome_running():
        log("Starting browser...")
        bot.start_chrome()

    for post in todays:
        log(f"--- Post {post['num']}: {post['title']} at {post['time']} ---")
        if not os.path.exists(os.path.join(post["folder"], "final.mp4")):
            log(f"  SKIPPED: final.mp4 not found in {post['folder']}")
            continue
        results = post_one(post, args.dry_run)
        log(f"  results: {results}")
        if not args.dry_run and all(v == "ok" for v in results.values()):
            state["posted"].append(post["num"])
            save_state(state)
        elif not args.dry_run:
            log("  NOT marked as posted (at least one platform failed) -- will retry on next run.")

    try:
        bot.show_idle_screen()
    except Exception:  # noqa: BLE001
        pass
    log(f"=== daily_post run for {today} complete ===")


if __name__ == "__main__":
    main()
