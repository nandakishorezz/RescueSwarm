import asyncio
import os
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Capture all network errors and console messages
        page.on("console", lambda msg: print(f"CONSOLE [{msg.type}]: {msg.text}"))
        page.on("pageerror", lambda exc: print(f"PAGE ERROR: {exc}"))
        page.on("requestfailed", lambda req: print(f"REQ FAILED: {req.url} - {req.failure.error_text if req.failure else 'Unknown'}"))
        page.on("response", lambda res: print(f"RES {res.status}: {res.url}") if res.status >= 400 else None)
        
        print("Navigating to app...")
        await page.goto("http://127.0.0.1:8000/")
        await page.wait_for_timeout(2000)
        
        print("Switching to MISSION tab...")
        await page.click("text=MISSION")
        await page.wait_for_timeout(3000)
        
        print("Capturing screenshot...")
        await page.screenshot(path="screenshot_mission.png")
        
        # Check map shell HTML
        map_html = await page.evaluate("document.querySelector('.map-shell') ? document.querySelector('.map-shell').innerHTML : 'NOT_FOUND'")
        print(f"Map Shell HTML length: {len(map_html)}")
        if len(map_html) < 200:
            print(f"Map Shell HTML content: {map_html}")
            
        await browser.close()

asyncio.run(main())
