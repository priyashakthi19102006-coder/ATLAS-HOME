"""Schema definitions for ATLAS Multi-Source Context & Evidence Fusion (Step 6.5).

Enforces strict domain separation between:
1. SOURCE (Physical / heterogeneous subsystems)
2. OBSERVATION (Normalized perceptual measurements)
3. CORRELATION (Temporal and entity linkages)
4. CONFIDENCE & UNCERTAINTY (Probabilistic bounds)
5. AVAILABILITY (Subsystem hardware/connection state)
6. EVIDENCE RELATIONSHIP (Agreement / Contradiction)
7. FUSED SITUATION (Aggregated contextual situation)

CORE PRINCIPLE: DEVICE != DECISION.
Devices observe; ATLAS correlates, fuses, and passes to deterministic rules & risk.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, Enum):
    """Supported sensing subsystem source categories."""
    CAMERA = "CAMERA"
    IMU = "IMU"
    GPS = "GPS"
    ENVIRONMENTAL_SENSOR = "ENVIRONMENTAL_SENSOR"
    WEARABLE = "WEARABLE"
    GLASSES = "GLASSES"
    DRONE = "DRONE"
    ROBOT = "ROBOT"
    OTHER = "OTHER"


class SourceAvailability(str, Enum):
    """Runtime availability states for sensing sources."""
    AVAILABLE = "AVAILABLE"            # Connected, streaming verified live data
    DEGRADED = "DEGRADED"              # Connected, but experiencing jitter/dropped frames
    DISCONNECTED = "DISCONNECTED"      # Configured hardware but currently lost signal
    UNAVAILABLE = "UNAVAILABLE"        # Hardware driver present but not operational
    NOT_CONFIGURED = "NOT_CONFIGURED"  # Architecturally supported, but no physical device present
    INVALID = "INVALID"                # Malformed or rejected source metadata


class EvidenceRelationship(str, Enum):
    """Deterministic evidence relationship classification across sources."""
    AGREES = "AGREES"                  # Multi-source observations corroborate the same state
    CONTRADICTS = "CONTRADICTS"        # Multi-source observations conflict (e.g. still vs high motion)
    INDEPENDENT = "INDEPENDENT"        # Multi-source observations describe orthogonal phenomena
    INSUFFICIENT = "INSUFFICIENT"      # Single source or insufficient data to evaluate corroboration
    UNKNOWN = "UNKNOWN"                # Relationship cannot be reliably determined


class EntityAssociationStatus(str, Enum):
    """Status of entity cross-source correlation."""
    ASSOCIATED = "ASSOCIATED"          # Validated cross-sensor association established
    INDEPENDENT = "INDEPENDENT"        # Verified different entities
    UNKNOWN = "UNKNOWN"                # Cross-source linkage unverified (default)


class Observation(BaseModel):
    """Normalized, source-neutral observation representation.
    
    All sensory inputs are adapted into this uniform model before entering
    temporal and entity fusion. Arbitrary untyped inputs are strictly rejected.
    """
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    observation_id: str = Field(
        default_factory=lambda: f"obs_{uuid.uuid4().hex[:12]}",
        description="Globally unique observation identifier.",
    )
    source_type: SourceType = Field(
        ...,
        description="Subsystem category providing this observation.",
    )
    source_id: str = Field(
        ...,
        description="Hardware interface or unique device identifier (e.g., 'camera_0').",
    )
    observed_at: str = Field(
        ...,
        description="ISO-8601 UTC timestamp of original hardware observation. Never rewritten.",
    )
    received_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 UTC timestamp when ATLAS ingested the observation.",
    )
    observation_type: str = Field(
        ...,
        description="Standardized observation classification (e.g., 'PERSON_MOTION', 'ACCELERATION_SPIKE').",
    )
    value: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured payload containing domain values (e.g., coordinates, vectors, velocities).",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Perception or sensor confidence score.",
    )
    uncertainty: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Measurement or model uncertainty estimate.",
    )
    availability: SourceAvailability = Field(
        default=SourceAvailability.AVAILABLE,
        description="Availability status of the originating source at observation time.",
    )
    event_id: Optional[str] = Field(
        default=None,
        description="Correlated ATLASEvent identifier if adapted from perception event.",
    )
    track_id: Optional[int] = Field(
        default=None,
        description="ByteTrack numeric tracking ID. NEVER converted to a human identity.",
    )
    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description="Causal lineage and device metadata.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Supplementary contextual metadata.",
    )


class TemporalCorrelation(BaseModel):
    """Explicit temporal relation between observations."""
    model_config = ConfigDict(extra="forbid")

    source_timestamp: str = Field(..., description="Timestamp of originating observation.")
    received_timestamp: str = Field(..., description="Timestamp when observation was received.")
    correlation_timestamp: str = Field(..., description="Timestamp when fusion evaluated correlation.")
    temporal_delta_seconds: float = Field(..., description="Delta in seconds between observation and reference.")
    is_within_window: bool = Field(..., description="Whether observation falls strictly inside correlation window.")
    source_provenance: dict[str, Any] = Field(default_factory=dict)


class EntityCorrelation(BaseModel):
    """Conservative entity correlation across sources."""
    model_config = ConfigDict(extra="forbid")

    track_id: Optional[int] = Field(None, description="ByteTrack integer track identifier.")
    source_id: str = Field(..., description="Originating sensor identifier.")
    association_status: EntityAssociationStatus = Field(
        default=EntityAssociationStatus.UNKNOWN,
        description="Association state. Strictly UNKNOWN unless an explicit, validated association exists.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    details: dict[str, Any] = Field(default_factory=dict)


class FusedSituation(BaseModel):
    """Aggregated, multi-source contextual situation object.
    
    Provides an explicit, explainable answer to:
    'WHY WERE THESE OBSERVATIONS CONSIDERED TOGETHER?'
    """
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    fusion_id: str = Field(
        default_factory=lambda: f"fus_{uuid.uuid4().hex[:12]}",
        description="Globally unique fusion snapshot identifier.",
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 UTC timestamp when fusion was evaluated.",
    )
    observation_ids: list[str] = Field(
        default_factory=list,
        description="List of observation identifiers incorporated in this situation.",
    )
    source_types: list[SourceType] = Field(
        default_factory=list,
        description="Unique source subsystem types providing evidence.",
    )
    correlated_entities: list[EntityCorrelation] = Field(
        default_factory=list,
        description="Correlated entity references with explicit association statuses.",
    )
    temporal_relationships: list[TemporalCorrelation] = Field(
        default_factory=list,
        description="Chronological alignments and deltas for member observations.",
    )
    supporting_evidence: list[str] = Field(
        default_factory=list,
        description="Observations corroborating the primary situational hypothesis.",
    )
    contradictory_evidence: list[str] = Field(
        default_factory=list,
        description="Observations conflicting with or qualifying the hypothesis.",
    )
    source_availability: dict[str, SourceAvailability] = Field(
        default_factory=dict,
        description="Snapshot of source availability across all subsystems at fusion time.",
    )
    aggregate_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Synthesized confidence score across all contributing observations.",
    )
    uncertainty: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Aggregated uncertainty estimate.",
    )
    relationship_state: EvidenceRelationship = Field(
        default=EvidenceRelationship.INSUFFICIENT,
        description="Evidence relationship: AGREES, CONTRADICTS, INDEPENDENT, INSUFFICIENT, or UNKNOWN.",
    )
    completeness: str = Field(
        default="SINGLE_SOURCE",
        description="Contextual completeness: 'COMPLETE', 'PARTIAL', or 'SINGLE_SOURCE'.",
    )
    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description="Full causal audit trail of fusion evaluation.",
    )
    situation_summary: str = Field(
        ...,
        description="Deterministic, human-readable summary of the fused situation.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Supplementary contextual metadata.",
    )
