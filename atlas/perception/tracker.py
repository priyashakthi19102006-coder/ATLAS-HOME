"""Multi-Object Tracking (ByteTrack) for ATLAS Perception.

Maintains temporary track IDs across frames without identity/facial recognition.
Tracks spatial continuity, velocity, and lifetime of detected entities.
"""

from __future__ import annotations

from collections import deque
import logging
import time
from typing import Sequence
import numpy as np

from atlas.perception.actions import TrackHistoryItem
from atlas.perception.detector import SUPPORTED_OBJECT_CLASSES
from atlas.perception.movement import MovementAnalyzer
from atlas.perception.schema import (
    BoundingBox,
    CenterPoint,
    MovementObservation,
)

logger = logging.getLogger("atlas.perception.tracker")


class TrackedEntity:
    """Internal state maintained for an actively tracked entity."""

    def __init__(
        self,
        track_id: int,
        class_name: str,
        bbox: BoundingBox,
        center: CenterPoint,
        confidence: float,
        timestamp: float,
        history_maxlen: int = 30,
    ) -> None:
        self.track_id = track_id
        self.class_name = class_name
        self.bbox = bbox
        self.center = center
        self.prev_center: CenterPoint | None = None
        self.prev_timestamp: float | None = None
        self.confidence = confidence
        self.first_seen = timestamp
        self.last_seen = timestamp
        self.visibility_state: str = "active"
        self.movement = MovementObservation(state="unknown")
        self.history: deque[TrackHistoryItem] = deque(maxlen=history_maxlen)

    def update(
        self,
        bbox: BoundingBox,
        center: CenterPoint,
        confidence: float,
        timestamp: float,
        movement_analyzer: MovementAnalyzer,
    ) -> None:
        """Update tracked entity with new frame detection."""
        self.prev_center = self.center
        self.prev_timestamp = self.last_seen
        self.bbox = bbox
        self.center = center
        self.confidence = confidence
        self.last_seen = timestamp
        self.visibility_state = "active"

        # Calculate movement using real spatial displacements
        self.movement = movement_analyzer.analyze(
            curr_center=self.center,
            curr_timestamp=timestamp,
            prev_center=self.prev_center,
            prev_timestamp=self.prev_timestamp,
        )

        # Append to bounded temporal history
        self.history.append(
            TrackHistoryItem(
                timestamp=timestamp,
                bbox=self.bbox,
                center=self.center,
                movement=self.movement,
            )
        )


class MultiObjectTracker:
    """Multi-object tracker wrapping ByteTrack with temporal entity state."""

    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        confidence_threshold: float = 0.35,
        target_classes: Sequence[str] | None = None,
        tracker_config: str = "bytetrack.yaml",
        track_timeout_seconds: float = 2.0,
        history_maxlen: int = 30,
    ) -> None:
        self.model_name = model_name
        self.confidence_threshold = confidence_threshold
        self.target_classes = set(target_classes) if target_classes else SUPPORTED_OBJECT_CLASSES
        self.tracker_config = tracker_config
        self.track_timeout_seconds = track_timeout_seconds
        self.history_maxlen = history_maxlen

        self.movement_analyzer = MovementAnalyzer()
        self._tracks: dict[int, TrackedEntity] = {}
        self._model = None
        self._initialized = False

    def load_model(self) -> None:
        if self._initialized:
            return
        from ultralytics import YOLO
        logger.info(f"[tracker] Initializing YOLO ByteTrack tracker with {self.model_name}...")
        self._model = YOLO(self.model_name)
        self._initialized = True

    def track(self, frame: np.ndarray, timestamp: float | None = None) -> list[TrackedEntity]:
        """Process real frame through YOLO ByteTrack and update entity tracks."""
        if not self._initialized:
            self.load_model()

        if frame is None or frame.size == 0:
            return []

        now = timestamp if timestamp is not None else time.time()

        # Execute ByteTrack tracking
        results = self._model.track(
            source=frame,
            persist=True,
            tracker=self.tracker_config,
            conf=self.confidence_threshold,
            verbose=False,
        )

        active_track_ids: set[int] = set()

        if results and len(results) > 0:
            result = results[0]
            boxes = result.boxes
            names = self._model.names

            if boxes is not None and len(boxes) > 0:
                for box in boxes:
                    cls_id = int(box.cls[0].item())
                    class_name = names.get(cls_id, str(cls_id)).lower()

                    if self.target_classes and class_name not in self.target_classes:
                        continue

                    # Retrieve tracker assigned ID
                    if box.id is not None:
                        track_id = int(box.id[0].item())
                    else:
                        # Unconfirmed track without ID yet
                        continue

                    active_track_ids.add(track_id)
                    conf = float(box.conf[0].item())
                    xyxy = box.xyxy[0].tolist()
                    x1, y1, x2, y2 = float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])
                    cx = (x1 + x2) / 2.0
                    cy = (y1 + y2) / 2.0

                    bbox = BoundingBox(x1=round(x1, 2), y1=round(y1, 2), x2=round(x2, 2), y2=round(y2, 2))
                    center = CenterPoint(x=round(cx, 2), y=round(cy, 2))

                    if track_id in self._tracks:
                        entity = self._tracks[track_id]
                        entity.update(
                            bbox=bbox,
                            center=center,
                            confidence=round(conf, 4),
                            timestamp=now,
                            movement_analyzer=self.movement_analyzer,
                        )
                    else:
                        entity = TrackedEntity(
                            track_id=track_id,
                            class_name=class_name,
                            bbox=bbox,
                            center=center,
                            confidence=round(conf, 4),
                            timestamp=now,
                            history_maxlen=self.history_maxlen,
                        )
                        self._tracks[track_id] = entity

        # Clean up tracks that have expired or disappeared
        expired_ids = []
        for tid, entity in self._tracks.items():
            if tid not in active_track_ids:
                entity.visibility_state = "lost"
                if now - entity.last_seen > self.track_timeout_seconds:
                    expired_ids.append(tid)

        for tid in expired_ids:
            del self._tracks[tid]

        # Return list of currently active tracked entities
        return [self._tracks[tid] for tid in active_track_ids if tid in self._tracks]
