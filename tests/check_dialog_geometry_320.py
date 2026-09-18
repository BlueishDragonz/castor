"""Real Chromium dialog-geometry acceptance; local fixture page only.
Run alongside security_dom_harness.py. Never enters real credentials.

P3-9: dialogs must not overflow a 320px phone viewport — the dialog panel
must derive its width from the responsive parent, never a fixed 400px.
"""
import asyncio
from playwright.async_api import async_playwright, expect


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 320, 'height': 720})
            await page.goto('http://127.0.0.1:18765/')
            await expect(page.get_by_role('button', name='Add passkey', exact=True)).to_be_visible()
            overflow = await page.evaluate('document.documentElement.scrollWidth - document.documentElement.clientWidth')
            assert overflow <= 0, f'page overflows 320px viewport by {overflow}px before any dialog'
            for opener in ('Add passkey', 'Change password'):
                await page.get_by_role('button', name=opener, exact=True).click()
                await expect(page.get_by_role('dialog')).to_be_visible()
                panel = page.locator('.bh-dialog-panel')
                await expect(panel).to_be_visible()
                # Geometry: panel fits inside the 320px viewport (Quasar
                # dialog is centered, so both edges must be on-screen).
                box = await panel.bounding_box()
                assert box is not None, f'{opener} dialog panel not found'
                assert box['x'] >= 0, f'{opener} dialog starts off-screen left ({box["x"]}px)'
                assert box['x'] + box['width'] <= 320, (
                    f'{opener} dialog panel is {box["width"]}px wide, ends at '
                    f'{box["x"] + box["width"]}px — exceeds 320px viewport'
                )
                overflow = await page.evaluate(
                    'document.documentElement.scrollWidth - document.documentElement.clientWidth')
                assert overflow <= 0, f'{opener} dialog causes {overflow}px horizontal overflow at 320px'
                print(f'  {opener}: panel {round(box["width"])}px, x={round(box["x"])}px — fits 320px viewport')
                await page.get_by_role('button', name='Cancel', exact=True).click()
                await expect(page.get_by_role('dialog')).not_to_be_visible()
        finally:
            await browser.close()
    print('PASS: 320px viewport — no horizontal overflow, dialog panels within viewport')


if __name__ == '__main__':
    asyncio.run(main())
