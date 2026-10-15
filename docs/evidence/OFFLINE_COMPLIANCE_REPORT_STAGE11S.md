# RescueSwarm Offline Compliance Report

Generated: 2026-09-05T10:48:51+05:30
Overall: **PASS**

| Check | Result | Detail |
|---|---|---|
| Frontend assets local | PASS | Production build present and runtime resource references are local |
| External map sources | PASS | No external tile/map source found |
| External script sources | PASS | No remote script, stylesheet, import, fetch, or CSS URL found |
| Cloud backend dependency | PASS | No cloud backend dependency configured |
| External auth dependency | PASS | No external authentication dependency configured |
| External API dependency | PASS | No external API dependency configured |
| Vision runtime local | PASS | Camera, model, and inference runtime contains no external network client |
| Geolocation runtime local | PASS | Calibration, pose, and geolocation runtime contains no external network client |
| Physical bench runtime local | PASS | Stage 6 diagnostics and static geolocation runtime contain no external network client |
| Autonomy and fleet runtime local | PASS | Stages 7S–11S autonomy, resilience, fleet, qualification, and digital-twin runtime contain no external network client |

## Violations

- None.

The scan covers application source, production build assets, and ground runtime configuration. Package registries used only during development installation are outside mission runtime.
