"""Schemas for ATLAS Immutable Audit Trail."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class AuditAction(str, Enum):
    """Audit action identifiers."""
    INCIDENT_CREATED = "INCIDENT_CREATED"
    INCIDENT_UPDATED = "INCIDENT_UPDATED"
    INCIDENT_ACKNOWLEDGED = "INCIDENT_ACKNOWLEDGED"
    INCIDENT_ESCALATED = "INCIDENT_ESCALATED"
    INCIDENT_RESOLVED = "INCIDENT_RESOLVED"
    INCIDENT_DISMISSED = "INCIDENT_DISMISSED"
    AUTHORIZATION_REQUESTED = "AUTHORIZATION_REQUESTED"
    AUTHORIZATION_GRANTED = "AUTHORIZATION_GRANTED"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    ALERT_GENERATED = "ALERT_GENERATED"
    ALERT_UPDATED = "ALERT_UPDATED"
    EVIDENCE_CAPTURED = "EVIDENCE_CAPTURED"
    EVIDENCE_CAPTURE_FAILED = "EVIDENCE_CAPTURE_FAILED"
    NOTIFICATION_CREATED = "NOTIFICATION_CREATED"
    NOTIFICATION_ATTEMPTED = "NOTIFICATION_ATTEMPTED"
    NOTIFICATION_SENT = "NOTIFICATION_SENT"
    NOTIFICATION_DELIVERED = "NOTIFICATION_DELIVERED"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    NOTIFICATION_ACKNOWLEDGED = "NOTIFICATION_ACKNOWLEDGED"
    ESCALATION_STARTED = "ESCALATION_STARTED"
    ESCALATION_ADVANCED = "ESCALATION_ADVANCED"
    ESCALATION_STOPPED = "ESCALATION_STOPPED"
    ESCALATION_CANCELLED = "ESCALATION_CANCELLED"
    NOTIFICATION_PROVIDER_UNAVAILABLE = "NOTIFICATION_PROVIDER_UNAVAILABLE"
    CAMERA_ENABLED = "CAMERA_ENABLED"
    CAMERA_DISABLED = "CAMERA_DISABLED"


class AuditRecord(BaseModel):
    """Append-only audit record tracking state mutations and authorized actions."""
    model_config = ConfigDict(validate_assignment=True)

    audit_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    actor_id: str = Field(..., description="ID of the actor performing or triggering the action.")
    action: AuditAction = Field(..., description="Specific audit action performed.")
    target_type: str = Field(..., description="Type of resource mutated (e.g. 'incident', 'alert').")
    target_id: str = Field(..., description="ID of the resource mutated.")
    previous_state: Optional[str] = None
    new_state: Optional[str] = None
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
