import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Log all console messages and network errors
        page.on("console", lambda msg: print(f"CONSOLE [{msg.type}]: {msg.text}"))
        page.on("pageerror", lambda exc: print(f"PAGE ERROR: {exc}"))
        page.on("response", lambda res: print(f"RES {res.status}: {res.url}") if res.status >= 400 else None)
        
        print("Navigating to app...")
        await page.goto("http://127.0.0.1:8000/?mode=simulation")
        await page.wait_for_timeout(2000)
        
        # In the simulation wizard, we need to click NEXT until step 2
        print("Clicking NEXT to step 2...")
        await page.click("text=NEXT: SEARCH AREA")
        await page.wait_for_timeout(500)
        
        # Mock drawing a polygon by evaluating JS to update the state
        print("Setting polygon...")
        await page.evaluate("""
            window.dispatchEvent(new CustomEvent('mock-draw-polygon', { detail: [[12.97, 77.59], [12.971, 77.59], [12.971, 77.591], [12.97, 77.591]] }))
        """)
        # Actually I can't easily set state this way. But wait, I can just click NEXT to the end if I mock the state!
        # Let's see if we can just click "GENERATE WORLD" by directly setting the step to 8.
        print("Switching to step 8...")
        await page.evaluate("window.useGcsStore.getState().setWorkspaceStep?.(8)")
        await page.wait_for_timeout(1000)
        
        # Or better, let's just click the "GENERATE WORLD" button if it exists
        generate_btn = await page.query_selector("button:has-text('GENERATE WORLD')")
        if generate_btn:
            print("Clicking GENERATE WORLD...")
            await generate_btn.click()
            await page.wait_for_timeout(3000)
        else:
            print("GENERATE WORLD button not found!")
            
        launch_btn = await page.query_selector("button:has-text('LAUNCH MISSION')")
        if launch_btn:
            print("Clicking LAUNCH MISSION...")
            # Automatically accept the confirmation dialog
            page.on("dialog", lambda dialog: dialog.accept())
            await launch_btn.click()
            await page.wait_for_timeout(5000)
        else:
            print("LAUNCH MISSION button not found!")
            
        await browser.close()

asyncio.run(main())
