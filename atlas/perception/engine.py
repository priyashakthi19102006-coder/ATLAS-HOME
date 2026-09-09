"""Perception Engine coordinating real-time detection, tracking, movement, and actions.

Pipeline:
Real Camera Frame
      ↓
Detector / Multi-Object Tracker (ByteTrack)
      ↓
Temporal Movement Analysis
      ↓
Human Action / Posture Observation
      ↓
Structured Perception Observation (Pydantic)
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
import time
from typing import Sequence
import numpy as np

from atlas.perception.actions import HumanActionObserver
from atlas.perception.schema import (
    ActionObservation,
    ObjectObservation,
    PerceptionObservation,
    PersonObservation,
)
from atlas.perception.tracker import MultiObjectTracker

logger = logging.getLogger("atlas.perception.engine")


class PerceptionEngine:
    """Core Perception Engine processing real frames into structured observations."""

    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        confidence_threshold: float = 0.35,
        target_classes: Sequence[str] | None = None,
        tracker_config: str = "bytetrack.yaml",
        history_maxlen: int = 30,
    ) -> None:
        self.model_name = model_name
        self.tracker = MultiObjectTracker(
            model_name=model_name,
            confidence_threshold=confidence_threshold,
            target_classes=target_classes,
            tracker_config=tracker_config,
            history_maxlen=history_maxlen,
        )
        self.action_observer = HumanActionObserver()

        self._lock = threading.Lock()
        self._latest_observation: PerceptionObservation | None = None
        self._frame_counter: int = 0
        self._fps: float = 0.0
        self._last_fps_calc_time = time.perf_counter()
        self._fps_counter: int = 0

    def process_frame(
        self,
        frame: np.ndarray,
        timestamp: float | None = None,
        source_name: str = "integrated_webcam",
    ) -> PerceptionObservation:
        """Process a real camera frame and return structured perception observations."""
        if frame is None or frame.size == 0:
            raise ValueError("Cannot process empty or None frame (zero-mock policy violated)")

        now = timestamp if timestamp is not None else time.time()
        self._frame_counter += 1
        frame_id = self._frame_counter

        # Calculate processing FPS
        self._fps_counter += 1
        t_perf = time.perf_counter()
        elapsed = t_perf - self._last_fps_calc_time
        if elapsed >= 1.0:
            self._fps = round(self._fps_counter / elapsed, 2)
            self._fps_counter = 0
            self._last_fps_calc_time = t_perf

        h, w = frame.shape[:2]

        # 1. Run ByteTrack multi-object tracking
        tracked_entities = self.tracker.track(frame=frame, timestamp=now)

        persons: list[PersonObservation] = []
        objects: list[ObjectObservation] = []

        # 2. Process tracked entities
        for entity in tracked_entities:
            if entity.class_name == "person":
                # Analyze human action / posture using temporal trajectory
                action = self.action_observer.observe(
                    curr_bbox=entity.bbox,
                    curr_movement=entity.movement,
                    history=entity.history,
                )

                person_obs = PersonObservation(
                    track_id=entity.track_id,
                    class_name="person",
                    bbox=entity.bbox,
                    center=entity.center,
                    movement=entity.movement,
                    action=action,
                    confidence=entity.confidence,
                    first_seen=entity.first_seen,
                    last_seen=entity.last_seen,
                )
                persons.append(person_obs)
            else:
                obj_obs = ObjectObservation(
                    track_id=entity.track_id,
                    class_name=entity.class_name,
                    bbox=entity.bbox,
                    center=entity.center,
                    movement=entity.movement,
                    confidence=entity.confidence,
                    first_seen=entity.first_seen,
                    last_seen=entity.last_seen,
                )
                objects.append(obj_obs)

        # 3. Construct root PerceptionObservation
        observation = PerceptionObservation(
            timestamp=datetime.now(timezone.utc).isoformat(),
            epoch_timestamp=now,
            source=source_name,
            frame_id=frame_id,
            fps=self._fps,
            resolution=(w, h),
            persons=persons,
            objects=objects,
        )

        with self._lock:
            self._latest_observation = observation

        return observation

    def get_latest_observation(self) -> PerceptionObservation | None:
        """Thread-safe getter for latest perception observation."""
        with self._lock:
            return self._latest_observation

    @property
    def fps(self) -> float:
        with self._lock:
            return self._fps


_global_perception_engine: PerceptionEngine | None = None


def get_perception_engine(model_name: str = "yolov8n.pt") -> PerceptionEngine:
    """Get or initialize singleton PerceptionEngine."""
    global _global_perception_engine
    if _global_perception_engine is None:
        _global_perception_engine = PerceptionEngine(model_name=model_name)
    return _global_perception_engine
