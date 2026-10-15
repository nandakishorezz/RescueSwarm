#!/usr/bin/env python3
"""Local Chrome acceptance probe for the Stage 12U desktop GCS.

This script never opens a remote URL. It records responsive layout evidence and
representative local screenshots against an already-running RescueSwarm backend.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

import websocket


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "stage12u_screenshots"
REPLAY_MISSION = "2908cf42-33b0-4728-94c2-dcfbba546128"


def _chrome() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Chrome or Edge was not found; browser acceptance is NOT_TESTED")


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Cdp:
    def __init__(self, endpoint: str) -> None:
        self.ws = websocket.create_connection(endpoint, timeout=15, origin="http://127.0.0.1")
        self.sequence = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sequence += 1
        request_id = self.sequence
        self.ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"CDP {method}: {message['error']}")
                return message.get("result", {})

    def evaluate(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        return result["result"].get("value")

    def close(self) -> None:
        self.ws.close()


def _wait_json(url: str, timeout: float = 15) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return json.load(response)
        except Exception:
            time.sleep(0.15)
    raise TimeoutError(f"Chrome debugging endpoint did not become ready: {url}")


def _wait_page(cdp: Cdp, selector: str, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = cdp.evaluate(f"document.readyState === 'complete' && Boolean(document.querySelector({json.dumps(selector)}))")
        if ready:
            time.sleep(1.2)
            return
        time.sleep(0.15)
    raise TimeoutError(f"Page never exposed required selector {selector}")


def _click_button(cdp: Cdp, label: str) -> None:
    clicked = cdp.evaluate(
        "(() => { const label = " + json.dumps(label) + "; "
        "const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === label); "
        "if (!button) return false; button.click(); return true; })()"
    )
    if not clicked:
        raise RuntimeError(f"Could not find the Stage 12U button labelled {label!r}")


PROBE = r"""
(() => {
  const shown = (selector) => {
    const element = document.querySelector(selector);
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
      && rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
  };
  const buttons = [...document.querySelectorAll('button')].filter((item) => {
    const rect = item.getBoundingClientRect(); return rect.width > 0 && rect.height > 0;
  });
  const memory = performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576 * 10) / 10 : null;
  return {
    title: document.title,
    viewport_css_px: [innerWidth, innerHeight],
    device_pixel_ratio: devicePixelRatio,
    horizontal_overflow_px: Math.max(0, document.documentElement.scrollWidth - innerWidth),
    status_bar_visible: shown('.status-bar'),
    navigation_visible: shown('.mode-nav'),
    mission_state_visible: shown('.mission-command-bar strong'),
    map_visible: shown('.map-region, .setup-preview, .review-map'),
    fleet_panel_visible: shown('.right-rail, .fleet-panel'),
    alert_surface_visible: shown('.failsafe-banner, .status-items, .global-alert'),
    start_visible: shown('.setup-start-dock .mission-start-button'),
    engineering_visible: shown('.engineering-page'),
    review_visible: shown('.review-heading'),
    canvas_count: document.querySelectorAll('canvas').length,
    button_count: buttons.length,
    disabled_button_count: buttons.filter((item) => item.disabled).length,
    dom_nodes: document.getElementsByTagName('*').length,
    rendered_events: document.querySelectorAll('.event-feed li, .review-timeline article, .replay-event-strip button').length,
    rendered_targets: document.querySelectorAll('.target-card, .target-row').length,
    render_metrics: document.querySelector('.spatial-toolbar small')?.textContent ?? null,
    js_heap_mb: memory,
  };
})()
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    arguments = parser.parse_args()
    hostname = urlparse(arguments.base_url).hostname
    if hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Stage 12U browser acceptance only permits a loopback RescueSwarm origin")
    with urllib.request.urlopen(f"{arguments.base_url}/api/health", timeout=3) as response:
        if response.status != 200:
            raise RuntimeError("Local RescueSwarm backend is not ready")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    port = _port()
    environment = dict(os.environ)
    environment["NO_PROXY"] = "127.0.0.1,localhost"
    with tempfile.TemporaryDirectory(prefix="rescueswarm_stage12u_") as profile:
        process = subprocess.Popen([
            str(_chrome()), "--headless=new", "--no-sandbox", "--use-angle=swiftshader",
            "--disable-background-networking", "--disable-component-update", "--no-first-run",
            "--remote-allow-origins=*", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
            "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
        try:
            targets = _wait_json(f"http://127.0.0.1:{port}/json/list")
            page = next(item for item in targets if item.get("type") == "page")
            cdp = Cdp(page["webSocketDebuggerUrl"])
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("HeapProfiler.enable")
            cases = [
                ("setup_1366x768", 1366, 768, 1.0, "?mode=setup", ".setup-page"),
                ("mission_parallel_search_1366x768", 1366, 768, 1.0, "?mode=mission", ".mission-page"),
                ("mission_parallel_search_1920x1080", 1920, 1080, 1.0, "?mode=mission", ".mission-page"),
                ("mission_split_1536x864", 1536, 864, 1.0, "?mode=mission&view=split", ".mission-page"),
                ("engineering_3d_camera_geometry_1920x1080", 1920, 1080, 1.0, "?mode=engineering&view=3d", ".engineering-page"),
                ("review_replay_1366x768", 1366, 768, 1.0, f"?mode=review&mission={REPLAY_MISSION}", ".review-page"),
                ("mission_125pct_zoom", 1093, 614, 1.25, "?mode=mission", ".mission-page"),
            ]
            results: list[dict[str, Any]] = []
            for name, width, height, scale, query, selector in cases:
                cdp.call("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": height, "deviceScaleFactor": scale, "mobile": False,
                })
                cdp.call("Page.navigate", {"url": f"{arguments.base_url}/{query}"})
                _wait_page(cdp, selector)
                metrics = cdp.evaluate(PROBE)
                image = cdp.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
                (OUTPUT / f"{name}.png").write_bytes(base64.b64decode(image["data"]))
                critical = ["status_bar_visible", "navigation_visible", "alert_surface_visible"]
                if "setup" in name: critical.append("start_visible")
                if "mission" in name: critical.extend(["mission_state_visible", "map_visible", "fleet_panel_visible"])
                if "engineering" in name: critical.append("engineering_visible")
                if "review" in name: critical.append("review_visible")
                metrics.update({
                    "case": name,
                    "screenshot": f"data/stage12u_screenshots/{name}.png",
                    "required_surfaces": critical,
                    "pass": metrics["horizontal_overflow_px"] <= 1 and all(metrics[item] for item in critical),
                })
                results.append(metrics)
            # Accelerated route/lifecycle soak: warm all expensive views, then
            # repeatedly return to the same 2D mission state and collect the
            # heap. This catches leaked maps, animation loops, DOM nodes, and
            # WebGL resources without pretending to be a 30-minute Pi 4 test.
            soak_samples: list[dict[str, Any]] = []
            for cycle in range(7):
                _click_button(cdp, "ENGINEERING")
                _wait_page(cdp, ".engineering-page")
                _click_button(cdp, "3D")
                _wait_page(cdp, ".digital-twin-shell")
                _click_button(cdp, "REVIEW")
                _wait_page(cdp, ".review-page")
                _click_button(cdp, "MISSION")
                _wait_page(cdp, ".mission-page")
                _click_button(cdp, "2D")
                _wait_page(cdp, ".map-region")
                cdp.call("HeapProfiler.collectGarbage")
                heap = cdp.call("Runtime.getHeapUsage")
                dom = cdp.call("Memory.getDOMCounters")
                soak_samples.append({
                    "cycle": cycle,
                    "used_js_heap_mb": round(heap["usedSize"] / 1048576, 2),
                    "documents": dom["documents"],
                    "dom_nodes": dom["nodes"],
                    "event_listeners": dom["jsEventListeners"],
                })
            measured = soak_samples[1:]
            first, last = measured[0], measured[-1]
            soak = {
                "method": "ACCELERATED_6_CYCLE_ENGINEERING_REVIEW_MISSION_ROUTE_SOAK",
                "physical_pi4_browser_measured": False,
                "samples": measured,
                "heap_growth_mb": round(last["used_js_heap_mb"] - first["used_js_heap_mb"], 2),
                "dom_node_growth": last["dom_nodes"] - first["dom_nodes"],
                "listener_growth": last["event_listeners"] - first["event_listeners"],
            }
            soak["pass"] = (
                soak["heap_growth_mb"] <= 4.0
                and soak["dom_node_growth"] <= 25
                and soak["listener_growth"] <= 10
            )
            report = {
                "stage": "12U",
                "source": "LOCAL_HEADLESS_CHROME_DEV_MACHINE",
                "physical_pi4_browser_measured": False,
                "hardware_gate_h1": "PENDING",
                "cases": results,
                "accelerated_lifecycle_soak": soak,
                "pass": all(item["pass"] for item in results) and soak["pass"],
            }
            (OUTPUT / "browser_acceptance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2))
            cdp.close()
            return 0 if report["pass"] else 1
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
