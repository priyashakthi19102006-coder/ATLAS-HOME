"""Unit tests for ATLAS Event Engine, Storage, and Context Manager."""

import time
import pytest
from atlas.context.memory import ContextManager
from atlas.events.engine import EventEngine
from atlas.events.schema import EventSeverity, EventStatus, EventType
from atlas.events.storage import EventStorage
from atlas.perception.schema import (
    ActionObservation,
    BoundingBox,
    CenterPoint,
    MovementObservation,
    ObjectObservation,
    PerceptionObservation,
    PersonObservation,
)


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_events.db"
    storage = EventStorage(db_path=str(db_file))
    yield storage
    storage.close()


@pytest.fixture
def context_mgr(temp_db):
    return ContextManager(retention_window_seconds=60.0, storage=temp_db)


@pytest.fixture
def event_engine(context_mgr):
    return EventEngine(
        context_manager=context_mgr,
        track_expiry_seconds=1.0,
        association_distance_px=100.0,
    )


def make_observation(
    frame_id: int,
    timestamp: float,
    persons: list[PersonObservation] | None = None,
    objects: list[ObjectObservation] | None = None,
) -> PerceptionObservation:
    return PerceptionObservation(
        frame_id=frame_id,
        timestamp="2026-09-09T12:00:00.000000Z",
        epoch_timestamp=timestamp,
        source="atlas_home_camera",
        persons=persons or [],
        objects=objects or [],
    )


def test_person_entered_and_deduplication(event_engine):
    t0 = 100.0
    person1 = PersonObservation(
        track_id=1,
        bbox=BoundingBox(x1=100, y1=100, x2=200, y2=300),
        center=CenterPoint(x=150, y=200),
        confidence=0.88,
        first_seen=t0,
        last_seen=t0,
        movement=MovementObservation(state="stationary", speed_px_s=2.0),
        action=ActionObservation(label="standing", confidence=0.85),
    )

    # Frame 1: New person appears -> PERSON_ENTERED event emitted
    obs1 = make_observation(frame_id=1, timestamp=t0, persons=[person1])
    events1 = event_engine.process_observation(obs1)
    assert len(events1) == 1
    assert events1[0].event_type == EventType.PERSON_ENTERED.value
    assert events1[0].person_id == "1"
    assert events1[0].status == EventStatus.ACTIVE.value
    assert events1[0].severity == EventSeverity.INFO.value

    # Frame 2: Same person, same action, same movement -> Deduplicated, 0 new events
    obs2 = make_observation(frame_id=2, timestamp=t0 + 0.1, persons=[person1])
    events2 = event_engine.process_observation(obs2)
    assert len(events2) == 0


def test_action_and_movement_transitions(event_engine):
    t0 = 100.0
    person_standing = PersonObservation(
        track_id=1,
        bbox=BoundingBox(x1=100, y1=100, x2=200, y2=300),
        center=CenterPoint(x=150, y=200),
        confidence=0.85,
        first_seen=t0,
        last_seen=t0,
        movement=MovementObservation(state="stationary", speed_px_s=2.0),
        action=ActionObservation(label="standing", confidence=0.85),
    )
    # 1. Enter standing
    event_engine.process_observation(make_observation(1, t0, persons=[person_standing]))

    # 2. Transition to walking
    person_walking = PersonObservation(
        track_id=1,
        bbox=BoundingBox(x1=120, y1=100, x2=220, y2=300),
        center=CenterPoint(x=170, y=200),
        confidence=0.87,
        first_seen=t0,
        last_seen=t0 + 0.2,
        movement=MovementObservation(state="moving", speed_px_s=45.0),
        action=ActionObservation(label="walking", confidence=0.82),
    )
    events = event_engine.process_observation(make_observation(2, t0 + 0.2, persons=[person_walking]))
    assert len(events) == 1
    assert events[0].event_type == EventType.PERSON_WALKING.value
    assert events[0].person_id == "1"

    # 3. Transition to fall_like observation -> Emits PERSON_FALL_LIKE with WARNING severity
    person_fall = PersonObservation(
        track_id=1,
        bbox=BoundingBox(x1=120, y1=250, x2=320, y2=330),
        center=CenterPoint(x=220, y=290),
        confidence=0.89,
        first_seen=t0,
        last_seen=t0 + 0.5,
        movement=MovementObservation(state="stationary", speed_px_s=5.0),
        action=ActionObservation(label="fall_like", confidence=0.84, uncertainty="warning"),
    )
    events_fall = event_engine.process_observation(make_observation(3, t0 + 0.5, persons=[person_fall]))
    assert len(events_fall) == 1
    assert events_fall[0].event_type == EventType.PERSON_FALL_LIKE.value
    assert events_fall[0].severity == EventSeverity.WARNING.value


def test_person_departure_lifecycle(event_engine, context_mgr):
    t0 = 100.0
    person1 = PersonObservation(
        track_id=42,
        bbox=BoundingBox(x1=50, y1=50, x2=150, y2=250),
        center=CenterPoint(x=100, y=150),
        confidence=0.9,
        first_seen=t0,
        last_seen=t0,
        movement=MovementObservation(state="stationary", speed_px_s=0.0),
        action=ActionObservation(label="standing", confidence=0.9),
    )
    # Enter
    event_engine.process_observation(make_observation(1, t0, persons=[person1]))
    ctx_before = context_mgr.get_recent_context(window_seconds=10.0)
    assert ctx_before["active_persons_count"] == 1

    # Frame after track expiry seconds without track 42 -> PERSON_LEFT emitted
    obs_empty = make_observation(2, t0 + 1.5, persons=[])
    events_left = event_engine.process_observation(obs_empty)
    assert len(events_left) == 1
    assert events_left[0].event_type == EventType.PERSON_LEFT.value
    assert events_left[0].person_id == "42"
    assert events_left[0].status == EventStatus.RESOLVED.value

    # Context should show 0 active persons
    ctx_after = context_mgr.get_recent_context(window_seconds=10.0)
    assert ctx_after["active_persons_count"] == 0


def test_object_movement_and_proximity_association(event_engine):
    t0 = 100.0
    person = PersonObservation(
        track_id=1,
        bbox=BoundingBox(x1=100, y1=100, x2=200, y2=300),
        center=CenterPoint(x=150, y=200),
        confidence=0.85,
        first_seen=t0,
        last_seen=t0,
        movement=MovementObservation(state="stationary", speed_px_s=0.0),
        action=ActionObservation(label="standing", confidence=0.85),
    )
    # Object initially stationary nearby
    cup_stationary = ObjectObservation(
        track_id=10,
        class_name="cup",
        bbox=BoundingBox(x1=160, y1=180, x2=180, y2=210),
        center=CenterPoint(x=170, y=195),
        confidence=0.78,
        first_seen=t0,
        last_seen=t0,
        movement=MovementObservation(state="stationary", speed_px_s=0.0),
    )
    # 1. Enter events
    event_engine.process_observation(make_observation(1, t0, persons=[person], objects=[cup_stationary]))

    # 2. Cup begins moving near person
    cup_moving = ObjectObservation(
        track_id=10,
        class_name="cup",
        bbox=BoundingBox(x1=165, y1=175, x2=185, y2=205),
        center=CenterPoint(x=175, y=190),
        confidence=0.80,
        first_seen=t0,
        last_seen=t0 + 0.1,
        movement=MovementObservation(state="moving", speed_px_s=30.0),
    )
    events = event_engine.process_observation(make_observation(2, t0 + 0.1, persons=[person], objects=[cup_moving]))
    assert len(events) == 1
    assert events[0].event_type == EventType.OBJECT_MOVED.value
    assert events[0].object_id == "10"
    assert events[0].person_id == "1"  # Associated with nearby person
    assert events[0].metadata.get("relation") == "associated_with"


def test_storage_and_context_queries(temp_db, context_mgr, event_engine):
    t0 = 100.0
    person = PersonObservation(
        track_id=7,
        bbox=BoundingBox(x1=50, y1=50, x2=150, y2=250),
        center=CenterPoint(x=100, y=150),
        confidence=0.91,
        first_seen=t0,
        last_seen=t0,
        movement=MovementObservation(state="stationary", speed_px_s=0.0),
        action=ActionObservation(label="standing", confidence=0.91),
    )
    event_engine.process_observation(make_observation(1, t0, persons=[person]))

    # Test storage retrieval
    recent_db_events = temp_db.get_recent_events(limit=10)
    assert len(recent_db_events) == 1
    assert recent_db_events[0].person_id == "7"

    # Test track-specific retrieval
    track_events = temp_db.get_events_for_track(7, is_person=True)
    assert len(track_events) == 1

    # Test context manager retrieval
    person_ctx = context_mgr.get_person_context(7)
    assert person_ctx is not None
    assert person_ctx["track_id"] == 7
    assert person_ctx["current_action"] == "standing"
    assert len(person_ctx["event_history"]) == 1
