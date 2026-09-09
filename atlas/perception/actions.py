"""Human Activity and Posture Observation for ATLAS Perception.

Distinguishes:
- standing
- walking
- sitting
- fall_like (temporal rapid downward transition / aspect ratio collapse)
- unknown

Adheres to ATLAS architectural principle:
Perception provides structured evidence and explains uncertainty;
it does NOT make unilateral safety or emergency determinations.
"""

from __future__ import annotations

from typing import Sequence
from atlas.perception.schema import ActionObservation, BoundingBox, CenterPoint, MovementObservation


class TrackHistoryItem:
    """Historical snapshot of a tracked entity in a recent frame."""
    def __init__(
        self,
        timestamp: float,
        bbox: BoundingBox,
        center: CenterPoint,
        movement: MovementObservation,
    ) -> None:
        self.timestamp = timestamp
        self.bbox = bbox
        self.center = center
        self.movement = movement
        self.aspect_ratio = bbox.aspect_ratio


class HumanActionObserver:
    """Classifies human actions from temporal bounding box and movement metrics."""

    def __init__(
        self,
        standing_aspect_ratio_min: float = 1.65,
        sitting_aspect_ratio_max: float = 1.65,
        fall_downward_velocity_min: float = 80.0,  # px/s downward (positive dy)
    ) -> None:
        self.standing_aspect_ratio_min = standing_aspect_ratio_min
        self.sitting_aspect_ratio_max = sitting_aspect_ratio_max
        self.fall_downward_velocity_min = fall_downward_velocity_min

    def observe(
        self,
        curr_bbox: BoundingBox,
        curr_movement: MovementObservation,
        history: Sequence[TrackHistoryItem],
    ) -> ActionObservation:
        """Classify action using current posture and temporal trajectory."""
        curr_ratio = curr_bbox.aspect_ratio

        # 1. Check for temporal fall-like motion (requires at least 2 historical frames)
        if len(history) >= 2:
            earliest = history[0]
            dt = history[-1].timestamp - earliest.timestamp
            if 0.1 <= dt <= 1.5:
                # Downward displacement in image space (y increases downward)
                vertical_displacement = curr_bbox.y2 - earliest.bbox.y2
                vertical_velocity = vertical_displacement / dt if dt > 0 else 0.0
                ratio_drop = earliest.aspect_ratio - curr_ratio

                # If person was previously upright and experienced rapid downward shift + ratio collapse
                if (
                    earliest.aspect_ratio >= 1.4
                    and curr_ratio <= 1.15
                    and vertical_velocity > self.fall_downward_velocity_min
                    and ratio_drop > 0.4
                ):
                    return ActionObservation(
                        label="fall_like",
                        confidence=0.72,
                        uncertainty="rapid downward vertical transition and aspect ratio collapse observed",
                    )

        # 2. Check walking / horizontal movement
        if curr_ratio >= self.standing_aspect_ratio_min and curr_movement.state == "moving":
            # If motion is predominantly horizontal
            if abs(curr_movement.dx) >= abs(curr_movement.dy) or curr_movement.speed_px_s >= 20.0:
                return ActionObservation(
                    label="walking",
                    confidence=0.84,
                    uncertainty="upright posture with active translation",
                )

        # 3. Check standing (upright and stationary)
        if curr_ratio >= self.standing_aspect_ratio_min:
            return ActionObservation(
                label="standing",
                confidence=0.86,
                uncertainty="upright vertical aspect ratio, minimal displacement",
            )

        # 4. Check sitting (compact aspect ratio)
        if 0.75 <= curr_ratio < self.sitting_aspect_ratio_max:
            return ActionObservation(
                label="sitting",
                confidence=0.79,
                uncertainty="compact aspect ratio consistent with seated posture",
            )

        # 5. Low / ground posture without sudden drop
        if curr_ratio < 0.75:
            return ActionObservation(
                label="sitting",
                confidence=0.55,
                uncertainty="low aspect ratio, possibly seated or low posture",
            )

        # Fallback
        return ActionObservation(
            label="unknown",
            confidence=0.40,
            uncertainty="insufficient or ambiguous posture signals",
        )
