"""Automated WCAG contrast audit of every visible text element in both themes and three UI states.

Run from the repo root:  python3 tests/check_contrast.py
Uses a separate headless Chrome with mocked API data; nothing is posted. Emoji/glyph-only labels are ignored by eye:
read the output for any text (not a lone symbol) below 4.5:1 (3:1 for large text).
"""
import json, time, sys
from playwright.sync_api import sync_playwright
html = open('webapp/static/index.html').read(); conn = open('webapp/static/connections.js').read(); design = open('webapp/static/design.css').read()
now = time.time()
def L(*m): return [{"t": now - 30 + i * 3, "msg": x} for i, x in enumerate(m)]
JOBS = [{"id": "j1", "title": "T", "when": "Now", "platforms": ["youtube", "instagram", "facebook", "tiktok"], "dry": False,
  "steps": {"youtube": {"state": "done", "log": L("Done")}, "instagram": {"state": "running", "log": L("Connecting to posting browser", "Caption trimmed from 2431 to 2200 characters (instagram's limit is 2200).", "Uploading video file")},
            "facebook": {"state": "failed", "log": L("Uploading video file"), "error": "Couldn't find the upload button on the page."}, "tiktok": {"state": "queued", "log": []}},
  "started": now - 40, "finished": None, "current_platform": "instagram", "thumbnail_url": None, "accounts": {"youtube": "amoura"}}]
DONE = json.loads(json.dumps(JOBS)); DONE[0]["finished"] = now - 2; DONE[0]["current_platform"] = None; DONE[0]["steps"]["instagram"]["state"] = "done"; DONE[0]["steps"]["tiktok"]["state"] = "done"

AUDIT = r"""() => {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(x => parseFloat(x)); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); }; return .2126 * f(r) + .7152 * f(g) + .0722 * f(b); };
  const over = (top, bot) => { const a = top[3]; return [top[0] * a + bot[0] * (1 - a), top[1] * a + bot[1] * (1 - a), top[2] * a + bot[2] * (1 - a), 1]; };
  const pageBg = parse(getComputedStyle(document.documentElement).getPropertyValue('--pagebg') || '') || (document.documentElement.dataset.theme === 'dark' ? [11, 9, 23, 1] : [245, 242, 255, 1]);
  const bgOf = el => { const layers = []; let n = el; while (n && n.nodeType === 1) { const cs = getComputedStyle(n); let bg = parse(cs.backgroundColor); const img = cs.backgroundImage;
      if ((!bg || bg[3] === 0) && img && img !== 'none') { const m = img.match(/rgba?\([^)]+\)/g); if (m) { const cols = m.map(parse).filter(Boolean); bg = cols[Math.floor(cols.length / 2)]; } }
      if (bg && bg[3] > 0) { layers.push(bg); if (bg[3] >= .999) break; } n = n.parentElement; }
    let base = pageBg; for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base); return base; };
  const out = [], seen = new Set();
  const path = e => { const p = []; let n = e; for (let i = 0; i < 4 && n && n.nodeType === 1; i++) { p.unshift(n.tagName.toLowerCase() + (n.id ? '#' + n.id : '') + (n.className && typeof n.className === 'string' ? '.' + n.className.trim().split(/\s+/).slice(0, 2).join('.') : '')); n = n.parentElement; } return p.join(' > '); };
  const check = (el, txt, colorOverride) => {
    const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return; const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) return;
    let n = el, op = 1; while (n && n.nodeType === 1) { op *= parseFloat(getComputedStyle(n).opacity); if (getComputedStyle(n).display === 'none') return; n = n.parentElement; } if (op < .3) return;
    const bg = bgOf(el); let fg = parse(colorOverride || cs.color); if (!fg) return; fg = over(fg, bg);
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + .05) / (Math.min(L1, L2) + .05);
    const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700, large = size >= 24 || (size >= 18.66 && bold);
    const need = large ? 3 : 4.5;
    if (ratio < need) { const key = path(el) + '|' + txt.slice(0, 20); if (seen.has(key)) return; seen.add(key); out.push({ ratio: +ratio.toFixed(2), need, text: txt.slice(0, 48), path: path(el), fg: cs.color, bg: `rgb(${bg.slice(0, 3).map(Math.round)})` }); }
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) { const t = walker.currentNode, txt = t.textContent.trim(); if (!txt || !t.parentElement) continue; if (t.parentElement.closest('script,style,canvas,.jclip,.spark')) continue; check(t.parentElement, txt); }
  document.querySelectorAll('input[placeholder], textarea[placeholder]').forEach(e => { const ph = getComputedStyle(e, '::placeholder').color; check(e, 'placeholder: ' + e.placeholder, ph); });
  return out;
}"""

def run(theme, accounts, jobs, tag, dialogs=False):
    with sync_playwright() as p:
        b = p.chromium.launch(channel='chrome', headless=True)
        pg = b.new_page(viewport={'width': 1300, 'height': 1000})
        pg.add_init_script(f"try{{localStorage.setItem('theme','{theme}')}}catch(e){{}}")
        def route(r):
            u = r.request.url
            if u.endswith('/api/jobs'): r.fulfill(json=jobs)
            elif u.endswith('/api/accounts'): r.fulfill(json=accounts)
            elif u.endswith('/connections.js'): r.fulfill(body=conn, content_type='application/javascript')
            elif u.endswith('/static/design.css'): r.fulfill(body=design, content_type='text/css')
            elif u.rstrip('/').endswith('fake.local'): r.fulfill(body=html, content_type='text/html')
            else: r.fulfill(json={})
        pg.route('http://fake.local/**', route); pg.goto('http://fake.local/'); pg.wait_for_timeout(2600)
        pg.evaluate("document.querySelectorAll('details').forEach(d => d.open = true); document.querySelectorAll('[hidden]').forEach(e => { if (e.id === 'manualFinish') e.hidden = false; })")
        res = pg.evaluate(AUDIT)
        if dialogs:
            for d in ('authPrompt', 'connectionDialog'):
                pg.evaluate(f"document.getElementById('{d}').showModal()"); pg.wait_for_timeout(400)
                res += [dict(x, path='[' + d + '] ' + x['path']) for x in pg.evaluate(AUDIT) if 'dialog' in x['path'].lower() or True][:0]
                pg.evaluate(f"document.getElementById('{d}').close()")
        print(f'== {theme} / {tag}: {len(res)} low-contrast text element(s)')
        for x in res[:60]: print(f"  {x['ratio']:>5} (<{x['need']})  {x['text']!r:52s} fg {x['fg']} on {x['bg']}   {x['path']}")
        b.close()
ACC_OFF = {"chrome": False, "youtube": True, "meta": True, "tiktok": True, "manual_available": True}
ACC_ON = {"chrome": True, "youtube": True, "meta": True, "tiktok": True}
for theme in ('dark', 'light'):
    run(theme, ACC_OFF, [], 'idle, browser off')
    run(theme, ACC_ON, JOBS, 'job running + failure')
    run(theme, ACC_ON, DONE, 'job finished')
