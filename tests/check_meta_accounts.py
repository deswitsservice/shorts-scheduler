"""Local DOM fixtures only; never opens platform websites or submits posts."""
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot

def fixture(rows):
    return '<h2>Reel details</h2><img alt="Instagram" src="data:,">'+''.join(
        '<div role="option" aria-selected="'+str(selected).lower()+'" onclick="this.setAttribute(\'aria-selected\',this.getAttribute(\'aria-selected\')!==\'true\');this.parentElement.appendChild(this)"><img alt="'+platform+'" src="data:,">'+name+'</div>'
        for name,platform,selected in rows)

with sync_playwright() as p:
    browser=p.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page()
    cases=[
        ('instagram','Two',[('One','Instagram',True),('Two','Instagram',False),('Page','Facebook',True)],'Two'),
        ('instagram',None,[('Same','Facebook',True),('Same','Instagram',False)],'Same'),
        ('facebook','Same',[('Same','Facebook',False),('Same','Instagram',True)],'Same'),
    ]
    for target,name,rows,expected in cases:
        page.set_content(fixture(rows))
        assert bot.select_meta_account(page,target,name)==expected
        selected=page.locator('[role=option][aria-selected=true]')
        assert selected.count()==1
        assert selected.inner_text()==expected
        assert selected.locator('img').get_attribute('alt')==target.title()
    for name in [None,'Missing']:
        page.set_content(fixture([('One','Instagram',True),('Two','Instagram',False)]))
        try: bot.select_meta_account(page,'instagram',name)
        except SystemExit: pass
        else: raise AssertionError('Ambiguous or missing account must stop before upload')
    browser.close()
print('Passed Meta fixtures: exact account, same-name platforms, reordered rows, ambiguous and missing accounts.')
