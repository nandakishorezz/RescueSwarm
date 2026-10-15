import asyncio
from playwright.async_api import async_playwright
import traceback
import sys

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()

        try:
            print("Navigating to app...")
            await page.goto("http://127.0.0.1:5173/?mode=simulation", wait_until="networkidle")
            
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_01_loaded.png")
            print("debug_01_loaded.png saved")

            print("Clicking MAP / SEARCH AREA")
            await page.click("text=MAP / SEARCH AREA")
            await page.wait_for_timeout(1000)
            await page.screenshot(path="debug_02_map_clicked.png")
            print("debug_02_map_clicked.png saved")

            print("Waiting for Map SVG...")
            try:
                await page.wait_for_selector("svg[data-testid='mission-simulation-svg']", timeout=5000)
                print("Map SVG found!")
            except Exception as e:
                print("Could not find svg[data-testid='mission-simulation-svg']. Falling back to general SVG.")
                await page.screenshot(path="debug_02b_svg_missing.png")
            
            # Use another selector just in case, like the map canvas
            # or simply try to click in the middle of the screen
            print("Drawing polygon...")
            # Let's get the page bounding box
            cx = 1280 / 2
            cy = 800 / 2
            
            # Click 4 points to draw polygon
            await page.mouse.click(cx - 50, cy - 50)
            await page.wait_for_timeout(200)
            await page.mouse.click(cx + 50, cy - 50)
            await page.wait_for_timeout(200)
            await page.mouse.click(cx + 50, cy + 50)
            await page.wait_for_timeout(200)
            await page.mouse.click(cx - 50, cy + 50)
            await page.wait_for_timeout(200)
            await page.mouse.dblclick(cx - 50, cy + 50)

            await page.screenshot(path="debug_03_polygon_drawn.png")

            print("Clicking VALIDATE SEARCH REGION")
            await page.click("text=VALIDATE SEARCH REGION")
            await page.wait_for_timeout(1000)

            print("Clicking ENVIRONMENT")
            await page.click("text=ENVIRONMENT")
            await page.wait_for_timeout(1000)

            print("Clicking SURVIVORS")
            await page.click("text=SURVIVORS")
            await page.wait_for_timeout(500)
            # place 2 survivors
            await page.mouse.click(cx - 20, cy - 20)
            await page.wait_for_timeout(200)
            await page.mouse.click(cx + 20, cy + 20)
            await page.wait_for_timeout(500)
            
            print("Clicking START POSITION")
            await page.click("text=START POSITION")
            await page.wait_for_timeout(500)
            # place drone start
            await page.mouse.click(cx - 40, cy - 40)
            await page.wait_for_timeout(500)
            
            print("Clicking REVIEW")
            await page.click("text=REVIEW")
            await page.wait_for_timeout(1000)
            
            print("Clicking APPROVE & PREPARE")
            await page.click("text=APPROVE & PREPARE")
            await page.wait_for_timeout(1000)
            
            print("Clicking GENERATE WORLD")
            await page.click("text=GENERATE WORLD")
            await page.wait_for_timeout(1000)
            
            print("Clicking PREPARE LAUNCH ENVIRONMENT")
            await page.click("text=PREPARE LAUNCH ENVIRONMENT")
            await page.wait_for_timeout(1000)
            
            print("Waiting for PLAN READY...")
            try:
                await page.wait_for_selector("text=PLAN READY", timeout=15000)
                print("PLAN READY is visible!")
            except Exception as e:
                print("PLAN READY not found!")
                await page.screenshot(path="debug_04_plan_ready_timeout.png")
                raise e
            
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_05_plan_ready.png")

            # Final static check
            print("Waiting 10s...")
            await page.wait_for_timeout(10000)
            await page.screenshot(path="debug_06_10s_later.png")

            print("Clicking START SIMULATION")
            await page.click("text=START SIMULATION")

            for i in range(15):
                await page.wait_for_timeout(2000)
                await page.screenshot(path=f"debug_07_run_{i}.png")
                
            print("Done!")

        except Exception as e:
            print("Exception during run:")
            traceback.print_exc()
            await page.screenshot(path="debug_error.png")

        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
