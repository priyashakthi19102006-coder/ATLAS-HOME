"""Unit and Mathematical Tests for ATLAS Perception Pipeline.

Tests:
1. BoundingBox geometry and aspect ratio calculation
2. CenterPoint calculation
3. MovementAnalyzer mathematical metrics (displacement, speed, jitter filtering)
4. HumanActionObserver posture and action rules (standing, walking, sitting, fall-like, unknown)
5. Structured PerceptionObservation schema validation and confidence constraints
6. TrackedEntity history bounds
"""

import pytest
from pydantic import ValidationError

from atlas.perception.actions import HumanActionObserver, TrackHistoryItem
from atlas.perception.movement import MovementAnalyzer
from atlas.perception.schema import (
    ActionObservation,
    BoundingBox,
    CenterPoint,
    DetectionItem,
    MovementObservation,
    ObjectObservation,
    PerceptionObservation,
    PersonObservation,
)
from atlas.perception.tracker import TrackedEntity


def test_bounding_box_geometry():
    bbox = BoundingBox(x1=100.0, y1=50.0, x2=200.0, y2=250.0)
    assert bbox.width == 100.0
    assert bbox.height == 200.0
    assert bbox.aspect_ratio == 2.0  # height / width = 200 / 100 = 2.0


def test_movement_analyzer_stationary():
    analyzer = MovementAnalyzer(jitter_threshold_px=6.0, speed_threshold_px_s=15.0)
    p1 = CenterPoint(x=100.0, y=100.0)
    p2 = CenterPoint(x=102.0, y=101.0)  # displacement ~2.23 px (< 6px jitter threshold)

    obs = analyzer.analyze(curr_center=p2, curr_timestamp=1.1, prev_center=p1, prev_timestamp=1.0)
    assert obs.state == "stationary"
    assert obs.dx == 2.0
    assert obs.dy == 1.0
    assert obs.displacement == pytest.approx(2.24, abs=0.1)


def test_movement_analyzer_moving():
    analyzer = MovementAnalyzer(jitter_threshold_px=6.0, speed_threshold_px_s=15.0)
    p1 = CenterPoint(x=100.0, y=100.0)
    p2 = CenterPoint(x=150.0, y=100.0)  # 50px displacement in 0.5s -> 100 px/s

    obs = analyzer.analyze(curr_center=p2, curr_timestamp=1.5, prev_center=p1, prev_timestamp=1.0)
    assert obs.state == "moving"
    assert obs.dx == 50.0
    assert obs.dy == 0.0
    assert obs.speed_px_s == pytest.approx(100.0, abs=1.0)


def test_action_observer_standing():
    observer = HumanActionObserver(standing_aspect_ratio_min=1.65)
    bbox = BoundingBox(x1=50.0, y1=20.0, x2=100.0, y2=150.0)  # w=50, h=130 -> ratio=2.6
    movement = MovementObservation(state="stationary", dx=1.0, dy=0.0, speed_px_s=2.0)

    action = observer.observe(curr_bbox=bbox, curr_movement=movement, history=[])
    assert action.label == "standing"
    assert action.confidence >= 0.80


def test_action_observer_walking():
    observer = HumanActionObserver(standing_aspect_ratio_min=1.65)
    bbox = BoundingBox(x1=50.0, y1=20.0, x2=100.0, y2=150.0)  # ratio=2.6 (upright)
    movement = MovementObservation(state="moving", dx=35.0, dy=2.0, speed_px_s=70.0)

    action = observer.observe(curr_bbox=bbox, curr_movement=movement, history=[])
    assert action.label == "walking"
    assert action.confidence >= 0.80


def test_action_observer_sitting():
    observer = HumanActionObserver()
    # Seated person has compact aspect ratio
    bbox = BoundingBox(x1=50.0, y1=50.0, x2=150.0, y2=160.0)  # w=100, h=110 -> ratio=1.1
    movement = MovementObservation(state="stationary", dx=0.0, dy=0.0, speed_px_s=0.0)

    action = observer.observe(curr_bbox=bbox, curr_movement=movement, history=[])
    assert action.label == "sitting"
    assert action.confidence >= 0.70


def test_action_observer_fall_like_motion():
    observer = HumanActionObserver()

    # Frame 1: Upright standing
    b1 = BoundingBox(x1=50.0, y1=20.0, x2=100.0, y2=160.0)  # ratio=2.8, bottom y2=160
    c1 = CenterPoint(x=75.0, y=90.0)
    m1 = MovementObservation(state="stationary")
    h1 = TrackHistoryItem(timestamp=1.0, bbox=b1, center=c1, movement=m1)

    # Frame 2: Midway down
    b2 = BoundingBox(x1=50.0, y1=80.0, x2=120.0, y2=200.0)
    c2 = CenterPoint(x=85.0, y=140.0)
    m2 = MovementObservation(state="moving", dy=50.0, speed_px_s=100.0)
    h2 = TrackHistoryItem(timestamp=1.2, bbox=b2, center=c2, movement=m2)

    # Current Frame: On the ground (collapsed aspect ratio, rapid drop)
    b3 = BoundingBox(x1=30.0, y1=150.0, x2=150.0, y2=230.0)  # w=120, h=80 -> ratio=0.67
    m3 = MovementObservation(state="moving", dy=30.0, speed_px_s=60.0)

    action = observer.observe(curr_bbox=b3, curr_movement=m3, history=[h1, h2])
    assert action.label == "fall_like"
    assert "rapid downward" in action.uncertainty


def test_schema_confidence_validation():
    # Valid observation
    bbox = BoundingBox(x1=0, y1=0, x2=10, y2=10)
    center = CenterPoint(x=5, y=5)
    item = DetectionItem(
        class_name="person",
        confidence=0.88,
        bbox=bbox,
        center=center,
        timestamp="2026-09-09T20:00:00Z",
        frame_id=1,
    )
    assert item.confidence == 0.88

    # Out-of-bounds confidence must raise ValidationError
    with pytest.raises(ValidationError):
        DetectionItem(
            class_name="person",
            confidence=1.5,
            bbox=bbox,
            center=center,
            timestamp="2026-09-09T20:00:00Z",
            frame_id=1,
        )


def test_tracked_entity_bounded_history():
    bbox = BoundingBox(x1=0, y1=0, x2=10, y2=10)
    center = CenterPoint(x=5, y=5)
    entity = TrackedEntity(
        track_id=1,
        class_name="person",
        bbox=bbox,
        center=center,
        confidence=0.9,
        timestamp=1.0,
        history_maxlen=5,
    )

    analyzer = MovementAnalyzer()
    for i in range(10):
        entity.update(
            bbox=bbox,
            center=CenterPoint(x=5 + i, y=5),
            confidence=0.9,
            timestamp=1.0 + (i + 1) * 0.1,
            movement_analyzer=analyzer,
        )

    # Must not exceed history_maxlen
    assert len(entity.history) == 5
