import sys
sys.path.insert(0, 'c:/Users/nanda/Downloads/SURVIVOR_DETECTION/ground/backend')
from app.domain.vehicle import VehicleState
from app.sitl.telemetry import SITLTelemetrySnapshot
from app.services.telemetry_service import RealtimeEnvelope

try:
    vehicle = VehicleState(
        vehicle_id="drone_01",
        name="ArduCopter SITL 01",
        latitude=0,
        longitude=0,
        connected=False,
        flight_mode="UNKNOWN",
        simulated=False,
    )
    snapshot = SITLTelemetrySnapshot(vehicle=vehicle)
    env = RealtimeEnvelope(
        type="vehicle.telemetry", mission_id="mission123", vehicle_id=vehicle.vehicle_id,
        sequence=vehicle.sequence,
        payload={
            **vehicle.model_dump(mode="json"), "source": "SITL",
            "current_mission_item": snapshot.current_mission_item,
            "total_mission_items": snapshot.total_mission_items,
        },
    )
    print("Telemetry Envelope OK!")
    
    env2 = RealtimeEnvelope(
        type="mission.progress", mission_id="mission123", vehicle_id=vehicle.vehicle_id,
        sequence=vehicle.sequence,
        payload={
            "execution_source": "SITL", "execution_state": "RUNNING",
            "rescue_state": None,
            "state": "RUNNING", "elapsed_seconds": 12.3,
            "coverage_percent": 10.0,
            "actual_trail_point": None,
        },
    )
    print("Progress Envelope OK!")
except Exception as e:
    import traceback
    traceback.print_exc()
