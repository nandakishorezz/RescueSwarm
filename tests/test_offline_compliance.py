from pathlib import Path

from tools.check_offline_compliance import scan


def test_application_source_has_no_external_runtime_dependency() -> None:
    checks, violations = scan(Path.cwd(), require_build=False)
    assert all(check.passed for check in checks), violations


def test_scanner_detects_remote_runtime_resource(tmp_path: Path) -> None:
    (tmp_path / "ground/frontend/src").mkdir(parents=True)
    (tmp_path / "ground/frontend/src/app.ts").write_text(
        'fetch("https://tile.openstreetmap.org/example")', encoding="utf-8"
    )
    (tmp_path / "ground/frontend/index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/ground.yaml").write_text(
        "network:\n  external_internet_required: false\nmap:\n  external_tile_sources: []\nexternal_services:\n  api_dependencies: []\n  auth_dependencies: []\n",
        encoding="utf-8",
    )
    checks, violations = scan(tmp_path, require_build=False)
    assert not all(check.passed for check in checks)
    assert any("openstreetmap" in violation for violation in violations)

