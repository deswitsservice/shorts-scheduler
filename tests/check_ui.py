"""Offline UI regression checks; no connection to the posting browser or platforms."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parents[1] / 'webapp' / 'static'

def job(jid, state, current=None, error=None):
    return dict(id=jid, title=jid, when='2026-11-27 10:00', dry=True,
                platforms=['youtube'], started=1790030000, finished=1 if state=='failed' else None,
                current_platform=current, thumbnail_url=None, accounts={'youtube':'UCverified'},
                steps={'youtube': dict(state=state, log=[], error=error)})

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel="chrome")
    page = browser.new_page(viewport={'width':1536,'height':1024})
    errors=[]
    page.on('pageerror', lambda e: errors.append(str(e)))
    jobs=[job('new queued job','queued'), job('active job','running','youtube')]
    def route(r):
        path=r.request.url.split('test.local')[-1]
        if path=='/api/auth/me': r.fulfill(json={'id':'fixture', 'email':'test@example.com'})
        elif path=='/api/jobs': r.fulfill(json=jobs)
        elif path=='/api/start': r.fulfill(status=503,json={'error':'Chrome did not become ready'})
        elif path=='/api/accounts': r.fulfill(json={'chrome':False})
        elif path=='/static/design.css': r.fulfill(path=str(STATIC/'design.css'),content_type='text/css')
        elif path=='/': r.fulfill(path=str(STATIC/'index.html'),content_type='text/html')
        else: r.abort()
    page.route('**/*',route)
    page.route_web_socket('**/ws',lambda ws: None)
    page.goto('http://test.local/')
    page.wait_for_function("document.querySelector('#summaryTitle').textContent === 'active job'")
    assert 'Running' in page.locator('#statusText').inner_text()
    page.locator('#jobSelect').select_option('new queued job')
    page.wait_for_function("document.querySelector('#summaryTitle').textContent === 'new queued job'")
    assert page.locator('#statusText').inner_text() == 'Queued'
    assert 'YouTube' in page.locator('#browserTitle').inner_text()
    page.locator('#jobSelect').select_option('')
    page.wait_for_function("document.querySelector('#summaryTitle').textContent === 'active job'")
    assert page.locator('#mfkGroup .choice-card.on').count()==1
    page.locator('#startBrowserBtn').click()
    page.wait_for_function("document.querySelector('#browserMessage').textContent.includes('Chrome did not become ready')")
    assert page.locator('#startBrowserBtn').is_enabled()
    page.locator('#desc').fill(' '.join('#tag'+str(i) for i in range(31)))
    assert page.locator('#tagCount').inner_text() == '31 / 30 hashtags'
    assert page.locator('#tags').evaluate('(el)=>!el.validity.valid')
    page.locator('#desc').fill('')
    page.locator('#when').fill('2099-01-01T12:00')
    assert page.locator('#when').evaluate('(el)=>!el.validity.valid')
    page.locator('input[name=mode][value=now]').check()
    assert page.locator('#when').is_disabled()
    assert page.locator('#when').evaluate('(el)=>el.validationMessage') == ''
    assert page.locator('#thumbTitle').is_disabled()
    page.locator('#thumb').set_input_files({'name':'cover.png','mimeType':'image/png','buffer':b'placeholder'})
    assert page.locator('#thumbTitle').is_enabled()
    page.locator('[data-clear=thumb]').click()
    assert page.locator('#thumbTitle').is_disabled()
    page.locator('input[name=p][value=instagram]').uncheck()
    page.locator('input[name=p][value=facebook]').uncheck()
    assert page.locator('#story').is_disabled()
    page.locator('input[name=p][value=instagram]').check()
    assert page.locator('#story').is_enabled()
    jobs[:]=[job('failed job','failed',error='Browser unavailable')]
    page.evaluate('refreshJobs()')
    assert 'Browser unavailable' in page.locator('#stepsList').inner_text()
    assert page.locator('#statusText').inner_text()=='Failed'
    assert page.locator('#summaryAccounts').inner_text() == 'YouTube: UCverified'
    assert 'Preview only' in page.locator('#summaryWhen').inner_text()
    assert page.locator('#summaryBar').evaluate('(e)=>e.parentElement.classList.contains("card")')
    page.screenshot(path='/tmp/love-shorts-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile overflow'
    page.screenshot(path='/tmp/love-shorts-mobile.png',full_page=True)
    jobs.clear()
    page.evaluate('refreshJobs()')
    assert page.locator('#summaryBar').is_hidden()
    assert page.locator('#statusText').inner_text() == 'Idle'
    assert not errors,errors
    browser.close()
print('UI checks passed: active-job priority, early errors, preview labeling, initial selection, summary placement, mobile overflow; no JS errors.')
