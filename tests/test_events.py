"""Tests for ATLAS Standardized Event Schema."""

import pytest
from pydantic import ValidationError

from atlas.events.schema import ATLASEvent, DeviceSource, EventType
from atlas.events.validator import create_event, validate_event


def test_valid_event_creation():
    event = create_event(
        event_type="person_detected",
        source_device=DeviceSource.ATLAS_HOME.value,
        location="living_room",
        person_id="person_01",
        action="standing",
        confidence=0.92,
        evidence={"frame_timestamp": 1726000000.0, "bbox": [10, 20, 100, 200]},
        metadata={"camera_id": "cam_front"},
    )
    assert event.event_id is not None
    assert event.event_type == "person_detected"
    assert event.source_device == "atlas_home"
    assert event.confidence == 0.92
    assert event.location == "living_room"
    assert event.evidence["bbox"] == [10, 20, 100, 200]


def test_cross_device_sources():
    # Verify events can originate from ATLAS Vision (glasses) or ATLAS Air (drone)
    vision_event = create_event(
        event_type="object_detected",
        source_device=DeviceSource.ATLAS_VISION.value,
        object_id="keys_01",
    )
    assert vision_event.source_device == "atlas_vision"

    air_event = create_event(
        event_type="zone_entry",
        source_device=DeviceSource.ATLAS_AIR.value,
        location="perimeter_north",
    )
    assert air_event.source_device == "atlas_air"


def test_invalid_confidence_range():
    with pytest.raises(ValidationError):
        ATLASEvent(
            event_type="test_event",
            confidence=1.5,  # Exceeds maximum 1.0
        )

    with pytest.raises(ValidationError):
        ATLASEvent(
            event_type="test_event",
            confidence=-0.1,  # Below minimum 0.0
        )


def test_validate_event_helper():
    valid_payload = {
        "event_type": "motion_detected",
        "source_device": "atlas_home",
        "confidence": 0.85,
    }
    is_valid, event, err = validate_event(valid_payload)
    assert is_valid is True
    assert event is not None
    assert err is None
    assert event.event_type == "motion_detected"

    invalid_payload = {
        "confidence": 0.85,
        # missing required event_type
    }
    is_valid, event, err = validate_event(invalid_payload)
    assert is_valid is False
    assert event is None
    assert err is not None
