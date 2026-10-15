#!/usr/bin/env python3
"""Scan Stage 2 runtime assets/config for accidental external dependencies."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_HOST_FRAGMENTS = (
    "tile.openstreetmap.org",
    "googleapis.com",
    "google.com/maps",
    "mapbox.com",
    "unpkg.com",
    "jsdelivr.net",
    "cdnjs.com",
    "fonts.googleapis.com",
    "firebase",
    "supabase",
    "auth0",
)
RUNTIME_SUFFIXES = {".html", ".js", ".css", ".ts", ".tsx", ".json", ".yaml", ".yml", ".py"}
REMOTE_RESOURCE = re.compile(
    r"(?:src|href|url\(|import\s+[^;]*?from\s+|fetch\()\s*[('\"]*https?://",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def _runtime_files(root: Path) -> list[Path]:
    frontend = root / "ground" / "frontend"
    candidates = [
        frontend / "src", frontend / "dist", root / "ground" / "backend" / "app",
        root / "rescue_core" / "vision",
        root / "rescue_core" / "geolocation",
        root / "rescue_core" / "hardware",
        root / "rescue_core" / "autonomy",
        root / "rescue_core" / "resilience",
        root / "rescue_core" / "qualification",
    ]
    files = [
        frontend / "index.html", frontend / "vite.config.ts",
        root / "config" / "ground.yaml", root / "config" / "vision.yaml",
        root / "models" / "manifest.yaml", root / "scripts" / "run_vision_agent.py",
        root / "scripts" / "run_stage4_pi_benchmark.py",
        root / "config" / "geolocation.yaml", root / "scripts" / "run_stage5_demo.py",
        root / "tools" / "stage5_synthetic_validation.py",
        root / "config" / "stage6.yaml",
        root / "scripts" / "run_stage6_hardware_baseline.py",
        root / "scripts" / "run_stage6_physical_validation.py",
        root / "scripts" / "run_stage6_readonly_agent.py",
        root / "scripts" / "run_stage6_static_geolocation.py",
        root / "scripts" / "capture_stage6_physical_vision.py",
        root / "scripts" / "run_stage7s_rescue_mission.py",
        root / "scripts" / "run_stage8s_fault_campaign.py",
        root / "scripts" / "run_stage9s_fleet.py",
        root / "scripts" / "run_stage10s_qualification.py",
        root / "scripts" / "run_stage11s_qualification.py",
        root / "scripts" / "build_stage12u_ui_release.py",
        root / "scripts" / "build_competition_release.py",
    ]
    for directory in candidates:
        if directory.is_dir():
            files.extend(path for path in directory.rglob("*") if path.is_file() and path.suffix in RUNTIME_SUFFIXES)
    return sorted(set(path for path in files if path.is_file()))


def scan(root: Path = REPOSITORY_ROOT, require_build: bool = True) -> tuple[list[Check], list[str]]:
    files = _runtime_files(root)
    violations: list[str] = []
    remote_resources: list[str] = []
    cloud_hits: list[str] = []
    auth_hits: list[str] = []
    map_hits: list[str] = []
    vision_network_hits: list[str] = []
    geolocation_network_hits: list[str] = []
    hardware_network_hits: list[str] = []
    autonomy_network_hits: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(root)
        lowered = text.lower()
        for fragment in FORBIDDEN_HOST_FRAGMENTS:
            if fragment in lowered:
                entry = f"{relative}: contains forbidden token '{fragment}'"
                violations.append(entry)
                if fragment in {"tile.openstreetmap.org", "mapbox.com", "google.com/maps"}:
                    map_hits.append(entry)
                if fragment in {"firebase", "supabase"}:
                    cloud_hits.append(entry)
                if fragment == "auth0":
                    auth_hits.append(entry)
        if REMOTE_RESOURCE.search(text):
            entry = f"{relative}: contains an external runtime resource reference"
            violations.append(entry)
            remote_resources.append(entry)
        if "vision" in relative.as_posix() or relative.name == "run_stage4_pi_benchmark.py":
            for token in ("requests.get(", "requests.post(", "urllib.request", "httpx.get(", "httpx.post("):
                if token in lowered:
                    vision_network_hits.append(f"{relative}: vision runtime contains network client token '{token}'")
        if "geolocation" in relative.as_posix() or "stage5" in relative.name:
            for token in ("requests.get(", "requests.post(", "urllib.request", "httpx.get(", "httpx.post("):
                if token in lowered:
                    geolocation_network_hits.append(f"{relative}: geolocation runtime contains network client token '{token}'")
        if "hardware" in relative.as_posix() or "stage6" in relative.name:
            for token in ("requests.get(", "requests.post(", "urllib.request", "httpx.get(", "httpx.post("):
                if token in lowered:
                    hardware_network_hits.append(f"{relative}: physical bench runtime contains network client token '{token}'")
        if (
            "fleet" in relative.as_posix()
            or "autonomy" in relative.as_posix()
            or "resilience" in relative.as_posix()
            or "qualification" in relative.as_posix()
            or any(stage in relative.name for stage in ("stage7", "stage8", "stage9", "stage10", "stage11", "stage12"))
        ):
            for token in ("requests.get(", "requests.post(", "urllib.request", "httpx.get(", "httpx.post("):
                if token in lowered:
                    autonomy_network_hits.append(
                        f"{relative}: autonomy/fleet runtime contains network client token '{token}'"
                    )
    config_path = root / "config" / "ground.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    external_tiles = config.get("map", {}).get("external_tile_sources", [])
    external_apis = config.get("external_services", {}).get("api_dependencies", [])
    external_auth = config.get("external_services", {}).get("auth_dependencies", [])
    internet_required = config.get("network", {}).get("external_internet_required", True)
    if external_tiles:
        map_hits.append("config/ground.yaml: external tile source configured")
    if external_apis or internet_required:
        cloud_hits.append("config/ground.yaml: external API/internet requirement configured")
    if external_auth:
        auth_hits.append("config/ground.yaml: external authentication configured")
    dist = root / "ground" / "frontend" / "dist"
    assets_ready = (dist / "index.html").is_file() if require_build else (root / "ground" / "frontend" / "src").is_dir()
    checks = [
        Check("Frontend assets local", assets_ready and not remote_resources, "Production build present and runtime resource references are local" if require_build else "Frontend source is present and runtime resource references are local"),
        Check("External map sources", not map_hits and not external_tiles, "No external tile/map source found"),
        Check("External script sources", not remote_resources, "No remote script, stylesheet, import, fetch, or CSS URL found"),
        Check("Cloud backend dependency", not cloud_hits and not external_apis and not internet_required, "No cloud backend dependency configured"),
        Check("External auth dependency", not auth_hits and not external_auth, "No external authentication dependency configured"),
        Check("External API dependency", not external_apis, "No external API dependency configured"),
        Check("Vision runtime local", not vision_network_hits, "Camera, model, and inference runtime contains no external network client"),
        Check("Geolocation runtime local", not geolocation_network_hits, "Calibration, pose, and geolocation runtime contains no external network client"),
        Check("Physical bench runtime local", not hardware_network_hits, "Stage 6 diagnostics and static geolocation runtime contain no external network client"),
        Check("Autonomy and fleet runtime local", not autonomy_network_hits, "Stages 7S–12U autonomy, resilience, fleet, qualification, digital-twin, and UI release runtime contain no external network client"),
    ]
    return checks, sorted(set(
        violations + map_hits + cloud_hits + auth_hits + vision_network_hits
        + geolocation_network_hits + hardware_network_hits + autonomy_network_hits
    ))


def render_report(checks: list[Check], violations: list[str]) -> str:
    result = "PASS" if all(check.passed for check in checks) else "FAIL"
    lines = [
        "# RescueSwarm Offline Compliance Report",
        "",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Overall: **{result}**",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| {check.name} | {'PASS' if check.passed else 'FAIL'} | {check.detail} |"
        for check in checks
    )
    lines.extend(["", "## Violations", ""])
    lines.extend(f"- {violation}" for violation in violations) if violations else lines.append("- None.")
    lines.extend([
        "",
        "The scan covers application source, production build assets, and ground runtime configuration. Package registries used only during development installation are outside mission runtime.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--output", type=Path, default=REPOSITORY_ROOT / "OFFLINE_COMPLIANCE_REPORT.md")
    parser.add_argument("--source-only", action="store_true", help="Do not require a production build")
    args = parser.parse_args()
    checks, violations = scan(args.root.resolve(), require_build=not args.source_only)
    args.output.write_text(render_report(checks, violations), encoding="utf-8")
    print(args.output)
    return 0 if all(check.passed for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
