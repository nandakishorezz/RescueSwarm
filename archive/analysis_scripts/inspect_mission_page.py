import asyncio
from playwright.async_api import async_playwright
import base64

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        errors = []
        page.on("console", lambda msg: errors.append(f"CONSOLE {msg.type}: {msg.text}"))
        page.on("pageerror", lambda err: errors.append(f"PAGE ERROR: {err}"))
        
        # Set scenario in localStorage
        await page.goto("http://127.0.0.1:5173")
        await page.evaluate("""() => {
            localStorage.setItem('rescueswarm.simulationScenario', JSON.stringify({
                polygon: [[77.594, 12.972], [77.595, 12.972], [77.595, 12.971], [77.594, 12.971]],
                survivors: [{id: '1', lng: 77.5945, lat: 12.9715}],
                startPosition: {lng: 77.5941, lat: 12.9719}
            }));
        }""")
        
        # WE MUST GO TO /?mode=mission TO SEE THE MISSION PAGE (WHICH HAS THE BLACK MAP ISSUE)
        await page.goto("http://127.0.0.1:5173/?mode=mission", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        
        # Inspect MapLibre canvas
        map_info = await page.evaluate("""() => {
            const canvas = document.querySelector('.maplibregl-canvas');
            if (!canvas) return { error: 'No MapLibre canvas found' };
            const gl = canvas.getContext('webgl') || canvas.getContext('webgl2');
            return {
                width: canvas.width,
                height: canvas.height,
                styleWidth: canvas.style.width,
                styleHeight: canvas.style.height,
                hasWebGL: !!gl
            };
        }""")
        
        print("=== MAP INFO ===")
        print(map_info)
        
        print("\n=== ERRORS ===")
        for e in errors:
            print(e)
            
        screenshot = await page.screenshot(type='jpeg', quality=30)
        b64 = base64.b64encode(screenshot).decode('utf-8')
        print(f"\nB64_THUMBNAIL_START\n{b64}\nB64_THUMBNAIL_END")
        
        await browser.close()

asyncio.run(main())
