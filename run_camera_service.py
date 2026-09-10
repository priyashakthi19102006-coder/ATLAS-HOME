"""Entrypoint to run ATLAS Home Camera Service and API.

Usage:
    python run_camera_service.py
"""

from __future__ import annotations

import logging
import sys

from atlas.api.app import create_app
from atlas.camera.stream import get_camera_stream
from atlas.config.settings import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("atlas.main")


def main() -> int:
    settings = get_settings()

    print("=" * 64)
    print("ATLAS HOME — CAMERA & INTELLIGENCE SERVICE (STEP 1)")
    print("=" * 64)
    print(f"Device ID:         {settings.device_id}")
    print(f"Configured Camera: {settings.raw_camera_url}")
    print(f"API Server Host:   {settings.api_host}:{settings.api_port}")
    print(f"Dashboard URL:     http://{settings.api_host}:{settings.api_port}/")
    print(f"Status Endpoint:   http://{settings.api_host}:{settings.api_port}/api/status")
    print(f"Camera Frame:      http://{settings.api_host}:{settings.api_port}/api/camera/frame")
    print(f"Diagnostics URL:   http://{settings.api_host}:{settings.api_port}/api/diagnostics")
    if getattr(settings, "companion_enabled", True):
        print(f"Companion Serial:  {settings.companion_port} @ {settings.companion_baud} baud")
    print("=" * 64)

    # Initialize physical companion serial bridge
    companion_bridge = None
    if getattr(settings, "companion_enabled", True):
        from atlas.companion.bridge import get_companion_bridge
        logger.info("Initializing ATLAS Companion serial bridge on %s...", settings.companion_port)
        companion_bridge = get_companion_bridge(port=settings.companion_port, baudrate=settings.companion_baud)
        companion_bridge.start()

    # Initialize the camera background capture thread
    logger.info("Initializing camera capture thread...")
    camera = get_camera_stream()

    # Start continuous background perception & event pipeline worker
    import threading
    import time

    class PipelineWorker(threading.Thread):
        def __init__(self, camera_stream, poll_interval: float = 0.04):
            super().__init__(daemon=True, name="AtlasPipelineWorker")
            self.camera = camera_stream
            self.poll_interval = poll_interval
            self._running = threading.Event()
            self._last_analysis_time = 0.0
            self.analysis_interval = 8.0

        def run(self):
            self._running.set()
            logger.info("PipelineWorker started: YOLOv8 + ByteTrack + EventEngine active.")
            try:
                from atlas.perception.engine import get_perception_engine
                from atlas.events.engine import get_event_engine
                from atlas.intelligence.pipeline import get_intelligence_pipeline
                from atlas.context.memory import get_context_manager

                perception = get_perception_engine()
                events_engine = get_event_engine()
                pipeline = get_intelligence_pipeline()
                context = get_context_manager()

                while self._running.is_set():
                    frame_data = self.camera.get_latest_frame()
                    if frame_data is None:
                        time.sleep(self.poll_interval)
                        continue

                    frame, frame_ts = frame_data
                    try:
                        obs = perception.process_frame(frame, timestamp=frame_ts)
                        new_events = events_engine.process_observation(obs)
                        now = time.time()
                        if new_events or (now - self._last_analysis_time >= self.analysis_interval):
                            self._last_analysis_time = now
                            recent_events = context.get_active_events() or context.get_recent_events(limit=10)
                            if recent_events or new_events:
                                pipeline.run_analysis(
                                    events=new_events or recent_events,
                                    source_health={
                                        "camera": {"is_connected": self.camera.is_connected, "source": "device_0"},
                                        "perception": {"status": "active"},
                                    },
                                )
                    except Exception as exc:
                        logger.debug("Error in pipeline worker iteration: %s", exc)

                    time.sleep(self.poll_interval)
            except Exception as exc:
                logger.error("Pipeline worker terminated unexpectedly: %s", exc)

        def stop(self):
            self._running.clear()

    worker = PipelineWorker(camera)
    worker.start()

    # Create and run Flask web server
    app = create_app(settings)

    try:
        app.run(
            host=settings.api_host,
            port=settings.api_port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    except KeyboardInterrupt:
        logger.info("Service interrupted by user. Shutting down...")
    finally:
        worker.stop()
        camera.stop()
        if companion_bridge:
            companion_bridge.stop()
        logger.info("Camera service, pipeline worker, and companion bridge shutdown complete.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
