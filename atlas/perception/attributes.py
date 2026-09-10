"""Visual attribute extraction and spatial association for ATLAS Home Perception.

Extracts deterministic, grounded visual attributes from real camera frames:
- Upper garment dominant color (HSV binning)
- Lower garment dominant color (HSV binning)
- Movement direction
- Carried object spatial containment/overlap
- Approximate camera quadrant / region

Zero-hallucination policy: If lighting, resolution, or contrast is insufficient,
explicitly returns 'unknown' or 'insufficient visual evidence'.
"""

from __future__ import annotations

import cv2
import numpy as np
from typing import Any, Sequence

from atlas.perception.schema import BoundingBox, CenterPoint


def get_dominant_color_name(crop: np.ndarray) -> str:
    """Classify dominant color in an RGB/BGR image crop using HSV color binning."""
    if crop is None or crop.size < 64:
        return "unknown"

    h, w = crop.shape[:2]
    if h < 8 or w < 8:
        return "unknown"

    # Contrast check
    std = float(np.std(crop))
    if std < 6.0:
        return "unknown"

    try:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    except Exception:
        return "unknown"

    # Separate channels
    h_chan = hsv[:, :, 0]
    s_chan = hsv[:, :, 1]
    v_chan = hsv[:, :, 2]

    mean_v = float(np.mean(v_chan))
    mean_s = float(np.mean(s_chan))

    # Achromatic checks
    if mean_v < 45:
        return "black / dark"
    if mean_v > 185 and mean_s < 40:
        return "white / light"
    if mean_s < 40 and 45 <= mean_v <= 185:
        return "grey"

    # Filter for chromatic pixels
    chromatic_mask = (s_chan > 45) & (v_chan > 45)
    chromatic_h = h_chan[chromatic_mask]

    if chromatic_h.size < (crop.size // 6):
        # Mostly neutral
        return "dark" if mean_v < 100 else "light / neutral"

    # Histogram of hue (0 to 180 in OpenCV)
    hist, bin_edges = np.histogram(chromatic_h, bins=18, range=(0, 180))
    peak_bin = int(np.argmax(hist))
    hue_deg = (bin_edges[peak_bin] + bin_edges[peak_bin + 1]) / 2.0

    if hue_deg < 10 or hue_deg >= 170:
        return "red"
    elif 10 <= hue_deg < 25:
        return "orange" if mean_v > 100 else "brown"
    elif 25 <= hue_deg < 38:
        return "yellow"
    elif 38 <= hue_deg < 85:
        return "green"
    elif 85 <= hue_deg < 135:
        return "blue"
    elif 135 <= hue_deg < 170:
        return "purple / violet"

    return "unknown"


def extract_clothing_attributes(frame: np.ndarray, bbox: BoundingBox) -> dict[str, Any]:
    """Extract clothing colors for upper and lower body crops from a person bounding box."""
    if frame is None or frame.size == 0:
        return {
            "upper_clothing_color": "unknown",
            "lower_clothing_color": "unknown",
            "clarity": "insufficient_evidence",
        }

    h, w = frame.shape[:2]
    x1 = max(0, int(bbox.x1))
    y1 = max(0, int(bbox.y1))
    x2 = min(w, int(bbox.x2))
    y2 = min(h, int(bbox.y2))

    box_h = y2 - y1
    box_w = x2 - x1

    if box_h < 32 or box_w < 16:
        return {
            "upper_clothing_color": "unknown",
            "lower_clothing_color": "unknown",
            "clarity": "low_resolution",
        }

    # Upper torso: 20% to 55% of height
    upper_top = y1 + int(box_h * 0.20)
    upper_bottom = y1 + int(box_h * 0.55)
    upper_left = x1 + int(box_w * 0.15)
    upper_right = x2 - int(box_w * 0.15)
    upper_crop = frame[upper_top:upper_bottom, upper_left:upper_right]

    # Lower body: 55% to 90% of height
    lower_top = y1 + int(box_h * 0.55)
    lower_bottom = y1 + int(box_h * 0.90)
    lower_left = x1 + int(box_w * 0.15)
    lower_right = x2 - int(box_w * 0.15)
    lower_crop = frame[lower_top:lower_bottom, lower_left:lower_right]

    upper_color = get_dominant_color_name(upper_crop)
    lower_color = get_dominant_color_name(lower_crop)

    return {
        "upper_clothing_color": upper_color,
        "lower_clothing_color": lower_color,
        "clarity": "verified" if upper_color != "unknown" else "partial",
    }


def compute_camera_region(center: CenterPoint, frame_width: int, frame_height: int) -> str:
    """Determine approximate region/quadrant of the camera view."""
    if frame_width <= 0 or frame_height <= 0:
        return "monitored_area"

    x_rel = center.x / frame_width
    y_rel = center.y / frame_height

    horiz = "left" if x_rel < 0.35 else ("right" if x_rel > 0.65 else "center")
    vert = "foreground" if y_rel > 0.65 else ("background" if y_rel < 0.35 else "midground")

    return f"{horiz}_{vert}"


def determine_movement_direction(dx: float, dy: float, speed: float) -> str:
    """Classify physical direction of movement from frame displacement."""
    if speed < 12.0:
        return "stationary"

    abs_dx = abs(dx)
    abs_dy = abs(dy)

    if abs_dx > abs_dy * 1.3:
        return "moving right" if dx > 0 else "moving left"
    elif abs_dy > abs_dx * 1.3:
        return "moving forward" if dy > 0 else "moving backward"
    else:
        h_dir = "right" if dx > 0 else "left"
        v_dir = "forward" if dy > 0 else "backward"
        return f"moving diagonally ({h_dir}-{v_dir})"


def check_carried_objects(
    person_bbox: BoundingBox,
    objects: Sequence[tuple[int, str, BoundingBox]],
) -> list[dict[str, Any]]:
    """Check spatial association between a person and nearby carried objects.
    
    Args:
        person_bbox: Bounding box of the tracked person
        objects: List of (object_track_id, class_name, object_bbox)
        
    Returns:
        List of associated object details: [{"track_id": int, "class_name": str}]
    """
    carried = []
    p_x1, p_y1, p_x2, p_y2 = person_bbox.x1, person_bbox.y1, person_bbox.x2, person_bbox.y2
    p_w = p_x2 - p_x1
    p_h = p_y2 - p_y1

    # Focus on torso/hand region: from 20% to 80% height, with slight margin
    carry_zone_top = p_y1 + p_h * 0.20
    carry_zone_bottom = p_y1 + p_h * 0.85
    carry_zone_left = p_x1 - p_w * 0.20
    carry_zone_right = p_x2 + p_w * 0.20

    CARRIABLE_CLASSES = {
        "backpack", "handbag", "suitcase", "bag", "parcel", "box",
        "bottle", "cup", "cell phone", "laptop", "book", "umbrella"
    }

    for obj_id, obj_class, o_box in objects:
        c_name = obj_class.lower()
        if c_name not in CARRIABLE_CLASSES and "bag" not in c_name and "box" not in c_name:
            continue

        o_cx = (o_box.x1 + o_box.x2) / 2.0
        o_cy = (o_box.y1 + o_box.y2) / 2.0

        # Check if object center falls inside the person's carry zone
        if carry_zone_left <= o_cx <= carry_zone_right and carry_zone_top <= o_cy <= carry_zone_bottom:
            carried.append({
                "track_id": obj_id,
                "class_name": obj_class,
            })

    return carried
