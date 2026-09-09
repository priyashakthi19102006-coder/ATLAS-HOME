"""Source Registry and Subsystem Availability Model for ATLAS Home (Step 6.5).

Tracks real hardware status without fabricating sensors.
Current live runtime:
- CAMERA: dynamically reports physical camera device 0 state.
- IMU, GPS, WEARABLE, GLASSES, DRONE, ROBOT, ENVIRONMENTAL_SENSOR:
  Reported explicitly as NOT_CONFIGURED or UNAVAILABLE.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from atlas.camera.base import CameraState
from atlas.fusion.schema import SourceAvailability, SourceType

logger = logging.getLogger("atlas.fusion.sources")


class SourceRegistry:
    """Registry tracking sensing subsystems and their genuine runtime availability."""

    def __init__(self, camera_stream: Any | None = None) -> None:
        self._lock = threading.Lock()
        self._camera_stream = camera_stream
        
        # Explicit hardware adapters registry
        # Only sources with real verified connections may be marked AVAILABLE
        self._adapters: dict[str, dict[str, Any]] = {
            SourceType.IMU.value: {
                "source_type": SourceType.IMU,
                "source_id": "imu_none",
                "availability": SourceAvailability.NOT_CONFIGURED,
                "details": {"description": "Inertial Measurement Unit hardware adapter not configured."},
            },
            SourceType.GPS.value: {
                "source_type": SourceType.GPS,
                "source_id": "gps_none",
                "availability": SourceAvailability.NOT_CONFIGURED,
                "details": {"description": "GPS receiver hardware adapter not configured."},
            },
            SourceType.ENVIRONMENTAL_SENSOR.value: {
                "source_type": SourceType.ENVIRONMENTAL_SENSOR,
                "source_id": "env_none",
                "availability": SourceAvailability.NOT_CONFIGURED,
                "details": {"description": "Environmental sensor bus not configured."},
            },
            SourceType.WEARABLE.value: {
                "source_type": SourceType.WEARABLE,
                "source_id": "wearable_none",
                "availability": SourceAvailability.NOT_CONFIGURED,
                "details": {"description": "Personal biometric/wearable link not configured."},
            },
            SourceType.GLASSES.value: {
                "source_type": SourceType.GLASSES,
                "source_id": "glasses_none",
                "availability": SourceAvailability.NOT_CONFIGURED,
                "details": {"description": "Smart glasses vision interface not configured."},
            },
            SourceType.DRONE.value: {
                "source_type": SourceType.DRONE,
                "source_id": "drone_none",
                "availability": SourceAvailability.NOT_CONFIGURED,
                "details": {"description": "Aerial telemetry interface not configured."},
            },
            SourceType.ROBOT.value: {
                "source_type": SourceType.ROBOT,
                "source_id": "robot_none",
                "availability": SourceAvailability.NOT_CONFIGURED,
                "details": {"description": "Ground robot telemetry interface not configured."},
            },
        }

    @property
    def camera(self) -> Any:
        if self._camera_stream is None:
            from atlas.camera.stream import get_camera_stream
            self._camera_stream = get_camera_stream()
        return self._camera_stream

    def get_camera_status(self) -> dict[str, Any]:
        """Query real live hardware status of the primary camera stream."""
        cam = self.camera
        if cam is None:
            return {
                "source_type": SourceType.CAMERA,
                "source_id": "camera_none",
                "availability": SourceAvailability.UNAVAILABLE,
                "is_connected": False,
                "details": {"error": "Camera stream driver not initialized."},
            }

        try:
            stat = cam.get_status()
            is_conn = bool(stat.get("is_connected", False))
            raw_state = stat.get("state", "")
            state_str = raw_state.value if hasattr(raw_state, "value") else str(raw_state)

            if is_conn and state_str in ("connected", "CameraState.CONNECTED"):
                avail = SourceAvailability.AVAILABLE
            elif state_str in ("connecting", "CameraState.CONNECTING"):
                avail = SourceAvailability.DEGRADED
            else:
                avail = SourceAvailability.DISCONNECTED

            return {
                "source_type": SourceType.CAMERA,
                "source_id": f"camera_{stat.get('source', '0')}",
                "availability": avail,
                "is_connected": is_conn,
                "details": {
                    "source": str(stat.get("source", "0")),
                    "fps": stat.get("fps", 0.0),
                    "resolution": stat.get("resolution", {}),
                    "state": state_str,
                    "last_frame_timestamp": stat.get("last_frame_timestamp"),
                },
            }
        except Exception as e:
            logger.warning("Error querying camera stream status: %s", e)
            return {
                "source_type": SourceType.CAMERA,
                "source_id": "camera_0",
                "availability": SourceAvailability.UNAVAILABLE,
                "is_connected": False,
                "details": {"error": str(e)},
            }

    def get_source_availability(self, source_type: SourceType | str) -> SourceAvailability:
        """Get availability for a given source category."""
        st_val = source_type.value if hasattr(source_type, "value") else str(source_type)
        if st_val == SourceType.CAMERA.value:
            return self.get_camera_status()["availability"]

        with self._lock:
            adapter = self._adapters.get(st_val)
            if adapter:
                return adapter["availability"]
            return SourceAvailability.NOT_CONFIGURED

    def get_sources_status(self) -> list[dict[str, Any]]:
        """Return comprehensive status list across all defined sensing subsystems."""
        sources: list[dict[str, Any]] = []

        # 1. Real physical camera
        cam_info = self.get_camera_status()
        sources.append({
            "source_type": cam_info["source_type"].value,
            "source_id": cam_info["source_id"],
            "availability": cam_info["availability"].value,
            "is_connected": cam_info["is_connected"],
            "details": cam_info["details"],
        })

        # 2. Configured or unconfigured subsystems
        with self._lock:
            for k, adapter in self._adapters.items():
                sources.append({
                    "source_type": adapter["source_type"].value,
                    "source_id": adapter["source_id"],
                    "availability": adapter["availability"].value,
                    "is_connected": adapter["availability"] == SourceAvailability.AVAILABLE,
                    "details": adapter["details"],
                })

        return sources

    def register_custom_source(
        self,
        source_type: SourceType,
        source_id: str,
        availability: SourceAvailability,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Register or update an explicit hardware source adapter (used for verified test or integration adapters)."""
        with self._lock:
            self._adapters[source_type.value] = {
                "source_type": source_type,
                "source_id": source_id,
                "availability": availability,
                "details": details or {},
            }


_global_registry: SourceRegistry | None = None
_registry_lock = threading.Lock()


def get_source_registry(camera_stream: Any | None = None) -> SourceRegistry:
    """Retrieve or initialize singleton SourceRegistry."""
    global _global_registry
    if _global_registry is None:
        with _registry_lock:
            if _global_registry is None:
                _global_registry = SourceRegistry(camera_stream=camera_stream)
    return _global_registry


def reset_global_source_registry() -> None:
    """Reset global registry for isolated test execution."""
    global _global_registry
    with _registry_lock:
        _global_registry = None
