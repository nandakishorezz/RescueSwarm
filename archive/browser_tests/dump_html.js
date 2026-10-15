
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:5173/");
  await page.waitForTimeout(5000);
  const html = await page.content();
  console.log(html);
  await browser.close();
})();

