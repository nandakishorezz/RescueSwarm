from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "ground" / "frontend"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_stage12u_accessibility_and_responsive_gates_are_encoded() -> None:
    css = read("ground/frontend/src/styles.css")
    assert ":focus-visible" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "@media (max-width:" in css
    assert "overflow-x: hidden" in css


def test_expensive_routes_and_spatial_renderers_are_lazy_loaded() -> None:
    app = read("ground/frontend/src/app/App.tsx")
    spatial = read("ground/frontend/src/components/SpatialMissionView.tsx")
    map_view = read("ground/frontend/src/components/MapView.tsx")
    assert "lazy(" in app
    assert "lazy(" in spatial
    assert "import(\"./digitalTwinLayer\")" in map_view


def test_webgl_and_map_resources_have_explicit_cleanup() -> None:
    twin = read("ground/frontend/src/components/DigitalTwinView.tsx")
    layer = read("ground/frontend/src/components/digitalTwinLayer.ts")
    map_view = read("ground/frontend/src/components/MapView.tsx")
    assert "cancelAnimationFrame" in twin
    assert "forceContextLoss" in twin
    assert "renderLists.dispose" in twin
    assert "renderer.dispose" in layer
    assert "map.remove()" in map_view


def test_render_state_is_bounded() -> None:
    store = read("ground/frontend/src/stores/gcsStore.ts")
    event_feed = read("ground/frontend/src/components/EventFeed.tsx")
    assert "recentEventsMax" in store
    assert "trailMaxPoints" in store
    assert ".slice(-100)" in store
    assert ".slice(-12)" in event_feed


def test_competition_and_hardware_provenance_remain_visible() -> None:
    mode_nav = read("ground/frontend/src/components/ModeNav.tsx")
    status = read("ground/frontend/src/components/StatusBar.tsx")
    assert "competition && item === \"ENGINEERING\"" in mode_nav
    assert "SIMULATION" in status
    assert "Hardware Gate H1" in status
    assert "PENDING" in status


def test_browser_acceptance_covers_required_desktop_cases() -> None:
    probe = read("scripts/run_stage12u_browser_acceptance.py")
    for case in (
        "setup_1366x768",
        "mission_parallel_search_1366x768",
        "mission_parallel_search_1920x1080",
        "mission_split_1536x864",
        "engineering_3d_camera_geometry_1920x1080",
        "review_replay_1366x768",
        "mission_125pct_zoom",
    ):
        assert case in probe
    assert "127.0.0.1" in probe


def test_no_stage12_runtime_asset_uses_remote_http() -> None:
    for path in (FRONTEND / "src").rglob("*"):
        if path.is_file() and path.suffix in {".ts", ".tsx", ".css", ".html"}:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            assert "http://" not in text
            assert "https://" not in text
