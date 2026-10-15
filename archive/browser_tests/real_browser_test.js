
const { chromium } = require("playwright");
const fs = require("fs");

(async () => {
  console.log("Launching HEADLESS: FALSE Chrome...");
  const browser = await chromium.launch({ headless: false, args: ["--window-size=1200,800"] });
  
  async function testUrl(url, label) {
    console.log(`\n================ Testing ${label} (${url}) ================`);
    const context = await browser.newContext({ viewport: { width: 1200, height: 800 } });
    const page = await context.newPage();
    
    const logs = [];
    page.on("console", msg => logs.push(`[${msg.type()}] ${msg.text()}`));
    page.on("pageerror", err => logs.push(`[UNCAUGHT] ${err.message}`));
    
    await page.goto(url + "?mode=simulation");
    console.log("Navigated. Waiting for setup...");
    
    try {
      // Just wait for MissionPage since it's already ready in backend
      await page.waitForSelector(".mission-simulation", { timeout: 15000 });
      await page.waitForTimeout(3000); // let rendering settle
      
      // Take screenshot
      const screenPath = `REAL_CHROME_${label}.png`;
      await page.screenshot({ path: screenPath });
      console.log(`Saved screenshot: ${screenPath}`);
      
      // Metrics & Diagnostics
      const metrics = await page.evaluate(() => {
        const sw = navigator.serviceWorker ? navigator.serviceWorker.getRegistrations().then(r => r.length) : Promise.resolve(0);
        
        const sim = document.querySelector("[data-testid=\"mission-simulation-2d\"]");
        const simRect = sim ? sim.getBoundingClientRect() : null;
        const simStyle = sim ? window.getComputedStyle(sim) : null;
        
        let coveringElements = [];
        if (simRect) {
            const centerX = simRect.x + simRect.width / 2;
            const centerY = simRect.y + simRect.height / 2;
            const points = [
                {x: centerX, y: centerY, label: "center"},
                {x: simRect.x + 10, y: simRect.y + 10, label: "top-left"},
                {x: simRect.x + simRect.width - 10, y: simRect.y + simRect.height - 10, label: "bottom-right"}
            ];
            
            points.forEach(pt => {
                const el = document.elementFromPoint(pt.x, pt.y);
                if (el) {
                    coveringElements.push({
                        label: pt.label,
                        tag: el.tagName,
                        className: el.className,
                        id: el.id,
                        zIndex: window.getComputedStyle(el).zIndex,
                        opacity: window.getComputedStyle(el).opacity,
                        pointerEvents: window.getComputedStyle(el).pointerEvents,
                        width: el.getBoundingClientRect().width,
                        height: el.getBoundingClientRect().height
                    });
                }
            });
        }

        return {
          url: window.location.href,
          dpr: window.devicePixelRatio,
          innerWidth: window.innerWidth,
          innerHeight: window.innerHeight,
          simRect: simRect ? { width: simRect.width, height: simRect.height, display: simStyle.display, visibility: simStyle.visibility, opacity: simStyle.opacity, zIndex: simStyle.zIndex } : null,
          coveringElements,
          mediaMin768: window.matchMedia("(min-width: 768px)").matches,
          prefersDark: window.matchMedia("(prefers-color-scheme: dark)").matches
        };
      });
      
      console.log("Metrics:", JSON.stringify(metrics, null, 2));
      console.log("Logs:", logs.slice(-10));
      
    } catch (err) {
      console.error(`Error in ${label}:`, err.message);
      console.log("Logs in catch:", logs.slice(-20));
      const html = await page.content();
      fs.writeFileSync(`dump_${label}.html`, html);
      console.log(`Saved dump_${label}.html`);
    }
    await context.close();
  }
  
  await testUrl("http://127.0.0.1:5173", "127");
  await testUrl("http://localhost:5173", "LOCALHOST");
  
  await browser.close();
})();

