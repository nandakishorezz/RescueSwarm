import asyncio
from playwright.async_api import async_playwright
import json
import traceback

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()

        try:
            page.on("console", lambda msg: print(f"PAGE CONSOLE: {msg.text}"))
            page.on("pageerror", lambda exc: print(f"PAGE ERROR: {exc}"))
            
            print("Navigating to app...")
            await page.goto("http://127.0.0.1:5173/?mode=simulation", wait_until="networkidle")
            await page.wait_for_timeout(2000)

            print("Clicking NEXT: MAP / SEARCH AREA")
            await page.click("text=NEXT: MAP / SEARCH AREA")
            await page.wait_for_timeout(1000)

            print("Drawing polygon...")
            # maplibre canvas
            canvas = await page.wait_for_selector(".wizard-map-container canvas")
            box = await canvas.bounding_box()
            cx = box['x'] + box['width'] / 2
            cy = box['y'] + box['height'] / 2

            await page.mouse.click(cx - 50, cy - 50)
            await page.wait_for_timeout(300)
            await page.mouse.click(cx + 50, cy - 50)
            await page.wait_for_timeout(300)
            await page.mouse.click(cx + 50, cy + 50)
            await page.wait_for_timeout(300)
            await page.mouse.click(cx - 50, cy + 50)
            await page.wait_for_timeout(500)
            
            await page.screenshot(path="debug_final_01_polygon.png")

            print("Clicking VALIDATE SEARCH REGION")
            await page.click("text=VALIDATE SEARCH REGION")
            await page.wait_for_timeout(1000)

            print("Clicking NEXT: ENVIRONMENT")
            await page.click("text=NEXT: ENVIRONMENT")
            await page.wait_for_timeout(1000)

            print("Clicking NEXT: SURVIVORS")
            await page.click("text=NEXT: SURVIVORS")
            await page.wait_for_timeout(1000)
            
            # place 2 survivors
            await page.mouse.click(cx - 20, cy - 20)
            await page.wait_for_timeout(300)
            await page.mouse.click(cx + 20, cy + 20)
            await page.wait_for_timeout(500)
            
            print("Clicking NEXT: START POSITION")
            await page.click("text=NEXT: START POSITION")
            await page.wait_for_timeout(1000)
            
            # place drone start
            await page.mouse.click(cx - 40, cy - 40)
            await page.wait_for_timeout(500)
            
            print("Clicking NEXT: REVIEW")
            await page.click("text=NEXT: REVIEW")
            await page.wait_for_timeout(1000)
            
            print("Clicking APPROVE & PREPARE")
            await page.click("text=APPROVE & PREPARE")
            await page.wait_for_timeout(1000)
            
            print("Clicking GENERATE WORLD")
            await page.click("button:has-text('GENERATE WORLD')")
            
            await page.wait_for_timeout(500)
            await page.screenshot(path="debug_final_02_generating.png")
            
            status_message = await page.locator('.run-controls p').first.inner_text()
            print(f"PAGE STATE MESSAGE: {status_message}")
            
            status_spans = await page.locator('.run-controls b').first.inner_text()
            print(f"PAGE STATE ITEMS: {status_spans}")
            
            print("Waiting for PREPARE LAUNCH ENVIRONMENT...")
            await page.wait_for_selector("text=PREPARE LAUNCH ENVIRONMENT", timeout=30000)
            print("Clicking PREPARE LAUNCH ENVIRONMENT")
            await page.click("text=PREPARE LAUNCH ENVIRONMENT")
            
            print("Waiting for START SIMULATION on Mission Page...")
            await page.wait_for_selector("text=START SIMULATION", timeout=60000)
            print("START SIMULATION is visible!")
            
            await page.wait_for_timeout(2000)

            # 4. TAKE A REAL HEADED-CHROME SCREENSHOT
            print("Taking initial screenshot...")
            await page.screenshot(path="FINAL_REAL_BROWSER_STATIC.png")

            # 5. VERIFY DOM + PIXELS
            dom_info = await page.evaluate('''() => {
                const svg = document.querySelector('svg[data-testid="mission-simulation-svg"]');
                if (!svg) return { error: "SVG not found" };
                const rect = svg.getBoundingClientRect();
                const viewBox = svg.getAttribute("viewBox");
                const polygons = Array.from(svg.querySelectorAll('polygon'));
                const survivors = Array.from(svg.querySelectorAll('.survivor-marker, [data-testid="survivor-marker"], circle[fill="red"]'));
                const droneStart = Array.from(svg.querySelectorAll('.drone-start-marker, [data-testid="drone-start-marker"], circle[fill="blue"], path[fill="blue"]'));
                
                return {
                    viewBox: viewBox,
                    dimensions: { width: rect.width, height: rect.height },
                    polygonCount: polygons.length,
                    survivorCount: survivors.length,
                    droneStartCount: droneStart.length,
                    svgHTML: svg.outerHTML.substring(0, 500) + '...'
                };
            }''')
            print(f"DOM Info: {json.dumps(dom_info, indent=2)}")

            # 6. WAIT 10 SECONDS
            print("Waiting 10 seconds for stability check...")
            await page.wait_for_timeout(10000)
            
            print("Taking 10s stability screenshot...")
            await page.screenshot(path="FINAL_REAL_BROWSER_STATIC_10s.png")
            
            dom_info_10s = await page.evaluate('''() => {
                const svg = document.querySelector('svg[data-testid="mission-simulation-svg"]');
                if (!svg) return { error: "SVG not found" };
                const polygons = Array.from(svg.querySelectorAll('polygon'));
                return {
                    polygonCount: polygons.length
                };
            }''')
            print(f"DOM Info 10s: {json.dumps(dom_info_10s, indent=2)}")

            # 7. CLICK START SIMULATION
            print("Clicking START SIMULATION...")
            await page.click("text=START SIMULATION")
            
            # Wait for SITL to run
            for i in range(15):
                await page.wait_for_timeout(2000)
                await page.screenshot(path=f"FINAL_REAL_BROWSER_LIVE_{i}.png")
                print(f"SITL Run Screenshot {i} taken.")

            print("Done!")

        except Exception as e:
            print("Exception during run:")
            traceback.print_exc()
            await page.screenshot(path="debug_error.png")

        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
