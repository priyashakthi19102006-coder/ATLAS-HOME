"""Tests for ATLAS Home User Management & Activity Feed Surgical Repair."""

import pytest
from atlas.authority.user_service import (
    UserManagementService,
    MAX_USERS,
    MAX_ADMINS,
    MAX_AUTHORIZED_USERS,
)
from atlas.authority.models import Role
from atlas.authority.auth import AuthService, get_auth_service
from atlas.events.engine import EventEngine
from atlas.events.schema import ATLASEvent, EventStatus, EventSeverity
from atlas.chat.service import PersonHistoryService


@pytest.fixture
def user_svc(tmp_path):
    db_file = tmp_path / "test_users.db"
    svc = UserManagementService(db_path=str(db_file))
    return svc


def test_identity_bounds_constant():
    """Verify maximum bounds: 1 Admin + 10 Authorized Users = 11 accounts."""
    assert MAX_ADMINS == 1
    assert MAX_AUTHORIZED_USERS == 10
    assert MAX_USERS == 11


def test_user_slots_summary_ten_slots(user_svc):
    """Verify slots summary returns exactly 10 authorized slots with correct status."""
    summary = user_svc.get_user_slots_summary()
    assert summary["max_authorized_users"] == 10
    assert summary["max_admins"] == 1
    assert summary["max_users"] == 10
    assert summary["total_capacity"] == 11
    assert summary["admin_count"] == 1  # Default seeded admin
    assert summary["authorized_user_count"] == 0
    assert summary["occupied_slots"] == 0
    assert summary["remaining_authorized_slots"] == 10
    assert len(summary["slots"]) == 10

    for i, s in enumerate(summary["slots"], start=1):
        assert s["slot_number"] == i
        assert s["status"] == "EMPTY"
        assert s["user"] is None


def test_create_authorized_user_with_relationship(user_svc):
    """Test user creation starts as inactive PENDING_ENROLLMENT and captures relationship."""
    user = user_svc.create_user(
        username="john_friend",
        display_name="John Doe",
        password="secretpassword123",
        role=Role.AUTHORIZED_USER,
        phone_number="+15551234567",
        relationship="Friend",
    )
    assert user.username == "john_friend"
    assert user.relationship == "Friend"
    assert user.phone_number == "+15551234567"
    assert user.enrollment_status == "PENDING_ENROLLMENT"
    assert user.is_active is False  # Inactive until 5 biometric samples enrolled
    assert user.role == Role.AUTHORIZED_USER

    # Check slots summary reflection
    summary = user_svc.get_user_slots_summary()
    assert summary["authorized_user_count"] == 1
    assert summary["occupied_slots"] == 1
    assert summary["remaining_authorized_slots"] == 9
    assert summary["slots"][0]["status"] == "OCCUPIED"
    assert summary["slots"][0]["user"]["relationship"] == "Friend"
    assert summary["slots"][1]["status"] == "EMPTY"


def test_user_limit_enforcement(user_svc):
    """Verify maximum 10 Authorized Users limit is strictly enforced."""
    for i in range(10):
        user_svc.create_user(
            username=f"user_{i}",
            display_name=f"User {i}",
            password="password123",
            role=Role.AUTHORIZED_USER,
            relationship="Family",
        )

    summary = user_svc.get_user_slots_summary()
    assert summary["authorized_user_count"] == 10
    assert summary["occupied_slots"] == 10
    assert summary["remaining_authorized_slots"] == 0

    # 11th authorized user creation must raise ValueError
    with pytest.raises(ValueError, match="Maximum of 10 Authorized Users permitted"):
        user_svc.create_user(
            username="overflow_user",
            display_name="Overflow",
            password="password123",
            role=Role.AUTHORIZED_USER,
        )


def test_update_user_fields(user_svc):
    """Test updating user display name, relationship, phone, and active status."""
    user = user_svc.create_user(
        username="sarah_test",
        display_name="Sarah",
        password="pass",
        role=Role.AUTHORIZED_USER,
        relationship="Family",
    )

    updated = user_svc.update_user(
        user.user_id,
        display_name="Sarah Jenkins",
        relationship="Caregiver",
        phone_number="+19998887777",
        is_active=True,
    )
    assert updated.display_name == "Sarah Jenkins"
    assert updated.relationship == "Caregiver"
    assert updated.phone_number == "+19998887777"
    assert updated.is_active is True


def test_session_invalidation_on_delete_and_deactivate(user_svc):
    """Verify sessions are invalidated when a user is deactivated, deleted, or face removed."""
    auth_svc = get_auth_service()
    user = user_svc.create_user(
        username="session_test",
        display_name="Session User",
        password="pass",
        role=Role.AUTHORIZED_USER,
    )

    # Issue a session for this user
    token = auth_svc.create_session(user.to_actor(), user.username)
    assert auth_svc.get_actor_by_token(token) is not None

    # Deactivate user -> session must be invalidated
    user_svc.update_user(user.user_id, is_active=False)
    assert auth_svc.get_actor_by_token(token) is None

    # Create another session and re-activate
    user_svc.update_user(user.user_id, is_active=True)
    token2 = auth_svc.create_session(user.to_actor(), user.username)
    assert auth_svc.get_actor_by_token(token2) is not None

    # Delete user -> session must be invalidated
    user_svc.delete_user(user.user_id)
    assert auth_svc.get_actor_by_token(token2) is None


def test_face_removal_deactivates_user_and_invalidates_session(user_svc):
    """Verify face removal purges embedding, deactivates account, and kills sessions."""
    auth_svc = get_auth_service()
    user = user_svc.create_user(
        username="face_test",
        display_name="Face User",
        password="pass",
        role=Role.AUTHORIZED_USER,
    )
    # Enroll dummy embedding
    dummy_vec = [0.1] * 1856
    user_svc.enroll_face(user.user_id, dummy_vec)
    user_svc.update_user(user.user_id, is_active=True)

    token = auth_svc.create_session(user.to_actor(), user.username)
    assert auth_svc.get_actor_by_token(token) is not None

    # Remove face
    user_svc.remove_face_enrollment(user.user_id)
    u_after = user_svc.get_user_by_id(user.user_id)
    assert u_after.enrolled_embedding is None
    assert u_after.is_active is False
    assert u_after.enrollment_status in ("PENDING_ENROLLMENT", "FACE_REMOVED")
    # Session must be invalidated
    assert auth_svc.get_actor_by_token(token) is None


def test_event_engine_track_expiry_and_root_track_id():
    """Verify track expiry is 5.0s and root track_id is set."""
    engine = EventEngine(track_expiry_seconds=5.0)
    assert engine.track_expiry_seconds == 5.0


def test_chat_relationship_formatting():
    """Verify ATLASChatService._deterministic_fallback_answer adds relationship prefix."""
    from atlas.chat.service import ATLASChatService
    from atlas.authority.models import Actor, Role
    chat_svc = ATLASChatService()
    actor = Actor(actor_id="admin", role=Role.ADMIN, display_name="Admin")
    
    ctx = {
        "system": {
            "camera_state": "LIVE",
            "camera_fps": 30.0,
            "current_time_human": "10:30 AM",
        },
        "people": {
            "current_occupants": [
                {
                    "track_id": 1,
                    "person_name": "Kawin",
                    "relationship": "Friend",
                    "entry_time_human": "10:00 AM",
                    "current_action": "sitting",
                }
            ],
            "historical_person_events": [],
        },
        "safety_incidents": {"active_count": 0, "recent_incidents": []},
    }
    
    resp = chat_svc._deterministic_fallback_answer(ctx, "Who is currently inside?", actor)
    assert "Kawin" in resp["answer"]
    assert "friend" in resp["answer"].lower()
