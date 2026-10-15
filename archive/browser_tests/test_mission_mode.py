import os
os.environ['PYTHONIOENCODING'] = 'utf-8'
from playwright.sync_api import sync_playwright
import time

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto("http://localhost:5173/?mode=mission")
        
        print("Waiting for start button...")
        page.wait_for_selector("#btn-start-simulation", timeout=10000)
        
        print("Clicking start button")
        page.click("#btn-start-simulation")
        
        print("Waiting for mission to start RUNNING...")
        start_time = time.time()
        has_run = False
        while True:
            try:
                state_element = page.locator(".mission-state")
                if state_element.count() > 0:
                    state = state_element.inner_text()
                    print(f"Current state: {state}")
                    if "RUNNING" in state.upper() or "SEARCHING" in state.upper() or "LANDING" in state.upper():
                        has_run = True
                    if has_run and ("COMPLETED" in state.upper() or "FAILED" in state.upper() or "ABORTED" in state.upper()):
                        print(f"Mission ended with state: {state}")
                        time.sleep(2)  # Allow telemetry to settle
                        break
            except Exception as e:
                pass
                
            if time.time() - start_time > 300:
                print("Test timed out")
                break
            time.sleep(2)
            
        print("Getting telemetry data from API directly...")
        try:
            import requests
            response = requests.get("http://localhost:8000/api/missions")
            missions = response.json()
            if missions:
                mission_id = missions[-1]["id"]
                twin = requests.get(f"http://localhost:8000/api/missions/{mission_id}/digital-twin").json()
                v = twin.get("vehicle", {})
                print("Final telemetry:")
                print(f"Alt: {v.get('relative_altitude_m')} (abs: {v.get('absolute_altitude_m')})")
                print(f"Armed: {v.get('armed')}")
                print(f"Landed State: {twin.get('landed_state')}")
                print(f"Distance from home: (lat: {v.get('latitude')}, lon: {v.get('longitude')})")
        except Exception as e:
            print("Could not fetch final telemetry", e)
        
        browser.close()

if __name__ == '__main__':
    run()
