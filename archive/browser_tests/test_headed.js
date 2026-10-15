const { chromium } = require('playwright');

(async () => {
  console.log("Launching headed Chrome...");
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();

  console.log("Navigating to http://localhost:5173/?mode=simulation");
  await page.goto("http://localhost:5173/?mode=simulation");

  // Wait for React to mount
  await page.waitForTimeout(2000);

  // Check the DOM
  console.log("\nDOM VERIFICATION for ?mode=simulation:");
  const marker1 = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('div')).find(el => el.textContent && el.textContent.includes('REAL SIMULATION ROUTE ACTIVE'))?.textContent || 'NOT FOUND';
  });
  console.log("MARKER 1:", marker1);

  const marker2 = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('div')).find(el => el.textContent && el.textContent.includes('ABOUT TO MOUNT MissionSimulation2D'))?.textContent || 'NOT FOUND';
  });
  console.log("MARKER 2:", marker2);

  const vpNode = await page.$('.simulation-viewport');
  console.log("simulation-viewport exists:", !!vpNode);

  const simNode = await page.$('.mission-simulation');
  console.log("mission-simulation exists:", !!simNode);

  console.log("\nNavigating to http://localhost:5173/?mode=mission");
  await page.goto("http://localhost:5173/?mode=mission");
  await page.waitForTimeout(2000);

  console.log("\nDOM VERIFICATION for ?mode=mission:");
  const marker1_mission = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('div')).find(el => el.textContent && el.textContent.includes('REAL SIMULATION ROUTE ACTIVE'))?.textContent || 'NOT FOUND';
  });
  console.log("MARKER 1:", marker1_mission);

  const marker2_mission = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('div')).find(el => el.textContent && el.textContent.includes('ABOUT TO MOUNT MissionSimulation2D'))?.textContent || 'NOT FOUND';
  });
  console.log("MARKER 2:", marker2_mission);

  const vpNode_mission = await page.$('.simulation-viewport');
  console.log("simulation-viewport exists:", !!vpNode_mission);

  const simNode_mission = await page.$('.mission-simulation');
  console.log("mission-simulation exists:", !!simNode_mission);

  await browser.close();
})();
