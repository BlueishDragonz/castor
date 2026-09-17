"""Chromium autofill hints in lazily mounted dialogs; no real credentials."""
import asyncio
from playwright.async_api import async_playwright, expect

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page()
        await page.goto('http://127.0.0.1:18765/')
        await page.get_by_role('button',name='Add passkey',exact=True).click()
        field=page.get_by_label('Passkey nickname',exact=True)
        await expect(field).to_have_attribute('autocomplete','off')
        await expect(field).to_have_value('')
        await field.fill('Laptop')
        await page.wait_for_timeout(1100)
        await expect(field).to_have_value('Laptop')
        await page.get_by_role('button',name='Cancel',exact=True).click()
        await page.get_by_role('button',name='Change password',exact=True).click()
        fields=page.locator('input:visible')
        await expect(fields).to_have_count(3)
        assert await fields.evaluate_all('els=>els.every(e=>e.value === "" && e.autocomplete === "new-password" && e.hasAttribute("data-1p-ignore"))')
        await page.get_by_role('button',name='Cancel',exact=True).click()
        await page.get_by_role('button',name='Add passkey',exact=True).click()
        await expect(field).to_have_value('')
        await browser.close()
        print('DOM_PASS: dialog hints, empty fields, typing retained, reopening clears')
if __name__=='__main__': asyncio.run(main())
