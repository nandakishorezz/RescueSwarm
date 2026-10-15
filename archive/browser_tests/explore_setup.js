const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  console.log("Navigating to setup...");
  await page.goto('http://127.0.0.1:5173/?mode=setup');
  
  // Wait for the setup UI to load
  await page.waitForTimeout(2000);
  
  // Try to create a scenario by interacting with the setup UI
  // Note: Since I don't know the exact DOM of SetupPage, I'll log the DOM or try to click obvious buttons.
  // Actually, I can just extract the localStorage directly if the app has a default scenario,
  // but if the user wants me to generate it, I need to know the buttons.
  
  // Let's just dump the HTML of the setup page to see what buttons exist.
  const html = await page.content();
  fs.writeFileSync('setup_page.html', html);
  console.log("Setup page HTML saved to setup_page.html");

  await browser.close();
})();
