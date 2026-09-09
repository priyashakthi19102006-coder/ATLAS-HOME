"""Tests for ATLAS Camera Abstraction and Zero-Mock Enforcement."""

import time
from atlas.camera.base import CameraState
from atlas.camera.stream import CameraStream


def test_initial_disconnected_state():
    stream = CameraStream(source="http://127.0.0.1:9999/dummy", auto_start=False)
    assert stream.connection_state == CameraState.DISCONNECTED
    assert stream.is_connected is False
    assert stream.last_frame_timestamp is None
    # STRICT ZERO-MOCK REQUIREMENT: Must be None, never a synthetic image
    assert stream.get_latest_frame() is None


def test_failed_connection_reports_error_and_no_mock_frames():
    # Attempt connecting to an unreachable port
    stream = CameraStream(
        source="http://127.0.0.1:9999/nonexistent_stream",
        retry_interval=1.0,
        auto_start=True,
    )

    try:
        # Wait up to 6 seconds for worker thread to fail connection
        start_wait = time.time()
        while time.time() - start_wait < 6.0:
            if stream.connection_state == CameraState.ERROR:
                break
            time.sleep(0.2)

        # State should be ERROR, not CONNECTED
        assert stream.connection_state == CameraState.ERROR
        assert stream.is_connected is False

        # STRICT ZERO-MOCK: Must NEVER return mock or synthetic frames
        assert stream.get_latest_frame() is None
        assert stream.error_message is not None

        status = stream.get_status()
        assert status["is_connected"] is False
        assert status["state"] == "error"
    finally:
        stream.stop()
        assert stream.connection_state == CameraState.DISCONNECTED
