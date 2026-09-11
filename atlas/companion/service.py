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
        self._last_ollama_check = 0.0
        self._ollama_online = False
        self._ollama_model: Optional[str] = None

    @property
    def chat_service(self) -> ATLASChatService:
        if self._chat_service is None:
            self._chat_service = get_chat_service()
        return self._chat_service

    def _check_ollama_health(self) -> tuple[bool, Optional[str]]:
        """Non-blocking cached check for local Ollama service availability."""
        now = time.time()
        if now - self._last_ollama_check < 10.0:
            return self._ollama_online, self._ollama_model

        self._last_ollama_check = now
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.getcode() == 200:
                    import json
                    data = json.loads(resp.read().decode("utf-8"))
                    models = [m.get("name") or m.get("model") for m in data.get("models", []) if (m.get("name") or m.get("model"))]
                    self._ollama_online = True
                    self._ollama_model = models[0] if models else "qwen2.5:3b"
                    return True, self._ollama_model
        except Exception:
            self._ollama_online = False
            self._ollama_model = None

        return self._ollama_online, self._ollama_model

    def get_status(self) -> dict[str, Any]:
        """Return comprehensive hardware, Ollama, and companion status."""
        status: CompanionStatus = self.bridge.get_status()
        online, model = self._check_ollama_health()
        status.ollama_online = online
        status.ollama_model = model
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
        logger.info("[COMPANION] Processing chat inquiry from actor '%s': '%s'", getattr(actor, 'display_name', 'Admin'), clean_prompt)

        try:
            # 2. Query EXISTING ATLAS Core intelligence
            t0 = time.time()
            try:
                logger.info("[ATLAS CORE] Querying ATLAS Core intelligence with prompt: '%s'", clean_prompt)
                chat_result = self.chat_service.query(clean_prompt, actor)
                answer = chat_result.get("text") or chat_result.get("answer") or ""
                confidence_status = chat_result.get("confidence_status", "ANSWERABLE")
            except Exception as core_err:
                logger.error("[ATLAS CORE] Error during query '%s': %s", clean_prompt, core_err)
                self.bridge.set_state(CompanionState.CONFUSED)
                self.bridge.active_task = None
                self.bridge.log_activity("SYS", "ERROR", f"ATLAS Core failure: {core_err}")
                return {
                    "status": "error",
                    "error": "ATLAS_CORE_FAILURE",
                    "details": f"ATLAS Core error: {str(core_err)}",
                    "message": clean_prompt,
                    "text": None,
                    "answer": None,
                    "emotion": "confused",
                    "speak": False,
                    "state": CompanionState.CONFUSED.value,
                    "affective_state": CompanionState.CONFUSED.value,
                    "robot_state": "Confused",
                    "dispatched_to_hardware": False,
                    "is_connected": self.bridge.is_connected,
                }

            elapsed = time.time() - t0
            logger.info("[ATLAS CORE] Query completed in %.2fs. Answer: '%s'", elapsed, answer[:80])

            # 3. Determine affective reaction directly from LLM's dynamic emotion output
            raw_emotion = str(chat_result.get("emotion") or "").lower().strip()
            emotion_map = {
                "neutral": CompanionState.IDLE,
                "happy": CompanionState.HAPPY,
                "sad": CompanionState.SAD,
                "surprised": CompanionState.SURPRISED,
                "confused": CompanionState.CONFUSED,
                "thinking": CompanionState.THINKING,
                "concerned": CompanionState.CONCERNED,
            }
            if raw_emotion in emotion_map:
                reaction = emotion_map[raw_emotion]
            else:
                reaction = CompanionState.HAPPY if "good to see you" in answer.lower() else CompanionState.IDLE
                raw_emotion = "happy" if reaction == CompanionState.HAPPY else "neutral"

            logger.info("[EMOTION] LLM determined emotion: '%s' -> CompanionState.%s", raw_emotion, reaction.name)

            # 4. Dispatch emotion/state to physical ESP32 (triggers OLED reaction + servo reaction)
            self.bridge.set_state(reaction)
            self.bridge.send_emotion(raw_emotion)
            logger.info("[ESP32] Dispatched companion state: %s, emotion: %s", reaction.value, raw_emotion)

            # 5. Handle physical audio dispatch or failure modes
            if not self.bridge.is_connected:
                logger.warning("[ESP32] Physical companion disconnected on %s; speech playback skipped", self.bridge.port)
                self.bridge.log_activity("SYS", "ERROR", f"Speech playback skipped: ESP32 hardware disconnected on {self.bridge.port}")
                return {
                    "status": "ok",
                    "message": clean_prompt,
                    "text": answer,
                    "answer": answer,
                    "emotion": raw_emotion,
                    "speak": chat_result.get("speak", True),
                    "confidence_status": confidence_status,
                    "citations": chat_result.get("citations", []),
                    "why_atlas_said_this": chat_result.get("why_atlas_said_this"),
                    "technical_trace": chat_result.get("technical_trace"),
                    "model": chat_result.get("model"),
                    "companion_reaction": reaction.value,
                    "state": reaction.value,
                    "affective_state": reaction.value,
                    "robot_state": "Offline (Disconnected)",
                    "dispatched_to_hardware": False,
                    "hardware_error": f"Physical ESP32 companion is disconnected on {self.bridge.port}.",
                    "audio_duration_seconds": 0.0,
                    "audio_pcm_bytes": 0,
                    "latency_seconds": round(elapsed, 3),
                }

            # Generate real TTS from the actual response text and convert to 16 kHz unsigned 8-bit PCM
            audio_dispatched = False
            total_duration = 0.0
            total_pcm_bytes = 0
            audio_error = None

            try:
                from atlas.companion.tts import text_to_speech_packets
                logger.info("[TTS] Synthesizing speech for answer: '%s'", answer[:80])
                chunk_payloads = text_to_speech_packets(answer)
                for pcm_c, dur_c in chunk_payloads:
                    total_pcm_bytes += len(pcm_c)
                    total_duration += dur_c

                logger.info("[TTS] Synthesized %d audio chunks (%.2fs, %d bytes)", len(chunk_payloads), total_duration, total_pcm_bytes)

                def _stream_sequence():
                    # Display emotion reaction on OLED & servo for 0.8s before speaking
                    time.sleep(0.8)
                    self.bridge.set_state(CompanionState.SPEAKING)

                    for pcm_c, dur_c in chunk_payloads:
                        if not self.bridge.is_connected:
                            break
                        # Stream synchronously within this background thread so packets don't collide
                        self.bridge.send_voice_audio(pcm_c, duration=dur_c, blocking=True)
                        time.sleep(0.05)

                    # Return robot to Idle state after playback completes
                    self.bridge.set_state(CompanionState.IDLE)
                    self.bridge.active_task = None

                threading.Thread(target=_stream_sequence, daemon=True, name="CompanionAudioPlaybackSequence").start()
                audio_dispatched = True
                logger.info("[ESP32] Audio stream dispatched to hardware")
            except Exception as tts_err:
                logger.error("[TTS] Speech synthesis or audio conversion failure: %s", tts_err)
                audio_error = f"Speech synthesis / audio conversion failed: {str(tts_err)}"
                self.bridge.log_activity("SYS", "ERROR", audio_error)

            return {
                "status": "ok",
                "message": clean_prompt,
                "text": answer,
                "answer": answer,
                "emotion": raw_emotion,
                "speak": chat_result.get("speak", True),
                "confidence_status": confidence_status,
                "citations": chat_result.get("citations", []),
                "why_atlas_said_this": chat_result.get("why_atlas_said_this"),
                "technical_trace": chat_result.get("technical_trace"),
                "model": chat_result.get("model"),
                "companion_reaction": reaction.value,
                "state": reaction.value,
                "affective_state": reaction.value,
                "robot_state": "Speaking" if audio_dispatched else ("TTS_FAILED" if audio_error else "Idle"),
                "is_speaking": audio_dispatched,
                "dispatched_to_hardware": audio_dispatched,
                "audio_error": audio_error,
                "audio_duration_seconds": round(total_duration, 3),
                "audio_pcm_bytes": total_pcm_bytes,
                "latency_seconds": round(elapsed, 3),
            }
        except Exception as exc:
            logger.error("Unexpected error during companion interaction: %s", exc)
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

    def test_servo(self) -> dict:
        """Execute physical servo diagnostic test sequence (90 -> 60 -> 120 -> 90)."""
        self.bridge.active_task = "Running SERVO_TEST sequence"
        self.bridge.log_activity("SYS", "SERVO_TEST", "Triggered SERVO_TEST (90 -> 60 -> 120 -> 90)")
        sent = self.bridge.send_servo_test()
        return {
            "status": "ok",
            "message": "SERVO_TEST diagnostic sequence dispatched to hardware.",
            "port": self.bridge.port,
            "connected": self.bridge.is_connected,
            "dispatched_to_hardware": sent,
            "expected_sequence": "90 -> 60 -> 120 -> 90",
            "timestamp": time.time(),
        }

    def control_servo(self, command: str) -> dict:
        """Send explicit servo command (SERVO_CENTER, SERVO_LEFT, SERVO_RIGHT)."""
        sent = self.bridge.send_servo_command(command)
        return {
            "status": "ok",
            "command": command,
            "port": self.bridge.port,
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
