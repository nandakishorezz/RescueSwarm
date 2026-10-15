const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  page.on('dialog', dialog => dialog.accept());

  console.log("Navigating to setup (simulation)...");
  await page.goto('http://127.0.0.1:5173/?mode=simulation');
  
  // Step 1: Drone & Sensor Configuration
  console.log("Step 1...");
  await page.waitForSelector('text=NEXT: MAP / SEARCH AREA');
  await page.click('text=NEXT: MAP / SEARCH AREA');
  
  // Wait for MapLibre to load map tiles
  await page.waitForTimeout(5000); 

  // Function to click reliably on MapLibre
  const clickMap = async (x, y) => {
    await page.evaluate(({x, y}) => {
      const canvas = document.querySelector('.wizard-map-container canvas');
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      const clientX = rect.left + x;
      const clientY = rect.top + y;
      const opts = { bubbles: true, clientX, clientY, button: 0, pointerType: 'mouse', isPrimary: true };
      canvas.dispatchEvent(new PointerEvent('pointerdown', opts));
      canvas.dispatchEvent(new MouseEvent('mousedown', opts));
      canvas.dispatchEvent(new PointerEvent('pointerup', opts));
      canvas.dispatchEvent(new MouseEvent('mouseup', opts));
      canvas.dispatchEvent(new MouseEvent('click', opts));
    }, {x, y});
    await page.waitForTimeout(600);
  };

  // Step 2: Draw Polygon
  console.log("Step 2: Drawing Polygon...");
  await page.waitForSelector('text=VALIDATE SEARCH REGION');
  await clickMap(100, 100);
  await clickMap(400, 100);
  await clickMap(400, 400);
  await clickMap(100, 400);
  await page.click('text=VALIDATE SEARCH REGION');

  // Step 3
  console.log("Step 3...");
  await page.waitForSelector('text=NEXT: ENVIRONMENT');
  await page.click('text=NEXT: ENVIRONMENT');
  
  // Step 4
  console.log("Step 4...");
  await page.waitForSelector('text=NEXT: SURVIVORS');
  await page.click('text=NEXT: SURVIVORS');
  
  // Step 5: Survivors
  console.log("Step 5: Placing Survivors...");
  await page.waitForSelector('text=NEXT: START POSITION');
  await page.waitForTimeout(1000);
  await clickMap(200, 200);
  await clickMap(250, 350);
  await clickMap(300, 250);
  await page.click('text=NEXT: START POSITION');
  
  // Step 6: Start Position
  console.log("Step 6: Placing Start Position...");
  await page.waitForSelector('text=NEXT: REVIEW');
  await page.waitForTimeout(1000);
  await clickMap(250, 450);
  await page.click('text=NEXT: REVIEW');
  
  // Step 7: Review
  console.log("Step 7...");
  await page.waitForSelector('text=APPROVE & PREPARE');
  await page.click('text=APPROVE & PREPARE');
  
  // Step 8: Generate
  console.log("Step 8...");
  await page.waitForSelector('button:has-text("GENERATE WORLD")');
  await page.click('button:has-text("GENERATE WORLD")');
  
  // Wait for READY or just wait a few seconds (since SITL backend might be offline)
  console.log("Waiting for backend API (might fail if SITL offline)...");
  try {
    await page.waitForSelector('button:has-text("PREPARE LAUNCH ENVIRONMENT")', { timeout: 20000 });
    await page.click('button:has-text("PREPARE LAUNCH ENVIRONMENT")');
  } catch (e) {
    console.log("Backend offline, continuing anyway...");
  }
  
  // Navigate to Mission explicitly since auto-nav might not trigger if backend failed
  console.log("Waiting for auto-navigation to Mission tab...");
  // Do NOT use page.goto because it interrupts the React state and setTimeout!

  // Wait to navigate to Mission
  console.log("Waiting for Mission page to load...");
  await page.waitForSelector('.mission-simulation', { timeout: 30000 });
  
  console.log("Mission page loaded. Taking screenshot A...");
  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'FINAL_STATIC_PROOF.png' });
  
  // Verify HTML elements
  const hasPolygon = await page.locator('.search-area').count();
  const hasSurvivors = await page.locator('.survivor').count();
  const hasStart = await page.locator('.drone-start').count();
  
  console.log(`DOM elements: Polygon: ${hasPolygon}, Survivors: ${hasSurvivors}, Start: ${hasStart}`);
  
  // Click start simulation
  console.log("Clicking Start Simulation...");
  const startBtn = page.locator('#btn-start-simulation');
  if (await startBtn.isVisible()) {
    await startBtn.click();
    console.log("Taking Live A screenshot...");
    await page.waitForTimeout(2000);
    await page.screenshot({ path: 'FINAL_LIVE_A.png' });
    
    console.log("Waiting 5 seconds for live movement...");
    await page.waitForTimeout(5000);
    await page.screenshot({ path: 'FINAL_LIVE_B.png' });
    
    const hasLiveDrone = await page.locator('.live-drone').count();
    const hasTrail = await page.locator('.trail-point').count();
    console.log(`Live drone: ${hasLiveDrone}, Trail points: ${hasTrail}`);
  } else {
    console.log("Start button not visible.");
  }
  
  await browser.close();
})();
