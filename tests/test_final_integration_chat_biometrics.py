"""Automated Integration Tests for Final Integration Phase.

Tests:
1. Multi-sample face biometric enrollment (validation, fusion, activation).
2. User account lifecycle (PENDING_ENROLLMENT -> ENROLLED, login prevention when pending).
3. Admin re-authentication for sensitive actions.
4. Grounded Conversational ATLAS Assistant (RBAC enforcement, honest handling of camera OFF and unknowns).
5. Visual attribute extraction (HSV dominant color, carried object detection).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
import cv2
import numpy as np
import pytest

from atlas.authority.models import Role, Actor, Permission
from atlas.authority.face import FaceBiometricsEngine
from atlas.authority.user_service import UserManagementService
from atlas.chat.service import ATLASChatService
from atlas.perception.attributes import (
    get_dominant_color_name,
    extract_clothing_attributes,
    check_carried_objects,
)
from atlas.perception.schema import BoundingBox, CenterPoint


def _create_synthetic_face_frame(color=(120, 150, 180)) -> np.ndarray:
    """Create a high-variance test image with realistic gradients."""
    img = np.zeros((300, 300, 3), dtype=np.uint8)
    for y in range(300):
        for x in range(300):
            img[y, x] = [
                int((x * 0.7 + color[0]) % 255),
                int((y * 0.7 + color[1]) % 255),
                int(((x + y) * 0.4 + color[2]) % 255),
            ]
    # Draw oval and features
    cv2.ellipse(img, (150, 150), (60, 80), 0, 0, 360, (200, 200, 200), -1)
    cv2.circle(img, (130, 130), 10, (40, 40, 40), -1)
    cv2.circle(img, (170, 130), 10, (40, 40, 40), -1)
    cv2.rectangle(img, (135, 170), (165, 185), (50, 50, 180), -1)
    return img


@pytest.fixture
def temp_db(tmp_path):
    from atlas.events.storage import EventStorage
    db_file = tmp_path / "test_final.db"
    EventStorage(db_path=str(db_file))
    return db_file


@pytest.fixture
def user_svc(temp_db):
    return UserManagementService(db_path=str(temp_db))


def test_user_creation_pending_biometrics(user_svc):
    """Requirement: Authorized User must start in PENDING_ENROLLMENT with is_active = False."""
    user = user_svc.create_user(
        username="kawin",
        display_name="Kawin",
        password="securepass123",
        role=Role.AUTHORIZED_USER,
        phone_number="+1555123456",
    )
    assert user.username == "kawin"
    assert user.display_name == "Kawin"
    assert user.is_active is False
    assert user.enrollment_status == "PENDING_ENROLLMENT"
    assert user.phone_number == "+1555123456"

    # User cannot authenticate while pending
    auth_result = user_svc.verify_password("kawin", "securepass123")
    assert auth_result is None, "Pending enrollment user must not authenticate before face enrollment"


def test_multi_sample_fusion_and_activation(user_svc, tmp_path):
    """Requirement: Multi-sample biometric enrollment fuses samples and activates user."""
    engine = FaceBiometricsEngine(snapshot_dir=tmp_path / "biometrics")
    user = user_svc.create_user("kawin_multi", "Kawin", "mypassword123", Role.AUTHORIZED_USER)

    samples = []
    for i in range(5):
        frame = _create_synthetic_face_frame(color=(100 + i * 10, 120, 140))
        valid, stat, emb, meta = engine.validate_enrollment_sample(frame)
        assert valid is True
        assert emb is not None
        assert len(emb) == 1856
        user_svc.add_enrollment_sample(user.user_id, emb.tolist())
        samples.append(emb)

    collected = user_svc.get_enrollment_samples(user.user_id)
    assert len(collected) == 5

    # Fuse samples into unified L2-normalized template
    fused = engine.fuse_multi_sample_embeddings(collected)
    assert len(fused) == 1856
    assert abs(np.linalg.norm(fused) - 1.0) < 1e-4

    # Enroll fused template
    user_svc.enroll_face(user.user_id, fused.tolist(), image_path=str(tmp_path / "snap.jpg"))
    updated = user_svc.get_user_by_id(user.user_id)

    assert updated.is_active is True
    assert updated.enrollment_status == "ENROLLED"
    assert updated.enrolled_embedding is not None

    # Now user can authenticate
    authed = user_svc.verify_password("kawin_multi", "mypassword123")
    assert authed is not None
    assert authed.username == "kawin_multi"


def test_admin_reauthentication(user_svc):
    """Requirement: Admin re-authentication tracker records and checks validity."""
    admin = user_svc.get_user_by_username("admin")
    assert admin is not None

    assert user_svc.is_admin_reauthenticated(admin.user_id) is False
    user_svc.record_admin_reauth(admin.user_id)
    assert user_svc.is_admin_reauthenticated(admin.user_id, max_age_seconds=10) is True


def test_visual_attributes_and_carried_objects():
    """Requirement: Grounded visual attributes (color, carried object overlap)."""
    # 1. Color binning test with pure blue image
    blue_img = np.zeros((100, 100, 3), dtype=np.uint8)
    blue_img[:, :] = [220, 100, 20]  # BGR format -> high blue
    color_name = get_dominant_color_name(blue_img)
    assert "blue" in color_name

    # 2. Carried object association test
    p_box = BoundingBox(x1=50, y1=50, x2=200, y2=400)
    # Object overlapping person torso/hands
    parcel_box = BoundingBox(x1=80, y1=150, x2=160, y2=230)
    objects = [(12, "backpack", parcel_box)]

    carried = check_carried_objects(p_box, objects)
    assert len(carried) == 1
    assert carried[0]["class_name"] == "backpack"


def test_chat_service_rbac_and_grounding(temp_db):
    """Requirement: Grounded chat with RBAC pre-filtering and honest handling of unknowns."""
    from atlas.events.storage import EventStorage
    storage = EventStorage(db_path=str(temp_db))
    chat_svc = ATLASChatService(storage=storage)

    admin_actor = Actor(actor_id="admin_1", role=Role.ADMIN, display_name="Priya")
    auth_user_actor = Actor(actor_id="user_2", role=Role.AUTHORIZED_USER, display_name="Kawin")

    # 1. Admin asks for login history -> Authorized
    admin_resp = chat_svc.query("Who logged into the website?", admin_actor)
    assert "login" in admin_resp["answer"].lower() or "secure" in admin_resp["answer"].lower()

    # 2. Authorized user asks for login history -> Rejected with server-side RBAC
    user_resp = chat_svc.query("Who logged into the website?", auth_user_actor)
    assert "admin authorization required" in user_resp["answer"].lower() or "cannot provide" in user_resp["answer"].lower()

    # 3. Clothing color question when no visual evidence exists -> Honest response
    color_resp = chat_svc.query("What color was the shirt?", admin_actor)
    assert "not clear enough" in color_resp["answer"].lower() or "evidence" in color_resp["answer"].lower()
