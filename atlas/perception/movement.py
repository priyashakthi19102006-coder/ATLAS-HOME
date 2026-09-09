"""Temporal Movement Analysis for ATLAS Perception.

Calculates real spatial displacements, pixel velocities, and determines
whether tracked entities are stationary, moving, or unknown.
Applies configurable jitter filters to prevent detector noise from being
misclassified as movement.
"""

from __future__ import annotations

import math
from typing import Optional
from atlas.perception.schema import CenterPoint, MovementObservation


class MovementAnalyzer:
    """Analyzes spatial displacements across temporal observations."""

    def __init__(
        self,
        jitter_threshold_px: float = 6.0,
        speed_threshold_px_s: float = 15.0,
    ) -> None:
        self.jitter_threshold_px = jitter_threshold_px
        self.speed_threshold_px_s = speed_threshold_px_s

    def analyze(
        self,
        curr_center: CenterPoint,
        curr_timestamp: float,
        prev_center: Optional[CenterPoint] = None,
        prev_timestamp: Optional[float] = None,
    ) -> MovementObservation:
        """Compute movement metrics between consecutive observations."""
        if prev_center is None or prev_timestamp is None:
            return MovementObservation(
                state="unknown",
                dx=0.0,
                dy=0.0,
                displacement=0.0,
                speed_px_s=0.0,
            )

        dt = curr_timestamp - prev_timestamp
        if dt <= 0.0:
            dt = 0.001  # Protect against division by zero

        dx = round(curr_center.x - prev_center.x, 2)
        dy = round(curr_center.y - prev_center.y, 2)
        displacement = round(math.sqrt(dx * dx + dy * dy), 2)
        speed = round(displacement / dt, 2)

        # Distinguish stationary from moving using jitter and velocity thresholds
        if displacement < self.jitter_threshold_px or speed < self.speed_threshold_px_s:
            state = "stationary"
        else:
            state = "moving"

        return MovementObservation(
            state=state,
            dx=dx,
            dy=dy,
            displacement=displacement,
            speed_px_s=speed,
        )
