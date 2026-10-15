import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Capture all console messages
        page.on("console", lambda msg: print(f"CONSOLE [{msg.type}]: {msg.text}"))
        page.on("pageerror", lambda exc: print(f"PAGE ERROR: {exc}"))
        
        print("Navigating...")
        await page.goto("http://127.0.0.1:8000/?mode=simulation")
        await page.wait_for_timeout(3000)
        
        # We need to trigger the map render by switching to MISSION mode
        await page.evaluate("window.useGcsStore.getState().setMode('MISSION')")
        print("Switched to MISSION mode, waiting for map...")
        await page.wait_for_timeout(5000)
        
        # Let's also execute some JS to check if MapLibre layers exist!
        layers = await page.evaluate("Array.from(document.querySelectorAll('.map-shell')).length")
        print(f"Map shells found: {layers}")
        
        await browser.close()

asyncio.run(main())
