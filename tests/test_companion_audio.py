"""Automated tests for ATLAS Companion Voice synthesis and streaming protocol."""

from __future__ import annotations

import io
from pathlib import Path
import struct
import sys
import wave
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas.authority.models import Role, Permission, Actor
from atlas.authority.user_service import UserManagementService
from atlas.companion.tts import (
    clean_text_for_tts,
    convert_wav_to_16k_u8_pcm,
    synthesize_speech_wav,
    text_to_16k_u8_pcm,
)
from atlas.companion.bridge import CompanionSerialBridge
from atlas.companion.service import CompanionService
from atlas.companion.models import CompanionState
from atlas.events.storage import EventStorage
from atlas.api.app import create_app


@pytest.fixture(scope="module")
def test_env(tmp_path_factory):
    """Set up isolated testing database and Flask test app."""
    d = tmp_path_factory.mktemp("companion_audio_test")
    db_path = d / "test_audio.db"
    storage = EventStorage(db_path=str(db_path))
    user_svc = UserManagementService(db_path=str(db_path))

    auth_user = user_svc.create_user(
        username="family_audio_tester",
        display_name="Family Tester",
        password="family_password_123!",
        role=Role.AUTHORIZED_USER,
    )

    app = create_app()
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "companion_audio_secret"

    return {
        "db_path": str(db_path),
        "storage": storage,
        "user_svc": user_svc,
        "auth_user": auth_user,
        "app": app,
    }


def _login_actor(client, actor: Actor):
    """Inject authenticated session token for test actor."""
    from atlas.authority.auth import get_auth_service
    import secrets
    from datetime import datetime, timezone

    auth_svc = get_auth_service()
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    auth_svc._sessions[token] = {
        "username": getattr(actor, "username", actor.actor_id),
        "actor": actor,
        "created_at": now,
        "expires_at": now + auth_svc.session_ttl,
    }
    client.set_cookie("atlas_session", token)
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return token


class TestTTSAudioProcessing:
    """Test Text-to-Speech preprocessing and audio format conversion."""

    def test_clean_text_for_tts(self):
        raw = "**ATLAS Home**: Perimeter is SECURE! [INCIDENT-001] Check `camera_0` on COM5."
        cleaned = clean_text_for_tts(raw)
        assert "ATLAS Home: Perimeter is SECURE!" in cleaned
        assert "[INCIDENT-001]" not in cleaned
        assert "`" not in cleaned
        assert "C-O-M 5" in cleaned

    def test_convert_wav_to_16k_u8_pcm(self):
        # Create a synthetic 1-second 22050 Hz 16-bit mono WAV in memory
        framerate = 22050
        duration_sec = 1.0
        t = np.linspace(0, duration_sec, int(framerate * duration_sec), endpoint=False)
        tone_16bit = (np.sin(2 * np.pi * 440 * t) * 30000).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(framerate)
            w.writeframes(tone_16bit.tobytes())

        wav_bytes = buf.getvalue()

        # Convert to 16,000 Hz unsigned 8-bit PCM
        pcm_u8, duration = convert_wav_to_16k_u8_pcm(wav_bytes)

        # 1. Exact sample count should be ~16000
        assert abs(len(pcm_u8) - 16000) <= 2
        # 2. Duration ~ 1.0s
        assert abs(duration - 1.0) <= 0.05
        # 3. All samples are uint8 in range [0, 255]
        u8_arr = np.frombuffer(pcm_u8, dtype=np.uint8)
        assert u8_arr.min() >= 0
        assert u8_arr.max() <= 255
        # 4. Midpoint centered near 128
        assert 120 <= np.mean(u8_arr) <= 136

    def test_synthesize_speech_wav_live(self):
        """Test Windows SAPI / System.Speech synthesis."""
        wav_data = synthesize_speech_wav("System operational.")
        assert len(wav_data) > 100
        # Verify valid WAV container
        with wave.open(io.BytesIO(wav_data), "rb") as w:
            assert w.getnchannels() in (1, 2)
            assert w.getsampwidth() in (1, 2)
            assert w.getframerate() in (16000, 22050, 44100, 48000)

    def test_text_to_16k_u8_pcm_pipeline(self):
        """Test full pipeline from text to 16kHz unsigned 8-bit PCM."""
        pcm_u8, duration = text_to_16k_u8_pcm("All systems nominal.")
        assert len(pcm_u8) > 5000
        assert duration > 0.5
        u8_arr = np.frombuffer(pcm_u8, dtype=np.uint8)
        assert u8_arr.dtype == np.uint8


class TestVoiceProtocolAndBridge:
    """Test serial protocol packet formatting and transmission logic."""

    def test_send_voice_audio_protocol_formatting(self):
        """Verify b'VOICE\\n' + uint32 length + pcm_u8."""
        bridge = CompanionSerialBridge(port="COM5", baudrate=921600)

        # Mock serial port
        mock_ser = MagicMock()
        mock_ser.is_open = True
        bridge._ser = mock_ser

        test_pcm = bytes([128, 130, 125, 140, 120] * 200)  # 1000 bytes
        sent = bridge.send_voice_audio(test_pcm, duration=0.2)
        assert sent is True
        assert bridge.is_speaking is True

        # Wait briefly for worker thread to flush payload
        import time
        time.sleep(0.05)

        # Check that written data begins with b"VOICE\n" + <uint32 little endian 1000>
        written_chunks = [call.args[0] for call in mock_ser.write.call_args_list]
        total_written = b"".join(written_chunks)

        expected_header = b"VOICE\n" + struct.pack("<I", len(test_pcm))
        assert total_written.startswith(expected_header)
        assert total_written[len(expected_header):] == test_pcm

    def test_send_voice_audio_disconnected_fails(self):
        bridge = CompanionSerialBridge(port="COM5", baudrate=921600)
        bridge._ser = None
        sent = bridge.send_voice_audio(b"12345", duration=0.1)
        assert sent is False
        assert bridge.is_speaking is False


class TestCompanionAudioAPI:
    """Test API authorization on /api/admin/companion/speak."""

    def test_admin_can_call_speak(self, test_env):
        app = test_env["app"]
        user_svc = test_env["user_svc"]
        admin_account = user_svc.get_user_by_username("admin")
        admin_actor = admin_account.to_actor()

        with patch("atlas.authority.user_service._global_user_service", user_svc):
            with app.test_client() as client:
                _login_actor(client, admin_actor)
                resp = client.post(
                    "/api/admin/companion/speak",
                    json={"text": "Test speech synthesis."},
                )
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["status"] == "ok"
                assert "duration_seconds" in data
                assert "pcm_bytes" in data
                assert data["format"] == "unsigned 8-bit mono PCM"
                assert data["sample_rate"] == 16000

    def test_authorized_user_cannot_call_speak(self, test_env):
        app = test_env["app"]
        user_svc = test_env["user_svc"]
        auth_user = test_env["auth_user"]
        auth_actor = auth_user.to_actor()

        with patch("atlas.authority.user_service._global_user_service", user_svc):
            with app.test_client() as client:
                _login_actor(client, auth_actor)
                resp = client.post(
                    "/api/admin/companion/speak",
                    json={"text": "Unauthorized speech."},
                )
                assert resp.status_code == 403

    def test_guest_cannot_call_speak(self, test_env):
        app = test_env["app"]
        with app.test_client() as client:
            resp = client.post(
                "/api/admin/companion/speak",
                json={"text": "Guest speech."},
            )
            assert resp.status_code == 401
