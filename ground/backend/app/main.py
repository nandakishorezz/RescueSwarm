"""RescueSwarm offline GCS through Stage 8S SITL resilience validation."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import digital_twin, engineering, fleet, health, missions, multidrone, resilience, simulation, sitl, system, vision, websocket
from .config import GroundConfig, load_ground_config
from .repositories import (
    Database,
    SqlAlchemyEventRepository,
    SqlAlchemyExecutionRepository,
    SqlAlchemyMissionRepository,
    SqlAlchemyPlanRepository,
    SqlAlchemyTargetRepository,
    SqlAlchemyVehicleRepository,
)
from .services.boundary_service import BoundaryService
from .services.mission_service import MissionNotFoundError, MissionService
from .services.geolocation_service import GeolocationMissionService
from .services.offline_guard import OfflineGuard
from .services.planning_service import PlanningService
from .services.websocket_manager import WebSocketManager
from .simulation.simulator_manager import SimulatorManager
from .sitl.manager import SITLManager
from .resilience.manager import ResilienceManager
from .fleet.manager import FleetManager
from .multidrone.runtime import MultiDroneRuntime
from rescue_core.simulation.orchestration import SimulationManager as SimulationLabManager

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def create_app(config: GroundConfig | None = None) -> FastAPI:
    settings = config or load_ground_config(REPOSITORY_ROOT / "config" / "ground.yaml")
    database = Database(settings.database.url)
    database.create_schema()
    mission_repository = SqlAlchemyMissionRepository(database.session_factory)
    vehicle_repository = SqlAlchemyVehicleRepository(database.session_factory)
    target_repository = SqlAlchemyTargetRepository(database.session_factory)
    event_repository = SqlAlchemyEventRepository(database.session_factory)
    plan_repository = SqlAlchemyPlanRepository(database.session_factory)
    execution_repository = SqlAlchemyExecutionRepository(database.session_factory)
    mission_service = MissionService(
        mission_repository, event_repository, vehicle_repository, target_repository
    )
    websocket_manager = WebSocketManager()
    geolocation_service = GeolocationMissionService(target_repository, event_repository, websocket_manager)
    planning_service = PlanningService(mission_repository, plan_repository, event_repository)
    simulator_manager = SimulatorManager(
        mission_service=mission_service,
        vehicles=vehicle_repository,
        targets=target_repository,
        events=event_repository,
        websocket=websocket_manager,
        seed=settings.simulator.seed,
        total_steps=settings.simulator.total_steps,
        interval_seconds=settings.simulator.update_interval_seconds,
        telemetry_persist_interval_seconds=settings.database.telemetry_persist_interval_seconds,
    )
    sitl_manager = SITLManager(
        settings,
        REPOSITORY_ROOT,
        mission_service,
        plan_repository,
        execution_repository,
        vehicle_repository,
        event_repository,
        websocket_manager,
        geolocation_service,
    )
    resilience_manager = ResilienceManager(
        settings, REPOSITORY_ROOT, event_repository, execution_repository,
        target_repository, vehicle_repository, websocket_manager, sitl_manager,
    )
    fleet_manager = FleetManager(settings, REPOSITORY_ROOT, planning_service)
    simulation_lab_manager = SimulationLabManager(REPOSITORY_ROOT / "data" / "simulation_runs")
    multidrone_runtime = MultiDroneRuntime(
        REPOSITORY_ROOT, settings, mission_service, planning_service,
        simulation_lab_manager, sitl_manager, websocket_manager,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await simulator_manager.shutdown()
        await sitl_manager.shutdown()
        await multidrone_runtime.shutdown()
        simulation_lab_manager.close()

    app = FastAPI(
        title="RescueSwarm Offline GCS",
        version="0.13.0-phase1",
        description="Local-only RescueSwarm GCS and truth-isolated Gazebo simulation laboratory",
        lifespan=lifespan,
    )
    app.state.config = settings
    app.state.database = database
    app.state.mission_service = mission_service
    app.state.planning_service = planning_service
    app.state.execution_repository = execution_repository
    app.state.boundary_service = BoundaryService(settings.competition.max_area_hectares)
    app.state.offline_guard = OfflineGuard(settings, REPOSITORY_ROOT)
    app.state.websocket_manager = websocket_manager
    app.state.geolocation_service = geolocation_service
    app.state.simulator_manager = simulator_manager
    app.state.sitl_manager = sitl_manager
    app.state.resilience_manager = resilience_manager
    app.state.fleet_manager = fleet_manager
    app.state.simulation_lab_manager = simulation_lab_manager
    app.state.multidrone_runtime = multidrone_runtime
    app.state.repository_root = REPOSITORY_ROOT

    @app.exception_handler(MissionNotFoundError)
    async def mission_not_found_handler(_, exc: MissionNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"Mission not found: {exc.args[0]}"})

    @app.exception_handler(ValueError)
    async def value_error_handler(_, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    cors_origins = [] if settings.competition.enabled else settings.server.allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(system.router)
    app.include_router(missions.router)
    app.include_router(digital_twin.router)
    app.include_router(digital_twin.artifact_router)
    app.include_router(engineering.router)
    app.include_router(sitl.router)
    app.include_router(resilience.router)
    app.include_router(simulation.router)
    app.include_router(fleet.router)
    app.include_router(multidrone.router)
    app.include_router(vision.router)
    app.include_router(websocket.router)

    frontend_dist = REPOSITORY_ROOT / "ground" / "frontend" / "dist"
    if frontend_dist.is_dir():
        assets = frontend_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

        @app.get("/{frontend_path:path}", include_in_schema=False)
        async def frontend(frontend_path: str):
            requested = (frontend_dist / frontend_path).resolve()
            
            # Helper to add no-cache headers to index.html
            def no_cache(res: FileResponse):
                res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
                res.headers["Pragma"] = "no-cache"
                res.headers["Expires"] = "0"
                return res

            if requested.is_file() and frontend_dist.resolve() in requested.parents:
                if requested.name == "index.html":
                    return no_cache(FileResponse(requested))
                return FileResponse(requested)
            
            return no_cache(FileResponse(frontend_dist / "index.html"))
    else:
        @app.get("/", include_in_schema=False)
        async def backend_root() -> dict[str, str]:
            return {
                "service": "RescueSwarm Offline GCS backend",
                "frontend": "Run npm build to create local static assets",
            }
    return app

app = create_app()

