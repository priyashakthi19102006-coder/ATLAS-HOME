"""Comprehensive test suite for Physical ATLAS Home Companion (ESP32 COM5).

Verifies:
1. CompanionState enum definitions and domain models.
2. CompanionSerialBridge command formatting, queuing, activity buffer, and metrics.
3. CompanionService operational flows, affective state determination, and diagnostic routines.
4. API Route security:
   - Authenticated Admin permitted to access status, test, state, and chat endpoints.
   - Authorized User blocked with 403 Forbidden.
   - Unauthenticated Guest blocked with 401 Unauthorized.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas.authority.models import Role, Permission, Actor
from atlas.authority.user_service import UserManagementService
from atlas.companion.models import CompanionState, CompanionActivity, CompanionStatus
from atlas.companion.bridge import CompanionSerialBridge
from atlas.companion.service import CompanionService
from atlas.events.storage import EventStorage
from atlas.api.app import create_app


@pytest.fixture(scope="module")
def test_env(tmp_path_factory):
    """Set up isolated testing database and Flask test app."""
    d = tmp_path_factory.mktemp("companion_test")
    db_path = d / "test_companion.db"
    storage = EventStorage(db_path=str(db_path))
    user_svc = UserManagementService(db_path=str(db_path))

    auth_user = user_svc.create_user(
        username="auth_user_test",
        display_name="Authorized Family",
        password="family_password_123!",
        role=Role.AUTHORIZED_USER,
    )

    app = create_app()
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "companion_test_secret_key"

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


class TestCompanionDomain:
    """Test Companion domain models and schemas."""

    def test_companion_state_enum(self):
        """Verify all mandatory companion states are defined."""
        expected_states = ["Idle", "Listening", "Thinking", "Speaking", "Happy", "Sad", "Surprised", "Confused"]
        for s in expected_states:
            state = CompanionState(s)
            assert state.value == s

    def test_companion_activity_model(self):
        """Verify CompanionActivity serialization."""
        act = CompanionActivity(
            direction="OUT",
            event_type="SPEAK",
            payload="Hello from test",
            state="Speaking",
        )
        data = act.model_dump()
        assert data["direction"] == "OUT"
        assert data["event_type"] == "SPEAK"
        assert "Hello from test" in data["payload"]
        assert data["state"] == "Speaking"

    def test_companion_status_model(self):
        """Verify CompanionStatus model default values."""
        status = CompanionStatus(port="COM5", baudrate=921600)
        assert status.port == "COM5"
        assert status.baudrate == 921600
        assert status.state == CompanionState.IDLE
        assert status.connected is False
        assert status.bytes_sent == 0


class TestCompanionSerialBridge:
    """Test CompanionSerialBridge non-blocking queue and telemetry."""

    def test_bridge_command_queuing(self):
        """Verify set_state, send_speak, and send_test enqueue valid dual payloads."""
        bridge = CompanionSerialBridge(port="COM5", baudrate=921600)

        # 1. State change
        queued = bridge.set_state(CompanionState.HAPPY)
        assert queued is True
        assert bridge.current_state == CompanionState.HAPPY

        # Pop from TX queue
        q_item1 = bridge._tx_queue.get_nowait()
        q_item2 = bridge._tx_queue.get_nowait()
        payloads = [q_item1.decode("utf-8"), q_item2.decode("utf-8")]
        assert any("Happy" in p for p in payloads)
        assert any("STATE:HAPPY" in p for p in payloads)

        # 2. Speak command
        queued_speak = bridge.send_speak("Perimeter is secure.")
        assert queued_speak is True
        q_speak1 = bridge._tx_queue.get_nowait()
        q_speak2 = bridge._tx_queue.get_nowait()
        speak_payloads = [q_speak1.decode("utf-8"), q_speak2.decode("utf-8")]
        assert any("Perimeter is secure." in p for p in speak_payloads)
        assert any("SPEAK:Perimeter is secure." in p for p in speak_payloads)

        # 3. Test command
        queued_test = bridge.send_test()
        assert queued_test is True
        q_test1 = bridge._tx_queue.get_nowait()
        q_test2 = bridge._tx_queue.get_nowait()
        test_payloads = [q_test1.decode("utf-8"), q_test2.decode("utf-8")]
        assert any("test" in p for p in test_payloads)
        assert any("TEST" in p for p in test_payloads)

    def test_bridge_activity_ring_buffer(self):
        """Verify activity log maintains entries bounded in ring buffer."""
        bridge = CompanionSerialBridge(port="COM5", baudrate=921600)
        for i in range(10):
            bridge.log_activity("SYS", "TEST_EVENT", f"Payload {i}")
        status = bridge.get_status()
        assert len(status.recent_activities) == 10
        assert status.recent_activities[-1].payload == "Payload 9"


class TestCompanionService:
    """Test CompanionService coordination with ATLAS Core and hardware bridge."""

    def test_set_state_valid_and_invalid(self):
        bridge = CompanionSerialBridge(port="COM5", baudrate=921600)
        svc = CompanionService(bridge=bridge)

        res = svc.set_state("Listening")
        assert res["status"] == "ok"
        assert res["state"] == "Listening"

        # Case-insensitive
        res2 = svc.set_state("confused")
        assert res2["state"] == "Confused"

        with pytest.raises(ValueError) as exc:
            svc.set_state("InvalidStateName")
        assert "Invalid companion state" in str(exc.value)

    def test_chat_and_react_affective_resolution(self):
        """Verify emotion resolution based on intelligence answer."""
        bridge = CompanionSerialBridge(port="COM5", baudrate=921600)
        mock_chat = MagicMock()
        svc = CompanionService(bridge=bridge, chat_service=mock_chat)

        actor = Actor(actor_id="admin_test", role=Role.ADMIN, display_name="Admin Test")

        # Scenario 1: Warning/Incident -> SURPRISED
        mock_chat.query.return_value = {
            "answer": "There is an active warning: unauthorized person at perimeter.",
            "confidence_status": "ANSWERABLE",
            "citations": ["INCIDENT-001"],
            "why_atlas_said_this": "Observed unfamiliar individual",
            "technical_trace": "Rule R_UNVERIFIED",
            "model": "qwen2.5:3b",
        }
        res1 = svc.chat_and_react("Is there any problem?", actor)
        assert res1["companion_reaction"] == "Surprised"
        assert res1["answer"] == "There is an active warning: unauthorized person at perimeter."

        # Scenario 2: Normal/Secure -> HAPPY
        mock_chat.query.return_value = {
            "answer": "All entryways are safe and perimeter is secure.",
            "confidence_status": "ANSWERABLE",
            "citations": ["OBS-002"],
        }
        res2 = svc.chat_and_react("Are we safe?", actor)
        assert res2["companion_reaction"] == "Happy"

        # Scenario 3: Unclear/Unknown -> CONFUSED
        mock_chat.query.return_value = {
            "answer": "I don't have enough information to confirm that.",
            "confidence_status": "UNANSWERABLE",
            "citations": [],
        }
        res3 = svc.chat_and_react("Who is at the gate?", actor)
        assert res3["companion_reaction"] == "Confused"

    def test_test_companion_routine(self):
        bridge = CompanionSerialBridge(port="COM5", baudrate=921600)
        svc = CompanionService(bridge=bridge)
        res = svc.test_companion()
        assert res["status"] == "ok"
        assert res["port"] == "COM5"
        assert res["baudrate"] == 921600


class TestCompanionAPIAuthorization:
    """Test API route authorization rules for Admin vs Non-Admin."""

    def test_admin_access_allowed(self, test_env):
        """Admin can access all companion API endpoints."""
        app = test_env["app"]
        user_svc = test_env["user_svc"]
        admin_account = user_svc.get_user_by_username("admin")
        admin_actor = admin_account.to_actor()

        with patch("atlas.authority.user_service._global_user_service", user_svc):
            with app.test_client() as client:
                _login_actor(client, admin_actor)

                # GET status
                resp = client.get("/api/admin/companion/status")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["status"] == "ok"
                assert "companion" in data
                assert data["companion"]["port"] == "COM5"

                # POST state
                resp_state = client.post(
                    "/api/admin/companion/state",
                    json={"state": "Happy"},
                )
                assert resp_state.status_code == 200
                assert resp_state.get_json()["state"] == "Happy"

                # POST test
                resp_test = client.post("/api/admin/companion/test")
                assert resp_test.status_code == 200
                assert resp_test.get_json()["status"] == "ok"

    def test_authorized_user_blocked(self, test_env):
        """Authorized User receives 403 Forbidden on all companion endpoints."""
        app = test_env["app"]
        user_svc = test_env["user_svc"]
        auth_user = test_env["auth_user"]
        auth_actor = auth_user.to_actor()

        with patch("atlas.authority.user_service._global_user_service", user_svc):
            with app.test_client() as client:
                _login_actor(client, auth_actor)

                # GET status
                resp = client.get("/api/admin/companion/status")
                assert resp.status_code == 403

                # POST chat
                resp_chat = client.post(
                    "/api/admin/companion/chat",
                    json={"message": "Hello robot"},
                )
                assert resp_chat.status_code == 403

                # POST test
                resp_test = client.post("/api/admin/companion/test")
                assert resp_test.status_code == 403

                # POST state
                resp_state = client.post(
                    "/api/admin/companion/state",
                    json={"state": "Idle"},
                )
                assert resp_state.status_code == 403

    def test_guest_blocked(self, test_env):
        """Unauthenticated Guest receives 401 Unauthorized on companion endpoints."""
        app = test_env["app"]
        with app.test_client() as client:
            resp = client.get("/api/admin/companion/status")
            assert resp.status_code == 401

            resp_chat = client.post("/api/admin/companion/chat", json={"message": "Hello"})
            assert resp_chat.status_code == 401
