"""Threaded Real-Time Camera Stream Abstraction for ATLAS Home.

Supports DroidCam (HTTP / MJPEG / RTSP) and local camera indices.
Runs background threaded acquisition to drain driver buffers and provide
real-time, zero-latency access to the latest real camera frame.
Includes automatic discovery and fallback across standard DroidCam endpoints
(/video, /mjpegfeed, /video/force) and multi-IP configurations.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any, Tuple, Union
from urllib.parse import urlparse
import socket
import cv2
import numpy as np
import requests

from atlas.camera.base import BaseCameraStream, CameraState

logger = logging.getLogger("atlas.camera")


def parse_and_expand_sources(raw_source: Union[str, int]) -> list[Union[str, int]]:
    """Expand a raw camera source into prioritized candidate endpoints.
    
    Supports:
    - Integer device indices: 0, 1, 2
    - Comma-separated sources/IPs: "192.168.1.100, 10.178.179.140"
    - DroidCam URLs: generates /video, /mjpegfeed, /video/force fallback routes.
    """
    if isinstance(raw_source, int):
        return [raw_source]

    raw_str = str(raw_source).strip()
    if raw_str.isdigit():
        return [int(raw_str)]

    tokens = [t.strip() for t in raw_str.replace(";", ",").split(",") if t.strip()]
    candidates: list[Union[str, int]] = []

    for token in tokens:
        if token.isdigit():
            candidates.append(int(token))
            continue

        url = token
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("rtsp://")):
            url = f"http://{url}"

        if url.startswith("http://") or url.startswith("https://"):
            parsed = urlparse(url)
            port_str = f":{parsed.port}" if parsed.port else ":4747"
            base = f"{parsed.scheme}://{parsed.hostname}{port_str}"

            path = parsed.path
            ordered_paths: list[str] = []
            if path in ("/video", "/mjpegfeed", "/video/force"):
                ordered_paths.append(path)

            for p in ("/video", "/mjpegfeed", "/video/force"):
                if p not in ordered_paths:
                    ordered_paths.append(p)

            for p in ordered_paths:
                full_cand = f"{base}{p}"
                if full_cand not in candidates:
                    candidates.append(full_cand)
        else:
            if url not in candidates:
                candidates.append(url)

    return candidates


class CameraStream(BaseCameraStream):
    """Threaded OpenCV capture client with auto-discovery and zero-mock enforcement."""

    def __init__(
        self,
        source: Union[str, int],
        retry_interval: float = 3.0,
        buffer_size: int = 1,
        auto_start: bool = False,
    ) -> None:
        self.source = source
        self.retry_interval = retry_interval
        self.buffer_size = buffer_size
        self._candidates = parse_and_expand_sources(source)
        self._active_source: Union[str, int] = self._candidates[0] if self._candidates else source

        self._state = CameraState.DISCONNECTED
        self._error_message: str | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False

        self._latest_frame: np.ndarray | None = None
        self._last_frame_timestamp: float | None = None
        self._resolution: Tuple[int, int] | None = None
        self._fps: float = 0.0
        self._frame_count: int = 0
        self._last_fps_calc_time: float = time.time()
        self._fps_counter: int = 0

        if auto_start:
            self.start()

    def start(self) -> None:
        """Start the background frame capture worker thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._state = CameraState.CONNECTING
            self._error_message = None

        self._thread = threading.Thread(
            target=self._capture_worker,
            name="AtlasCameraWorker",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[camera] Started capture thread for source candidates: {self._candidates}")

    def stop(self) -> None:
        """Stop capture worker thread and release hardware/network resources."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

        with self._lock:
            self._state = CameraState.DISCONNECTED
            self._latest_frame = None
            self._last_frame_timestamp = None
            self._fps = 0.0
        logger.info("[camera] Stopped camera capture stream.")

    def _open_capture_for_candidate(self, cand: Union[str, int]) -> Tuple[cv2.VideoCapture | None, str | None]:
        """Attempt to open and verify a single stream candidate."""
        cand_repr = f"device {cand}" if isinstance(cand, int) else f"URL '{cand}'"

        # Pre-check HTTP URL with lightweight request if applicable
        if isinstance(cand, str) and cand.startswith("http"):
            try:
                resp = requests.get(cand, timeout=2.0, stream=True)
                content_type = resp.headers.get("Content-Type", "").lower()
                if resp.status_code == 200 and "text/html" in content_type:
                    # Check if body indicates busy or inactive
                    body_preview = resp.raw.read(1024).decode("utf-8", errors="ignore")
                    if "droidcam_busy" in body_preview or "DroidCam is Busy" in body_preview:
                        return None, f"DroidCam is Busy on {cand_repr} (Desktop client or another app is connected)."
                    if "video_inactive" in body_preview:
                        return None, f"DroidCam video is inactive on {cand_repr}."
                elif resp.status_code == 404:
                    return None, f"Endpoint not found (404) at {cand_repr}."
            except Exception as e:
                return None, f"Network handshake failed for {cand_repr}: {e}"

        try:
            if isinstance(cand, str):
                params = []
                if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
                    params.extend([cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000])
                if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
                    params.extend([cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000])

                if hasattr(cv2, "CAP_FFMPEG"):
                    cap = cv2.VideoCapture(cand, cv2.CAP_FFMPEG, params)
                else:
                    cap = cv2.VideoCapture(cand)
            else:
                # Integer camera device index (e.g. 0 for local integrated webcam)
                cap = None
                if (sys.platform.startswith("win") or os.name == "nt") and hasattr(cv2, "CAP_DSHOW"):
                    try:
                        cap = cv2.VideoCapture(cand, cv2.CAP_DSHOW)
                        if not cap.isOpened():
                            cap.release()
                            cap = None
                    except Exception as e:
                        logger.warning(f"[camera] DirectShow open failed for device {cand}: {e}")
                        cap = None

                # Fallback to standard backend if DirectShow was not opened or not supported
                if cap is None:
                    cap = cv2.VideoCapture(cand)

            if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, self.buffer_size)
                except Exception:
                    pass

            if not cap.isOpened():
                cap.release()
                return None, f"Failed to open video capture for {cand_repr}."

            # Verify that we can actually read 1 real frame from this capture
            ret, test_frame = cap.read()
            if not ret or test_frame is None or test_frame.size == 0:
                cap.release()
                return None, f"Stream opened for {cand_repr} but returned empty frame."

            # Re-seed latest frame with the verified read
            h, w = test_frame.shape[:2]
            with self._lock:
                self._latest_frame = test_frame
                self._last_frame_timestamp = time.time()
                self._resolution = (w, h)
                self._frame_count += 1
                self._active_source = cand

            return cap, None

        except Exception as exc:
            return None, f"Exception opening {cand_repr}: {exc}"

    def _open_capture(self) -> cv2.VideoCapture | None:
        """Attempt candidates in order until a working real stream is found."""
        last_error = "No camera candidates configured."
        busy_error: str | None = None
        dead_hosts: set[str] = set()

        for cand in self._candidates:
            # If this is a network URL and host:port was already determined unreachable, skip immediately
            if isinstance(cand, str) and cand.startswith("http"):
                parsed = urlparse(cand)
                host = parsed.hostname or "127.0.0.1"
                port = parsed.port or 4747
                host_key = f"{host}:{port}"
                if host_key in dead_hosts:
                    continue

                # Fast 1.0s socket pre-check for new hosts
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                try:
                    is_open = sock.connect_ex((host, port)) == 0
                except Exception:
                    is_open = False
                finally:
                    try:
                        sock.close()
                    except Exception:
                        pass

                if not is_open:
                    dead_hosts.add(host_key)
                    last_error = f"Host port {host_key} unreachable."
                    logger.warning(f"[camera] {last_error}")
                    continue

            logger.info(f"[camera] Probing candidate: {cand}")
            cap, err = self._open_capture_for_candidate(cand)
            if cap is not None:
                logger.info(f"[camera] Connected successfully to: {cand}")
                return cap
            last_error = err or f"Failed to connect to {cand}"
            if err and ("Busy" in err or "busy" in err):
                busy_error = err
            logger.warning(f"[camera] Candidate probe failed: {last_error}")

        with self._lock:
            self._state = CameraState.ERROR
            self._error_message = busy_error or last_error
            self._latest_frame = None

        return None

    def _capture_worker(self) -> None:
        """Continuous frame grabber running in background thread."""
        while self._running:
            cap = self._open_capture()
            if cap is None:
                sleep_end = time.time() + self.retry_interval
                while self._running and time.time() < sleep_end:
                    time.sleep(0.2)
                continue

            with self._lock:
                self._state = CameraState.CONNECTED
                self._error_message = None
                self._last_fps_calc_time = time.time()
                self._fps_counter = 0

            try:
                while self._running:
                    ret, frame = cap.read()
                    now = time.time()

                    if not ret or frame is None or frame.size == 0:
                        err = f"Lost frame from camera stream ({self._active_source}). Stream interrupted."
                        logger.warning(f"[camera] {err}")
                        with self._lock:
                            self._state = CameraState.ERROR
                            self._error_message = err
                            self._latest_frame = None
                        break

                    self._fps_counter += 1
                    elapsed = now - self._last_fps_calc_time
                    if elapsed >= 1.0:
                        self._fps = round(self._fps_counter / elapsed, 2)
                        self._fps_counter = 0
                        self._last_fps_calc_time = now

                    h, w = frame.shape[:2]

                    with self._lock:
                        self._latest_frame = frame
                        self._last_frame_timestamp = now
                        self._resolution = (w, h)
                        self._frame_count += 1
                        self._state = CameraState.CONNECTED
                        self._error_message = None

            except Exception as exc:
                err = f"Unexpected error during frame read: {exc}"
                logger.error(f"[camera] {err}")
                with self._lock:
                    self._state = CameraState.ERROR
                    self._error_message = err
                    self._latest_frame = None
            finally:
                try:
                    cap.release()
                except Exception:
                    pass

            if self._running:
                logger.info(f"[camera] Will retry candidate connections in {self.retry_interval}s...")
                time.sleep(self.retry_interval)

    def get_latest_frame(self) -> Tuple[np.ndarray, float] | None:
        """Return a copy of the latest acquired real frame and timestamp.
        
        Strict Real Data Policy: Returns None if no real frame is available.
        Never generates fake or mock frames.
        """
        with self._lock:
            if self._latest_frame is None or self._last_frame_timestamp is None:
                return None
            return self._latest_frame.copy(), self._last_frame_timestamp

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._state == CameraState.CONNECTED and self._latest_frame is not None

    @property
    def connection_state(self) -> CameraState:
        with self._lock:
            return self._state

    @property
    def active_source(self) -> Union[str, int]:
        with self._lock:
            return self._active_source

    @property
    def last_frame_timestamp(self) -> float | None:
        with self._lock:
            return self._last_frame_timestamp

    @property
    def fps(self) -> float:
        with self._lock:
            return self._fps

    @property
    def resolution(self) -> Tuple[int, int] | None:
        with self._lock:
            return self._resolution

    @property
    def frame_count(self) -> int:
        with self._lock:
            return self._frame_count

    @property
    def error_message(self) -> str | None:
        with self._lock:
            return self._error_message

    def get_status(self) -> dict[str, Any]:
        """Return a comprehensive summary of camera connection and metrics."""
        with self._lock:
            return {
                "source": str(self.source),
                "active_source": str(self._active_source),
                "candidates": [str(c) for c in self._candidates],
                "is_connected": self._state == CameraState.CONNECTED and self._latest_frame is not None,
                "state": self._state.value,
                "resolution": {
                    "width": self._resolution[0] if self._resolution else None,
                    "height": self._resolution[1] if self._resolution else None,
                } if self._resolution else None,
                "fps": self._fps,
                "total_frames_received": self._frame_count,
                "last_frame_timestamp": self._last_frame_timestamp,
                "error_message": self._error_message,
            }


_global_camera: CameraStream | None = None


def get_camera_stream(source: Union[str, int] | None = None) -> CameraStream:
    """Get or initialize singleton camera stream."""
    global _global_camera
    if _global_camera is None:
        if source is None:
            from atlas.config.settings import get_settings
            source = get_settings().camera_source
        _global_camera = CameraStream(source=source, auto_start=True)
    return _global_camera
