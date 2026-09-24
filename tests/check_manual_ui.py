"""Offline direct sign-in control flow; does not launch a worker or contact platforms."""
from pathlib import Path
from playwright.sync_api import sync_playwright
STATIC = Path(__file__).resolve().parents[1] / 'webapp' / 'static'
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    state = {'manual': False, 'can_finish': False}
    def route(r):
        path = r.request.url.split('test.local')[-1]
        if path == '/':
            r.fulfill(path=str(STATIC / 'index.html'), content_type='text/html')
        elif path.startswith('/static/'):
            r.fulfill(path=str(STATIC / path.removeprefix('/static/')))
        elif path == '/api/auth/me':
            r.fulfill(json={'id': 'fixture', 'email': 'fixture@example.com'})
        elif path == '/api/accounts':
            r.fulfill(json={'chrome': not state['manual'], 'manual_available': True, 'manual_active': state['manual']})
        elif path == '/api/jobs':
            r.fulfill(json=[])
        elif path == '/api/manual-login/start?platform=youtube':
            state['manual'] = True
            r.fulfill(json={'ok': True})
        elif path == '/api/manual-login/finish':
            if state['can_finish']:
                state['manual'] = False
                r.fulfill(json={'ok': True})
            else:
                r.fulfill(status=409, json={'error': 'Quit the sign-in Chrome instance with Command-Q, then click Continue.'})
        else:
            r.abort()
    page.route('**/*', route)
    page.route_web_socket('**/ws', lambda ws: None)
    page.goto('http://test.local/')
    page.get_by_role('button', name='Sign in to YouTube in Chrome', exact=True).click()
    page.wait_for_function("document.getElementById('manualFinish').hidden === false")
    assert page.locator('#go').is_disabled()
    assert page.locator('[data-connect="youtube"]').is_disabled()
    page.get_by_role('button', name='Continue after quitting Chrome', exact=True).click()
    page.wait_for_function("document.getElementById('manualStatus').textContent.startsWith('Quit the sign-in')")
    state['can_finish'] = True
    page.get_by_role('button', name='Continue after quitting Chrome', exact=True).click()
    page.wait_for_function("document.getElementById('manualStatus').textContent.startsWith('Profile reopened')")
    assert not page.locator('#go').is_disabled()
    assert not page.locator('[data-connect="youtube"]').is_disabled()
    assert not errors, errors
    browser.close()
print('Passed: direct sign-in UI, blocked posting, quit-first error, resume controls; no JavaScript errors.')
