#!/usr/bin/env python3
"""Upload/schedule bot driving a saved browser profile.

  python3 bot.py login                 open the browser once; sign in to YouTube, TikTok, Facebook; close it
"""
import sys, os, re, glob, subprocess, time, urllib.request, json, datetime
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, "chrome_profile")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9222
ACTIVE_TARGET_ID = None  # Explicit tab identity for the in-app viewer; never inferred from window focus.
SITES = ["https://studio.youtube.com", "https://www.tiktok.com/tiktokstudio", "https://business.facebook.com"]


def chrome_running():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
        return True
    except Exception:
        return False


def _move_running_chrome_offscreen():
    """-g/-j (below) only affect Chrome's own startup -- they can't do anything about a window that's
    already open in the foreground when start_chrome() is called on an already-running instance (confirmed
    during review). Push it off-screen directly via CDP instead, the same way the launch flags do for a
    fresh start."""
    try:
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}", no_defaults=True)
            for ctx in b.contexts:
                for pg in ctx.pages:
                    session = pg.context.new_cdp_session(pg)
                    try:
                        target_id = session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
                        window_id = session.send("Browser.getWindowForTarget", {"targetId": target_id})["windowId"]
                        session.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"left": -2400, "top": -2400}})
                    finally:
                        session.detach()
                    return  # one window move covers this single-profile automation instance
    except Exception:
        pass  # best-effort -- don't let a repositioning failure block the caller


def start_chrome(urls=(), background=True):
    """Plain Chrome (not launched by Playwright, so Google sign-in works) with a debug port. Starts at most one."""
    if chrome_running():
        if background:
            _move_running_chrome_offscreen()
        return
    already = subprocess.run(["pgrep", "-f", f"user-data-dir={PROFILE}"], capture_output=True, text=True).stdout.strip()
    if not already:
        # A forceful kill (pkill -9, crash, force-quit) leaves Chrome's own SingletonLock
        # files behind, since only a graceful quit removes them. On the next launch Chrome's
        # internal lock-detection can get confused by the stale lock and exit shortly after
        # starting -- flaky, not a deterministic failure. Since we've just confirmed no live
        # process holds this profile, any lock files here are stale; clear them first.
        for f in glob.glob(os.path.join(PROFILE, "Singleton*")):
            try:
                os.remove(f)
            except OSError:
                pass
        # Keep regular Chrome positioned away from the dashboard. OS window placement
        # may vary; this is a presentation preference, not an anti-detection measure.
        command = ["open", "-n"]
        if background:
            # -g alone isn't reliable for Chrome: it can self-activate shortly after its first
            # window appears, overriding the "don't foreground" hint. -j launches it already
            # hidden (like Cmd+H), a stronger state Chrome has to actively undo, not just skip.
            command += ["-g", "-j"]
        command += ["-a", "/Applications/Google Chrome.app", "--args",
                    f"--user-data-dir={PROFILE}", f"--remote-debugging-port={PORT}",
                    "--no-first-run", "--no-default-browser-check", "--window-size=" + os.environ.get("SHORTS_WINDOW_SIZE", "1800x900").lower().replace("x", ",")]
        if background:
            command.append("--window-position=-2400,-2400")
        command.extend(urls)
        subprocess.Popen(command,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):
        if chrome_running():
            break
        time.sleep(0.5)


def login():
    start_chrome(SITES, background=False)
    print("Chrome opened. Sign in to YouTube, TikTok and Facebook in that window, then tell Claude. Leave it open.")


def show_idle_screen(url="https://www.youtube.com"):
    """Park the browser on one neutral tab (plain YouTube home) instead of Chrome's own New Tab page, which
    surfaces the real profile's personal shortcuts/history. Called after startup and after each job finishes."""
    if not chrome_running():
        return
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}", no_defaults=True)
        ctx = b.contexts[0]
        pages = list(ctx.pages)
        keep = pages[0] if pages else ctx.new_page()
        for pg in pages[1:]:
            try:
                pg.close(run_before_unload=False)
            except Exception:
                pass
        try:
            keep.goto(url, timeout=15000, wait_until="domcontentloaded")
        except Exception:
            pass

def resolve_channel(pg):
    """The channel-less root redirects to whichever channel is signed in -- no hardcoded ID needed,
    so this works for any user's account, not just one specific channel. The redirect timing varies
    (client-side JS resolves sign-in state), so poll for it rather than guessing a fixed delay."""
    pg.goto("https://studio.youtube.com/", wait_until="domcontentloaded", timeout=20000)
    for _ in range(20):
        m = re.search(r"/channel/(UC[\w-]+)", pg.url)
        if m:
            return m.group(1)
        pg.wait_for_timeout(500)
    sys.exit(f"Could not resolve a YouTube channel from the signed-in account (landed on {pg.url!r}).")


def _auto_dismiss_dialogs(pg):
    """connect_over_cdp(no_defaults=True) -- required for Chrome 150+ compatibility -- also turns off
    Playwright's own automatic dialog dismissal. Without this, a native "leave page? changes won't be
    saved" confirm (e.g. navigating away from an unsaved composer draft) has nothing to answer it and
    the call that triggered the navigation hangs/errors instead of just proceeding. Each call site gets
    a fresh Page wrapper (new connect_over_cdp() per with-block), so this is safe to attach every time."""
    pg.on("dialog", lambda dialog: dialog.accept())
    _restore_if_minimized(pg)
    return pg


def _restore_if_minimized(pg):
    """Chrome doesn't render a minimized window, so screenshots hang and every actionability check waits
    forever on "element is not stable" -- and TikTok builds its cover from a rendered frame, so a
    minimized window saves a blank cover (all confirmed live). Un-minimize without foregrounding."""
    try:
        session = pg.context.new_cdp_session(pg)
        try:
            tid = session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
            wid = session.send("Browser.getWindowForTarget", {"targetId": tid})["windowId"]
            if session.send("Browser.getWindowBounds", {"windowId": wid})["bounds"].get("windowState") == "minimized":
                session.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
        finally:
            session.detach()
    except Exception:
        pass  # best effort; never block a posting job on window housekeeping


def get_page(ctx, host):
    for pg in ctx.pages:
        if host in pg.url:
            return _auto_dismiss_dialogs(pg)
    return _auto_dismiss_dialogs(ctx.new_page())


def track_automation_page(page):
    """Let the viewer follow this tab without activating a Chrome window."""
    global ACTIVE_TARGET_ID
    session = page.context.new_cdp_session(page)
    try:
        ACTIVE_TARGET_ID = session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
    finally:
        session.detach()


def fresh_page(ctx, match):
    """Reuse the automation tab; callers navigate to the start of a new upload flow."""
    pages = [page for page in ctx.pages if not page.is_closed()]
    pg = next((page for page in pages if match in page.url), None) or (pages[0] if pages else ctx.new_page())
    return _auto_dismiss_dialogs(pg)


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
    on_step("Connecting to posting browser")
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}", no_defaults=True)
        on_step("Navigating to YouTube Studio")
        pg = get_page(b.contexts[0], "studio.youtube.com") if resume else fresh_page(b.contexts[0], "studio.youtube.com")
        track_automation_page(pg)
        if not resume:
            channel = resolve_channel(pg)
            expected = vid.get("youtube_channel", "").strip()
            if expected and expected != channel:
                sys.exit(f"youtube: signed-in channel is {channel}, not requested {expected}. Switch channels in Chrome before retrying.")
            vid.setdefault("accounts", {})["youtube"] = channel
            on_step(f"YouTube channel: {channel}")
            pg.goto(f"https://studio.youtube.com/channel/{channel}/videos/upload?d=ud")
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
                    # YouTube gates custom thumbnails behind a one-time account-verification prompt
                    # after some number of uses per day (confirmed live) -- that verification needs a
                    # human, so cancel it and continue without a custom thumbnail rather than getting
                    # stuck on a click nothing will ever satisfy.
                    verify = pg.get_by_text("One-time verification needed")
                    if verify.count():
                        on_step("Thumbnail needs one-time verification -- skipping it")
                        pg.get_by_role("button", name="Cancel").click(timeout=5000)
                        pg.wait_for_timeout(1000)
            # The title box appears almost immediately (well under 10% uploaded) -- it's not a signal
            # the transfer is done, only that the form is ready. Without waiting for the real "Upload
            # complete" milestone, a caller can reuse this tab (fresh_page() picks whichever tab is
            # available) for another job while this upload is still in flight; navigating the tab away
            # then silently aborts it, and the script has no way to detect that (confirmed during
            # testing -- a video reported as successfully scheduled never actually finished uploading).
            on_step("Waiting for upload to finish")
            # A JS-string wait_for_function() doesn't work here: YouTube Studio's Trusted Types CSP
            # rejects Playwright's eval-based predicate outright (confirmed live -- "violates this
            # document's Trusted Type assignment requirements"). Poll the same element's text from the
            # Python side instead, which only uses native accessibility/DOM reads, not in-page eval.
            progress = pg.locator("ytcp-video-upload-progress")
            for _ in range(600):
                if progress.count() and "Upload complete" in progress.inner_text():
                    break
                pg.wait_for_timeout(1000)
            else:
                sys.exit("youtube: upload never reached 'Upload complete' within 10 minutes")
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


def _row_state(handle):
    """(platform alt, selected?) for one [role=option] element handle in the account picker."""
    alt = next((a for a in (img.get_attribute("alt") for img in handle.query_selector_all("img"))
                if a in ("Instagram", "Facebook")), None)
    return alt, handle.get_attribute("aria-selected") == "true"


def select_meta_account(pg, target, expected_name=None):
    """Selects exactly one account of the requested platform (instagram or facebook) in the composer's
    account picker, identified by each row's platform icon (img alt="Instagram"/"Facebook") rather than a
    hardcoded display name -- works for any signed-in account, not just one specific one. Returns the
    selected account's display name for logging.

    Operates on element handles pinned to each row's actual DOM node, not locators re-queried by index or
    by name text:
    - an index can go stale, since clicking a row can reorder/re-render the list (confirmed during
      testing -- a stale `.nth(i)` silently referred to a different account after an earlier click)
    - a name isn't a safe re-location key either, since a Facebook Page and its linked Instagram account
      can share the same display name (confirmed during review) -- filtering by that name would then
      match whichever row happens to come first, not necessarily the intended one
    An element handle sidesteps both: it's a direct reference to one specific DOM node, unaffected by
    reordering or by other rows sharing its name.

    Requires a unique target platform/name match and deselects every other row,
    including other rows of the *same* platform: with more than one connected account of a platform, an
    earlier version selected all of them instead of just one (confirmed during review)."""
    want_alt = "Instagram" if target == "instagram" else "Facebook"
    icons = pg.locator("img[alt=Instagram], img[alt=Facebook]")
    if icons.count() == 0:
        sys.exit("meta: no Instagram/Facebook account icons found in the composer")
    icons.first.click(force=True)
    pg.wait_for_timeout(1000)
    handles = pg.locator("[role=option]").element_handles()
    if not handles:
        sys.exit("meta: account picker did not open (no [role=option] rows)")
    states = [_row_state(h) for h in handles]
    target_indices = [i for i, (alt, _) in enumerate(states) if alt == want_alt]
    if not target_indices:
        sys.exit(f"meta: no {want_alt} account found among the {len(handles)} available accounts")
    if expected_name:
        target_indices = [i for i in target_indices if handles[i].inner_text().strip() == expected_name.strip()]
        if not target_indices:
            sys.exit(f"meta: no {want_alt} account exactly matching {expected_name!r}; check the account name.")
    if len(target_indices) != 1:
        sys.exit(f"meta: multiple {want_alt} accounts match. Specify one unique account name in the posting form.")
    keep = target_indices[0]
    target_name = handles[keep].inner_text().strip()
    if len(target_indices) > 1:
        print(f"meta: WARNING {len(target_indices)} {want_alt} accounts connected; using the first ({target_name!r})")
    for i, handle in enumerate(handles):
        want_selected = i == keep
        if states[i][1] == want_selected:
            continue
        handle.click()
        pg.wait_for_timeout(700)
    bad = [i for i, h in enumerate(handles) if _row_state(h)[1] != (i == keep)]
    if bad:
        sys.exit(f"meta: final account selection is wrong at row index(es) {bad} (wanted only {keep}, {target_name!r})")
    # Click a neutral heading rather than pressing Escape (Escape's effect on the underlying
    # selection was unconfirmed during testing) or blind page coordinates (risk of hitting an
    # unrelated control) to close the picker without disturbing the selection just verified above.
    pg.get_by_text("Reel details").first.click(force=True)
    pg.wait_for_timeout(500)
    return target_name


def meta(vid, target, at=None, commit=False, story=None, on_step=lambda msg: None):
    """Meta Business Suite Reel to one account (instagram or facebook), scheduled today (or posted immediately if
    vid['publish_now'] is set)."""
    now = vid.get("publish_now", False)
    when = None
    if not now:
        when = datetime.datetime.combine(datetime.date.today(), datetime.datetime.strptime(at, "%H:%M").time()) if at else \
            datetime.datetime.strptime(vid["schedule"], "%Y-%m-%d %H:%M")
    caption = vid["description"].replace(" #shorts", "")
    on_step("Connecting to posting browser")
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}", no_defaults=True)
        on_step(f"Navigating to Meta Business Suite ({target})")
        pg = fresh_page(b.contexts[0], "business.facebook.com/latest")
        track_automation_page(pg)
        # No asset_id/business_id in the URL: Business Suite auto-resolves to whichever business/page
        # context the signed-in account defaults to -- works for any user, not just one hardcoded pair.
        pg.goto("https://business.facebook.com/latest/reels_composer")
        pg.wait_for_selector("text=Reel details", timeout=60000)
        pg.wait_for_timeout(2000)
        on_step(f"Selecting {target} account")
        want_name = select_meta_account(pg, target, vid.get(f"{target}_account"))
        vid.setdefault("accounts", {})[target] = want_name
        on_step(f"{target.title()} account: {want_name}")
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
                    # Turning story-sharing off (when it was on by default) raises a confirmation
                    # dialog of its own -- "Stop sharing to Facebook Story" -- which otherwise sits
                    # on top of and blocks every later click in the flow, including the final submit
                    # button (confirmed live: this is what "Share" appearing stuck actually was).
                    confirm = pg.get_by_role("button", name="Confirm")
                    if confirm.count():
                        confirm.click(timeout=5000)
                        pg.wait_for_timeout(800)
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
            # "Share now"/"Schedule" (checked above via `when`) are the *mode-selector tabs*, not the
            # submit button -- clicking an already-selected tab is a no-op, which is exactly what
            # happened live (confirmed: the panel stayed put and nothing was submitted). The real
            # submit button is labeled "Schedule" for scheduling (so it happened to share a name with
            # its tab, masking this bug when only the scheduled path had been tested) but "Share" --
            # not "Share now" -- for immediate publish.
            btn_name = "Share" if now else "Schedule"
            pg.get_by_role("button", name=btn_name, exact=True).last.click()
            # Publishing a Reel isn't necessarily instant even for `now=True`: Meta shows "Reel
            # processing -- once it finishes, it will be published and you'll be notified" and this is
            # itself the confirmation of a successful submission (confirmed live), not a failure state.
            pg.wait_for_selector("text=/Reel scheduled|scheduled to publish|shared|posted|Reel processing/i", timeout=90000)
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
    on_step("Connecting to posting browser")
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}", no_defaults=True)
        on_step("Navigating to TikTok Studio")
        pg = fresh_page(b.contexts[0], "tiktok.com")
        track_automation_page(pg)
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
        if len(ed.inner_text().strip()) < min(20, len(caption)):
            # The first click can miss the editor (confirmed live: a post went out with a placeholder /
            # empty caption and TikTok won't let you fix the cover afterwards). Retry once, then fail loudly.
            ed.click(force=True); pg.keyboard.press("Meta+A"); pg.keyboard.press("Backspace")
            pg.keyboard.insert_text(caption); pg.wait_for_timeout(1200)
            if len(ed.inner_text().strip()) < min(20, len(caption)):
                sys.exit("tiktok: caption did not go into the editor")
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
        # The button is labeled "Post" for publish_now, "Schedule" otherwise -- this was hardcoded to
        # "Schedule" regardless of `now`, so a publish_now run waited on a button that doesn't exist
        # here and timed out (reproduced live) even though the final click below already handles both.
        btn = pg.get_by_role("button", name=("Post" if now else "Schedule"), exact=True).last
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
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}", no_defaults=True)
        pg = fresh_page(b.contexts[0], "studio.youtube.com")
        track_automation_page(pg)
        channel = resolve_channel(pg)
        pg.goto(f"https://studio.youtube.com/channel/{channel}/videos/short")
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
