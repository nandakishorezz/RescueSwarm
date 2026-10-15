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
        
        def handle_response(res):
            if "api/" in res.url:
                print(f"API RESPONSE {res.status}: {res.url}")
        page.on("response", handle_response)
        
        print("Navigating to app...")
        await page.goto("http://127.0.0.1:8000/?mode=simulation")
        await page.wait_for_timeout(2000)
        
        print("Bypassing wizard to step 8...")
        # Since state is private to React, let's use the local storage auto-save to set it up, or just evaluate React internals?
        # Actually, if I can't click it, I can't test it.
        # Let's take a screenshot of whatever they see on load.
        await page.screenshot(path="screenshot_load.png")
        
        print("Clicking SETUP tab to see if it works...")
        await page.click("text=SETUP")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="screenshot_setup.png")
        
        await browser.close()

asyncio.run(main())
