const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  // Set localStorage first
  await page.goto('http://127.0.0.1:5173/');
  await page.evaluate(() => {
    localStorage.setItem("rescueswarm.simulationScenario", JSON.stringify({
      polygon: [[12.971, 77.591], [12.971, 77.595], [12.975, 77.595], [12.975, 77.591]], // Provide [lat, lon] to test parser
      survivors: [{longitude: 77.592, latitude: 12.972}, {longitude: 77.594, latitude: 12.974}, {longitude: 77.593, latitude: 12.973}],
      startPosition: {lng: 77.59141, lat: 12.97283}
    }));
  });

  console.log("Navigating to http://127.0.0.1:5173/?mode=mission ...");
  await page.goto('http://127.0.0.1:5173/?mode=mission');

  // Wait for the mission simulation component to appear
  try {
    await page.waitForSelector('[data-testid="mission-simulation-2d"]', { timeout: 10000 });
  } catch (e) {
    console.error("FAIL: mission-simulation-2d not found.");
    await browser.close();
    process.exit(1);
  }

  const svg = await page.$('[data-testid="mission-simulation-svg"]');
  const svgBox = await svg.boundingBox();
  console.log("SVG Bounding Box:", svgBox);
  
  const svgStyle = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="mission-simulation-svg"]');
    const style = window.getComputedStyle(el);
    return {
      display: style.display,
      visibility: style.visibility,
      opacity: style.opacity,
      overflow: style.overflow,
      position: style.position
    };
  });
  console.log("SVG Computed Style:", svgStyle);

  const elements = await page.evaluate((svgBox) => {
    const el = document.querySelector('[data-testid="mission-simulation-svg"]');
    const children = el.querySelectorAll('*');
    const results = [];
    children.forEach(c => {
      const tag = c.tagName.toLowerCase();
      if (['polygon', 'text', 'polyline', 'circle', 'g'].includes(tag) && !c.getAttribute('key')?.startsWith('hx') && !c.getAttribute('key')?.startsWith('vx')) {
        const rect = c.getBoundingClientRect();
        let inside = false;
        if (rect.width > 0 && rect.height > 0) {
          inside = rect.x >= svgBox.x && rect.y >= svgBox.y && (rect.x + rect.width) <= (svgBox.x + svgBox.width) && (rect.y + rect.height) <= (svgBox.y + svgBox.height);
        }
        results.push({
          tag: tag,
          text: c.textContent,
          bbox: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
          inside: inside,
          transform: c.getAttribute('transform'),
          points: c.getAttribute('points')
        });
      }
    });
    return results;
  }, svgBox);
  
  console.log("Elements inside SVG:", elements);

  await page.screenshot({ path: 'frontend_verification_A.png' });
  console.log("Screenshot A saved.");

  // Test live movement by clicking START SIMULATION
  console.log("Clicking START SIMULATION...");
  const startBtn = await page.$('#btn-start-simulation');
  if (startBtn) {
    await startBtn.click();
    console.log("Started simulation. Waiting 10 seconds...");
    await page.waitForTimeout(10000);
    await page.screenshot({ path: 'frontend_verification_B.png' });
    console.log("Screenshot B saved.");
  } else {
    console.log("Start simulation button not found.");
  }

  await browser.close();
})();
