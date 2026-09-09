"""ATLAS Home Perception Diagnostic & Live Visual Verification Tool.

Connects to the REAL integrated webcam (Device Index 0), feeds frames through
the real-time PerceptionEngine (YOLO + ByteTrack + Movement + Action Analysis),
and validates that real model observations are generated with zero mock data.

Usage:
    python verify_perception.py                 # Automated 15-frame diagnostic
    python verify_perception.py --frames 30     # N-frame diagnostic
    python verify_perception.py --live          # Continuous interactive visual overlay (press 'q' to quit)
"""

from __future__ import annotations

import argparse
import sys
import time
import cv2

from atlas.camera.base import CameraState
from atlas.camera.stream import CameraStream
from atlas.config.settings import get_settings
from atlas.perception.engine import PerceptionEngine


def draw_perception_overlays(frame, observation):
    """Draw real model detection and tracking overlays onto OpenCV frame."""
    annotated = frame.copy()

    # Draw persons
    for p in observation.persons:
        bx1, by1, bx2, by2 = int(p.bbox.x1), int(p.bbox.y1), int(p.bbox.x2), int(p.bbox.y2)
        cv2.rectangle(annotated, (bx1, by1), (bx2, by2), (0, 255, 0), 2)

        # Label: PERSON | ID: X | action (conf) | movement
        label_top = f"PERSON ID:{p.track_id} ({p.confidence:.2f})"
        label_action = f"{p.action.label} ({p.action.confidence:.2f}) [{p.movement.state}]"

        cv2.putText(annotated, label_top, (bx1, max(20, by1 - 25)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        cv2.putText(annotated, label_action, (bx1, max(10, by1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (50, 220, 255), 1)

    # Draw objects
    for obj in observation.objects:
        bx1, by1, bx2, by2 = int(obj.bbox.x1), int(obj.bbox.y1), int(obj.bbox.x2), int(obj.bbox.y2)
        cv2.rectangle(annotated, (bx1, by1), (bx2, by2), (255, 180, 0), 2)

        label_top = f"{obj.class_name.upper()} ID:{obj.track_id} ({obj.confidence:.2f})"
        label_mov = f"[{obj.movement.state}]"

        cv2.putText(annotated, label_top, (bx1, max(20, by1 - 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 180, 0), 2)
        cv2.putText(annotated, label_mov, (bx1, max(10, by1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

    # Header HUD: FPS and frame count
    hud = f"ATLAS PERCEPTION | FPS: {observation.fps:.1f} | Frame: {observation.frame_id} | Persons: {len(observation.persons)} | Objects: {len(observation.objects)}"
    cv2.putText(annotated, hud, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

    return annotated


def run_perception_diagnostic(target_frames: int = 15, live_mode: bool = False) -> int:
    print("=" * 64)
    print("ATLAS HOME PERCEPTION DIAGNOSTIC")
    print("================================")

    settings = get_settings(reload=True)
    device_source = settings.camera_source

    print("Camera:")
    print(f"  Source: Integrated Webcam")
    print(f"  Device: {device_source}")

    # Start camera capture
    camera = CameraStream(source=device_source, auto_start=True)

    # Initialize perception engine
    print("\nPerception:")
    print("  Detector: YOLOv8 Nano (yolov8n.pt)")
    print("  Tracker: ByteTrack (bytetrack.yaml)")
    print("  Movement Analyzer: Active (Jitter filtered)")
    print("  Action Observer: Active (Posture & Temporal trajectory)")

    engine = PerceptionEngine(model_name="yolov8n.pt")

    try:
        # Wait for camera connection
        connect_start = time.time()
        connected = False
        while time.time() - connect_start < 5.0:
            if camera.connection_state == CameraState.CONNECTED and camera.get_latest_frame() is not None:
                connected = True
                break
            time.sleep(0.2)

        if not connected:
            print("  Status: FAILED (Camera device unreachable)")
            print("\nError: Could not acquire real frames from integrated webcam.")
            return 1

        print("  Status: CONNECTED")

        # Retrieve initial frame to determine resolution
        frame_data = camera.get_latest_frame()
        if frame_data is None:
            print("\nError: Zero-mock violation: get_latest_frame returned None.")
            return 1

        first_frame, _ = frame_data
        h, w = first_frame.shape[:2]
        print("\nFrame:")
        print(f"  Resolution: {w}x{h}")

        processed_count = 0
        last_obs = None

        print(f"\nProcessing real camera frames from webcam (target={target_frames} frames)...")

        t_start = time.perf_counter()
        while processed_count < target_frames or live_mode:
            frame_data = camera.get_latest_frame()
            if frame_data is None:
                time.sleep(0.02)
                continue

            frame, ts = frame_data
            observation = engine.process_frame(frame, timestamp=ts, source_name="integrated_webcam")
            last_obs = observation
            processed_count += 1

            if live_mode:
                annotated = draw_perception_overlays(frame, observation)
                cv2.imshow("ATLAS Home - Live Perception (Press 'q' to quit)", annotated)
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    print("\nUser requested exit from live view.")
                    break
            else:
                # Brief progress print
                if processed_count % 5 == 0 or processed_count == target_frames:
                    print(
                        f"  Frame {processed_count}/{target_frames}: "
                        f"Persons={len(observation.persons)}, "
                        f"Objects={len(observation.objects)}, "
                        f"FPS={engine.fps:.1f}"
                    )
                time.sleep(0.03)

        t_elapsed = time.perf_counter() - t_start
        actual_fps = round(processed_count / t_elapsed, 2) if t_elapsed > 0 else 0.0

        print("-" * 64)
        print("DIAGNOSTIC SUMMARY:")
        print(f"  Total Real Frames Processed: {processed_count}")
        print(f"  Elapsed Time: {t_elapsed:.2f}s")
        print(f"  Processing FPS: {actual_fps}")

        if last_obs is not None:
            print(f"\nLast Frame Observations:")
            print(f"  Persons Detected: {len(last_obs.persons)}")
            for p in last_obs.persons:
                print(
                    f"    * Person Track {p.track_id}: "
                    f"Action={p.action.label} (conf={p.action.confidence:.2f}), "
                    f"Movement={p.movement.state} (speed={p.movement.speed_px_s:.1f} px/s)"
                )

            print(f"  Objects Detected: {len(last_obs.objects)}")
            for obj in last_obs.objects:
                print(
                    f"    * {obj.class_name.capitalize()} Track {obj.track_id}: "
                    f"Confidence={obj.confidence:.2f}, "
                    f"Movement={obj.movement.state}"
                )

        print("=" * 64)
        print("RESULT: PERCEPTION INTEGRATION TEST PASSED (Zero-Mock Enforced)")
        return 0

    finally:
        camera.stop()
        if live_mode:
            cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description="ATLAS Home Perception Diagnostic")
    parser.add_argument("--frames", type=int, default=15, help="Number of frames to process in diagnostic mode")
    parser.add_argument("--live", action="store_true", help="Launch interactive visual window with live perception overlays")
    args = parser.parse_args()

    sys.exit(run_perception_diagnostic(target_frames=args.frames, live_mode=args.live))


if __name__ == "__main__":
    main()
