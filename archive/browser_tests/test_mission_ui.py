import asyncio
from playwright.async_api import async_playwright
import json

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Capture console messages
        errors = []
        page.on('console', lambda msg: errors.append(f'[{msg.type}] {msg.text}') if msg.type in ['error', 'warning'] else None)
        page.on('pageerror', lambda err: errors.append(f'[EXCEPTION] {err}'))
        
        print('Navigating to Simulation tab to populate state...')
        await page.goto('http://127.0.0.1:8000/?mode=simulation')
        await page.wait_for_timeout(2000)
        
        print('Setting localStorage explicitly to mimic the wizard...')
        scenario = {
            'polygon': [[77.594, 12.971], [77.595, 12.971], [77.595, 12.972], [77.594, 12.972]],
            'startPosition': {'lng': 77.5945, 'lat': 12.9715},
            'survivors': [{'id': 'surv_1', 'longitude': 77.5942, 'latitude': 12.9712}]
        }
        await page.evaluate(f"localStorage.setItem('rescueswarm.simulationScenario', '{json.dumps(scenario)}')")
        
        print('Navigating to Mission tab...')
        await page.goto('http://127.0.0.1:8000/?mode=mission')
        await page.wait_for_timeout(3000)
        
        # Check if Start Simulation button exists
        btn_html = await page.evaluate('''() => {
            const btn = Array.from(document.querySelectorAll("button")).find(b => b.textContent.includes("START SIMULATION"));
            return btn ? btn.outerHTML : "NOT FOUND";
        }''')
        print(f'Start button: {btn_html}')
        
        # Check mission state
        mission_state = await page.evaluate('''() => {
            const el = document.querySelector(".mission-command-bar strong");
            return el ? el.textContent : "NOT FOUND";
        }''')
        print(f'Mission state UI text: {mission_state}')
        
        if errors:
            print('Errors encountered:')
            for e in errors: print(e)
        else:
            print('No console errors.')
            
        await browser.close()

asyncio.run(main())
