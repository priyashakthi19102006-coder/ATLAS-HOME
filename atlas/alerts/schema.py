"""Schemas for ATLAS Internal Alert abstraction."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class AlertStatus(str, Enum):
    """Internal lifecycle states of an Alert."""
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DISMISSED = "DISMISSED"
    RESOLVED = "RESOLVED"


class AlertSeverity(str, Enum):
    """Alert severity levels."""
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Alert(BaseModel):
    """Internal Alert representation associated with an active Incident."""
    model_config = ConfigDict(validate_assignment=True)

    alert_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    incident_id: str = Field(..., description="Foreign reference to associated Incident.")
    severity: AlertSeverity = AlertSeverity.LOW
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    status: AlertStatus = AlertStatus.ACTIVE
    is_acknowledged: bool = False
    is_escalated: bool = False

    notification_count: int = 0
    last_notified_at: Optional[str] = None
    cooldown_seconds: float = 60.0
    cooldown_until: Optional[str] = None
