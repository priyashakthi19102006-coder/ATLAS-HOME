"""Domain models and schemas for ATLAS Home Physical Companion."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class CompanionState(str, Enum):
    """Observable states and affective expressions of the physical ATLAS companion."""
    IDLE = "Idle"
    LISTENING = "Listening"
    THINKING = "Thinking"
    SPEAKING = "Speaking"
    HAPPY = "Happy"
    SAD = "Sad"
    SURPRISED = "Surprised"
    CONFUSED = "Confused"


class CompanionActivity(BaseModel):
    """Log record of physical robot interaction or serial communication event."""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    direction: str = Field(description="IN (from ESP32) or OUT (to ESP32) or SYS (internal event)")
    event_type: str = Field(description="STATE_CHANGE, SPEAK, TEST, AUDIO_FRAME, STATUS, ERROR")
    payload: str = Field(description="Human readable summary or serialized payload")
    state: str = Field(description="Current companion state at time of event")


class CompanionStatus(BaseModel):
    """Real-time physical hardware connection and operational metrics."""
    connected: bool = False
    port: str = "COM5"
    baudrate: int = 921600
    state: CompanionState = CompanionState.IDLE
    bytes_sent: int = 0
    bytes_received: int = 0
    packets_sent: int = 0
    packets_received: int = 0
    is_speaking: bool = False
    audio_bytes_sent: int = 0
    voice_packets_sent: int = 0
    last_seen_iso: Optional[str] = None
    last_error: Optional[str] = None
    firmware_banner: Optional[str] = None
    active_task: Optional[str] = None
    recent_activities: list[CompanionActivity] = Field(default_factory=list)
