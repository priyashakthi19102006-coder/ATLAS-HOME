"""Serial communication bridge for physical ATLAS Home Companion (ESP32).

Maintains a non-blocking background I/O thread over USB serial (COM5 @ 921600 baud),
handling real hardware connection lifecycle, bidirectional command dispatch,
and telemetry logging with zero fake or simulated data.
"""

from __future__ import annotations

import json
import logging
import queue
import struct
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    serial = None
    HAS_SERIAL = False

from atlas.companion.models import CompanionActivity, CompanionState, CompanionStatus
from atlas.config.settings import get_settings

logger = logging.getLogger("atlas.companion.bridge")


class CompanionSerialBridge:
    """Thread-safe USB serial bridge for ATLAS Home Companion hardware."""

    def __init__(self, port: str = "COM5", baudrate: int = 921600):
        self.port = port
        self.baudrate = baudrate
        self.current_state = CompanionState.IDLE
        self.active_task: Optional[str] = None
        self.firmware_banner: Optional[str] = None

        self._ser: Optional[serial.Serial] = None
        self._tx_queue: queue.Queue[bytes] = queue.Queue(maxsize=100)
        self._activities: deque[CompanionActivity] = deque(maxlen=60)
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()

        # Metrics & Voice state
        self.bytes_sent = 0
        self.bytes_received = 0
        self.packets_sent = 0
        self.packets_received = 0
        self.audio_bytes_sent = 0
        self.voice_packets_sent = 0
        self.is_speaking = False
        self.last_seen_iso: Optional[str] = None
        self.last_error: Optional[str] = None

        # Thread management
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_connected(self) -> bool:
        return self._ser is not None and getattr(self._ser, "is_open", False)

    def start(self) -> None:
        """Start background serial communication thread."""
        if self._running.is_set():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._io_loop, daemon=True, name="AtlasCompanionSerialThread")
        self._thread.start()
        logger.info("CompanionSerialBridge background worker started on %s @ %d baud", self.port, self.baudrate)

    def stop(self) -> None:
        """Gracefully terminate serial worker and close connection."""
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._close_serial()
        logger.info("CompanionSerialBridge stopped.")

    def log_activity(self, direction: str, event_type: str, payload: str) -> None:
        """Record interaction event in the thread-safe activity ring buffer."""
        record = CompanionActivity(
            direction=direction,
            event_type=event_type,
            payload=payload,
            state=self.current_state.value,
        )
        with self._lock:
            self._activities.append(record)

    def set_state(self, state: CompanionState) -> bool:
        """Update companion operational or affective state and send command to ESP32."""
        self.current_state = state
        self.log_activity("SYS", "STATE_CHANGE", f"State transitioned to {state.value}")
        
        state_upper = state.value.upper()
        json_cmd = json.dumps({"type": "state", "state": state.value}) + "\n"
        text_cmd = f"STATE:{state_upper}\n"
        
        success = self.queue_bytes(json_cmd.encode("utf-8"))
        self.queue_bytes(text_cmd.encode("utf-8"))
        
        if state == CompanionState.CONCERNED:
            # Dual dispatch for hardware firmware variants: sad/confused attentive eyes
            self.queue_bytes(b"EMOTION_CONFUSED\n")
            self.queue_bytes(b"STATE:CONFUSED\n")
        return success

    def send_emotion(self, emotion: str) -> bool:
        """Explicitly send EMOTION command to companion hardware."""
        emo_upper = emotion.strip().upper()
        self.log_activity("OUT", "EMOTION", f"EMOTION_{emo_upper}")
        cmd = f"EMOTION_{emo_upper}\n"
        return self.queue_bytes(cmd.encode("utf-8"))

    def send_servo_command(self, command: str) -> bool:
        """Send explicit servo command (SERVO_CENTER, SERVO_LEFT, SERVO_RIGHT, SERVO_TEST)."""
        cmd_clean = command.strip().upper()
        if not cmd_clean.startswith("SERVO_"):
            cmd_clean = f"SERVO_{cmd_clean}"
        self.log_activity("OUT", "SERVO", cmd_clean)
        return self.queue_bytes(f"{cmd_clean}\n".encode("utf-8"))

    def send_servo_test(self) -> bool:
        """Send explicit SERVO_TEST diagnostic command."""
        self.log_activity("OUT", "SERVO_TEST", "SERVO_TEST")
        return self.queue_bytes(b"SERVO_TEST\n")

    def send_speak(self, text: str) -> bool:
        """Forward text response to physical ESP32 companion."""
        clean_text = text.replace("\r", " ").replace("\n", " ").strip()
        self.log_activity("OUT", "SPEAK", f"Forwarding speech to companion: {clean_text[:120]}...")
        json_cmd = json.dumps({"type": "speak", "text": clean_text}) + "\n"
        text_cmd = f"SPEAK:{clean_text}\n"
        self.queue_bytes(json_cmd.encode("utf-8"))
        return self.queue_bytes(text_cmd.encode("utf-8"))

    def send_test(self) -> bool:
        """Dispatch diagnostic self-test command to companion."""
        self.log_activity("OUT", "TEST", "Dispatched diagnostic self-test command")
        json_cmd = json.dumps({"type": "test", "timestamp": time.time()}) + "\n"
        text_cmd = "TEST\n"
        self.queue_bytes(json_cmd.encode("utf-8"))
        return self.queue_bytes(text_cmd.encode("utf-8"))

    def send_voice_audio(self, pcm_u8: bytes, duration: float = 0.0, blocking: bool = False) -> bool:
        """Stream raw 16 kHz unsigned 8-bit PCM audio to ESP32 DAC using verified VOICE protocol.

        Protocol:
            VOICE\n
            <uint32 little-endian audio length>
            <raw unsigned 8-bit PCM audio>

        If blocking is True, transmission and playback pacing occur synchronously on the caller's thread.
        """
        length = len(pcm_u8)
        if length == 0:
            return False

        if not self.is_connected or not self._ser:
            self.log_activity("SYS", "ERROR", f"Cannot stream voice: ESP32 {self.port} not connected.")
            logger.warning("Voice streaming failed: ESP32 on %s is not connected.", self.port)
            return False

        # Assemble verified protocol header
        header = b"VOICE\n" + struct.pack("<I", length)
        full_payload = header + pcm_u8

        self.is_speaking = True
        self.log_activity(
            "OUT",
            "VOICE",
            f"Transmitting {length} bytes ({duration:.2f}s) 16kHz unsigned 8-bit PCM to ESP32 DAC",
        )

        def _stream_worker():
            try:
                chunk_size = 2048
                total_sent = 0
                with self._write_lock:
                    for i in range(0, len(full_payload), chunk_size):
                        if not self.is_connected or not self._ser:
                            break
                        chunk = full_payload[i : i + chunk_size]
                        self._ser.write(chunk)
                        self._ser.flush()
                        total_sent += len(chunk)
                        self.bytes_sent += len(chunk)
                        # Pace chunk transmission slightly to prevent ESP32 FIFO overflow
                        time.sleep(0.005)

                self.packets_sent += 1
                self.voice_packets_sent += 1
                self.audio_bytes_sent += length
                logger.info("Voice audio stream complete: %d bytes transmitted over %s", total_sent, self.port)

                # Keep is_speaking True for the physical playback duration
                play_time = max(duration, length / 16000.0)
                time.sleep(play_time)
            except Exception as exc:
                logger.error("Error during voice audio streaming: %s", exc)
                self.last_error = f"Audio stream error: {exc}"
            finally:
                self.is_speaking = False

        if blocking:
            _stream_worker()
            return True

        t = threading.Thread(target=_stream_worker, daemon=True, name="CompanionVoiceStream")
        self._last_voice_thread = t
        t.start()
        return True

    def queue_bytes(self, data: bytes) -> bool:
        """Queue raw bytes for transmission by the serial thread."""
        try:
            self._tx_queue.put_nowait(data)
            return True
        except queue.Full:
            logger.warning("Serial TX queue full; dropping packet (%d bytes)", len(data))
            return False

    def get_status(self) -> CompanionStatus:
        """Return snapshot of live physical connection status and metrics."""
        with self._lock:
            activities = list(self._activities)

        if not self.is_connected:
            robot_st = "Offline"
        elif self.is_speaking:
            robot_st = "Speaking"
        elif self.active_task and ("Processing" in self.active_task or "Thinking" in self.active_task):
            robot_st = "Thinking"
        elif self.current_state == CompanionState.LISTENING:
            robot_st = "Listening"
        elif self.current_state == CompanionState.THINKING:
            robot_st = "Thinking"
        elif self.current_state == CompanionState.SPEAKING:
            robot_st = "Speaking"
        else:
            robot_st = "Idle"

        return CompanionStatus(
            connected=self.is_connected,
            port=self.port,
            baudrate=self.baudrate,
            state=self.current_state,
            affective_state=self.current_state.value,
            robot_state=robot_st,
            bytes_sent=self.bytes_sent,
            bytes_received=self.bytes_received,
            packets_sent=self.packets_sent,
            packets_received=self.packets_received,
            is_speaking=self.is_speaking,
            audio_bytes_sent=self.audio_bytes_sent,
            voice_packets_sent=self.voice_packets_sent,
            last_seen_iso=self.last_seen_iso,
            last_error=self.last_error,
            firmware_banner=self.firmware_banner,
            active_task=self.active_task,
            recent_activities=activities[-30:],
        )

    # -------------------------------------------------------------------------
    # Internal I/O Loop
    # -------------------------------------------------------------------------
    def _io_loop(self) -> None:
        """Persistent worker loop managing serial connection, writes, and reads."""
        reconnect_delay = 1.0
        line_buffer = bytearray()

        while self._running.is_set():
            # 1. Connection check / reconnect
            if not self.is_connected:
                if not HAS_SERIAL:
                    self.last_error = "pyserial not installed in Python environment"
                    time.sleep(2.0)
                    continue

                try:
                    logger.info("Attempting to connect to ESP32 on %s @ %d baud...", self.port, self.baudrate)
                    self._ser = serial.Serial(
                        port=self.port,
                        baudrate=self.baudrate,
                        timeout=0.1,
                        write_timeout=0.5,
                    )
                    self.last_error = None
                    reconnect_delay = 1.0
                    self.last_seen_iso = datetime.now(timezone.utc).isoformat()
                    self.log_activity("SYS", "STATUS", f"Serial connection established on {self.port} @ {self.baudrate}")
                    logger.info("Connected successfully to physical ESP32 on %s", self.port)
                    # Query initial status from physical device
                    self.queue_bytes(b'{"type":"ping"}\n')
                    self.queue_bytes(b"STATUS\n")
                except Exception as exc:
                    self._close_serial()
                    self.last_error = str(exc)
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, 10.0)
                    continue

            # 2. Transmit queued packets
            try:
                with self._write_lock:
                    while not self._tx_queue.empty():
                        packet = self._tx_queue.get_nowait()
                        if self._ser and self._ser.is_open:
                            self._ser.write(packet)
                            self._ser.flush()
                            self.bytes_sent += len(packet)
                            self.packets_sent += 1
            except Exception as exc:
                logger.warning("Error writing to serial %s: %s", self.port, exc)
                self.last_error = f"Write error: {exc}"
                self._close_serial()
                continue

            # 3. Receive incoming data
            try:
                if self._ser and self._ser.is_open:
                    in_waiting = self._ser.in_waiting
                    if in_waiting > 0:
                        chunk = self._ser.read(min(in_waiting, 4096))
                        if chunk:
                            self.bytes_received += len(chunk)
                            self.last_seen_iso = datetime.now(timezone.utc).isoformat()

                            # Process chunk for line-based messages or banners
                            for b in chunk:
                                if b == ord("\n") or b == ord("\r"):
                                    if line_buffer:
                                        self._process_rx_line(bytes(line_buffer))
                                        line_buffer.clear()
                                else:
                                    # Collect printable or meaningful bytes
                                    line_buffer.append(b)
                                    if len(line_buffer) > 1024:
                                        line_buffer.clear()
            except Exception as exc:
                logger.warning("Error reading from serial %s: %s", self.port, exc)
                self.last_error = f"Read error: {exc}"
                self._close_serial()
                continue

            time.sleep(0.02)

        self._close_serial()

    def _process_rx_line(self, line_bytes: bytes) -> None:
        """Parse incoming line from physical ESP32."""
        try:
            # Check if valid text
            text = line_bytes.decode("utf-8", errors="ignore").strip()
            if not text:
                return

            self.packets_received += 1

            if "READY" in text or "ATLAS" in text:
                self.firmware_banner = text
                self.log_activity("IN", "STATUS", f"Hardware banner received: {text}")
                logger.info("ESP32 Firmware Banner: %s", text)
                return

            # Check if JSON payload
            if text.startswith("{") and text.endswith("}"):
                try:
                    payload = json.loads(text)
                    self.log_activity("IN", "STATUS", f"Hardware JSON: {text}")
                    if "state" in payload:
                        try:
                            self.current_state = CompanionState(payload["state"])
                        except ValueError:
                            pass
                    return
                except json.JSONDecodeError:
                    pass

            if "[SERVO" in text:
                self.log_activity("IN", "SERVO", text)
                logger.info("ESP32 Servo Confirmation: %s", text)
                return

            if "[COMMAND" in text:
                self.log_activity("IN", "COMMAND", text)
                logger.info("ESP32 Command Acknowledged: %s", text)
                return

            # Printable ASCII log
            self.log_activity("IN", "RAW", text[:120])
        except Exception as exc:
            logger.debug("Error processing line from serial: %s", exc)

    def _close_serial(self) -> None:
        """Safely close serial port."""
        if self._ser is not None:
            try:
                if self._ser.is_open:
                    self._ser.close()
            except Exception:
                pass
            self._ser = None


_global_companion_bridge: Optional[CompanionSerialBridge] = None
_bridge_lock = threading.Lock()


def get_companion_bridge(port: Optional[str] = None, baudrate: Optional[int] = None) -> CompanionSerialBridge:
    """Return singleton instance of CompanionSerialBridge."""
    global _global_companion_bridge
    with _bridge_lock:
        if _global_companion_bridge is None:
            settings = get_settings()
            use_port = port or getattr(settings, "companion_port", "COM5")
            use_baud = baudrate or getattr(settings, "companion_baud", 921600)
            _global_companion_bridge = CompanionSerialBridge(port=use_port, baudrate=use_baud)
        return _global_companion_bridge
