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

        # Identity tracking cache: {track_id: (user_id, display_name, confidence, is_known)}
        self._track_identities: dict[int, tuple[str, str, float, bool]] = {}
        self._enrolled_cache: list[tuple[str, str, list[float]]] = []
        self._enrolled_cache_time: float = 0.0

    def _get_enrolled_candidates(self) -> list[tuple[str, str, list[float]]]:
        """Fetch active enrolled candidates from UserManagementService, cached for 5s."""
        t_now = time.time()
        if (t_now - self._enrolled_cache_time) < 5.0 and self._enrolled_cache:
            return self._enrolled_cache

        try:
            from atlas.authority.user_service import get_user_management_service
            svc = get_user_management_service()
            users = svc.list_users()
            candidates = []
            for u in users:
                if u.is_active and u.enrolled_embedding:
                    candidates.append((u.user_id, u.display_name, u.enrolled_embedding))
            self._enrolled_cache = candidates
            self._enrolled_cache_time = t_now
            return candidates
        except Exception:
            return []

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

        from atlas.perception.attributes import (
            extract_clothing_attributes,
            compute_camera_region,
            determine_movement_direction,
            check_carried_objects,
            get_dominant_color_name,
        )

        # 1. Run ByteTrack multi-object tracking
        tracked_entities = self.tracker.track(frame=frame, timestamp=now)

        # Separate raw person and object tracks
        person_entities = [e for e in tracked_entities if e.class_name == "person"]
        object_entities = [e for e in tracked_entities if e.class_name != "person"]

        # Prepare object bounding box tuples for spatial association
        object_tuples = [(e.track_id, e.class_name, e.bbox) for e in object_entities]

        # Map to track object-to-person associations
        obj_to_person: dict[int, tuple[int, str]] = {}  # {obj_track_id: (person_track_id, person_name)}

        # 2. Process tracked persons
        enrolled_candidates = self._get_enrolled_candidates()
        persons: list[PersonObservation] = []

        for entity in person_entities:
            # Action observation
            action = self.action_observer.observe(
                curr_bbox=entity.bbox,
                curr_movement=entity.movement,
                history=entity.history,
            )

            # Visual attributes from real pixels
            clothing = extract_clothing_attributes(frame, entity.bbox)
            direction = determine_movement_direction(
                entity.movement.dx, entity.movement.dy, entity.movement.speed_px_s
            )
            region = compute_camera_region(entity.center, w, h)
            carried = check_carried_objects(entity.bbox, object_tuples)

            # Identity resolution (Real Biometrics or strictly Unknown Person)
            track_id = entity.track_id
            if track_id in self._track_identities:
                u_id, d_name, id_conf, is_known = self._track_identities[track_id]
            else:
                # Attempt live face identification from head crop
                u_id = "UNKNOWN"
                d_name = "Unknown Person"
                id_conf = 0.0
                is_known = False

                if enrolled_candidates:
                    try:
                        from atlas.authority.face import get_face_biometrics_engine
                        face_engine = get_face_biometrics_engine()
                        # Extract head region crop (top 30% of person box)
                        px1 = max(0, int(entity.bbox.x1))
                        py1 = max(0, int(entity.bbox.y1))
                        px2 = min(w, int(entity.bbox.x2))
                        py2 = min(h, int(entity.bbox.y1 + (entity.bbox.y2 - entity.bbox.y1) * 0.35))
                        head_crop = frame[py1:py2, px1:px2]
                        if head_crop.size > 0:
                            m_uid, m_name, m_sim, m_ok = face_engine.identify_face(
                                head_crop, enrolled_candidates, threshold=0.70
                            )
                            if m_ok:
                                u_id = m_uid
                                d_name = m_name
                                id_conf = m_sim
                                is_known = True
                    except Exception:
                        pass

                # Cache track identity for stability
                self._track_identities[track_id] = (u_id, d_name, id_conf, is_known)

            # Record associations
            for item in carried:
                obj_to_person[item["track_id"]] = (track_id, d_name)

            visual_attr = {
                "upper_clothing_color": clothing["upper_clothing_color"],
                "lower_clothing_color": clothing["lower_clothing_color"],
                "movement_direction": direction,
                "camera_region": region,
                "carried_objects": [it["class_name"] for it in carried],
                "clarity": clothing.get("clarity", "verified"),
            }

            person_obs = PersonObservation(
                track_id=track_id,
                class_name="person",
                bbox=entity.bbox,
                center=entity.center,
                movement=entity.movement,
                action=action,
                confidence=entity.confidence,
                first_seen=entity.first_seen,
                last_seen=entity.last_seen,
                identity_status="KNOWN_AUTHORIZED" if is_known else "UNKNOWN",
                person_name=d_name,
                user_id=u_id if is_known else None,
                identity_confidence=id_conf,
                visual_attributes=visual_attr,
            )
            persons.append(person_obs)

        # 3. Process tracked objects
        objects: list[ObjectObservation] = []
        for entity in object_entities:
            # Color extraction from object crop
            ox1 = max(0, int(entity.bbox.x1))
            oy1 = max(0, int(entity.bbox.y1))
            ox2 = min(w, int(entity.bbox.x2))
            oy2 = min(h, int(entity.bbox.y2))
            obj_crop = frame[oy1:oy2, ox1:ox2]
            obj_color = get_dominant_color_name(obj_crop)

            assoc_person_tid = None
            assoc_person_name = None
            if entity.track_id in obj_to_person:
                assoc_person_tid, assoc_person_name = obj_to_person[entity.track_id]

            obj_obs = ObjectObservation(
                track_id=entity.track_id,
                class_name=entity.class_name,
                bbox=entity.bbox,
                center=entity.center,
                movement=entity.movement,
                confidence=entity.confidence,
                first_seen=entity.first_seen,
                last_seen=entity.last_seen,
                approximate_color=obj_color if obj_color != "unknown" else None,
                associated_person_track_id=assoc_person_tid,
                associated_person_name=assoc_person_name,
            )
            objects.append(obj_obs)

        # 4. Construct root PerceptionObservation
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
