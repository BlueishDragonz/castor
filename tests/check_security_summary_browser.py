"""Real Chromium summary/dialog acceptance; local fixture page only.
Run alongside security_dom_harness.py. Never enters real credentials.
"""
import asyncio
from playwright.async_api import async_playwright, expect

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for width in (390, 1280):
            page = await browser.new_page(viewport={'width':width,'height':850})
            await page.goto('http://127.0.0.1:18765/')
            await expect(page.get_by_role('button',name='Add passkey',exact=True)).to_be_visible()
            await expect(page.get_by_text('Recovery email',exact=True)).to_be_visible()
            await expect(page.get_by_text('fixture@example.com',exact=True)).to_be_visible()
            assert await page.locator('input:visible').count() == 0
            await page.get_by_role('button',name='Add passkey',exact=True).click()
            await expect(page.get_by_role('dialog')).to_be_visible()
            assert await page.locator('input:visible').count() == 1
            assert await page.locator('input:visible').input_value() == ''
            await page.get_by_role('button',name='Cancel',exact=True).click()
            await expect(page.get_by_role('dialog')).not_to_be_visible()
            await page.get_by_role('button',name='Change password',exact=True).click()
            await expect(page.get_by_role('dialog')).to_be_visible()
            assert await page.locator('input:visible').count() == 3
            assert await page.locator('input:visible').evaluate_all('els=>els.every(e=>e.value === "")')
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            await page.get_by_role('button',name='Cancel',exact=True).click()
            await page.close()
        await browser.close()
        print('PASS: summary recovery email, no page inputs, both dialogs, empty fields, 390/1280px no horizontal overflow')

if __name__ == '__main__': asyncio.run(main())
