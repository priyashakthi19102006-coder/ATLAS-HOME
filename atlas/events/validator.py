"""Event creation and validation utilities for ATLAS."""

from __future__ import annotations

from typing import Any, Mapping
from pydantic import ValidationError

from atlas.events.schema import ATLASEvent, DeviceSource


def create_event(
    event_type: str,
    source_device: str = DeviceSource.ATLAS_HOME.value,
    location: str | None = None,
    person_id: str | None = None,
    object_id: str | None = None,
    action: str | None = None,
    confidence: float | None = None,
    evidence: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
) -> ATLASEvent:
    """Create a validated ATLASEvent instance."""
    kwargs: dict[str, Any] = {
        "event_type": event_type,
        "source_device": source_device,
        "location": location,
        "person_id": person_id,
        "object_id": object_id,
        "action": action,
        "confidence": confidence,
        "evidence": evidence,
        "metadata": metadata or {},
    }
    if event_id is not None:
        kwargs["event_id"] = event_id
    if timestamp is not None:
        kwargs["timestamp"] = timestamp

    return ATLASEvent(**kwargs)


def validate_event(data: Mapping[str, Any]) -> tuple[bool, ATLASEvent | None, str | None]:
    """Validate raw event data against the ATLASEvent schema.
    
    Returns (is_valid, event_instance, error_message).
    """
    try:
        event = ATLASEvent.model_validate(data)
        return True, event, None
    except ValidationError as err:
        return False, None, str(err)
    except Exception as exc:
        return False, None, str(exc)
