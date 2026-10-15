#!/usr/bin/env python3
"""Local-only responsive acceptance and screenshots for Simulation Lab."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from run_stage12u_browser_acceptance import Cdp, _chrome, _port, _wait_json, _wait_page

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "stage13sim_phase1_ui"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if urlparse(args.base_url).hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Simulation browser acceptance permits loopback only")
    with urllib.request.urlopen(f"{args.base_url}/api/health", timeout=3) as response:
        if response.status != 200:
            raise RuntimeError("local backend is not ready")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    port = _port()
    environment = {**os.environ, "NO_PROXY": "127.0.0.1,localhost"}
    with tempfile.TemporaryDirectory(prefix="rescueswarm_stage13sim_") as profile:
        process = subprocess.Popen([
            str(_chrome()), "--headless=new", "--no-sandbox", "--use-angle=swiftshader",
            "--disable-background-networking", "--disable-component-update", "--no-first-run",
            "--remote-allow-origins=*", f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
        try:
            page = next(item for item in _wait_json(f"http://127.0.0.1:{port}/json/list") if item.get("type") == "page")
            cdp = Cdp(page["webSocketDebuggerUrl"]); cdp.call("Page.enable"); cdp.call("Runtime.enable")
            results = []
            for width, height, scale in ((1366, 768, 1.0), (1536, 864, 1.0), (1920, 1080, 1.0), (1093, 614, 1.25)):
                name = f"simulation_{width}x{height}_{scale:g}x"
                cdp.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": scale, "mobile": False})
                cdp.call("Page.navigate", {"url": f"{args.base_url}/?mode=simulation"}); _wait_page(cdp, ".simulation-page")
                metrics = cdp.evaluate("""(() => { const shown=s=>{const e=document.querySelector(s);if(!e)return false;const r=e.getBoundingClientRect();const c=getComputedStyle(e);return c.display!=='none'&&r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight}; return {horizontal_overflow_px:Math.max(0,document.documentElement.scrollWidth-innerWidth),hero:shown('.simulation-hero'),config:shown('.simulation-config'),world:shown('.simulation-world'),runbar:shown('.simulation-runbar'),canvas_count:document.querySelectorAll('.simulation-world canvas').length};})()""")
                screenshot = cdp.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
                (OUTPUT / f"{name}.png").write_bytes(base64.b64decode(screenshot["data"]))
                metrics.update({"case": name, "pass": metrics["horizontal_overflow_px"] <= 1 and all(metrics[key] for key in ("hero", "config", "world", "runbar")) and metrics["canvas_count"] == 1})
                results.append(metrics)
            report = {"source": "LOCAL_HEADLESS_CHROME_DEV_MACHINE", "execution_source": "SITL_GAZEBO", "hardware_gate_h1": "PENDING", "cases": results, "pass": all(item["pass"] for item in results)}
            (OUTPUT / "browser_acceptance.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
            print(json.dumps(report, indent=2)); cdp.close()
            return 0 if report["pass"] else 1
        finally:
            process.terminate()
            try: process.wait(5)
            except subprocess.TimeoutExpired: process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
