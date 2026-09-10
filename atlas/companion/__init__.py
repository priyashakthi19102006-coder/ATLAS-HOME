"""ATLAS Home Physical Companion Module.

Bridges ATLAS Core intelligence and physical ESP32 companion hardware via USB serial.
"""

from atlas.companion.models import CompanionState, CompanionStatus, CompanionActivity
from atlas.companion.bridge import CompanionSerialBridge, get_companion_bridge
from atlas.companion.service import CompanionService, get_companion_service

__all__ = [
    "CompanionState",
    "CompanionStatus",
    "CompanionActivity",
    "CompanionSerialBridge",
    "get_companion_bridge",
    "CompanionService",
    "get_companion_service",
]
