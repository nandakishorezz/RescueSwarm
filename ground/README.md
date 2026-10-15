# ground/ — Ground Control Station (GCS)

This is the full-stack GCS application used to plan, launch, monitor, and review missions.

## Sub-directories

```
ground/
├── backend/          Python FastAPI server
│   └── app/
│       ├── api/          HTTP + WebSocket route handlers
│       ├── domain/       Core domain models (Mission, Target, etc.)
│       ├── fleet/        Multi-drone fleet coordination
│       ├── planning/     Coverage path planner
│       ├── repositories/ Data persistence (SQLite)
│       ├── resilience/   Failsafe / safety monitor
│       ├── services/     WebSocket manager, telemetry service
│       ├── simulation/   Simulation perception / evaluator
│       └── sitl/         ArduPilot SITL launcher, mission runner, surveillance
└── frontend/         React + Vite GCS UI
    └── src/
        ├── app/          Root App component and router
        ├── components/   Reusable UI components (map, panels, feeds)
        ├── hooks/        Custom React hooks (WebSocket, mission socket)
        ├── pages/        Top-level pages (Mission, Setup, Review, Simulation)
        ├── services/     API client
        ├── spatial/      3D / MapLibre spatial utilities
        ├── stores/       Zustand state stores
        ├── types/        TypeScript domain types
        └── test/         Component unit tests
```

## Running Locally

```bash
# Backend
cd ground/backend
uvicorn app.main:app --host 127.0.0.1 --port 8000

# Frontend
cd ground/frontend
npm run dev
```
