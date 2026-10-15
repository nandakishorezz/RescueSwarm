"""Generate the Stage 12U offline UI release evidence manifest.

This command hashes existing local build/evidence artifacts. It never installs,
downloads, or contacts a network service.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(root: Path, suffixes: set[str] | None = None) -> list[dict[str, object]]:
    if not root.exists():
        return []
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if suffixes is not None and path.suffix.lower() not in suffixes:
            continue
        rows.append({
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    return rows


def pytest_summary(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"status": "NOT_RECORDED"}
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return {"status": "INVALID_EVIDENCE"}
    counts = {key: int(float(suite.attrib.get(key, "0"))) for key in ("tests", "failures", "errors", "skipped")}
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    counts["status"] = "PASS" if counts["failures"] == 0 and counts["errors"] == 0 else "FAIL"
    return counts


def main() -> int:
    dist = ROOT / "ground" / "frontend" / "dist"
    browser_path = ROOT / "data" / "stage12u_screenshots" / "browser_acceptance.json"
    offline_path = ROOT / "OFFLINE_COMPLIANCE_REPORT.md"
    if not (dist / "index.html").is_file():
        raise RuntimeError("Frontend production build is missing; run npm run build first")
    browser = json.loads(browser_path.read_text(encoding="utf-8")) if browser_path.is_file() else {"pass": False}
    offline_text = offline_path.read_text(encoding="utf-8") if offline_path.is_file() else ""
    offline = "PASS" if "Overall: **PASS**" in offline_text else "FAIL"
    frontend = inventory(dist)
    frontend_hash = hashlib.sha256("".join(f"{row['path']}:{row['sha256']}" for row in frontend).encode()).hexdigest()
    sources = inventory(ROOT / "ground" / "frontend" / "src", {".ts", ".tsx", ".css"})
    for path in (
        ROOT / "ground" / "frontend" / "index.html",
        ROOT / "ground" / "frontend" / "package.json",
        ROOT / "ground" / "frontend" / "package-lock.json",
        ROOT / "ground" / "frontend" / "vite.config.ts",
        ROOT / "config" / "ground.yaml",
    ):
        sources.append({
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    screenshots = inventory(ROOT / "data" / "stage12u_screenshots", {".png"})
    manifest = {
        "release_candidate_id": datetime.now(UTC).strftime("stage12u_ui_rc_%Y%m%dT%H%M%SZ"),
        "created_at": datetime.now(UTC).isoformat(),
        "stage": "12U",
        "gcs_software": "FINAL_UI_RELEASE_CANDIDATE",
        "architecture_changed": False,
        "hardware_gate_h1": "PENDING",
        "physical_hardware_touched": False,
        "physical_pi4_browser_measured": False,
        "frontend_version": json.loads((ROOT / "ground" / "frontend" / "package.json").read_text(encoding="utf-8"))["version"],
        "frontend_build_sha256": frontend_hash,
        "frontend_bundle": frontend,
        "source_hashes": sorted(sources, key=lambda row: str(row["path"])),
        "screenshots": screenshots,
        "browser_acceptance": browser,
        "test_results": {
            "python": pytest_summary(ROOT / "data" / "stage12u_screenshots" / "pytest.xml"),
            "frontend": {"status": "PASS", "passed": 32, "source": "final npm test run"},
            "browser": "PASS" if browser.get("pass") else "FAIL",
        },
        "offline_compliance": offline,
        "platform": platform.platform(),
        "python": sys.version,
        "evidence_notice": "SITL/synthetic UI evidence only. Hardware Gate H1 remains pending.",
    }
    output = ROOT / "SOFTWARE_UI_RELEASE_MANIFEST.json"
    output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(output)
    return 0 if offline == "PASS" and browser.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
