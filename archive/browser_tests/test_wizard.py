import asyncio
import os
import time
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Log all console messages and network errors
        page.on("console", lambda msg: print(f"CONSOLE [{msg.type}]: {msg.text}"))
        page.on("pageerror", lambda exc: print(f"PAGE ERROR: {exc}"))
        page.on("requestfailed", lambda req: print(f"REQ FAILED: {req.url} - {req.failure.error_text if req.failure else 'Unknown'}"))
        
        def handle_response(res):
            if "api/" in res.url:
                print(f"API RESPONSE {res.status}: {res.url}")
        page.on("response", handle_response)
        
        print("Navigating to app...")
        await page.goto("http://127.0.0.1:8000/?mode=simulation")
        await page.wait_for_timeout(2000)
        
        # In the simulation wizard, we need to click NEXT until step 8
        # Step 1: Scenario
        print("Clicking NEXT to step 2...")
        await page.click("text=NEXT: SEARCH AREA")
        await page.wait_for_timeout(500)
        
        # Step 2: Search Area
        print("Drawing polygon...")
        # To draw a polygon in maplibre we need to trigger click events.
        # But this is hard. Is there a way to bypass?
        # Let's see if there's a button to auto-draw, no.
        # Can we inject React state? No.
        
        print("Capturing screenshot of wizard state...")
        await page.screenshot(path="screenshot_wizard.png")
        
        # Let's try evaluating window.useGcsStore.getState()
        try:
            state = await page.evaluate("window.useGcsStore ? Object.keys(window.useGcsStore.getState()) : 'no window.useGcsStore'")
            print(f"GCS Store state keys: {state}")
        except Exception as e:
            print(f"Eval error: {e}")

        await browser.close()

asyncio.run(main())
