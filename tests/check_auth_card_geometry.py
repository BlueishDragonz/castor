"""Real Chromium auth-card geometry acceptance; local fixture page only.
Run alongside security_dom_harness.py. Never enters real credentials.

P3-9: the global .bh-dialog-panel must not hardcode 400px — the auth card
must follow its responsive parent (w-80 = 320px base, sm:w-96 = 384px at
>=640px) exactly like pre-redesign pages.
"""
import asyncio
from playwright.async_api import async_playwright, expect


async def measure(page, url):
    await page.goto(url)
    await expect(page.locator('.bh-dialog-panel')).to_be_visible()
    return await page.locator('.bh-dialog-panel').bounding_box()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            results = {}
            for width in (320, 390, 640, 1280):
                page = await browser.new_page(viewport={'width': width, 'height': 850})
                box = await measure(page, 'http://127.0.0.1:18765/auth-card')
                assert box is not None, 'auth card dialog panel not found'
                results[width] = round(box['width'])
                overflow = await page.evaluate(
                    'document.documentElement.scrollWidth - document.documentElement.clientWidth')
                assert overflow <= 0, f'auth card overflows {width}px viewport by {overflow}px'
                assert box['x'] >= 0 and box['x'] + box['width'] <= width, (
                    f'auth card panel exceeds {width}px viewport: x={box["x"]}, w={box["width"]}')
                await page.close()
            print(f'  auth-card panel widths by viewport: {results}')
            # Tailwind responsive contract: w-80 below sm, sm:w-96 at >=640px.
            assert results[320] <= 320, f'expected <=320px at 320px viewport, got {results[320]}px'
            assert results[390] <= 320, f'expected w-80 (320px) at 390px viewport, got {results[390]}px'
            assert 380 <= results[640] <= 384, (
                f'expected sm:w-96 (384px) at 640px viewport, got {results[640]}px — '
                'the 400px hardcode is overriding the responsive parent'
            )
            assert 380 <= results[1280] <= 384, f'expected sm:w-96 (384px) at 1280px, got {results[1280]}px'
        finally:
            await browser.close()
    print('PASS: auth-card panel follows responsive parent widths (w-80 / sm:w-96), no 400px hardcode')


if __name__ == '__main__':
    asyncio.run(main())
