"""Pydantic schemas for ATLAS Real-Time Perception stage.

Standardizes detections, tracking IDs, movement metrics, human actions,
and holistic perception observations.
"""

from __future__ import annotations

import time
import uuid
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class BoundingBox(BaseModel):
    """Normalized or pixel coordinate bounding box."""
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def aspect_ratio(self) -> float:
        w = self.width
        return (self.height / w) if w > 0 else 0.0


class CenterPoint(BaseModel):
    """Center coordinate of an object/person bounding box."""
    x: float
    y: float


class DetectionItem(BaseModel):
    """Raw single-frame detection item prior to temporal tracking."""
    class_name: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    bbox: BoundingBox
    center: CenterPoint
    timestamp: str
    frame_id: int


class MovementObservation(BaseModel):
    """Temporal movement analysis metrics."""
    state: Literal["stationary", "moving", "unknown"] = "unknown"
    dx: float = 0.0
    dy: float = 0.0
    displacement: float = 0.0
    speed_px_s: float = 0.0


class ActionObservation(BaseModel):
    """Human activity / posture observation with explicit uncertainty."""
    label: Literal["standing", "walking", "sitting", "fall_like", "unknown"] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainty: str = "normal"


class PersonObservation(BaseModel):
    """Tracked person state and observed actions."""
    track_id: int
    class_name: str = "person"
    bbox: BoundingBox
    center: CenterPoint
    movement: MovementObservation
    action: ActionObservation
    confidence: float = Field(..., ge=0.0, le=1.0)
    first_seen: float
    last_seen: float


class ObjectObservation(BaseModel):
    """Tracked non-person object state and movement."""
    track_id: int
    class_name: str
    bbox: BoundingBox
    center: CenterPoint
    movement: MovementObservation
    confidence: float = Field(..., ge=0.0, le=1.0)
    first_seen: float
    last_seen: float


class PerceptionObservation(BaseModel):
    """Root perception observation container for ATLAS Home."""
    model_config = ConfigDict(validate_assignment=True)

    observation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str
    epoch_timestamp: float = Field(default_factory=time.time)
    source: str = "integrated_webcam"
    frame_id: int
    fps: float = 0.0
    resolution: tuple[int, int] = (640, 480)
    persons: list[PersonObservation] = Field(default_factory=list)
    objects: list[ObjectObservation] = Field(default_factory=list)
