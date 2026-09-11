"""Automated tests for synchronized companion control interface and failure modes.

Verifies:
1. "Hey ATLAS" query executes:
   Admin UI -> existing ATLAS Core -> "Hey! It's good to see you." -> emotion Happy -> OLED/servo -> physical speech -> Robot Speaking -> Robot Idle.
2. Failure mode: ESP32 disconnected / COM port unavailable.
3. Failure mode: TTS / Audio conversion failure.
4. Failure mode: ATLAS Core failure.
5. Zero fabrication of answers or telemetry.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from atlas.authority.models import Actor, Role
from atlas.chat.service import ATLASChatService, get_chat_service
from atlas.companion.bridge import CompanionSerialBridge
from atlas.companion.models import CompanionState, CompanionStatus
from atlas.companion.service import CompanionService


class TestCompanionSynchronizedControl(unittest.TestCase):
    """Test suite for synchronized companion control interface and failure handling."""

    def setUp(self):
        self.mock_bridge = MagicMock(spec=CompanionSerialBridge)
        self.mock_bridge.is_connected = True
        self.mock_bridge.port = "COM5"
        self.mock_bridge.baudrate = 921600
        self.mock_bridge.current_state = CompanionState.IDLE
        self.mock_bridge.is_speaking = False
        self.mock_bridge.active_task = None
        self.mock_bridge.bytes_sent = 100
        self.mock_bridge.bytes_received = 50
        self.mock_bridge.packets_sent = 5
        self.mock_bridge.packets_received = 2
        self.mock_bridge.audio_bytes_sent = 0
        self.mock_bridge.voice_packets_sent = 0
        self.mock_bridge.last_seen_iso = "2026-09-11T00:00:00Z"
        self.mock_bridge.last_error = None
        self.mock_bridge.firmware_banner = "ATLAS 16K AUDIO READY"
        self.mock_bridge._activities = []

        self.mock_chat = MagicMock(spec=ATLASChatService)
        self.service = CompanionService(bridge=self.mock_bridge, chat_service=self.mock_chat)
        self.admin = Actor(actor_id="usr_admin", display_name="Priya", role=Role.ADMIN)

    def test_hey_atlas_grounded_response_and_happy_emotion(self):
        """When Admin sends 'Hey ATLAS', system queries ATLAS Core, resolves Happy emotion, and speaks."""
        # 1. Existing ATLAS Core returns actual greeting response
        self.mock_chat.query.return_value = {
            "answer": "Hey! It's good to see you.",
            "confidence_status": "ANSWERABLE",
            "citations": [],
            "why_atlas_said_this": {"rules_triggered": ["GREETING_RULE"]},
        }

        with patch("atlas.companion.tts.text_to_speech_packets") as mock_tts:
            mock_tts.return_value = [(b"\x80" * 16000, 1.0)]
            result = self.service.chat_and_react("Hey ATLAS", self.admin)

        # 2. Verify ATLAS Core was called with exact prompt
        self.mock_chat.query.assert_called_once_with("Hey ATLAS", self.admin)

        # 3. Verify actual response
        self.assertEqual(result["answer"], "Hey! It's good to see you.")
        self.assertEqual(result["state"], CompanionState.HAPPY.value)
        self.assertEqual(result["affective_state"], "Happy")
        self.assertEqual(result["robot_state"], "Speaking")
        self.assertTrue(result["is_speaking"])
        self.assertTrue(result["dispatched_to_hardware"])
        self.assertEqual(result["audio_pcm_bytes"], 16000)

        # 4. Verify physical ESP32 state was dispatched (OLED + servo reaction)
        self.mock_bridge.set_state.assert_any_call(CompanionState.HAPPY)

    def test_atlas_chat_service_greeting_direct_response(self):
        """Test that the existing ATLASChatService natively responds dynamically to 'Hey ATLAS'."""
        chat_service = get_chat_service()
        res = chat_service.query("Hey ATLAS", self.admin)
        self.assertTrue(bool(res.get("text") or res.get("answer")))
        self.assertEqual(res["confidence_status"], "ANSWERABLE")

    def test_failure_mode_esp32_disconnected(self):
        """When ESP32 is disconnected, ATLAS Core still responds honestly, but hardware offline is reported."""
        self.mock_bridge.is_connected = False
        self.mock_chat.query.return_value = {
            "answer": "Hey! It's good to see you.",
            "confidence_status": "ANSWERABLE",
            "citations": [],
            "why_atlas_said_this": {},
        }

        result = self.service.chat_and_react("Hey ATLAS", self.admin)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["answer"], "Hey! It's good to see you.")
        self.assertEqual(result["state"], "Happy")
        self.assertEqual(result["robot_state"], "Offline (Disconnected)")
        self.assertFalse(result["dispatched_to_hardware"])
        self.assertIn("disconnected", result["hardware_error"].lower())
        self.mock_bridge.log_activity.assert_any_call(
            "SYS", "ERROR", "Speech playback skipped: ESP32 hardware disconnected on COM5"
        )

    def test_failure_mode_tts_failure(self):
        """When TTS or audio conversion fails, system reports error without pretending robot spoke."""
        self.mock_chat.query.return_value = {
            "answer": "Hey! It's good to see you.",
            "confidence_status": "ANSWERABLE",
            "citations": [],
            "why_atlas_said_this": {},
        }

        with patch("atlas.companion.tts.text_to_speech_packets", side_effect=RuntimeError("SAPI COM initialization error")):
            result = self.service.chat_and_react("Hey ATLAS", self.admin)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["answer"], "Hey! It's good to see you.")
        self.assertFalse(result["dispatched_to_hardware"])
        self.assertFalse(result["is_speaking"])
        self.assertEqual(result["robot_state"], "TTS_FAILED")
        self.assertIn("SAPI COM initialization error", result["audio_error"])

    def test_failure_mode_atlas_core_failure(self):
        """When ATLAS Core encounters an unexpected exception, error is reported without fake response."""
        self.mock_chat.query.side_effect = RuntimeError("Database perception pipeline timeout")

        result = self.service.chat_and_react("Hey ATLAS", self.admin)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "ATLAS_CORE_FAILURE")
        self.assertIn("Database perception pipeline timeout", result["details"])
        self.assertIsNone(result["answer"])
        self.assertEqual(result["state"], CompanionState.CONFUSED.value)
        self.assertEqual(result["robot_state"], "Confused")
        self.assertFalse(result["dispatched_to_hardware"])
        self.mock_bridge.set_state.assert_called_with(CompanionState.CONFUSED)


if __name__ == "__main__":
    unittest.main()
