# Offline Architecture

## Runtime dependency boundary

```text
Operator browser
      |
      | RescueSwarm AP/LAN only
      v
FastAPI :80 on Pi 4
  |-- local frontend dist assets
  |-- local REST /api
  |-- local WebSocket /ws/mission/{id}
  `-- local PostgreSQL/PostGIS
```

The MapLibre style contains only a background layer and empty local sources. Runtime geometry is created from the local mission database/WebSocket. No raster/vector tile URL, glyph URL, sprite URL, remote font, CDN, analytics endpoint, external API, cloud database, or external authentication provider is configured.

Frontend requests are relative in the production build. The optional `VITE_API_ORIGIN` is for a developer-supplied local backend during Vite hot reload. The production browser never needs DNS or WAN access.

## OfflineGuard

`GET /api/system/offline-status` reports:

- external internet requirement;
- external map/API/auth dependency counts;
- local source/static asset presence;
- local database placement/health;
- local WebSocket availability;
- competition mode and violations.

`OfflineGuard.enable_competition_network_lockdown()` deliberately raises `NotImplementedError`: Stage 2 never changes firewall or host networking. The prepared Pi AP script also defaults to a dry run and requires `--apply`, a Pi model check, root authorization, and a local password environment variable.

## Compliance scan

`tools/check_offline_compliance.py` scans frontend source, built JavaScript/CSS/HTML, and ground configuration. It rejects known online tile/CDN/cloud/auth providers and external runtime resource syntax. It writes `OFFLINE_COMPLIANCE_REPORT.md` and exits nonzero on failure.

Development package registries are not runtime resources. Before field operation, Python wheels, Debian packages, npm packages/build output, and PostgreSQL must already be installed locally. Do not configure package/image pulling during mission boot.

## Competition mode

Set `competition.enabled: true` only in a reviewed Pi-local configuration. This removes CORS origins, disables every `/api/engineering/simulator/...` operation, hides Engineering Mode in the UI, and makes offline compliance prominent. It does not enable any flight command.

