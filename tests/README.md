# tests/ — Unit and Integration Test Suite

Automated tests run by `pytest`. These test library code in `rescue_core/` without requiring hardware or a running simulation.

## Structure

```
tests/
├── test_adapter.py                  MAVLink adapter unit tests
├── test_builders.py                 Mission plan builder tests
├── test_geolocation_*.py            Geolocation pipeline tests (6 files)
├── test_vision_*.py                 YOLO vision pipeline tests
├── test_phase4_*.py                 Phase 4 qualification tests
├── test_stage*.py                   Per-stage regression tests
├── test_config.py                   Config loading tests
├── test_telemetry.py                Telemetry model tests
└── test_offline_compliance.py       Offline operation compliance
```

## Running Tests

```bash
# All tests
pytest

# Specific module
pytest tests/test_geolocation_chain.py -v

# Quick smoke test (no hardware)
python test_all.py
```
