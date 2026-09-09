"""ATLAS Temporal Context and Short-Term Memory Layer.

Maintains bounded recent situational context answering:
'What has been happening recently?'

Tracks:
- active persons (first/last seen, action changes, movement trajectory)
- active objects (first/last seen, movement states)
- chronological event timeline within a sliding temporal window
"""

from __future__ import annotations

from collections import deque
import threading
import time
from typing import Any

from atlas.events.schema import ATLASEvent, EventStatus
from atlas.events.storage import EventStorage, get_event_storage


class ContextManager:
    """In-memory bounded temporal context manager."""

    def __init__(
        self,
        retention_window_seconds: float = 120.0,
        max_timeline_events: int = 300,
        storage: EventStorage | None = None,
    ) -> None:
        self.retention_window_seconds = retention_window_seconds
        self.max_timeline_events = max_timeline_events
        self.storage = storage or get_event_storage()

        self._lock = threading.Lock()
        self._timeline: deque[ATLASEvent] = deque(maxlen=max_timeline_events)
        self._persons: dict[int, dict[str, Any]] = {}
        self._objects: dict[int, dict[str, Any]] = {}

    def record_event(self, event: ATLASEvent) -> None:
        """Register an event in context memory and persistent storage."""
        with self._lock:
            self._timeline.append(event)
        # Persist to database
        self.storage.save_event(event)

        # Step 6.5: Ingest into FusionEngine as normalized Observation
        try:
            from atlas.fusion.engine import get_fusion_engine
            fusion_engine = get_fusion_engine(storage=self.storage)
            fusion_engine.ingest_event(event)
        except Exception:
            pass

    def update_person_track(
        self,
        track_id: int,
        action: str,
        movement_state: str,
        bbox: dict[str, float] | None,
        center: dict[str, float] | None,
        confidence: float,
        timestamp: float,
    ) -> None:
        """Update recent situational context for a tracked person."""
        with self._lock:
            if track_id in self._persons:
                ctx = self._persons[track_id]
                prev_action = ctx["current_action"]
                prev_mov = ctx["movement_state"]
                ctx["previous_action"] = prev_action if prev_action != action else ctx.get("previous_action")
                ctx["previous_movement_state"] = prev_mov if prev_mov != movement_state else ctx.get("previous_movement_state")
                ctx["current_action"] = action
                ctx["movement_state"] = movement_state
                ctx["bbox"] = bbox
                ctx["center"] = center
                ctx["confidence"] = confidence
                ctx["last_seen"] = timestamp
                ctx["active"] = True
            else:
                self._persons[track_id] = {
                    "track_id": track_id,
                    "first_seen": timestamp,
                    "last_seen": timestamp,
                    "current_action": action,
                    "previous_action": None,
                    "movement_state": movement_state,
                    "previous_movement_state": None,
                    "bbox": bbox,
                    "center": center,
                    "confidence": confidence,
                    "active": True,
                }

    def update_object_track(
        self,
        track_id: int,
        class_name: str,
        movement_state: str,
        bbox: dict[str, float] | None,
        center: dict[str, float] | None,
        confidence: float,
        timestamp: float,
    ) -> None:
        """Update recent situational context for a tracked non-person object."""
        with self._lock:
            if track_id in self._objects:
                ctx = self._objects[track_id]
                prev_mov = ctx["movement_state"]
                ctx["previous_movement_state"] = prev_mov if prev_mov != movement_state else ctx.get("previous_movement_state")
                ctx["movement_state"] = movement_state
                ctx["bbox"] = bbox
                ctx["center"] = center
                ctx["confidence"] = confidence
                ctx["last_seen"] = timestamp
                ctx["active"] = True
            else:
                self._objects[track_id] = {
                    "track_id": track_id,
                    "class_name": class_name,
                    "first_seen": timestamp,
                    "last_seen": timestamp,
                    "movement_state": movement_state,
                    "previous_movement_state": None,
                    "bbox": bbox,
                    "center": center,
                    "confidence": confidence,
                    "active": True,
                }

    def mark_track_inactive(self, track_id: int, is_person: bool = True) -> None:
        """Mark a track as inactive/left."""
        with self._lock:
            target_dict = self._persons if is_person else self._objects
            if track_id in target_dict:
                target_dict[track_id]["active"] = False

    def prune_old_context(self, now: float | None = None) -> None:
        """Prune stale context items beyond retention window."""
        t_now = now if now is not None else time.time()
        cutoff = t_now - self.retention_window_seconds

        with self._lock:
            # Prune inactive tracks older than cutoff
            self._persons = {
                tid: data for tid, data in self._persons.items()
                if data["active"] or data["last_seen"] >= cutoff
            }
            self._objects = {
                tid: data for tid, data in self._objects.items()
                if data["active"] or data["last_seen"] >= cutoff
            }

    def get_recent_context(self, window_seconds: float | None = None, now: float | None = None) -> dict[str, Any]:
        """Return structured situational context within the temporal window."""
        window = window_seconds if window_seconds is not None else self.retention_window_seconds
        t_now = now if now is not None else time.time()
        cutoff = t_now - window

        with self._lock:
            active_persons = [
                dict(data) for data in self._persons.values()
                if data["active"] or data["last_seen"] >= cutoff
            ]
            active_objects = [
                dict(data) for data in self._objects.values()
                if data["active"] or data["last_seen"] >= cutoff
            ]
            timeline = [
                ev for ev in self._timeline
                if ev.status == EventStatus.ACTIVE.value or True
            ][-50:]

        return {
            "query_timestamp": t_now,
            "window_seconds": window,
            "active_persons_count": len([p for p in active_persons if p["active"]]),
            "active_objects_count": len([o for o in active_objects if o["active"]]),
            "persons": active_persons,
            "objects": active_objects,
            "recent_events_count": len(timeline),
        }

    def get_person_context(self, track_id: int | str) -> dict[str, Any] | None:
        """Retrieve temporal context and associated events for a specific person track."""
        try:
            tid = int(track_id)
        except ValueError:
            return None

        with self._lock:
            person_data = self._persons.get(tid)
            if not person_data:
                return None
            context_copy = dict(person_data)

        # Retrieve related events from storage
        events = self.storage.get_events_for_track(tid, is_person=True)
        context_copy["event_history"] = [ev.model_dump() for ev in events]
        return context_copy

    def get_object_context(self, track_id: int | str) -> dict[str, Any] | None:
        """Retrieve temporal context and associated events for a specific object track."""
        try:
            tid = int(track_id)
        except ValueError:
            return None

        with self._lock:
            obj_data = self._objects.get(tid)
            if not obj_data:
                return None
            context_copy = dict(obj_data)

        events = self.storage.get_events_for_track(tid, is_person=False)
        context_copy["event_history"] = [ev.model_dump() for ev in events]
        return context_copy

    def get_recent_events(self, limit: int = 50) -> list[ATLASEvent]:
        """Query recent events from in-memory timeline or fallback to storage."""
        with self._lock:
            if len(self._timeline) >= limit:
                return list(self._timeline)[-limit:]
        return self.storage.get_recent_events(limit=limit)

    def get_active_events(self) -> list[ATLASEvent]:
        """Query currently active unresolved events."""
        with self._lock:
            in_mem_active = [ev for ev in self._timeline if ev.status == EventStatus.ACTIVE.value]
            if in_mem_active:
                return in_mem_active
        return self.storage.get_active_events()

    def get_event(self, event_id: str) -> ATLASEvent | None:
        """Retrieve a specific event by ID."""
        with self._lock:
            for ev in reversed(self._timeline):
                if ev.event_id == event_id:
                    return ev
        return self.storage.get_event(event_id)


_global_context_manager: ContextManager | None = None


def get_context_manager() -> ContextManager:
    """Get or initialize singleton ContextManager."""
    global _global_context_manager
    if _global_context_manager is None:
        _global_context_manager = ContextManager()
    return _global_context_manager
