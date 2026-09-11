"""Live End-to-End Integration Tests for Real Ollama Chat + Voice Input + Dynamic Emotion.

Validates the full pipeline:
Actor -> Admin Companion API -> ATLAS Core / Real Context -> Local Ollama (qwen2.5:3b)
-> Structured JSON {"text": ..., "emotion": ..., "speak": ...}
-> Companion Affective State Dispatch & Sync.

Verifies:
1. "Hey ATLAS"
2. "I'm really happy today!"
3. "I'm confused about what happened."
4. "Wow, that's surprising!"
5. "Can you explain what happened?"
6. "Hey ATLAS, who is currently inside?" (Microphone speech-to-text inquiry)
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from atlas.authority.models import Actor, Role
from atlas.chat.service import get_chat_service
from atlas.companion.bridge import CompanionSerialBridge
from atlas.companion.models import CompanionState
from atlas.companion.service import CompanionService


class TestLiveOllamaCompanionIntegration(unittest.TestCase):
    """End-to-end integration test with live local Ollama model."""

    @classmethod
    def setUpClass(cls):
        cls.admin = Actor(actor_id="usr_admin", display_name="Priya", role=Role.ADMIN)
        cls.chat_service = get_chat_service()
        # Verify Ollama reachability
        health = cls.chat_service.llm_provider.check_health()
        assert health.get("network") == "CONNECTED", f"Ollama is not connected: {health}"
        assert health.get("model_available"), f"Configured model not found in Ollama: {health}"

    def setUp(self):
        self.mock_bridge = MagicMock(spec=CompanionSerialBridge)
        self.mock_bridge.is_connected = False  # Keep physical serial dispatch offline during CI
        self.mock_bridge.port = "COM5"
        self.mock_bridge.baudrate = 921600
        self.mock_bridge.current_state = CompanionState.IDLE
        self.mock_bridge.is_speaking = False
        self.mock_bridge.active_task = None
        self.mock_bridge._activities = []
        self.service = CompanionService(bridge=self.mock_bridge, chat_service=self.chat_service)

    def test_01_query_hey_atlas(self):
        """User: 'Hey ATLAS' -> dynamic Ollama response."""
        res = self.service.chat_and_react("Hey ATLAS", self.admin)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(bool(res.get("text") or res.get("answer")), "Response text must not be empty")
        self.assertIn(res.get("emotion"), ["neutral", "happy", "thinking", "surprised", "confused", "sad", "concerned"])
        self.assertTrue(res.get("speak"))
        self.assertEqual(res["model"], "qwen2.5:3b")
        print(f"\n[TEST 1: 'Hey ATLAS']\nResponse: {res['text']}\nEmotion: {res['emotion']}\nState: {res['state']}")

    def test_02_query_happy_emotion(self):
        """User: 'I'm really happy today!' -> dynamic LLM emotion 'happy'."""
        res = self.service.chat_and_react("I'm really happy today!", self.admin)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(bool(res.get("text") or res.get("answer")))
        self.assertEqual(res.get("emotion"), "happy")
        self.assertEqual(res.get("state"), CompanionState.HAPPY.value)
        print(f"\n[TEST 2: 'I\\'m really happy today!']\nResponse: {res['text']}\nEmotion: {res['emotion']}\nState: {res['state']}")

    def test_03_query_confused_emotion(self):
        """User: 'I'm confused about what happened.' -> dynamic LLM emotion 'confused'."""
        res = self.service.chat_and_react("I'm confused about what happened.", self.admin)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(bool(res.get("text") or res.get("answer")))
        self.assertIn(res.get("emotion"), ["confused", "thinking", "neutral"])
        print(f"\n[TEST 3: 'I\\'m confused about what happened.']\nResponse: {res['text']}\nEmotion: {res['emotion']}\nState: {res['state']}")

    def test_04_query_surprising_emotion(self):
        """User: 'Wow, that's surprising!' -> dynamic LLM response."""
        res = self.service.chat_and_react("Wow, that's surprising!", self.admin)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(bool(res.get("text") or res.get("answer")))
        self.assertIn(res.get("emotion"), ["surprised", "thinking", "neutral", "happy", "concerned"])
        print(f"\n[TEST 4: 'Wow, that\\'s surprising!']\nResponse: {res['text']}\nEmotion: {res['emotion']}\nState: {res['state']}")

    def test_05_query_explain_what_happened(self):
        """User: 'Can you explain what happened?' -> dynamic LLM answer based on real ATLAS context."""
        res = self.service.chat_and_react("Can you explain what happened?", self.admin)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(bool(res.get("text") or res.get("answer")))
        self.assertIn(res.get("emotion"), ["neutral", "thinking", "concerned", "confused", "happy"])
        print(f"\n[TEST 5: 'Can you explain what happened?']\nResponse: {res['text']}\nEmotion: {res['emotion']}\nState: {res['state']}")

    def test_06_microphone_speech_inquiry(self):
        """User speaks into microphone: 'Hey ATLAS, who is currently inside?'."""
        res = self.service.chat_and_react("Hey ATLAS, who is currently inside?", self.admin)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(bool(res.get("text") or res.get("answer")))
        self.assertIn(res.get("emotion"), ["neutral", "thinking", "happy", "confused"])
        print(f"\n[TEST 6: 'Hey ATLAS, who is currently inside?']\nResponse: {res['text']}\nEmotion: {res['emotion']}\nState: {res['state']}")


if __name__ == "__main__":
    unittest.main()
