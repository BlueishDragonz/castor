import asyncio, json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page()
        await page.goto('http://127.0.0.1:18765/')
        await page.locator('input[type=password]').first.wait_for()
        await page.wait_for_timeout(1200)
        info=await page.locator('input').evaluate_all('els => els.map(e => ({id:e.id,autocomplete:e.autocomplete,empty:e.value === "",ignore:e.hasAttribute("data-1p-ignore")}))')
        print(json.dumps(info))
        assert len(info)==4
        assert all(x['empty'] for x in info)
        assert [x['autocomplete'] for x in info]==['off','new-password','new-password','new-password']
        assert all(x['ignore'] for x in info)
        # Use ordinary non-secret nickname text to verify no delayed clearing.
        await page.locator('input').first.fill('Laptop')
        await page.wait_for_timeout(11000)
        assert await page.locator('input').first.input_value()=='Laptop'
        await page.reload()
        await page.locator('input[type=password]').first.wait_for()
        assert await page.locator('input').evaluate_all('els => els.every(e => e.value === "")')
        print('DOM_PASS: attributes, empty initial/reload, typing preserved')
        await browser.close()
asyncio.run(main())
