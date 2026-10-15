# config/ — System Configuration Files

YAML configuration files for all system components. These are checked into git and can be changed without modifying code.

## Files

| File | Purpose |
|------|---------|
| `ground.yaml` | GCS backend settings (ports, timeouts, paths) |
| `vision.yaml` | YOLO model selection, confidence thresholds |
| `geolocation.yaml` | Camera FOV, geolocation pipeline parameters |
| `rescue_orchestration.yaml` | Rescue mission parameters (hover time, drop height) |
| `resilience_scenarios.yaml` | Fault injection scenarios and recovery policies |
| `competition_profile.yaml` | Competition-specific overrides |
| `logging.yaml` | Log levels per module |
| `prototype.yaml` | Development / prototype settings |
| `stage6.yaml` | Stage 6 hardware test configuration |

## Usage

The backend reads `config/ground.yaml` at startup. Override with environment variable:
```bash
RESCUESWARM_CONFIG=config/competition_profile.yaml uvicorn app.main:app
```
