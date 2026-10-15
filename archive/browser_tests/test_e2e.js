const { chromium } = require('playwright');

(async () => {
  console.log("Launching headed Chrome for final E2E test...");
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();

  console.log("1. Opening Simulation Lab...");
  await page.goto("http://localhost:5173/?mode=simulation");
  await page.waitForTimeout(3000);

  console.log("2. Navigating wizard...");
  await page.click('button:has-text("NEXT: MAP")');
  await page.waitForTimeout(1000);

  // Draw polygon
  console.log("Drawing polygon...");
  await page.mouse.click(400, 300);
  await page.waitForTimeout(500);
  await page.mouse.click(600, 300);
  await page.waitForTimeout(500);
  await page.mouse.click(600, 500);
  await page.waitForTimeout(500);
  await page.mouse.click(400, 500);
  await page.waitForTimeout(500);

  await page.click('button:has-text("NEXT: ENVIRONMENT")');
  await page.waitForTimeout(500);
  await page.click('button:has-text("NEXT: SENSORS")');
  await page.waitForTimeout(500);
  await page.click('button:has-text("NEXT: SURVIVORS")');
  await page.waitForTimeout(500);

  // Place survivor
  console.log("Placing survivor...");
  await page.mouse.click(500, 400);
  await page.waitForTimeout(500);

  await page.click('button:has-text("NEXT: START")');
  await page.waitForTimeout(500);

  // Place start pos
  console.log("Placing start pos...");
  await page.mouse.click(450, 450);
  await page.waitForTimeout(500);

  await page.click('button:has-text("NEXT: REVIEW")');
  await page.waitForTimeout(500);

  await page.click('button:has-text("APPROVE")');
  await page.waitForTimeout(1000);

  console.log("3. Generating World...");
  await page.click('button:has-text("GENERATE WORLD")');
  
  console.log("Waiting for world to be ready...");
  await page.waitForSelector('button:has-text("PREPARE LAUNCH ENVIRONMENT")', { timeout: 60000 });
  
  // Intercept the confirm dialog!
  page.on('dialog', async dialog => {
    console.log("Dialog intercepted:", dialog.message());
    await dialog.accept();
  });

  console.log("4. Booting Environment...");
  await page.click('button:has-text("PREPARE LAUNCH ENVIRONMENT")');

  console.log("Waiting for auto-navigation to Mission Tab...");
  await page.waitForSelector('button:has-text("START SIMULATION")', { timeout: 60000 });

  console.log("5. In Mission Tab. Taking screenshot before start...");
  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'regression_before_start.png' });

  console.log("6. Starting Simulation...");
  await page.click('button:has-text("START SIMULATION")');

  console.log("Waiting 10 seconds for telemetry...");
  await page.waitForTimeout(10000);

  await page.screenshot({ path: 'regression_after_start.png' });

  await browser.close();
  console.log("Final E2E complete.");
})();
