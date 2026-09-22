#!/usr/bin/env python3
"""Upload/schedule bot driving a saved browser profile.

  python3 bot.py login                 open the browser once; sign in to YouTube, TikTok, Facebook; close it
"""
import sys, os, re, subprocess, time, urllib.request, json, datetime
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, "chrome_profile")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9222
SITES = ["https://studio.youtube.com", "https://www.tiktok.com/tiktokstudio", "https://business.facebook.com"]


def chrome_running():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
        return True
    except Exception:
        return False


def start_chrome(urls=()):
    """Plain Chrome (not launched by Playwright, so Google sign-in works) with a debug port. Starts at most one."""
    if chrome_running():
        return
    already = subprocess.run(["pgrep", "-f", f"user-data-dir={PROFILE}"], capture_output=True, text=True).stdout.strip()
    if not already:
        subprocess.Popen([CHROME, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={PORT}",
                          "--no-first-run", "--no-default-browser-check", *urls],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):
        if chrome_running():
            break
        time.sleep(0.5)


def login():
    start_chrome(SITES)
    print("Chrome opened. Sign in to YouTube, TikTok and Facebook in that window, then tell Claude. Leave it open.")

CHANNEL = "UC4gsfYjRlgp36oWy54juMHw"


def get_page(ctx, host):
    for pg in ctx.pages:
        if host in pg.url:
            return pg
    return ctx.new_page()


def fresh_page(ctx, match):
    """New tab for a flow; stale tabs of the same site are closed without the 'leave page?' prompt."""
    for pg in list(ctx.pages):
        if match in pg.url:
            try:
                pg.close(run_before_unload=False)
            except Exception:
                pass
    return ctx.new_page()


def yt_fill_schedule(pg, when):
    """when = datetime. Assumes the Visibility step is open."""
    if not pg.locator("#datepicker-trigger").is_visible():
        pg.locator("#second-container-expand-button").click()
        pg.wait_for_timeout(600)
    trig = pg.locator("#datepicker-trigger")
    trig.scroll_into_view_if_needed()
    trig.click()
    pg.wait_for_timeout(600)
    cell = pg.locator("span.calendar-day:not(.disabled)").filter(has_text=re.compile(rf"^\s*{when.day}\s*$")).first
    cell.click()
    pg.wait_for_timeout(600)
    got = trig.inner_text().strip()
    if when.strftime("%b %-d, %Y") not in got:
        sys.exit(f"Date not applied (wanted {when:%b %-d, %Y}, page shows {got!r})")
    t = pg.locator("ytcp-datetime-picker #time-of-day-container input, ytcp-datetime-picker input").last
    t.click()
    t.fill(when.strftime("%-I:%M %p"))
    t.press("Enter")
    pg.wait_for_timeout(600)
    return pg.locator("ytcp-datetime-picker").inner_text().replace("\n", " ")


def youtube(vid, commit=False, resume=False, on_step=lambda msg: None):
    now = vid.get("publish_now", False)
    when = None if now else (datetime.datetime.strptime(vid["schedule"], "%Y-%m-%d %H:%M") if vid.get("schedule") else None)
    if not now and when is None:
        sys.exit("youtube: no schedule time and publish_now is not set")
    on_step("Opening Chrome browser")
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        on_step("Navigating to YouTube Studio")
        pg = get_page(b.contexts[0], "studio.youtube.com") if resume else fresh_page(b.contexts[0], "studio.youtube.com")
        pg.bring_to_front()
        if not resume:
            pg.goto(f"https://studio.youtube.com/channel/{CHANNEL}/videos/upload?d=ud")
            on_step("Uploading video file")
            pg.locator("input[type=file]").first.set_input_files(vid["file"], timeout=180000)
            pg.wait_for_selector("#title-textarea #textbox", timeout=60000)
            on_step("Adding title and description")
            t = pg.locator("#title-textarea #textbox"); t.click(); t.fill(vid["title"])
            d = pg.locator("#description-textarea #textbox"); d.click(); d.fill(vid["description"])
            if vid.get("thumbnail"):
                ti = pg.locator("input[type=file][accept*='image']")
                print("youtube: thumbnail inputs found:", ti.count())
                if ti.count():
                    on_step("Setting custom thumbnail")
                    ti.first.set_input_files(vid["thumbnail"], timeout=60000)
                    pg.wait_for_timeout(3000)
            on_step("Setting audience and content options")
            mfk_name = "VIDEO_MADE_FOR_KIDS_MFK" if vid.get("made_for_kids") else "VIDEO_MADE_FOR_KIDS_NOT_MFK"
            pg.locator(f"[name={mfk_name}]").first.click()
            pg.locator("#toggle-button").click()
            pg.locator("[name=VIDEO_HAS_ALTERED_CONTENT_YES]").first.click()
            for _ in range(3):
                pg.locator("#next-button").click()
                pg.wait_for_timeout(1500)
        if now:
            on_step("Setting video to Public")
            pub = pg.locator("tp-yt-paper-radio-button[name=PUBLIC]")
            pub.scroll_into_view_if_needed(); pub.click(); pg.wait_for_timeout(500)
            if pub.get_attribute("aria-checked") != "true":
                sys.exit("youtube: could not select Public visibility")
        elif when:
            on_step("Setting publish schedule")
            print("Schedule fields now read:", yt_fill_schedule(pg, when))
        pg.screenshot(path=os.path.join(HERE, "shots", f"yt_{vid['id']}.png"))
        if commit and (when or now):
            on_step("Publishing video" if now else "Scheduling video")
            pg.locator("#done-button").click()
            pg.wait_for_timeout(4000)
            on_step("Done")
            print("YouTube:", "published" if now else "scheduled", vid["id"], "" if now else f"for {when}")
        else:
            print("YouTube: stopped before the final button (dry run).")


PAGE_ASSET = "1310083138856565"
BUSINESS = "1662852205306739"
ACCOUNTS = {"instagram": "amourawhispers", "facebook": "Amoura"}


def meta(vid, target, at=None, commit=False, story=None, on_step=lambda msg: None):
    """Meta Business Suite Reel to one account (instagram or facebook), scheduled today (or posted immediately if
    vid['publish_now'] is set)."""
    now = vid.get("publish_now", False)
    when = None
    if not now:
        when = datetime.datetime.combine(datetime.date.today(), datetime.datetime.strptime(at, "%H:%M").time()) if at else \
            datetime.datetime.strptime(vid["schedule"], "%Y-%m-%d %H:%M")
    caption = vid["description"].replace(" #shorts", "")
    on_step("Opening Chrome browser")
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        on_step(f"Navigating to Meta Business Suite ({target})")
        pg = fresh_page(b.contexts[0], "business.facebook.com/latest")
        pg.bring_to_front()
        pg.goto(f"https://business.facebook.com/latest/reels_composer?asset_id={PAGE_ASSET}&business_id={BUSINESS}")
        pg.wait_for_selector("text=Reel details", timeout=60000)
        pg.wait_for_timeout(2000)
        on_step(f"Selecting {target} account")
        want = ACCOUNTS[target]
        trigger = pg.locator("text=/Amoura|amourawhispers/").first
        trigger.click(); pg.wait_for_timeout(1000)
        for name in ACCOUNTS.values():
            cur = pg.locator("text=/Amoura|amourawhispers/").first.inner_text()
            selected = name in cur
            if selected != (name == want):
                pg.get_by_text(name, exact=True).last.click(); pg.wait_for_timeout(700)
        pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
        shown = pg.locator("text=/Amoura|amourawhispers/").first.inner_text()
        if shown.strip() != want:
            sys.exit(f"Post-to shows {shown!r}, wanted only {want!r}")
        on_step("Uploading video file")
        with pg.expect_file_chooser(timeout=30000) as fc:
            pg.get_by_role("button", name="Add Video").click()
        try:
            fc.value.set_files(vid["file"], timeout=180000)
        except Exception:
            pass
        pg.wait_for_selector("text=100%", timeout=240000)
        on_step("Adding caption")
        cf = pg.locator("textarea, div[role=textbox]").first
        cf.click(); cf.fill(caption); pg.keyboard.press("Escape"); pg.wait_for_timeout(800)
        if vid.get("thumbnail"):
            on_step("Setting custom thumbnail")
            pg.get_by_text("Upload image", exact=True).first.click(); pg.wait_for_timeout(1000)
            with pg.expect_file_chooser(timeout=20000) as fc2:
                pg.get_by_text("Upload image", exact=True).last.click()
            fc2.value.set_files(vid["thumbnail"]); pg.wait_for_timeout(4000)
            print(f"{target}: thumbnail set")
        for _ in range(4):
            if pg.get_by_text("Scheduling options").count():
                break
            nxt = pg.get_by_role("button", name="Next").last
            for _ in range(60):
                if nxt.get_attribute("aria-disabled") != "true":
                    break
                pg.wait_for_timeout(1000)
            nxt.click(); pg.wait_for_timeout(5000)
        if not now:
            on_step("Setting publish schedule")
            pg.get_by_role("button", name="Schedule").first.click(); pg.wait_for_timeout(1500)
            for name, val in (("hours", when.strftime("%I")), ("minutes", when.strftime("%M")), ("meridiem", when.strftime("%p"))):
                el = pg.locator(f"input[aria-label='{name}']").first
                el.click(); pg.keyboard.press("Meta+A"); pg.keyboard.type(val, delay=80)
            pg.wait_for_timeout(600)
        story = vid.get("story", False) if story is None else story
        toggles = pg.locator("input[aria-label*='story' i]")
        if toggles.count():
            on_step("Setting story sharing")
            for i in range(toggles.count()):
                t = toggles.nth(i)
                if t.is_checked() != story:
                    t.click(force=True)
                    pg.wait_for_timeout(500)
            print(f"{target}: story toggle set to {'ON' if story else 'OFF'} ({toggles.count()} found)")
        elif story:
            print(f"{target}: WARNING no story option on this screen; posted without story")
        if not now:
            datev = pg.locator("input[aria-label='mm/dd/yyyy'], input[placeholder='mm/dd/yyyy']").first.input_value()
            if datev != when.strftime("%b %-d, %Y"):
                sys.exit(f"Date shows {datev!r}; only same-day scheduling is automated so far")
        pg.mouse.move(350, 300); pg.mouse.wheel(0, -600); pg.wait_for_timeout(500)
        pg.screenshot(path=os.path.join(HERE, "shots", f"meta_{target}_{vid['id']}.png"))
        print(f"{target}: ready to {'post now' if now else f'post at {when:%b %-d %-I:%M %p}'}")
        if commit:
            on_step("Sharing post" if now else "Scheduling post")
            btn_name = "Share now" if now else "Schedule"
            pg.get_by_role("button", name=btn_name).last.click()
            pg.wait_for_selector("text=/Reel scheduled|scheduled to publish|shared|posted/i", timeout=90000)
            try:
                pg.get_by_role("button", name="Done").click(timeout=5000)
            except Exception:
                pass
            on_step("Done")
            print(f"{target}: {'posted' if now else 'scheduled'} {vid['id']}")
        else:
            on_step("Stopped before publishing (dry run)")
            print(f"{target}: stopped before the final button (dry run).")


def tiktok(vid, at=None, commit=False, on_step=lambda msg: None):
    now = vid.get("publish_now", False)
    when = None
    if not now:
        when = datetime.datetime.combine(datetime.date.today(), datetime.datetime.strptime(at, "%H:%M").time()) if at else \
            datetime.datetime.strptime(vid["schedule"], "%Y-%m-%d %H:%M")
        if when.minute % 5:
            sys.exit("TikTok schedules in 5-minute steps")
    caption = vid["description"].replace(" #shorts", "")
    on_step("Opening Chrome browser")
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        on_step("Navigating to TikTok Studio")
        pg = fresh_page(b.contexts[0], "tiktok.com")
        pg.bring_to_front()
        pg.goto("https://www.tiktok.com/tiktokstudio/upload?from=webapp")
        on_step("Uploading video file")
        pg.locator("input[type=file]").first.set_input_files(vid["file"], timeout=180000)
        pg.wait_for_selector("text=Uploaded", timeout=240000)
        pg.wait_for_timeout(2000)
        try:
            pg.get_by_role("button", name="Got it").click(timeout=3000)
        except Exception:
            pass
        if vid.get("thumbnail"):
            on_step("Setting cover image")
            pg.get_by_text("Edit cover").first.scroll_into_view_if_needed()
            pg.get_by_text("Edit cover").first.click(); pg.wait_for_timeout(2500)
            pg.locator("input[type=file][accept*='image']").first.set_input_files(vid["thumbnail"], timeout=60000)
            pg.wait_for_timeout(2500)
            pg.get_by_role("button", name="Save", exact=True).last.click(); pg.wait_for_timeout(2500)
            print("tiktok: cover set")
        on_step("Adding caption")
        ed = pg.locator(".public-DraftEditor-content").first
        ed.scroll_into_view_if_needed(); ed.click()
        pg.keyboard.press("Meta+A"); pg.keyboard.press("Backspace")
        pg.keyboard.insert_text(caption); pg.wait_for_timeout(1200)
        if not now:
            on_step("Setting publish schedule")
            pg.locator("input[value=schedule]").locator("xpath=..").click(); pg.wait_for_timeout(1200)
            try:
                pg.get_by_role("button", name="Allow").click(timeout=4000)
                pg.wait_for_timeout(1200)
            except Exception:
                pass
            pg.locator("input.TUXTextInputCore-input").first.click(); pg.wait_for_timeout(800)
            pg.locator("span.tiktok-timepicker-left").filter(has_text=re.compile(rf"^{when:%H}$")).first.click(); pg.wait_for_timeout(400)
            pg.locator("span.tiktok-timepicker-right").filter(has_text=re.compile(rf"^{when:%M}$")).first.click(); pg.wait_for_timeout(600)
            vals = pg.eval_on_selector_all("input.TUXTextInputCore-input", "els=>els.map(e=>e.value+'|'+e.getAttribute('aria-invalid'))")
            if vals[0] != f"{when:%H:%M}|false" or not vals[1].startswith(f"{when:%Y-%m-%d}"):
                sys.exit(f"TikTok schedule fields read {vals}, wanted {when:%Y-%m-%d %H:%M}")
            pg.mouse.click(700, 300); pg.wait_for_timeout(500)
        on_step("Turning on AI-generated content label")
        pg.get_by_text("Show more").first.scroll_into_view_if_needed(); pg.get_by_text("Show more").first.click(); pg.wait_for_timeout(1000)
        ai = pg.locator("text=AI-generated content").first.locator("xpath=following::input[@type='checkbox'][1]")
        ai.scroll_into_view_if_needed(); ai.click(force=True); pg.wait_for_timeout(1200)
        try:
            pg.get_by_role("button", name="Turn on").click(timeout=4000)
            pg.wait_for_timeout(1000)
        except Exception:
            pass
        if not ai.is_checked():
            sys.exit("AI-generated content label did not turn on")
        on_step("Running TikTok's content checks")
        pg.wait_for_function("!document.body.innerText.includes('Checks can only start after the file is uploaded')", timeout=300000)
        btn = pg.get_by_role("button", name="Schedule", exact=True).last
        for _ in range(120):
            if btn.is_enabled():
                break
            pg.wait_for_timeout(1000)
        pg.mouse.move(600, 300); pg.mouse.wheel(0, 3000); pg.wait_for_timeout(800)
        pg.screenshot(path=os.path.join(HERE, "shots", f"tiktok_{vid['id']}.png"))
        print(f"tiktok: ready to {'post now' if now else f'post at {when:%b %-d %-I:%M %p}'}, AI label on")
        if commit:
            on_step("Posting video" if now else "Scheduling video")
            btn_name = "Post" if now else "Schedule"
            pg.get_by_role("button", name=btn_name, exact=True).last.click()
            try:
                pg.get_by_role("button", name="Post now").click(timeout=10000)
            except Exception:
                pass
            pg.wait_for_url("**/tiktokstudio/content**", timeout=90000, wait_until="commit")
            on_step("Done")
            print(f"tiktok: {'posted' if now else 'scheduled'} {vid['id']}")
        else:
            on_step("Stopped before publishing (dry run)")
            print("tiktok: stopped before the final button (dry run).")


def prepare_thumbnail(vid):
    """Put the title (centered) on the chosen picture; the composed file replaces vid['thumbnail']."""
    src = vid.get("thumbnail")
    if src and vid.get("thumb_title", True) and not vid.get("_thumb_done"):
        import thumb
        out = os.path.join(HERE, "shots", "thumbs", f"{vid['id']}.png")
        thumb.compose(src, vid["title"], out)
        vid["thumbnail"], vid["_thumb_done"] = out, True
    return vid


def youtube_thumbnail(title_start, thumb_path):
    """Set a custom thumbnail on an existing SCHEDULED Short (matched by title prefix) in YouTube Studio."""
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        pg = fresh_page(b.contexts[0], "studio.youtube.com")
        pg.bring_to_front()
        pg.goto(f"https://studio.youtube.com/channel/{CHANNEL}/videos/short")
        pg.wait_for_selector("ytcp-video-row", timeout=60000)
        pg.wait_for_timeout(2500)
        rows = pg.locator("ytcp-video-row")
        target = None
        for i in range(rows.count()):
            t = rows.nth(i).inner_text()
            if title_start in t and "Scheduled" in t:
                target = rows.nth(i)
                break
        if target is None:
            sys.exit(f"No scheduled video starting with {title_start!r} found")
        target.locator("a#video-title").click()
        pg.wait_for_selector("input[type=file][accept*='image']", state="attached", timeout=60000)
        pg.wait_for_timeout(2000)
        pg.locator("input[type=file][accept*='image']").first.set_input_files(thumb_path, timeout=60000)
        pg.wait_for_timeout(4000)
        pg.screenshot(path=os.path.join(HERE, "shots", "yt_thumb_edit.png"))
        pg.locator("#save").click()
        pg.wait_for_timeout(5000)
        print("youtube: thumbnail saved for", title_start)


def load(vid_id):
    for v in json.load(open(os.path.join(HERE, "queue.json")))["videos"]:
        if v["id"] == vid_id:
            return prepare_thumbnail(v)
    sys.exit(f"unknown id {vid_id}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login":
        login()
    elif cmd == "ytthumb":
        youtube_thumbnail(sys.argv[2], sys.argv[3])
    elif cmd == "tiktok":
        at = sys.argv[sys.argv.index("--at") + 1] if "--at" in sys.argv else None
        tiktok(load(sys.argv[2]), at=at, commit="--commit" in sys.argv)
    elif cmd == "meta":
        at = sys.argv[sys.argv.index("--at") + 1] if "--at" in sys.argv else None
        st = True if "--story" in sys.argv else (False if "--no-story" in sys.argv else None)
        meta(load(sys.argv[2]), sys.argv[3], at=at, commit="--commit" in sys.argv, story=st)
    elif cmd == "youtube":
        youtube(load(sys.argv[2]), commit="--commit" in sys.argv, resume="--resume" in sys.argv)
    else:
        print(__doc__)
