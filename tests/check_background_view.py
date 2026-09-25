"""Local browser fixtures only: no account access, uploads, or publishing."""
import asyncio
import sys
from pathlib import Path
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
from webapp.engine import find_automation_page

with sync_playwright() as p:
    b=p.chromium.launch(channel='chrome',headless=True)
    ctx=b.new_context()
    page=ctx.new_page()
    page.set_content('<h1>Posting fixture</h1>')
    assert bot.fresh_page(ctx,'studio.youtube.com') is page
    assert len(ctx.pages)==1
    bot.track_automation_page(page)
    assert bot.ACTIVE_TARGET_ID
    b.close()
    bot.ACTIVE_TARGET_ID=None

async def check():
    async with async_playwright() as p:
        b=await p.chromium.launch(channel='chrome',headless=True)
        ctx=await b.new_context()
        posting=await ctx.new_page()
        await posting.set_content('<h1>Posting fixture</h1>')
        cdp=await ctx.new_cdp_session(posting)
        target=(await cdp.send('Target.getTargetInfo'))['targetInfo']['targetId']
        await cdp.detach()
        unrelated=await ctx.new_page()
        await unrelated.set_content('<h1>Unrelated page</h1>')
        await unrelated.bring_to_front()
        found=await find_automation_page(ctx,target)
        assert found is posting
        assert await found.locator('h1').inner_text()=='Posting fixture'
        assert (await found.screenshot(type='jpeg'))[:2]==b'\xff\xd8'
        assert await find_automation_page(ctx,None) is None
        await posting.close()
        assert await find_automation_page(ctx,target) is None
        await b.close()
asyncio.run(check())
print('Passed: tab reuse, explicit target tracking, capture with unrelated tab selected, idle and closed targets.')
