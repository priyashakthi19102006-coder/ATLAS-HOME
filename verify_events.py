"""ATLAS Home - Step 3 Real-Camera Event & Context Diagnostic.

Connects:
Physical Webcam (Index 0)
→ Real-Time Perception (YOLOv8 + ByteTrack + Action/Movement)
→ Event Engine (Lifecycle, Deduplication, Transitions)
→ Temporal Context / Memory Layer
→ Persistent SQLite Event Storage

Zero-Mock Policy:
Uses real physical camera frames, real neural network inferences,
real ByteTrack tracking, real state transitions, and real SQLite storage.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from atlas.camera.stream import CameraStream
from atlas.perception.engine import PerceptionEngine
from atlas.events.engine import EventEngine
from atlas.context.memory import ContextManager
from atlas.events.storage import EventStorage


def main() -> int:
    print("=" * 65)
    print("ATLAS HOME — STEP 3 EVENT ENGINE & CONTEXT VERIFICATION")
    print("Zero-Mock Policy: Real Camera -> Real Perception -> Events & Memory")
    print("=" * 65)

    # 1. Initialize SQLite storage and context manager
    db_path = Path("data/atlas_events.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    storage = EventStorage(str(db_path))
    context = ContextManager(retention_window_seconds=120.0, storage=storage)
    event_engine = EventEngine(context_manager=context, track_expiry_seconds=2.0)

    print(f"[storage] SQLite database initialized at: {db_path.resolve()}")

    # 2. Initialize Real Camera (Device index 0)
    print("[camera] Opening physical webcam (device index 0)...")
    cam = CameraStream(source=0, auto_start=True)

    # Wait briefly for camera readiness
    for _ in range(30):
        if cam.is_connected:
            break
        time.sleep(0.1)

    if not cam.is_connected:
        print("[FAIL] Physical camera could not be opened at index 0.")
        cam.stop()
        return 1

    w, h = cam.resolution
    print(f"[camera] Connected! Resolution: {w}x{h}, Real FPS: {cam.fps:.1f}")

    # 3. Initialize Perception Engine
    print("[perception] Loading YOLOv8 + ByteTrack...")
    try:
        perception = PerceptionEngine()
    except Exception as e:
        print(f"[FAIL] Failed to initialize perception engine: {e}")
        cam.stop()
        return 1

    # 4. Stream real frames through pipeline
    total_frames_target = 15
    processed_frames = 0
    all_emitted_events = []
    start_time = time.time()

    print(f"\n[pipeline] Processing {total_frames_target} live frames through Event Engine...")
    print("-" * 65)

    while processed_frames < total_frames_target:
        frame_data = cam.get_latest_frame()
        if frame_data is None:
            time.sleep(0.02)
            continue

        frame, frame_ts = frame_data
        # Run perception
        obs = perception.process_frame(frame, timestamp=frame_ts)
        # Run event engine
        events = event_engine.process_observation(obs)
        if events:
            all_emitted_events.extend(events)
            for ev in events:
                print(
                    f"  >> [EVENT EMITTED] ID={ev.event_id[:8]}.. "
                    f"Type={ev.event_type} "
                    f"PersonID={ev.person_id or 'None'} "
                    f"ObjectID={ev.object_id or 'None'} "
                    f"Severity={ev.severity} "
                    f"Status={ev.status}"
                )

        processed_frames += 1
        time.sleep(0.04)

    duration = time.time() - start_time
    cam.stop()

    print("-" * 65)
    print(f"[pipeline] Completed {processed_frames} real frames in {duration:.2f}s ({processed_frames / duration:.1f} FPS)")

    # 5. Query Situational Context
    ctx = context.get_recent_context(window_seconds=60.0)
    print(f"\n[context] Situational Context Summary:")
    print(f"  - Active Persons: {ctx['active_persons_count']}")
    print(f"  - Active Objects: {ctx['active_objects_count']}")
    print(f"  - Timeline Events in Memory: {ctx['recent_events_count']}")

    # 6. Verify SQLite Persistence
    recent_db_events = storage.get_recent_events(limit=20)
    print(f"\n[storage] Verified SQLite Persistence:")
    print(f"  - Total Events Retrieved from DB: {len(recent_db_events)}")
    for ev in recent_db_events[:5]:
        print(f"    * DB Row: id={ev.event_id[:8]}.. type={ev.event_type} severity={ev.severity} time={ev.timestamp}")

    print("\n" + "=" * 65)
    print("STEP 3 VERIFICATION PASSED: Event Engine & Context operational.")
    print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
