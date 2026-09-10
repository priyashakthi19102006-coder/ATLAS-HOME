"""Companion Service orchestrating ATLAS Core intelligence and physical robot hardware."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from atlas.authority.models import Actor
from atlas.chat.service import ATLASChatService, get_chat_service
from atlas.companion.bridge import CompanionSerialBridge, get_companion_bridge
from atlas.companion.models import CompanionState, CompanionStatus

logger = logging.getLogger("atlas.companion.service")


class CompanionService:
    """Coordinates conversational AI inference and physical companion reaction."""

    def __init__(
        self,
        bridge: Optional[CompanionSerialBridge] = None,
        chat_service: Optional[ATLASChatService] = None,
    ):
        self.bridge = bridge or get_companion_bridge()
        self._chat_service = chat_service
        self._lock = threading.Lock()

    @property
    def chat_service(self) -> ATLASChatService:
        if self._chat_service is None:
            self._chat_service = get_chat_service()
        return self._chat_service

    def get_status(self) -> dict[str, Any]:
        """Return comprehensive hardware and companion status."""
        status: CompanionStatus = self.bridge.get_status()
        return status.model_dump()

    def set_state(self, state_str: str) -> dict[str, Any]:
        """Manually transition the companion state."""
        try:
            state = CompanionState(state_str)
        except ValueError:
            # Case-insensitive match
            for s in CompanionState:
                if s.value.lower() == state_str.lower():
                    state = s
                    break
            else:
                valid = [s.value for s in CompanionState]
                raise ValueError(f"Invalid companion state '{state_str}'. Valid states: {valid}")

        success = self.bridge.set_state(state)
        return {
            "status": "ok",
            "state": state.value,
            "dispatched_to_hardware": success,
            "is_connected": self.bridge.is_connected,
        }

    def chat_and_react(self, message: str, actor: Actor) -> dict[str, Any]:
        """Process user query via existing ATLAS Core and react via physical robot.

        1. Sets physical companion to Listening -> Thinking.
        2. Queries existing grounded ATLASChatService.
        3. Analyzes response context to choose emotion (Happy, Surprised, Confused, Speaking).
        4. Transmits speech and emotion to physical ESP32.
        5. Logs full trace and returns complete answer payload to UI.
        """
        clean_prompt = (message or "").strip()
        if not clean_prompt:
            raise ValueError("Message cannot be empty.")

        # 1. Transition to Thinking
        self.bridge.set_state(CompanionState.THINKING)
        self.bridge.active_task = f"Processing: {clean_prompt[:50]}"
        self.bridge.log_activity("IN", "USER_QUERY", f"Admin query: {clean_prompt}")

        try:
            # 2. Query EXISTING ATLAS Core intelligence
            t0 = time.time()
            chat_result = self.chat_service.query(clean_prompt, actor)
            elapsed = time.time() - t0

            answer = chat_result.get("answer", "")
            confidence_status = chat_result.get("confidence_status", "ANSWERABLE")

            # 3. Determine affective reaction
            lower_ans = answer.lower()
            if "incident" in lower_ans or "alert" in lower_ans or "warning" in lower_ans or "unauthorized" in lower_ans:
                reaction = CompanionState.SURPRISED
            elif "don't have enough" in lower_ans or "unclear" in lower_ans or "unknown" in lower_ans or confidence_status == "UNANSWERABLE":
                reaction = CompanionState.CONFUSED
            elif "secure" in lower_ans or "safe" in lower_ans or "normal" in lower_ans:
                reaction = CompanionState.HAPPY
            else:
                reaction = CompanionState.SPEAKING

            # 4. Transmit speech to physical ESP32
            self.bridge.set_state(CompanionState.SPEAKING)

            # Generate real TTS from the actual response text and convert to 16 kHz unsigned 8-bit PCM
            audio_dispatched = False
            total_duration = 0.0
            total_pcm_bytes = 0

            try:
                from atlas.companion.tts import text_to_speech_packets
                chunk_payloads = text_to_speech_packets(answer)
                for pcm_c, dur_c in chunk_payloads:
                    total_pcm_bytes += len(pcm_c)
                    total_duration += dur_c

                def _stream_sequence():
                    for pcm_c, dur_c in chunk_payloads:
                        if not self.bridge.is_connected:
                            break
                        self.bridge.send_voice_audio(pcm_c, duration=dur_c)
                        time.sleep(dur_c + 0.3)

                    # Briefly express mood reaction after speech playback completes before returning to idle
                    self.bridge.set_state(reaction)
                    time.sleep(3.0)
                    self.bridge.set_state(CompanionState.IDLE)
                    self.bridge.active_task = None

                threading.Thread(target=_stream_sequence, daemon=True, name="CompanionStateSettle").start()
                audio_dispatched = self.bridge.is_connected
            except Exception as tts_err:
                logger.error("TTS synthesis/streaming error: %s; falling back to text speak", tts_err)
                self.bridge.send_speak(answer)

            return {
                "status": "ok",
                "message": clean_prompt,
                "answer": answer,
                "confidence_status": confidence_status,
                "citations": chat_result.get("citations", []),
                "why_atlas_said_this": chat_result.get("why_atlas_said_this"),
                "technical_trace": chat_result.get("technical_trace"),
                "model": chat_result.get("model"),
                "companion_reaction": reaction.value,
                "dispatched_to_hardware": audio_dispatched if self.bridge.is_connected else False,
                "audio_duration_seconds": round(total_duration, 3),
                "audio_pcm_bytes": total_pcm_bytes,
                "latency_seconds": round(elapsed, 3),
            }
        except Exception as exc:
            logger.error("Error during companion chat interaction: %s", exc)
            self.bridge.set_state(CompanionState.CONFUSED)
            self.bridge.active_task = None
            raise

    def speak_text(self, text: str) -> dict[str, Any]:
        """Synthesize and stream direct text to ESP32 DAC using verified VOICE protocol."""
        clean = (text or "").strip()
        if not clean:
            raise ValueError("Text cannot be empty.")

        from atlas.companion.tts import text_to_speech_packets

        self.bridge.set_state(CompanionState.SPEAKING)
        chunk_payloads = text_to_speech_packets(clean)
        total_duration = sum(dur for _, dur in chunk_payloads)
        total_pcm_bytes = sum(len(pcm) for pcm, _ in chunk_payloads)

        def _stream_sequence():
            for pcm_c, dur_c in chunk_payloads:
                if not self.bridge.is_connected:
                    break
                self.bridge.send_voice_audio(pcm_c, duration=dur_c)
                time.sleep(dur_c + 0.3)
            self.bridge.set_state(CompanionState.IDLE)
            self.bridge.active_task = None

        threading.Thread(target=_stream_sequence, daemon=True, name="CompanionDirectSpeakStream").start()

        return {
            "status": "ok",
            "text": clean,
            "duration_seconds": round(total_duration, 3),
            "pcm_bytes": total_pcm_bytes,
            "chunks": len(chunk_payloads),
            "sample_rate": 16000,
            "format": "unsigned 8-bit mono PCM",
            "dispatched_to_hardware": self.bridge.is_connected,
            "is_connected": self.bridge.is_connected,
        }

    def test_companion(self) -> dict[str, Any]:
        """Execute physical self-test sequence on connected ESP32 companion."""
        self.bridge.active_task = "Running diagnostic self-test"
        self.bridge.log_activity("SYS", "TEST", "Beginning physical companion self-test routine")

        # Step 1: Send test trigger to hardware
        sent = self.bridge.send_test()

        # Step 2: Cycle states to verify motor / screen / LED reactions
        def _test_sequence():
            try:
                states_cycle = [
                    (CompanionState.LISTENING, 0.8),
                    (CompanionState.THINKING, 0.8),
                    (CompanionState.HAPPY, 1.0),
                    (CompanionState.SURPRISED, 0.8),
                    (CompanionState.SPEAKING, 1.0),
                    (CompanionState.IDLE, 0.5),
                ]
                for st, delay in states_cycle:
                    self.bridge.set_state(st)
                    time.sleep(delay)
                self.bridge.active_task = None
                self.bridge.log_activity("SYS", "TEST", "Physical self-test routine completed successfully")
            except Exception as e:
                logger.error("Error in companion test sequence: %s", e)
                self.bridge.set_state(CompanionState.IDLE)
                self.bridge.active_task = None

        threading.Thread(target=_test_sequence, daemon=True, name="CompanionTestRoutine").start()

        return {
            "status": "ok",
            "message": "Diagnostic self-test initiated on physical ATLAS companion.",
            "port": self.bridge.port,
            "baudrate": self.bridge.baudrate,
            "connected": self.bridge.is_connected,
            "dispatched_to_hardware": sent,
            "timestamp": time.time(),
        }


_global_companion_service: Optional[CompanionService] = None
_service_lock = threading.Lock()


def get_companion_service() -> CompanionService:
    """Return singleton CompanionService instance."""
    global _global_companion_service
    with _service_lock:
        if _global_companion_service is None:
            _global_companion_service = CompanionService()
        return _global_companion_service
