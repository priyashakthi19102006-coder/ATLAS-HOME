"""ATLAS Event Engine.

Transforms continuous real-time perception observations into meaningful,
lifecycle-managed, de-duplicated structured events.
Maintains situational state transitions without flooding identical events.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from atlas.context.memory import ContextManager

from atlas.events.schema import (
    ATLASEvent,
    EventSeverity,
    EventStatus,
    EventType,
)
from atlas.perception.schema import PerceptionObservation, PersonObservation, ObjectObservation

logger = logging.getLogger("atlas.events.engine")


class TrackLifecycleState:
    """Tracks state transitions and presence lifecycle for a tracked entity."""

    def __init__(
        self,
        track_id: int,
        class_name: str,
        first_seen: float,
        presence_event_id: str,
    ) -> None:
        self.track_id = track_id
        self.class_name = class_name
        self.first_seen = first_seen
        self.last_seen = first_seen
        self.presence_event_id = presence_event_id

        # Last reported states to prevent event flooding
        self.last_action: str | None = None
        self.last_movement_state: str | None = None
        self.last_event_timestamp: float = first_seen
        self.active_event_ids: set[str] = {presence_event_id}


class EventEngine:
    """Transforms raw real perception observations into normalized ATLAS events."""

    def __init__(
        self,
        context_manager: Any = None,
        track_expiry_seconds: float = 5.0,
        association_distance_px: float = 160.0,
    ) -> None:
        if context_manager is None:
            from atlas.context.memory import get_context_manager
            self.context_manager = get_context_manager()
        else:
            self.context_manager = context_manager
        self.track_expiry_seconds = track_expiry_seconds
        self.association_distance_px = association_distance_px

        self._lock = threading.Lock()
        self._person_states: dict[int, TrackLifecycleState] = {}
        self._object_states: dict[int, TrackLifecycleState] = {}
        self._last_processed_frame_id: int = -1

    def process_observation(self, obs: PerceptionObservation) -> list[ATLASEvent]:
        """Process a real perception observation, perform deduplication and emit lifecycle events."""
        with self._lock:
            if obs.frame_id == self._last_processed_frame_id:
                # Deduplicate: frame already processed
                return []
            self._last_processed_frame_id = obs.frame_id

            now = obs.epoch_timestamp if obs.epoch_timestamp else time.time()
            emitted_events: list[ATLASEvent] = []

            curr_person_ids = {p.track_id for p in obs.persons}
            curr_object_ids = {o.track_id for o in obs.objects}

            # -------------------------------------------------------------
            # 1. Process Persons
            # -------------------------------------------------------------
            for person in obs.persons:
                pid = person.track_id
                bbox_dict = person.bbox.model_dump()
                center_dict = person.center.model_dump()

                # Update context memory with real identity and visual attributes
                self.context_manager.update_person_track(
                    track_id=pid,
                    action=person.action.label,
                    movement_state=person.movement.state,
                    bbox=bbox_dict,
                    center=center_dict,
                    confidence=person.confidence,
                    timestamp=now,
                    identity_status=person.identity_status,
                    person_name=person.person_name,
                    user_id=person.user_id,
                    visual_attributes=person.visual_attributes,
                )

                if pid not in self._person_states:
                    # New Track -> Emit PERSON_ENTERED (OBSERVED transition)
                    enter_event = ATLASEvent(
                        event_type=EventType.PERSON_ENTERED.value,
                        source=obs.source,
                        source_device="atlas_home",
                        timestamp=obs.timestamp,
                        status=EventStatus.ACTIVE.value,
                        severity=EventSeverity.INFO.value,
                        confidence=person.confidence,
                        person_id=str(pid),
                        action=person.action.label,
                        evidence={
                            "frame_id": obs.frame_id,
                            "track_id": pid,
                            "detection_confidence": person.confidence,
                            "action": person.action.label,
                            "action_confidence": person.action.confidence,
                            "movement": person.movement.model_dump(),
                            "bbox": [person.bbox.x1, person.bbox.y1, person.bbox.x2, person.bbox.y2],
                            "person_name": person.person_name,
                            "identity_status": person.identity_status,
                            "visual_attributes": person.visual_attributes,
                        },
                        metadata={
                            "lifecycle": "enter",
                            "person_name": person.person_name,
                            "identity_status": person.identity_status,
                            "track_id": pid,
                        },
                    )
                    emitted_events.append(enter_event)

                    state = TrackLifecycleState(
                        track_id=pid,
                        class_name="person",
                        first_seen=now,
                        presence_event_id=enter_event.event_id,
                    )
                    state.last_action = person.action.label
                    state.last_movement_state = person.movement.state
                    self._person_states[pid] = state

                else:
                    # Existing Track -> Check for state / action transitions
                    state = self._person_states[pid]
                    state.last_seen = now

                    # Fall-like observation transition (High priority observation warning)
                    if person.action.label == "fall_like" and state.last_action != "fall_like":
                        fall_event = ATLASEvent(
                            event_type=EventType.PERSON_FALL_LIKE.value,
                            source=obs.source,
                            source_device="atlas_home",
                            timestamp=obs.timestamp,
                            status=EventStatus.ACTIVE.value,
                            severity=EventSeverity.WARNING.value,
                            confidence=person.action.confidence,
                            person_id=str(pid),
                            action="fall_like",
                            evidence={
                                "frame_id": obs.frame_id,
                                "track_id": pid,
                                "uncertainty": person.action.uncertainty,
                                "action_confidence": person.action.confidence,
                                "movement": person.movement.model_dump(),
                                "bbox": [person.bbox.x1, person.bbox.y1, person.bbox.x2, person.bbox.y2],
                            },
                            metadata={"note": "Perceptual fall-like trajectory; requires deterministic safety rule evaluation", "track_id": pid},
                        )
                        emitted_events.append(fall_event)
                        state.active_event_ids.add(fall_event.event_id)
                        state.last_action = "fall_like"

                    # Action transition (e.g. standing -> walking, standing -> sitting)
                    elif person.action.label != state.last_action and person.action.label != "unknown":
                        # Transition previous active action events to RESOLVED
                        for prev_eid in list(state.active_event_ids):
                            if prev_eid != state.presence_event_id:
                                try:
                                    self.context_manager.storage.update_event_status(prev_eid, EventStatus.RESOLVED.value)
                                    state.active_event_ids.remove(prev_eid)
                                except Exception:
                                    pass

                        event_type_map = {
                            "standing": EventType.PERSON_STANDING.value,
                            "walking": EventType.PERSON_WALKING.value,
                            "sitting": EventType.PERSON_SITTING.value,
                        }
                        ev_type = event_type_map.get(person.action.label, EventType.PERSON_DETECTED.value)
                        action_ev = ATLASEvent(
                            event_type=ev_type,
                            source=obs.source,
                            source_device="atlas_home",
                            timestamp=obs.timestamp,
                            status=EventStatus.ACTIVE.value,
                            severity=EventSeverity.INFO.value,
                            confidence=person.action.confidence,
                            person_id=str(pid),
                            action=person.action.label,
                            evidence={
                                "frame_id": obs.frame_id,
                                "track_id": pid,
                                "previous_action": state.last_action,
                                "action": person.action.label,
                                "movement": person.movement.model_dump(),
                            },
                            metadata={"track_id": pid, "person_name": person.person_name},
                        )
                        emitted_events.append(action_ev)
                        state.active_event_ids.add(action_ev.event_id)
                        state.last_action = person.action.label

                    # Movement transition (e.g. stationary -> moving)
                    elif person.movement.state != state.last_movement_state and person.movement.state != "unknown":
                        mov_type = (
                            EventType.PERSON_MOVING.value
                            if person.movement.state == "moving"
                            else EventType.PERSON_STATIONARY.value
                        )
                        mov_ev = ATLASEvent(
                            event_type=mov_type,
                            source=obs.source,
                            source_device="atlas_home",
                            timestamp=obs.timestamp,
                            status=EventStatus.ACTIVE.value,
                            severity=EventSeverity.INFO.value,
                            confidence=person.confidence,
                            person_id=str(pid),
                            action=person.action.label,
                            evidence={
                                "frame_id": obs.frame_id,
                                "track_id": pid,
                                "speed_px_s": person.movement.speed_px_s,
                                "movement_state": person.movement.state,
                            },
                        )
                        emitted_events.append(mov_ev)
                        state.last_movement_state = person.movement.state

            # -------------------------------------------------------------
            # 2. Process Objects
            # -------------------------------------------------------------
            for obj in obs.objects:
                oid = obj.track_id
                bbox_dict = obj.bbox.model_dump()
                center_dict = obj.center.model_dump()

                self.context_manager.update_object_track(
                    track_id=oid,
                    class_name=obj.class_name,
                    movement_state=obj.movement.state,
                    bbox=bbox_dict,
                    center=center_dict,
                    confidence=obj.confidence,
                    timestamp=now,
                    approximate_color=obj.approximate_color,
                    associated_person_track_id=obj.associated_person_track_id,
                    associated_person_name=obj.associated_person_name,
                )

                if oid not in self._object_states:
                    # New Object Track -> Emit OBJECT_ENTERED
                    obj_enter = ATLASEvent(
                        event_type=EventType.OBJECT_ENTERED.value,
                        source=obs.source,
                        source_device="atlas_home",
                        timestamp=obs.timestamp,
                        status=EventStatus.ACTIVE.value,
                        severity=EventSeverity.INFO.value,
                        confidence=obj.confidence,
                        object_id=str(oid),
                        evidence={
                            "frame_id": obs.frame_id,
                            "track_id": oid,
                            "class_name": obj.class_name,
                            "confidence": obj.confidence,
                            "movement": obj.movement.model_dump(),
                            "approximate_color": obj.approximate_color,
                            "associated_person_name": obj.associated_person_name,
                        },
                        metadata={
                            "class_name": obj.class_name,
                            "approximate_color": obj.approximate_color,
                            "associated_person_name": obj.associated_person_name,
                            "track_id": oid,
                        },
                    )
                    emitted_events.append(obj_enter)

                    obj_state = TrackLifecycleState(
                        track_id=oid,
                        class_name=obj.class_name,
                        first_seen=now,
                        presence_event_id=obj_enter.event_id,
                    )
                    obj_state.last_movement_state = obj.movement.state
                    self._object_states[oid] = obj_state

                else:
                    obj_state = self._object_states[oid]
                    obj_state.last_seen = now

                    # Object Movement Transition
                    if obj.movement.state != obj_state.last_movement_state and obj.movement.state != "unknown":
                        ev_type = (
                            EventType.OBJECT_MOVED.value
                            if obj.movement.state == "moving"
                            else EventType.OBJECT_STATIONARY.value
                        )
                        # Check spatial relationship with any active person
                        related_person_id = None
                        for p in obs.persons:
                            dist = math.hypot(p.center.x - obj.center.x, p.center.y - obj.center.y)
                            if dist < self.association_distance_px:
                                related_person_id = p.track_id
                                break

                        obj_ev = ATLASEvent(
                            event_type=ev_type,
                            source=obs.source,
                            source_device="atlas_home",
                            timestamp=obs.timestamp,
                            status=EventStatus.ACTIVE.value,
                            severity=EventSeverity.NOTICE.value if obj.movement.state == "moving" else EventSeverity.INFO.value,
                            confidence=obj.confidence,
                            object_id=str(oid),
                            person_id=str(related_person_id) if related_person_id is not None else None,
                            evidence={
                                "frame_id": obs.frame_id,
                                "track_id": oid,
                                "class_name": obj.class_name,
                                "speed_px_s": obj.movement.speed_px_s,
                                "movement_state": obj.movement.state,
                            },
                            metadata={
                                "class_name": obj.class_name,
                                "relation": "associated_with" if related_person_id is not None else None,
                                "person_track_id": related_person_id,
                            },
                        )
                        emitted_events.append(obj_ev)
                        obj_state.last_movement_state = obj.movement.state

            # -------------------------------------------------------------
            # 3. Handle Departures / Exits
            # -------------------------------------------------------------
            left_persons = []
            for pid, state in self._person_states.items():
                if pid not in curr_person_ids and (now - state.last_seen > self.track_expiry_seconds):
                    left_persons.append(pid)

            for pid in left_persons:
                state = self._person_states.pop(pid)
                self.context_manager.mark_track_inactive(pid, is_person=True)

                # Resolve active events for this track
                for eid in state.active_event_ids:
                    self.context_manager.storage.update_event_status(eid, EventStatus.RESOLVED.value)

                left_event = ATLASEvent(
                    event_type=EventType.PERSON_LEFT.value,
                    source=obs.source,
                    source_device="atlas_home",
                    timestamp=obs.timestamp,
                    status=EventStatus.RESOLVED.value,
                    severity=EventSeverity.INFO.value,
                    person_id=str(pid),
                    evidence={
                        "frame_id": obs.frame_id,
                        "track_id": pid,
                        "duration_seconds": round(state.last_seen - state.first_seen, 2),
                    },
                    metadata={"lifecycle": "exit"},
                )
                emitted_events.append(left_event)

            left_objects = []
            for oid, state in self._object_states.items():
                if oid not in curr_object_ids and (now - state.last_seen > self.track_expiry_seconds):
                    left_objects.append(oid)

            for oid in left_objects:
                state = self._object_states.pop(oid)
                self.context_manager.mark_track_inactive(oid, is_person=False)

                left_obj = ATLASEvent(
                    event_type=EventType.OBJECT_LEFT.value,
                    source=obs.source,
                    source_device="atlas_home",
                    timestamp=obs.timestamp,
                    status=EventStatus.RESOLVED.value,
                    severity=EventSeverity.INFO.value,
                    object_id=str(oid),
                    evidence={
                        "frame_id": obs.frame_id,
                        "track_id": oid,
                        "class_name": state.class_name,
                        "duration_seconds": round(state.last_seen - state.first_seen, 2),
                    },
                    metadata={"lifecycle": "exit", "class_name": state.class_name},
                )
                emitted_events.append(left_obj)

            # -------------------------------------------------------------
            # 4. Record and Persist Emitted Events
            # -------------------------------------------------------------
            for ev in emitted_events:
                self.context_manager.record_event(ev)

            return emitted_events


_global_event_engine: EventEngine | None = None


def get_event_engine() -> EventEngine:
    """Get or initialize singleton EventEngine."""
    global _global_event_engine
    if _global_event_engine is None:
        _global_event_engine = EventEngine()
    return _global_event_engine
