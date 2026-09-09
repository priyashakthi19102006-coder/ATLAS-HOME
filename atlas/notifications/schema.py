"""Data models and schemas for ATLAS Home Notifications & Escalation subsystem (Step 7).

LOCKED PRINCIPLES:
- Notifications NEVER make safety decisions.
- LLM output cannot directly dispatch notifications.
- The subsystem strictly consumes already-verified Incident & Risk state.
- SYSTEM_ACTOR cannot be a notification recipient.
- External mobile/push providers report NOT_CONFIGURED honestly unless configured.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import uuid
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from atlas.authority.models import Role


class NotificationChannel(str, Enum):
    """Supported delivery channels."""
    IN_APP = "IN_APP"
    MOBILE_PUSH = "MOBILE_PUSH"
    WEB_PUSH = "WEB_PUSH"
    EMAIL = "EMAIL"


class NotificationStatus(str, Enum):
    """Lifecycle states of a notification."""
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class ProviderState(str, Enum):
    """Availability state of a notification delivery provider."""
    ACTIVE = "ACTIVE"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNAVAILABLE = "UNAVAILABLE"
    DEGRADED = "DEGRADED"


class EscalationStage(str, Enum):
    """Discrete levels of response escalation."""
    LEVEL_1 = "LEVEL_1"
    LEVEL_2 = "LEVEL_2"
    LEVEL_3 = "LEVEL_3"
    MAX_EXCEEDED = "MAX_EXCEEDED"
    TERMINATED = "TERMINATED"


class ProviderResult(BaseModel):
    """Result returned by a notification provider."""
    model_config = ConfigDict(validate_assignment=True)

    success: bool
    channel: NotificationChannel
    status: NotificationStatus
    provider_state: ProviderState
    delivered_at: Optional[str] = None
    failure_reason: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)


class Notification(BaseModel):
    """Persistent notification record representing an auditable dispatch attempt."""
    model_config = ConfigDict(validate_assignment=True)

    notification_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    incident_id: str = Field(..., description="Foreign reference to the triggering Incident.")
    recipient_user_id: str = Field(..., description="Authenticated human actor ID / username.")
    recipient_role: Role = Field(..., description="Role of the recipient (ADMIN, OPERATOR, VIEWER).")
    severity: str = Field(..., description="Severity inherited from Incident (LOW, MEDIUM, HIGH, CRITICAL).")
    channel: NotificationChannel = Field(default=NotificationChannel.IN_APP)
    status: NotificationStatus = Field(default=NotificationStatus.PENDING)

    # Privacy-safe minimal content
    title: str = Field(..., description="Short, privacy-safe title.")
    summary: str = Field(..., description="Minimal privacy-safe summary without biometric/raw frame details.")

    escalation_level: int = Field(default=1, ge=1)
    attempt_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    failure_reason: Optional[str] = None

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sent_at: Optional[str] = None
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[str] = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class EscalationStateRecord(BaseModel):
    """Current progression of an incident through the escalation state machine."""
    model_config = ConfigDict(validate_assignment=True)

    incident_id: str
    severity: str
    current_level: int = 1
    is_active: bool = True
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_escalated_at: Optional[str] = None
    next_escalation_epoch: Optional[float] = None
    stopped_reason: Optional[str] = None
    notifications_sent_count: int = 0
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[str] = None
