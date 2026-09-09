"""Real Person and Object Detection for ATLAS Perception.

Uses lightweight YOLO (Ultralytics) for low-latency inference on the
physical webcam.
Identifies 'person' and COCO objects (e.g. backpack, handbag, suitcase,
laptop, cellphone, chair, bottle, cup, etc.) directly from real frames.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Sequence
import numpy as np

from atlas.perception.schema import BoundingBox, CenterPoint, DetectionItem

logger = logging.getLogger("atlas.perception.detector")

# COCO object classes of particular interest to ATLAS Home
SUPPORTED_OBJECT_CLASSES = {
    "person",
    "backpack",
    "handbag",
    "suitcase",
    "laptop",
    "cell phone",
    "chair",
    "couch",
    "bed",
    "dining table",
    "bottle",
    "cup",
    "book",
}


class ObjectDetector:
    """YOLO-based real-time detector for person and environment objects."""

    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        confidence_threshold: float = 0.35,
        target_classes: Sequence[str] | None = None,
    ) -> None:
        self.model_name = model_name
        self.confidence_threshold = confidence_threshold
        self.target_classes = set(target_classes) if target_classes else SUPPORTED_OBJECT_CLASSES
        self._model = None
        self._initialized = False

    def load_model(self) -> None:
        """Lazy load YOLO model."""
        if self._initialized:
            return
        from ultralytics import YOLO
        logger.info(f"[detector] Loading YOLO model: {self.model_name}...")
        self._model = YOLO(self.model_name)
        self._initialized = True
        logger.info(f"[detector] YOLO model {self.model_name} loaded successfully.")

    @property
    def is_loaded(self) -> bool:
        return self._initialized and self._model is not None

    def detect(self, frame: np.ndarray, frame_id: int = 0) -> list[DetectionItem]:
        """Run object detection on a real frame."""
        if not self._initialized:
            self.load_model()

        if frame is None or frame.size == 0:
            return []

        timestamp_str = datetime.now(timezone.utc).isoformat()

        # Run inference in inference/evaluation mode
        results = self._model.predict(
            source=frame,
            conf=self.confidence_threshold,
            verbose=False,
        )

        detections: list[DetectionItem] = []
        if not results or len(results) == 0:
            return detections

        result = results[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return detections

        names = self._model.names

        for box in boxes:
            cls_id = int(box.cls[0].item())
            class_name = names.get(cls_id, str(cls_id)).lower()
            conf = float(box.conf[0].item())

            # Optional filter by target classes
            if self.target_classes and class_name not in self.target_classes:
                continue

            xyxy = box.xyxy[0].tolist()
            x1, y1, x2, y2 = float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0

            item = DetectionItem(
                class_name=class_name,
                confidence=round(conf, 4),
                bbox=BoundingBox(x1=round(x1, 2), y1=round(y1, 2), x2=round(x2, 2), y2=round(y2, 2)),
                center=CenterPoint(x=round(cx, 2), y=round(cy, 2)),
                timestamp=timestamp_str,
                frame_id=frame_id,
            )
            detections.append(item)

        return detections
