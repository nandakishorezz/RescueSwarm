import asyncio
from playwright.async_api import async_playwright
import json

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()

        print("Navigating to app...")
        await page.goto("http://127.0.0.1:5173/?mode=simulation", wait_until="networkidle")

        # Step 3: CREATE A REAL SCENARIO
        print("Creating scenario...")
        
        # Click MAP / SEARCH AREA
        await page.click("text=MAP / SEARCH AREA")
        
        # Draw 4-point polygon (just click on the map 4 times then double click to close, or click Validate if it's already there)
        # Wait for SVG map
        await page.wait_for_selector("svg[data-testid='mission-simulation-svg']", state="visible")
        
        # Let's get the SVG bounding box to click inside
        svg_bb = await page.locator("svg[data-testid='mission-simulation-svg']").bounding_box()
        cx = svg_bb['x'] + svg_bb['width'] / 2
        cy = svg_bb['y'] + svg_bb['height'] / 2

        # Draw a polygon (simulate clicks on map)
        # Often there's a specific way to draw, let's just click 4 points
        await page.mouse.click(cx - 50, cy - 50)
        await page.mouse.click(cx + 50, cy - 50)
        await page.mouse.click(cx + 50, cy + 50)
        await page.mouse.click(cx - 50, cy + 50)
        # maybe double click to finish?
        await page.mouse.dblclick(cx - 50, cy + 50)
        
        await page.click("text=VALIDATE SEARCH REGION")
        
        # ENVIRONMENT
        await page.click("text=ENVIRONMENT")
        
        # SURVIVORS
        await page.click("text=SURVIVORS")
        # place 2 survivors
        await page.mouse.click(cx - 20, cy - 20)
        await page.mouse.click(cx + 20, cy + 20)
        
        # START POSITION
        await page.click("text=START POSITION")
        # place drone start
        await page.mouse.click(cx - 40, cy - 40)
        
        # REVIEW
        await page.click("text=REVIEW")
        
        # APPROVE & PREPARE
        await page.click("text=APPROVE & PREPARE")
        
        # GENERATE WORLD
        await page.click("text=GENERATE WORLD")
        
        # PREPARE LAUNCH ENVIRONMENT
        await page.click("text=PREPARE LAUNCH ENVIRONMENT")
        
        print("Waiting for PLAN READY...")
        # Wait until PLAN READY
        await page.wait_for_selector("text=PLAN READY", timeout=15000)
        print("PLAN READY is visible!")
        
        # Wait a moment for rendering
        await page.wait_for_timeout(2000)

        # 4. TAKE A REAL HEADED-CHROME SCREENSHOT
        print("Taking initial screenshot...")
        await page.screenshot(path="screenshot_static_initial.png")

        # 5. VERIFY DOM + PIXELS
        # document.querySelector('svg[data-testid="mission-simulation-svg"]')
        # polygon count
        # survivor-marker count
        # drone-start-marker count
        # boundingClientRect for each
        # computed styles
        # SVG viewBox
        # SVG dimensions
        
        dom_info = await page.evaluate('''() => {
            const svg = document.querySelector('svg[data-testid="mission-simulation-svg"]');
            if (!svg) return { error: "SVG not found" };
            
            const rect = svg.getBoundingClientRect();
            const viewBox = svg.getAttribute("viewBox");
            const polygons = Array.from(svg.querySelectorAll('polygon'));
            const survivors = Array.from(svg.querySelectorAll('.survivor-marker, [data-testid="survivor-marker"], circle[fill="red"]'));
            // Try to find drone start marker
            const droneStart = Array.from(svg.querySelectorAll('.drone-start-marker, [data-testid="drone-start-marker"], circle[fill="blue"], path[fill="blue"]'));
            
            // Let's be less specific and just list elements that look like markers
            // because I don't know the exact class names.
            
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
        await page.screenshot(path="screenshot_static_10s.png")
        
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
        
        # Wait a bit for SITL to do something
        for i in range(15):
            await page.wait_for_timeout(2000)
            await page.screenshot(path=f"screenshot_sitl_run_{i}.png")
            print(f"SITL Run Screenshot {i} taken.")

        # Let's collect some telemetry data
        telem = await page.evaluate('''() => {
            // maybe we can extract some telemetry history if it's stored on window, or just count drone path points
            const svg = document.querySelector('svg[data-testid="mission-simulation-svg"]');
            if (!svg) return null;
            const dronePath = svg.querySelector('polyline.drone-trail');
            return {
                points: dronePath ? dronePath.getAttribute("points") : null
            };
        }''')
        print(f"Telemetry Info: {telem}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
