"""Build the offline Stage 10S release candidate without downloading anything."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.qualification import load_competition_profile
from rescue_core.qualification.audit import secret_audit, sha256_file, source_manifest
from rescue_core.vision.manifest import load_model_manifest, validate_model_artifact
from tools.check_offline_compliance import scan


def copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules"))
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-frontend-build", action="store_true")
    parser.add_argument("--release-id")
    args = parser.parse_args()
    profile = load_competition_profile(ROOT / "config" / "competition_profile.yaml")
    release_id = args.release_id or datetime.now(UTC).strftime("stage10s_rc_%Y%m%dT%H%M%SZ")
    destination = ROOT / "release" / release_id

    if not args.skip_frontend_build:
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if npm is None:
            raise RuntimeError("npm was not found; cannot build the offline frontend release")
        subprocess.run([npm, "run", "build"], cwd=ROOT / "ground" / "frontend", check=True)
    checks, violations = scan(ROOT, require_build=True)
    if violations or not all(item.passed for item in checks):
        raise RuntimeError("Offline compliance failed; release bundle was not created")
    secret_result = secret_audit(ROOT)
    if not secret_result["passed"]:
        raise RuntimeError("Potential repository secret detected; inspect redacted audit before release")

    model_manifest_path = ROOT / "models" / "manifest.yaml"
    model_manifest = load_model_manifest(model_manifest_path)
    model_results = []
    for entry in model_manifest.models:
        if not entry.available:
            continue
        artifact = validate_model_artifact(entry, model_manifest_path)
        model_results.append({"model_id": entry.id, "backend": entry.backend, "artifact": str(artifact.relative_to(ROOT)) if artifact else None, "sha256": entry.sha256})

    required_reports = [
        "STAGE_10S_REPORT.md", "SOFTWARE_COMPETITION_SCORECARD.md",
        "PHYSICAL_VALIDATION_GAPS.md", "COMPETITION_READINESS_REPORT.md",
    ]
    missing = [name for name in required_reports if not (ROOT / name).is_file()]
    if missing:
        raise RuntimeError("Generate Stage 10S reports before release: " + ", ".join(missing))

    manifest = source_manifest(ROOT)
    source_project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for source, relative in (
        (ROOT / "ground" / "frontend" / "dist", Path("frontend_dist")),
        (ROOT / "config", Path("config_templates")),
        (ROOT / "models" / "manifest.yaml", Path("model_manifest/manifest.yaml")),
        (ROOT / "ground" / "backend" / "migrations", Path("database_migrations")),
        (ROOT / "deployment", Path("systemd_templates")),
        (ROOT / "docs", Path("docs")),
        (ROOT / "pyproject.toml", Path("dependency_records/pyproject.toml")),
        (ROOT / "requirements-lock.txt", Path("dependency_records/requirements-lock.txt")),
        (ROOT / "ground" / "frontend" / "package-lock.json", Path("dependency_records/frontend-package-lock.json")),
    ):
        copy_path(source, destination / relative)
    for name in required_reports:
        copy_path(ROOT / name, destination / name)

    packages = {}
    for package in ("rescue-swarm", "pydantic", "pymavlink", "shapely", "fastapi", "psutil"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = "UNKNOWN"
    build_hash = hashlib.sha256()
    for path in sorted(item for item in (destination / "frontend_dist").rglob("*") if item.is_file()):
        build_hash.update(path.relative_to(destination / "frontend_dist").as_posix().encode())
        build_hash.update(bytes.fromhex(sha256_file(path)))
    release_manifest = {
        "release_candidate_id": release_id,
        "created_at": datetime.now(UTC).isoformat(),
        "software_release_candidate": "PASS",
        "software_architecture": "FROZEN",
        "hardware_gate_h1": "PENDING",
        "physical_ready": False,
        "competition_ready": False,
        "rescueswarm_commit": "UNKNOWN",
        "source_declared_name": source_project["name"],
        "source_declared_version": source_project["version"],
        "source_manifest_sha256": manifest["aggregate_sha256"],
        "configuration_hashes": {
            path.name: sha256_file(path) for path in sorted((ROOT / "config").glob("*.yaml"))
        },
        "frontend_build_sha256": build_hash.hexdigest(),
        "python": sys.version,
        "platform": platform.platform(),
        "packages": packages,
        "installed_package_note": (
            "Installed environment metadata may predate this source tree; "
            "source_declared_version and source_manifest_sha256 are release-authoritative."
        ),
        "ardupilot": {
            "version": "ArduCopter 4.8.0",
            "commit": "af2a1bafc8ab993418e6714cbc2674dcc178d11a",
            "evidence": "Stage 9S live SITL; verify current installation for every new live campaign",
        },
        "models": model_results,
        "offline_compliance": "PASS",
        "secret_audit": secret_result,
        "competition_profile": profile.model_dump(mode="json"),
        "test_summary": "See STAGE_10S_REPORT.md",
    }
    manifest_path = destination / "SOFTWARE_RELEASE_MANIFEST.json"
    manifest_path.write_text(json.dumps(release_manifest, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PASS", "release": str(destination), "manifest": str(manifest_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
