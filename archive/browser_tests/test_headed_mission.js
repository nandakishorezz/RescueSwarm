const { chromium } = require("playwright");

(async () => {
  console.log("Launching headed Chrome...");
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();
  
  page.on("dialog", dialog => dialog.accept());
  
  console.log("Navigating to http://127.0.0.1:5173/ to inject scenario...");
  await page.goto("http://127.0.0.1:5173/");
  await page.waitForTimeout(1000);

  console.log("Injecting scenario into localStorage...");
  await page.evaluate(() => {
    localStorage.setItem("rescueswarm.simulationScenario", JSON.stringify({
      polygon: [
        { lat: 12.971, lng: 77.591 },
        { lat: 12.971, lng: 77.599 },
        { lat: 12.979, lng: 77.599 },
        { lat: 12.979, lng: 77.591 }
      ],
      survivors: [
        { lat: 12.975, lng: 77.595 },
        { lat: 12.973, lng: 77.597 }
      ],
      startPosition: { lat: 12.970, lng: 77.590 }
    }));
  });

  console.log("Navigating to http://127.0.0.1:5173/?mode=mission...");
  await page.goto("http://127.0.0.1:5173/?mode=mission");
  
  console.log("Waiting for MissionSimulation2D to mount...");
  await page.waitForSelector(".mission-simulation", { timeout: 10000 });
  await page.waitForTimeout(2000); // Wait for animations/renders

  console.log("\nDOM EXTRACTION:");
  const extractInfo = async (selector) => {
    return await page.evaluate((sel) => {
      const els = Array.from(document.querySelectorAll(sel));
      return els.map(el => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return {
          selector: sel,
          parentClass: el.parentElement?.className,
          offsetParentClass: el.offsetParent?.className,
          offsetParentTag: el.offsetParent?.tagName,
          offsetLeft: el.offsetLeft,
          offsetTop: el.offsetTop,
          inlineStyle: el.getAttribute('style'),
          rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
          style: {
            position: style.position,
            left: style.left,
            top: style.top,
            transform: style.transform,
            width: style.width,
            height: style.height,
            zIndex: style.zIndex
          }
        };
      });
    }, selector);
  };

  const simContainer = await extractInfo('.mission-simulation');
  console.log("mission-simulation:", JSON.stringify(simContainer, null, 2));

  const vpContainer = await extractInfo('.simulation-viewport');
  console.log("simulation-viewport:", JSON.stringify(vpContainer, null, 2));

  const searchArea = await extractInfo('.search-area');
  console.log("search-area:", JSON.stringify(searchArea, null, 2));

  const survivors = await extractInfo('.survivor');
  console.log("survivors (count):", survivors.length);
  if (survivors.length > 0) {
    console.log("survivor 1:", JSON.stringify(survivors[0], null, 2));
  }

  const droneStart = await extractInfo('.drone-start');
  console.log("drone-start:", JSON.stringify(droneStart, null, 2));

  console.log("\nTaking screenshot before start...");
  await page.screenshot({ path: 'mission_before_start.png' });

  // Start simulation to see live drone
  console.log("\nStarting simulation...");
  try {
    const startBtn = await page.$("button:has-text('START SIMULATION')");
    if (startBtn) {
      await startBtn.click();
      console.log("Clicked START SIMULATION");
    } else {
      console.log("START SIMULATION button not found!");
    }
  } catch(e) {
    console.log("Could not click start:", e);
  }

  // Wait 10 seconds for drone movement
  console.log("Waiting 10s for telemetry/movement...");
  await page.waitForTimeout(10000);

  const liveDrone = await extractInfo('.live-drone');
  console.log("\nlive-drone:", JSON.stringify(liveDrone, null, 2));
  const trail = await extractInfo('.trail-point');
  console.log("trail points (count):", trail.length);

  console.log("\nTaking final screenshot...");
  await page.screenshot({ path: 'mission_after_start.png' });
  console.log("Saved screenshots to mission_before_start.png and mission_after_start.png");

  await browser.close();
})();
