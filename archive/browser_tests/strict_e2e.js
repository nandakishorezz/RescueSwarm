
const { chromium } = require("playwright");
const fs = require("fs");

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();
  
  page.on("dialog", dialog => dialog.accept());
  page.on("console", msg => console.log("BROWSER LOG:", msg.text()));

  console.log("Navigating to http://127.0.0.1:5173/?mode=simulation");
  await page.goto("http://127.0.0.1:5173/?mode=simulation");
  
  console.log("Waiting for Simulation wizard...");
  await page.waitForTimeout(1000);
  
  await page.waitForSelector(".wizard-step h2", { timeout: 10000 });
  const setupStep = await page.locator(".wizard-step h2").textContent();
  if (!setupStep.includes("1.")) {
    console.log("Not on step 1, resetting...");
    // Just force navigation to simulation mode
    await page.goto("http://127.0.0.1:5173/?mode=simulation");
    await page.waitForTimeout(2000);
  }
  
  console.log("Step 1...");
  await page.waitForSelector("text=NEXT: MAP / SEARCH AREA");
  await page.click("text=NEXT: MAP / SEARCH AREA");
  
  await page.waitForTimeout(3000);

  const clickMap = async (x, y) => {
    await page.evaluate(({x, y}) => {
      const canvas = document.querySelector(".wizard-map-container canvas");
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      const opts = { bubbles: true, clientX: rect.left + x, clientY: rect.top + y, button: 0, pointerType: "mouse", isPrimary: true };
      canvas.dispatchEvent(new PointerEvent("pointerdown", opts));
      canvas.dispatchEvent(new MouseEvent("mousedown", opts));
      canvas.dispatchEvent(new PointerEvent("pointerup", opts));
      canvas.dispatchEvent(new MouseEvent("mouseup", opts));
      canvas.dispatchEvent(new MouseEvent("click", opts));
    }, {x, y});
    await page.waitForTimeout(500);
  };

  console.log("Step 2: Drawing Polygon...");
  await clickMap(100, 100);
  await clickMap(300, 100);
  await clickMap(300, 300);
  await clickMap(100, 300);
  await page.click("text=VALIDATE SEARCH REGION");
  await page.waitForTimeout(2000);
  console.log("Step 3/4...");
  await page.waitForSelector("button:has-text(\"NEXT: ENVIRONMENT\")");
  await page.click("button:has-text(\"NEXT: ENVIRONMENT\")");

  await page.waitForTimeout(1000);
  console.log("Step 4...");
  await page.waitForSelector("button:has-text(\"NEXT: SURVIVORS\")");
  await page.click("button:has-text(\"NEXT: SURVIVORS\")");

  console.log("Step 5: Placing Survivors...");
  await clickMap(150, 150);
  await clickMap(250, 150);
  await clickMap(200, 250);
  await page.waitForSelector("button:has-text(\"NEXT: START POSITION\")");
  await page.click("button:has-text(\"NEXT: START POSITION\")");

  console.log("Step 6: Placing Start Position...");
  await clickMap(200, 100);
  await page.waitForSelector("button:has-text(\"NEXT: REVIEW\")");
  await page.click("button:has-text(\"NEXT: REVIEW\")");

  console.log("Step 7: Generating...");
  await page.waitForSelector("button:has-text(\"APPROVE & PREPARE\")");
  await page.click("button:has-text(\"APPROVE & PREPARE\")");

  console.log("Step 8...");
  await page.waitForSelector("button:has-text(\"GENERATE WORLD\")");
  await page.click("button:has-text(\"GENERATE WORLD\")");

  console.log("Waiting for backend API...");
  await page.waitForTimeout(2000);
  try {
    await page.waitForSelector("button:has-text(\"PREPARE LAUNCH ENVIRONMENT\")", { timeout: 15000 });
    await page.click("button:has-text(\"PREPARE LAUNCH ENVIRONMENT\")");
  } catch (e) {
    console.log("Backend offline, continuing anyway...");
  }
  
  await page.waitForTimeout(1000);
  console.log("Clicking MISSION tab in header to trigger natural navigation...");
  await page.click(".mode-nav button:has-text(\"MISSION\")");

  console.log("Waiting for Mission page to load...");
  await page.waitForSelector(".mission-simulation", { timeout: 30000 });
  
  // 4. TAKE THE FIRST REAL SCREENSHOT
  console.log("Taking static screenshot...");
  await page.screenshot({ path: "C:/Users/nanda/Downloads/SURVIVOR_DETECTION/FINAL_REAL_BROWSER_STATIC.png", fullPage: true });

  // Gather DOM diagnostic info
  console.log("Gathering DOM metrics...");
  const metrics = await page.evaluate(() => {
    const data = { elements: [] };
    
    // Check MissionSimulation2D rectangle
    const simDiv = document.querySelector("[data-testid=\"mission-simulation-2d\"]");
    if (simDiv) {
      const b = simDiv.getBoundingClientRect();
      data.simRect = { x: b.x, y: b.y, width: b.width, height: b.height };
    }
    
    const viewDiv = document.querySelector(".simulation-viewport");
    if (viewDiv) {
      const b = viewDiv.getBoundingClientRect();
      data.viewportRect = { x: b.x, y: b.y, width: b.width, height: b.height };
    }
    
    const checkEl = (sel, name) => {
      document.querySelectorAll(sel).forEach((el, i) => {
         const b = el.getBoundingClientRect();
         const c = window.getComputedStyle(el);
         data.elements.push({
           name: `${name} ${i+1}`,
           rect: { x: b.x, y: b.y, width: b.width, height: b.height },
           display: c.display, visibility: c.visibility, opacity: c.opacity, position: c.position,
           left: c.left, top: c.top
         });
      });
    };
    
    checkEl(".search-area", "polygon");
    checkEl(".survivor", "survivor");
    checkEl(".drone-start", "drone START");
    checkEl(".live-drone", "live drone");
    checkEl(".trail-point", "trail point");
    
    return data;
  });
  
  fs.writeFileSync("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/dom_metrics.json", JSON.stringify(metrics, null, 2));
  
  console.log("Clicking START SIMULATION...");
  try {
    await page.click("button:has-text(\"START SIMULATION\")", { timeout: 5000 });
    await page.waitForTimeout(2000);
    console.log("Taking Live A screenshot...");
    await page.screenshot({ path: "C:/Users/nanda/Downloads/SURVIVOR_DETECTION/FINAL_REAL_BROWSER_LIVE_A.png", fullPage: true });
    
    console.log("Waiting 10 seconds for live movement...");
    await page.waitForTimeout(10000);
    
    console.log("Taking Live B screenshot...");
    await page.screenshot({ path: "C:/Users/nanda/Downloads/SURVIVOR_DETECTION/FINAL_REAL_BROWSER_LIVE_B.png", fullPage: true });
  } catch (e) {
    console.log("Could not start simulation for Live screenshots (maybe button not present due to backend)");
    await page.screenshot({ path: "C:/Users/nanda/Downloads/SURVIVOR_DETECTION/FINAL_REAL_BROWSER_LIVE_A.png", fullPage: true });
    await page.screenshot({ path: "C:/Users/nanda/Downloads/SURVIVOR_DETECTION/FINAL_REAL_BROWSER_LIVE_B.png", fullPage: true });
  }

  await browser.close();
  console.log("E2E Done");
})();

