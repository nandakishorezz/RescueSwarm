import { useEffect, useState } from "react";

export function DiagnosticTool() {
  const [report, setReport] = useState<any>(null);

  useEffect(() => {
    // Only run when URL has mode=simulation
    if (!window.location.search.includes("mode=simulation")) return;
    
    // Wait for everything to settle
    setTimeout(async () => {
      const data: any = {};
      
      // WINDOW
      data.WINDOW = {
        href: window.location.href,
        url: document.URL,
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        dpr: window.devicePixelRatio,
        userAgent: navigator.userAgent
      };
      
      // SERVICE WORKERS
      if ("serviceWorker" in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        if (regs.length === 0) {
          data.SERVICE_WORKER = "NONE";
        } else {
          data.SERVICE_WORKER = regs.map(r => ({ scope: r.scope, active: !!r.active }));
        }
      } else {
        data.SERVICE_WORKER = "UNSUPPORTED";
      }
      
      // SCRIPTS & CACHE
      data.SCRIPTS = Array.from(document.querySelectorAll("script")).map(s => s.src);
      data.RESOURCES = performance.getEntriesByType("resource").filter((r: any) => r.name.includes(".js")).map((r: any) => r.name);
      
      // APP MOUNT
      const simNode = document.querySelector(".mission-simulation");
      const vpNode = document.querySelector(".simulation-viewport");
      data.APP = {
        MissionSimulation2DMounted: simNode ? "YES" : "NO",
        simulationViewport: vpNode ? "YES" : "NO"
      };
      
      if (simNode) {
        const rect = simNode.getBoundingClientRect();
        data.SIMULATION_RECT = { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
        
        if (vpNode) {
          const comp = window.getComputedStyle(vpNode);
          data.COMPUTED_STYLE = {
            display: comp.display,
            visibility: comp.visibility,
            opacity: comp.opacity,
            position: comp.position,
            zIndex: comp.zIndex,
            overflow: comp.overflow,
            transform: comp.transform,
            background: comp.background,
            clipPath: comp.clipPath
          };
        }
        
        // CHILD RECTS
        const getRect = (sel: string) => {
          const els = Array.from(document.querySelectorAll(sel));
          return els.map(el => {
            const r = el.getBoundingClientRect();
            const c = window.getComputedStyle(el);
            const isInside = r.left >= rect.left && r.right <= rect.right && r.top >= rect.top && r.bottom <= rect.bottom;
            return {
              rect: { x: r.x, y: r.y, w: r.width, h: r.height },
              display: c.display,
              visibility: c.visibility,
              opacity: c.opacity,
              transform: c.transform,
              zIndex: c.zIndex,
              inViewport: isInside
            };
          });
        };
        
        data.ELEMENTS = {
          searchArea: getRect(".search-area"),
          survivors: getRect(".survivor"),
          droneStart: getRect(".drone-start"),
          liveDrone: getRect(".live-drone"),
          trailPoints: getRect(".trail-point"),
          redTest: getRect(".red-paint-test")
        };
        
        data.SCENARIO = {
          polygonCount: data.ELEMENTS.searchArea.length,
          survivorCount: data.ELEMENTS.survivors.length,
          droneStartPresent: data.ELEMENTS.droneStart.length > 0
        };
        
        data.LIVE = {
          liveDronePresent: data.ELEMENTS.liveDrone.length > 0,
          trailPointCount: data.ELEMENTS.trailPoints.length
        };
        
        // ELEMENT FROM POINT
        const getParentChain = (el: Element | null) => {
          const chain = [];
          while (el && el !== document.body) {
            chain.push(`${el.tagName.toLowerCase()}${el.id ? '#'+el.id : ''}${el.className ? '.'+el.className.replace(/ /g, '.') : ''}`);
            el = el.parentElement;
          }
          return chain.join(" > ");
        };
        
        const pts = [
          { label: "center", x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 },
          { label: "25/25", x: rect.x + rect.width * 0.25, y: rect.y + rect.height * 0.25 },
          { label: "75/25", x: rect.x + rect.width * 0.75, y: rect.y + rect.height * 0.25 },
          { label: "25/75", x: rect.x + rect.width * 0.25, y: rect.y + rect.height * 0.75 },
          { label: "75/75", x: rect.x + rect.width * 0.75, y: rect.y + rect.height * 0.75 }
        ];
        
        data.HIT_TESTS = pts.map(pt => {
          const el = document.elementFromPoint(pt.x, pt.y);
          if (!el) return { label: pt.label, hit: "NONE" };
          const c = window.getComputedStyle(el);
          const r = el.getBoundingClientRect();
          return {
            label: pt.label,
            tag: el.tagName,
            className: el.className,
            id: el.id,
            zIndex: c.zIndex,
            position: c.position,
            opacity: c.opacity,
            bg: c.background,
            pointerEvents: c.pointerEvents,
            rect: { x: r.x, y: r.y, w: r.width, h: r.height },
            chain: getParentChain(el)
          };
        });
      }
      
      setReport(data);
    }, 4000);
  }, []);

  if (!window.location.search.includes("mode=simulation")) return null;

  if (!report) {
    return <div style={{ position: "fixed", top: 10, right: 10, background: "yellow", color: "black", padding: "10px", zIndex: 999999 }}>COLLECTING DIAGNOSTICS... DO NOT CLICK</div>;
  }

  const goUrl = (base: string) => {
    window.location.href = `${base}/?mode=simulation&debug=${Date.now()}`;
  };

  return (
    <div style={{ position: "fixed", top: 0, left: 0, width: "100%", height: "100%", zIndex: 999999, backgroundColor: "rgba(0,0,0,0.9)", color: "#0f0", overflow: "auto", padding: "20px", fontFamily: "monospace", fontSize: "12px" }}>
      <div style={{ display: "flex", gap: "10px", marginBottom: "20px" }}>
        <button onClick={() => navigator.clipboard.writeText(JSON.stringify(report, null, 2))} style={{ padding: "10px", background: "#3b82f6", color: "#fff" }}>COPY DIAGNOSTIC</button>
        <button onClick={() => {
          const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url; a.download = "diagnostic.json"; a.click();
        }} style={{ padding: "10px", background: "#10b981", color: "#fff" }}>DOWNLOAD DIAGNOSTIC</button>
        <button onClick={() => goUrl("http://127.0.0.1:5173")} style={{ padding: "10px", background: "#f59e0b", color: "#fff" }}>OPEN 127.0.0.1</button>
        <button onClick={() => goUrl("http://localhost:5173")} style={{ padding: "10px", background: "#ef4444", color: "#fff" }}>OPEN LOCALHOST</button>
      </div>
      <h2>DIAGNOSTIC REPORT</h2>
      <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{JSON.stringify(report, null, 2)}</pre>
    </div>
  );
}
