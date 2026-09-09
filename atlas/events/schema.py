"""Standardized ATLAS cross-device event schema.

Shared across ATLAS Home, ATLAS Vision (glasses), and ATLAS Air (drone)
to provide structured evidence to ATLAS Core.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class DeviceSource(str, Enum):
    """Originating device types across the ATLAS ecosystem."""
    ATLAS_HOME = "atlas_home"
    ATLAS_VISION = "atlas_vision"
    ATLAS_AIR = "atlas_air"
    SYSTEM = "atlas_system"


class EventStatus(str, Enum):
    """Lifecycle states of an event."""
    OBSERVED = "OBSERVED"
    ACTIVE = "ACTIVE"
    UPDATED = "UPDATED"
    RESOLVED = "RESOLVED"


class EventSeverity(str, Enum):
    """Observation severity levels.
    
    IMPORTANT ARCHITECTURAL DISTINCTION:
    Event observation severity != final ATLAS safety risk score.
    Severity indicates raw perceptual novelty / urgency; the downstream
    Deterministic Rules and Risk Engine calculate actual incident risk.
    """
    INFO = "INFO"
    NOTICE = "NOTICE"
    WARNING = "WARNING"


class EventType(str, Enum):
    """Standardized event category identifiers."""
    # Person tracking & activity events
    PERSON_DETECTED = "PERSON_DETECTED"
    PERSON_ENTERED = "PERSON_ENTERED"
    PERSON_LEFT = "PERSON_LEFT"
    PERSON_MOVING = "PERSON_MOVING"
    PERSON_STATIONARY = "PERSON_STATIONARY"
    PERSON_SITTING = "PERSON_SITTING"
    PERSON_STANDING = "PERSON_STANDING"
    PERSON_WALKING = "PERSON_WALKING"
    PERSON_FALL_LIKE = "PERSON_FALL_LIKE"

    # Object tracking events
    OBJECT_DETECTED = "OBJECT_DETECTED"
    OBJECT_MOVED = "OBJECT_MOVED"
    OBJECT_STATIONARY = "OBJECT_STATIONARY"
    OBJECT_ENTERED = "OBJECT_ENTERED"
    OBJECT_LEFT = "OBJECT_LEFT"

    # Hardware & System events
    CAMERA_STATUS = "CAMERA_STATUS"
    PERCEPTION_STATUS = "PERCEPTION_STATUS"
    CAMERA_CONNECTED = "camera_connected"
    CAMERA_DISCONNECTED = "camera_disconnected"
    SYSTEM_DIAGNOSTIC = "system_diagnostic"
    CUSTOM = "custom"


class EvidencePayload(BaseModel):
    """Structured visual/sensor evidence attached to an event."""
    model_config = ConfigDict(extra="allow")

    source: Optional[str] = "integrated_webcam"
    frame_id: Optional[int] = None
    frame_timestamp: Optional[float] = None
    track_id: Optional[int] = None
    detection_confidence: Optional[float] = None
    action: Optional[str] = None
    action_confidence: Optional[float] = None
    movement: Optional[dict[str, Any]] = None
    bbox: Optional[list[float]] = None
    snapshot_uri: Optional[str] = None
    details: Optional[dict[str, Any]] = None


class ATLASEvent(BaseModel):
    """Standardized ATLAS Event Model.
    
    All evidence generated across ATLAS devices conforms to this schema
    before flowing into Context Memory, LLM Verification, and Deterministic Rules.
    """
    model_config = ConfigDict(validate_assignment=True, extra="allow")

    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Globally unique identifier for the event",
    )
    event_type: str = Field(
        ...,
        description="Standardized event category identifier",
    )
    source: str = Field(
        default="integrated_webcam",
        description="Specific source or hardware interface",
    )
    source_device: str = Field(
        default=DeviceSource.ATLAS_HOME.value,
        description="Originating device ecosystem: atlas_home, atlas_vision, or atlas_air",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 UTC timestamp when the event was recorded",
    )
    status: str = Field(
        default=EventStatus.ACTIVE.value,
        description="Lifecycle state: OBSERVED, ACTIVE, UPDATED, or RESOLVED",
    )
    severity: str = Field(
        default=EventSeverity.INFO.value,
        description="Observation severity: INFO, NOTICE, WARNING (NOT final safety risk)",
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Detection or perception confidence score between 0.0 and 1.0",
    )
    person_id: Optional[str] = Field(
        default=None,
        description="Temporary track ID from Step 2 tracker (NOT a real-world identity)",
    )
    object_id: Optional[str] = Field(
        default=None,
        description="Temporary object track ID from Step 2 tracker",
    )
    action: Optional[str] = Field(
        default=None,
        description="Observed action or posture",
    )
    location: Optional[str] = Field(
        default=None,
        description="Logical or spatial location descriptor",
    )
    evidence: Optional[dict[str, Any]] = Field(
        default=None,
        description="Structured perceptual evidence payload",
    )
    metadata: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="Device or relationship metadata",
    )
