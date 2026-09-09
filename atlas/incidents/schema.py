"""Schemas for ATLAS Incident Model and Lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class IncidentType(str, Enum):
    """Conservative, non-speculative incident type classifications."""
    POSSIBLE_FALL = "POSSIBLE_FALL"
    POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL = "POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL"
    UNEXPECTED_PERSON_PRESENCE = "UNEXPECTED_PERSON_PRESENCE"
    POSSIBLE_HIGH_RISK_INTERACTION = "POSSIBLE_HIGH_RISK_INTERACTION"
    GENERAL_SAFETY_REVIEW = "GENERAL_SAFETY_REVIEW"


class IncidentStatus(str, Enum):
    """Deterministic lifecycle states for an incident."""
    OBSERVED = "OBSERVED"
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class EscalationState(str, Enum):
    """Escalation authorization states distinct from incident lifecycle status."""
    NOT_ESCALATED = "NOT_ESCALATED"
    PENDING_AUTHORIZATION = "PENDING_AUTHORIZATION"
    AUTHORIZED = "AUTHORIZED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"


class ResolutionReason(str, Enum):
    """Explicit, controlled resolution classifications."""
    NORMAL_ACTIVITY = "NORMAL_ACTIVITY"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    USER_CONFIRMED_SAFE = "USER_CONFIRMED_SAFE"
    CONDITION_CLEARED = "CONDITION_CLEARED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    SYSTEM_TEST = "SYSTEM_TEST"


class Incident(BaseModel):
    """Persistent Incident Entity representing a verified safety situation."""
    model_config = ConfigDict(validate_assignment=True)

    incident_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    incident_type: IncidentType
    severity: str = "LOW"
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainty: Literal["low", "moderate", "high"] = "moderate"
    status: IncidentStatus = IncidentStatus.ACTIVE

    # Traceability links
    source_event_ids: list[str] = Field(default_factory=list)
    analysis_id: Optional[str] = None
    risk_id: Optional[str] = None
    triggering_rule_ids: list[str] = Field(default_factory=list)

    # Acknowledgement state
    is_acknowledged: bool = False
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[str] = None

    # Escalation state
    escalation_state: EscalationState = EscalationState.NOT_ESCALATED
    escalated_at: Optional[str] = None
    escalated_by: Optional[str] = None

    # Assignment & Resolution
    assigned_actor_id: Optional[str] = None
    is_resolved: bool = False
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution_reason: Optional[ResolutionReason] = None
    resolution_notes: Optional[str] = None

    metadata: dict[str, Any] = Field(default_factory=dict)
