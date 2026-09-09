"""ATLAS Real-Time Perception Module.

Components:
- ObjectDetector: YOLO-based detection of persons and environment objects
- MultiObjectTracker: ByteTrack multi-object tracking
- MovementAnalyzer: Spatial displacement and pixel velocity analysis
- HumanActionObserver: Posture and temporal action estimation
- PerceptionEngine: Unified pipeline producing structured observations
"""

from atlas.perception.actions import HumanActionObserver, TrackHistoryItem
from atlas.perception.detector import ObjectDetector, SUPPORTED_OBJECT_CLASSES
from atlas.perception.engine import PerceptionEngine, get_perception_engine
from atlas.perception.movement import MovementAnalyzer
from atlas.perception.schema import (
    ActionObservation,
    BoundingBox,
    CenterPoint,
    DetectionItem,
    MovementObservation,
    ObjectObservation,
    PerceptionObservation,
    PersonObservation,
)
from atlas.perception.tracker import MultiObjectTracker, TrackedEntity

__all__ = [
    "ActionObservation",
    "BoundingBox",
    "CenterPoint",
    "DetectionItem",
    "HumanActionObserver",
    "MovementAnalyzer",
    "MovementObservation",
    "MultiObjectTracker",
    "ObjectDetector",
    "ObjectObservation",
    "PerceptionEngine",
    "PerceptionObservation",
    "PersonObservation",
    "SUPPORTED_OBJECT_CLASSES",
    "TrackHistoryItem",
    "TrackedEntity",
    "get_perception_engine",
]
