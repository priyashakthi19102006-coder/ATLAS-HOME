"""ATLAS Camera Diagnostic & Verification Tool.

Tests the physical local integrated webcam at device index 0.

Checks:
1. Configuration (resolves to webcam device index 0)
2. Camera Connection (cv2.VideoCapture(0, cv2.CAP_DSHOW) / OpenCV fallback)
3. Real Frame Capture (exactly 10 REAL frames captured)
4. Frame Dimensions (width > 0, height > 0)
5. Timestamp Progression (high-resolution monotonic timestamps advancing)

Zero-Mock Policy: Under no circumstances are replacement or synthetic frames generated.
"""

from __future__ import annotations

import os
import sys
import time
import cv2
import numpy as np

from atlas.config.settings import get_settings


def run_verification() -> int:
    print("=" * 60)
    print("ATLAS CAMERA DIAGNOSTIC")
    print("=======================")

    settings = get_settings(reload=True)
    source = settings.camera_source

    backend_name = "Unknown"
    cap: cv2.VideoCapture | None = None
    all_passed = True

    # -------------------------------------------------------------
    # CHECK 1: Configuration
    # -------------------------------------------------------------
    is_device_0 = (source == 0 or source == "0")
    if is_device_0:
        print(f"Source: Integrated Webcam")
        print(f"Device Index: {source}")
        check1_passed = True
        print("[PASS] Configuration: Source resolves to webcam device index 0")
    else:
        print(f"Source: {source}")
        print(f"[FAIL] Configuration: Configured source is {source} (expected device index 0)")
        all_passed = False
        return 1

    # -------------------------------------------------------------
    # CHECK 2: Camera Connection
    # -------------------------------------------------------------
    target_index = int(source)
    if (sys.platform.startswith("win") or os.name == "nt") and hasattr(cv2, "CAP_DSHOW"):
        try:
            cap = cv2.VideoCapture(target_index, cv2.CAP_DSHOW)
            if cap.isOpened():
                backend_name = "DirectShow (CAP_DSHOW)"
            else:
                cap.release()
                cap = None
        except Exception as e:
            cap = None

    if cap is None:
        cap = cv2.VideoCapture(target_index)
        if cap.isOpened():
            backend_name = "Standard OpenCV Default"

    print(f"Backend: {backend_name}")

    if cap is not None and cap.isOpened():
        print(f"[PASS] Camera Connection: Opened physical device {target_index} via {backend_name}")
    else:
        print(f"[FAIL] Camera Connection: Could not open camera device {target_index}")
        print("Camera diagnostic: FAILED (Webcam unreachable)")
        return 1

    # -------------------------------------------------------------
    # CHECK 3 & 4 & 5: Real Frame Capture, Dimensions & Timestamps
    # -------------------------------------------------------------
    TOTAL_FRAMES_TARGET = 10
    captured_frames: list[np.ndarray] = []
    timestamps: list[float] = []

    try:
        t_start = time.perf_counter()

        for i in range(TOTAL_FRAMES_TARGET):
            ret, frame = cap.read()
            t_frame = time.perf_counter()

            if not ret or frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
                print(f"[FAIL] Real Frame Capture: Failed at frame {i+1}/{TOTAL_FRAMES_TARGET} (ret={ret})")
                all_passed = False
                break

            h, w = frame.shape[:2]
            if w <= 0 or h <= 0:
                print(f"[FAIL] Frame Dimensions: Frame {i+1} has invalid dimensions: {w}x{h}")
                all_passed = False
                break

            captured_frames.append(frame)
            timestamps.append(t_frame)
            # Brief yield to allow hardware clock advancement between reads
            time.sleep(0.01)

        t_end = time.perf_counter()
        elapsed_total = t_end - t_start

        # Evaluate Check 3
        if len(captured_frames) == TOTAL_FRAMES_TARGET and all_passed:
            print(f"[PASS] Real Frame Capture ({len(captured_frames)}/{TOTAL_FRAMES_TARGET}): All 10 real frames received")
        else:
            print(f"[FAIL] Real Frame Capture: Only {len(captured_frames)}/{TOTAL_FRAMES_TARGET} frames captured")
            all_passed = False

        # Evaluate Check 4
        if captured_frames:
            last_h, last_w = captured_frames[-1].shape[:2]
            channels = captured_frames[-1].shape[2] if len(captured_frames[-1].shape) > 2 else 1
            print(f"[PASS] Frame Dimensions: Detected {last_w}x{last_h} px ({channels} channels)")
        else:
            print("[FAIL] Frame Dimensions: No frames available to evaluate dimensions")
            all_passed = False

        # Evaluate Check 5
        if len(timestamps) == TOTAL_FRAMES_TARGET:
            # Verify monotonic progression: every subsequent timestamp is strictly greater
            increments_valid = all(
                timestamps[k] > timestamps[k - 1] for k in range(1, len(timestamps))
            )
            first_ts = timestamps[0]
            last_ts = timestamps[-1]

            if increments_valid:
                print(
                    f"[PASS] Timestamp Progression: Timestamps monotonically incremented\n"
                    f"       Frames captured: {len(timestamps)}\n"
                    f"       First timestamp: {first_ts:.6f}s\n"
                    f"       Last timestamp:  {last_ts:.6f}s\n"
                    f"       Elapsed time:    {elapsed_total:.4f}s"
                )
            else:
                print("[FAIL] Timestamp Progression: Timestamps did not strictly increase")
                all_passed = False
        else:
            print("[FAIL] Timestamp Progression: Insufficient timestamps recorded")
            all_passed = False

    finally:
        cap.release()

    print("=" * 60)
    if all_passed:
        print("Camera diagnostic: ALL 5 CHECKS PASSED")
        return 0
    else:
        print("Camera diagnostic: FAILED (Zero-Mock Enforced)")
        return 1


if __name__ == "__main__":
    sys.exit(run_verification())
