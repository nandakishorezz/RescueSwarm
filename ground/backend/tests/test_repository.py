from ground.backend.app.domain.events import EventSeverity, EventType, MissionEvent
from ground.backend.app.domain.mission import Mission
from ground.backend.app.repositories import Database, SqlAlchemyEventRepository, SqlAlchemyMissionRepository


def test_sqlite_repository_roundtrip_and_event_order() -> None:
    database = Database("sqlite:///:memory:")
    database.create_schema()
    missions = SqlAlchemyMissionRepository(database.session_factory)
    events = SqlAlchemyEventRepository(database.session_factory)
    mission = missions.add(Mission(name="Repository Test"))
    fetched = missions.get(mission.id)
    assert fetched is not None and fetched.name == mission.name
    event = events.add(
        MissionEvent(
            mission_id=mission.id,
            type=EventType.WARNING,
            severity=EventSeverity.WARNING,
            message="Test warning",
            metadata={"source": "unit-test"},
        )
    )
    restored = events.list_for_mission(mission.id)
    assert restored[0].event_id == event.event_id
    assert restored[0].metadata == {"source": "unit-test"}

