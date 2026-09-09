"""Abstract base class for camera capture interfaces in ATLAS."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Tuple
import numpy as np


class CameraState(str, Enum):
    """Camera connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class BaseCameraStream(ABC):
    """Standardized camera capture interface for ATLAS."""

    @abstractmethod
    def start(self) -> None:
        """Start the camera capture stream."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the camera capture stream and release resources."""
        pass

    @abstractmethod
    def get_latest_frame(self) -> Tuple[np.ndarray, float] | None:
        """Return the latest acquired real frame and its capture timestamp (epoch seconds).
        
        Returns None if no frame has been acquired or if camera is disconnected.
        Under no circumstances does this method return synthetic or mock frames.
        """
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """True if the camera is actively connected and delivering frames."""
        pass

    @property
    @abstractmethod
    def connection_state(self) -> CameraState:
        """Current detailed connection state."""
        pass

    @property
    @abstractmethod
    def last_frame_timestamp(self) -> float | None:
        """Monotonic or epoch timestamp of the latest real frame captured."""
        pass

    @property
    @abstractmethod
    def fps(self) -> float:
        """Current real-time frame rate."""
        pass

    @property
    @abstractmethod
    def resolution(self) -> Tuple[int, int] | None:
        """Current (width, height) resolution of the camera stream."""
        pass

    @property
    @abstractmethod
    def error_message(self) -> str | None:
        """Last encountered error description, or None."""
        pass

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        """Return status summary dictionary for API or diagnostics."""
        pass
