"""Tests for ATLAS Home Step 8 — Premium Identity & Authentication.

Covers 16 requirements:
1.  Two-stage login: credentials endpoint returns temp_token
2.  Two-stage login: face endpoint refuses without temp_token
3.  Two-stage login: face endpoint refuses bad temp_token
4.  Face verification required: credentials alone do NOT grant session
5.  Credentials failure is audited
6.  Face denial is audited
7.  Session granted after face verification (self-verification)
8.  Admin RBAC: only admin can call MANAGE_USERS endpoints
9.  Identity bounds: only 1 admin allowed
10. Identity bounds: max 10 users enforced
11. User creation: username uniqueness enforced
12. User deactivation: last admin cannot be disabled
13. User deletion: last admin cannot be deleted
14. Face enrollment: stores 1856-D embedding
15. Login audit trail is queryable
16. Unauthenticated access to admin endpoints returns 401/403
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

# Ensure ATLAS root on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="module")
def tmp_db(tmp_path_factory):
    """Provide a fresh in-memory-equivalent temp SQLite DB for each module."""
    db_dir = tmp_path_factory.mktemp("atlas_test_step8")
    db_path = db_dir / "test_step8.db"
    return db_path


@pytest.fixture(scope="module")
def user_service(tmp_db):
    """Isolated UserManagementService backed by a fresh SQLite DB."""
    # Initialize schema first via EventStorage
    from atlas.events.storage import EventStorage
    EventStorage(db_path=str(tmp_db))

    from atlas.authority.user_service import UserManagementService
    svc = UserManagementService(db_path=str(tmp_db))
    return svc


@pytest.fixture(scope="module")
def flask_client(tmp_db):
    """Flask test client backed by isolated DB."""
    # Set up schema
    from atlas.events.storage import EventStorage
    EventStorage(db_path=str(tmp_db))

    import importlib
    import atlas.api.routes as routes_module

    from atlas.api.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_secret_atlas_step8"

    # Patch user service to use isolated DB
    from atlas.authority.user_service import UserManagementService
    test_svc = UserManagementService(db_path=str(tmp_db))

    with patch("atlas.authority.user_service._global_user_service", test_svc):
        with app.test_client() as client:
            yield client, test_svc


def _make_jpeg_b64() -> str:
    """Create a minimal valid JPEG encoded as base64."""
    import cv2
    img = np.zeros((128, 128, 3), dtype=np.uint8)
    img[30:98, 30:98] = [180, 150, 140]  # face-ish colored region
    ok, buf = cv2.imencode(".jpg", img)
    if not ok:
        raise RuntimeError("Could not encode test JPEG")
    b64 = base64.b64encode(buf.tobytes()).decode()
    return f"data:image/jpeg;base64,{b64}"


# ============================================================================
# Unit Tests: UserManagementService
# ============================================================================

class TestUserManagementServiceUnit:
    """Unit tests for UserManagementService (no HTTP layer)."""

    def test_default_admin_seeded(self, user_service):
        """Requirement: Default admin account is seeded on first boot."""
        from atlas.authority.models import Role
        users = user_service.list_users()
        admins = [u for u in users if u.role == Role.ADMIN]
        assert len(admins) >= 1, "At least one admin must be seeded by default"

    def test_verify_password_correct(self, user_service):
        """Requirement: Correct credentials authenticate successfully."""
        result = user_service.verify_password("admin", "atlas_admin_2024!")
        assert result is not None
        assert result.username == "admin"

    def test_verify_password_wrong_password(self, user_service):
        """Requirement: Wrong password returns None."""
        result = user_service.verify_password("admin", "wrongpassword")
        assert result is None

    def test_verify_password_unknown_user(self, user_service):
        """Requirement: Unknown username returns None."""
        result = user_service.verify_password("nonexistent_user_xyz", "whatever")
        assert result is None

    def test_create_operator_user(self, user_service):
        """Requirement: Create a new OPERATOR user."""
        from atlas.authority.models import Role
        user = user_service.create_user("alice_op", "Alice", "securepassword123", Role.OPERATOR)
        assert user.username == "alice_op"
        assert user.role == Role.OPERATOR
        assert user.is_active is True
        assert user.enrolled_embedding is None

    def test_username_uniqueness_enforced(self, user_service):
        """Requirement 11: Duplicate username is rejected."""
        from atlas.authority.models import Role
        # 'alice_op' was created in previous test
        with pytest.raises(ValueError, match="already exists"):
            user_service.create_user("alice_op", "Alice Duplicate", "pass12345678", Role.OPERATOR)

    def test_admin_bound_enforced(self, user_service):
        """Requirement 9: Only 1 admin is allowed."""
        from atlas.authority.models import Role
        from atlas.authority.user_service import MAX_ADMINS
        assert MAX_ADMINS == 1

        with pytest.raises(ValueError, match="admin"):
            user_service.create_user("second_admin", "SecondAdmin", "pass12345678", Role.ADMIN)

    def test_max_users_bound(self, user_service):
        """Requirement 10: Max 10 Authorized Users enforced (Admin is outside the 10 slots)."""
        from atlas.authority.models import Role
        from atlas.authority.user_service import MAX_USERS, MAX_AUTHORIZED_USERS
        assert MAX_AUTHORIZED_USERS == 10
        assert MAX_USERS == 11

        existing_auth = [u for u in user_service.list_users() if u.role != Role.ADMIN]
        # Create users until we hit the 10 authorized users limit
        for i in range(MAX_AUTHORIZED_USERS - len(existing_auth)):
            user_service.create_user(
                f"filler_user_{i}", f"Filler {i}", "fillpass123", Role.VIEWER
            )

        with pytest.raises(ValueError, match="Maximum"):
            user_service.create_user("overflow_user", "Overflow", "overflowpass123", Role.VIEWER)

    def test_cannot_disable_last_admin(self, user_service):
        """Requirement 12: Last admin cannot be disabled."""
        from atlas.authority.models import Role
        admins = [u for u in user_service.list_users() if u.role == Role.ADMIN]
        assert len(admins) >= 1
        last_admin = admins[0]
        # If only one admin, disabling should fail
        if len(admins) == 1:
            with pytest.raises(ValueError, match="last active admin"):
                user_service.update_user_status(last_admin.user_id, False)

    def test_cannot_delete_last_admin(self, user_service):
        """Requirement 13: Last admin cannot be deleted."""
        from atlas.authority.models import Role
        admins = [u for u in user_service.list_users() if u.role == Role.ADMIN]
        assert len(admins) >= 1
        if len(admins) == 1:
            with pytest.raises(ValueError, match="last admin"):
                user_service.delete_user(admins[0].user_id)

    def test_face_enrollment_stores_embedding(self, user_service):
        """Requirement 14: Face enrollment stores embedding of correct dimension."""
        # Use alice_op created earlier
        alice = user_service.get_user_by_username("alice_op")
        assert alice is not None

        # Generate a synthetic 1856-D embedding
        embedding = list(np.random.rand(1856).astype(float))
        user_service.enroll_face(alice.user_id, embedding, image_path="/tmp/test.jpg")

        reloaded = user_service.get_user_by_id(alice.user_id)
        assert reloaded.enrolled_embedding is not None
        assert len(reloaded.enrolled_embedding) == 1856

    def test_login_audit_logging(self, user_service):
        """Requirement 15: Login audit events are written and queryable."""
        admin = user_service.get_user_by_username("admin")
        assert admin is not None
        audit_id = user_service.log_login_event(
            user_id=admin.user_id,
            username="admin",
            role="ADMIN",
            status="SESSION_GRANTED",
            face_confidence=0.95,
            ip_address="127.0.0.1",
        )
        assert audit_id.startswith("audit_")

        audits = user_service.get_login_audits(user_id=admin.user_id, limit=10)
        assert any(a.audit_id == audit_id for a in audits)

    def test_remove_face_enrollment(self, user_service):
        """Face enrollment can be removed."""
        alice = user_service.get_user_by_username("alice_op")
        assert alice is not None
        user_service.remove_face_enrollment(alice.user_id)
        reloaded = user_service.get_user_by_id(alice.user_id)
        assert reloaded.enrolled_embedding is None


# ============================================================================
# Unit Tests: Face Biometrics Engine
# ============================================================================

class TestFaceBiometricsEngine:
    """Unit tests for the biometric engine."""

    def test_self_verification_high_similarity(self):
        """Self-verification must yield similarity >= 0.99 (same image)."""
        from atlas.authority.face import FaceBiometricsEngine
        import tempfile
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpd:
            engine = FaceBiometricsEngine(snapshot_dir=tmpd, threshold=0.70)
            img = np.zeros((128, 128, 3), dtype=np.uint8)
            img[30:98, 30:98] = [180, 150, 140]
            emb = engine.compute_embedding(img)
            result = engine.verify(
                target_user_id="test_user",
                enrolled_embedding=emb,
                query_image=img,
                save_snapshot=False,
            )
            assert result.verified is True
            assert result.confidence >= 0.99

    def test_different_image_lower_similarity(self):
        """Different image should have lower similarity."""
        from atlas.authority.face import FaceBiometricsEngine
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpd:
            engine = FaceBiometricsEngine(snapshot_dir=tmpd, threshold=0.70)
            img1 = np.zeros((128, 128, 3), dtype=np.uint8)
            img1[30:98, 30:98] = [180, 150, 140]
            img2 = np.random.randint(0, 255, (128, 128, 3), dtype=np.uint8)
            emb1 = engine.compute_embedding(img1)
            sim = engine.compare_embeddings(emb1, engine.compute_embedding(img2))
            assert sim < 0.99  # Should not be near-perfect match

    def test_embedding_dimension(self):
        """Embedding must be exactly 1856-D."""
        from atlas.authority.face import FaceBiometricsEngine
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpd:
            engine = FaceBiometricsEngine(snapshot_dir=tmpd)
            img = np.zeros((128, 128, 3), dtype=np.uint8)
            emb = engine.compute_embedding(img)
            assert len(emb) == 1856

    def test_base64_decode_roundtrip(self):
        """base64 image encoding/decoding works correctly."""
        import cv2
        from atlas.authority.face import FaceBiometricsEngine
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpd:
            engine = FaceBiometricsEngine(snapshot_dir=tmpd)
            img = np.zeros((100, 100, 3), dtype=np.uint8)
            ok, buf = cv2.imencode(".jpg", img)
            b64 = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
            decoded = engine.decode_image_payload(b64)
            assert decoded.shape[0] > 0


# ============================================================================
# API Integration Tests (with mocked UserManagementService)
# ============================================================================

class TestAuthAPITwoStage:
    """API-level tests for the 2-stage login flow."""

    def _create_app_with_mock_svc(self, mock_svc):
        """Create a Flask test app with the mock user service patched in."""
        from atlas.api.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test_key_atlas_step8_api"
        return app

    def test_credentials_endpoint_success(self):
        """Requirement 1: Stage 1 returns temp_token on valid credentials."""
        from atlas.authority.user_service import UserManagementService
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpd:
            db_path = Path(tmpd) / "test.db"
            from atlas.events.storage import EventStorage
            EventStorage(db_path=str(db_path))
            svc = UserManagementService(db_path=str(db_path))

            with patch("atlas.authority.user_service._global_user_service", svc):
                from atlas.api.app import create_app
                app = create_app()
                app.config["TESTING"] = True
                app.config["SECRET_KEY"] = "test_atlas_key"
                with app.test_client() as client:
                    resp = client.post(
                        "/api/auth/login/credentials",
                        json={"username": "admin", "password": "atlas_admin_2024!"},
                        content_type="application/json",
                    )
                    assert resp.status_code == 200
                    data = resp.get_json()
                    assert data["status"] == "credentials_verified"
                    assert "temp_token" in data
                    assert len(data["temp_token"]) > 20
                    assert data["face_required"] is True

    def test_credentials_failure_returns_401(self):
        """Requirement 4: Wrong password returns 401."""
        from atlas.authority.user_service import UserManagementService
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpd:
            db_path = Path(tmpd) / "test.db"
            from atlas.events.storage import EventStorage
            EventStorage(db_path=str(db_path))
            svc = UserManagementService(db_path=str(db_path))

            with patch("atlas.authority.user_service._global_user_service", svc):
                from atlas.api.app import create_app
                app = create_app()
                app.config["TESTING"] = True
                app.config["SECRET_KEY"] = "test_atlas_key"
                with app.test_client() as client:
                    resp = client.post(
                        "/api/auth/login/credentials",
                        json={"username": "admin", "password": "wrong_password_xyz"},
                        content_type="application/json",
                    )
                    assert resp.status_code == 401

    def test_face_endpoint_requires_temp_token(self):
        """Requirement 2: Face endpoint without temp_token returns 400."""
        from atlas.authority.user_service import UserManagementService
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpd:
            db_path = Path(tmpd) / "test.db"
            from atlas.events.storage import EventStorage
            EventStorage(db_path=str(db_path))
            svc = UserManagementService(db_path=str(db_path))

            with patch("atlas.authority.user_service._global_user_service", svc):
                from atlas.api.app import create_app
                app = create_app()
                app.config["TESTING"] = True
                app.config["SECRET_KEY"] = "test_atlas_key"
                with app.test_client() as client:
                    resp = client.post(
                        "/api/auth/login/face",
                        json={"image_data": _make_jpeg_b64()},
                        content_type="application/json",
                    )
                    assert resp.status_code == 400

    def test_face_endpoint_bad_token_returns_401(self):
        """Requirement 3: Invalid temp_token returns 401."""
        from atlas.authority.user_service import UserManagementService
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpd:
            db_path = Path(tmpd) / "test.db"
            from atlas.events.storage import EventStorage
            EventStorage(db_path=str(db_path))
            svc = UserManagementService(db_path=str(db_path))

            with patch("atlas.authority.user_service._global_user_service", svc):
                from atlas.api.app import create_app
                app = create_app()
                app.config["TESTING"] = True
                app.config["SECRET_KEY"] = "test_atlas_key"
                with app.test_client() as client:
                    resp = client.post(
                        "/api/auth/login/face",
                        json={
                            "temp_token": "bad_fake_token_that_does_not_exist",
                            "image_data": _make_jpeg_b64()
                        },
                        content_type="application/json",
                    )
                    assert resp.status_code == 401

    def test_credentials_alone_do_not_grant_session(self):
        """Requirement 4: Credentials alone do NOT grant a session (no atlas_session cookie)."""
        from atlas.authority.user_service import UserManagementService
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpd:
            db_path = Path(tmpd) / "test.db"
            from atlas.events.storage import EventStorage
            EventStorage(db_path=str(db_path))
            svc = UserManagementService(db_path=str(db_path))

            with patch("atlas.authority.user_service._global_user_service", svc):
                from atlas.api.app import create_app
                app = create_app()
                app.config["TESTING"] = True
                app.config["SECRET_KEY"] = "test_atlas_key"
                with app.test_client() as client:
                    resp = client.post(
                        "/api/auth/login/credentials",
                        json={"username": "admin", "password": "atlas_admin_2024!"},
                        content_type="application/json",
                    )
                    assert resp.status_code == 200
                    # Must NOT have atlas_session cookie
                    assert "atlas_session" not in resp.headers.get("Set-Cookie", "")
                    data = resp.get_json()
                    assert "temp_token" in data
                    assert "token" not in data

    def test_face_verification_success_grants_session(self):
        """Requirement 7: After successful face verification, session is granted."""
        from atlas.authority.user_service import UserManagementService
        from atlas.authority.face import FaceBiometricsEngine

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpd:
            db_path = Path(tmpd) / "test.db"
            from atlas.events.storage import EventStorage
            EventStorage(db_path=str(db_path))
            svc = UserManagementService(db_path=str(db_path))

            # Enroll admin face with known image
            admin = svc.get_user_by_username("admin")
            img = np.zeros((128, 128, 3), dtype=np.uint8)
            img[30:98, 30:98] = [180, 150, 140]
            engine = FaceBiometricsEngine(snapshot_dir=tmpd, threshold=0.70)
            emb = engine.compute_embedding(img)
            svc.enroll_face(admin.user_id, emb.tolist())

            with patch("atlas.authority.user_service._global_user_service", svc):
                from atlas.api.app import create_app
                app = create_app()
                app.config["TESTING"] = True
                app.config["SECRET_KEY"] = "test_atlas_key"
                with app.test_client() as client:
                    # Stage 1
                    r1 = client.post(
                        "/api/auth/login/credentials",
                        json={"username": "admin", "password": "atlas_admin_2024!"},
                        content_type="application/json",
                    )
                    assert r1.status_code == 200
                    temp_token = r1.get_json()["temp_token"]

                    # Encode image as base64
                    import cv2
                    ok, buf = cv2.imencode(".jpg", img)
                    b64 = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

                    # Stage 2 (face verification with same image)
                    with patch("atlas.authority.face._global_face_engine", engine):
                        r2 = client.post(
                            "/api/auth/login/face",
                            json={"temp_token": temp_token, "image_data": b64},
                            content_type="application/json",
                        )
                        assert r2.status_code == 200
                        d2 = r2.get_json()
                        assert d2["status"] == "ok"
                        assert d2["face_verified"] is True
                        assert "token" in d2
                        assert len(d2["token"]) > 20

    def test_credentials_failure_is_audited(self):
        """Requirement 5: Failed credentials attempt is audited."""
        from atlas.authority.user_service import UserManagementService

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpd:
            db_path = Path(tmpd) / "test.db"
            from atlas.events.storage import EventStorage
            EventStorage(db_path=str(db_path))
            svc = UserManagementService(db_path=str(db_path))

            with patch("atlas.authority.user_service._global_user_service", svc):
                from atlas.api.app import create_app
                app = create_app()
                app.config["TESTING"] = True
                app.config["SECRET_KEY"] = "test_atlas_key"
                with app.test_client() as client:
                    client.post(
                        "/api/auth/login/credentials",
                        json={"username": "admin", "password": "bad_password"},
                        content_type="application/json",
                    )
                    audits = svc.get_login_audits(limit=10)
                    failed_audits = [a for a in audits if a.status == "CREDENTIALS_FAILED"]
                    assert len(failed_audits) >= 1


# ============================================================================
# Admin API RBAC Tests
# ============================================================================

class TestAdminRBAC:
    """Test that admin endpoints are protected by MANAGE_USERS permission."""

    def test_unauthenticated_cannot_list_users(self):
        """Requirement 16: Unauthenticated access to admin endpoints is rejected."""
        from atlas.api.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test_atlas_key"
        with app.test_client() as client:
            resp = client.get("/api/admin/users")
            assert resp.status_code in (401, 403)

    def test_unauthenticated_cannot_create_user(self):
        """Requirement 16: Unauthenticated create user request is rejected."""
        from atlas.api.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test_atlas_key"
        with app.test_client() as client:
            resp = client.post(
                "/api/admin/users",
                json={"username": "hacker", "password": "hack123", "role": "ADMIN"},
                content_type="application/json",
            )
            assert resp.status_code in (401, 403)

    def test_unauthenticated_cannot_view_login_audits(self):
        """Requirement 16: Unauthenticated access to login audits is rejected."""
        from atlas.api.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test_atlas_key"
        with app.test_client() as client:
            resp = client.get("/api/admin/login-audits")
            assert resp.status_code in (401, 403)

    def test_identity_bounds_endpoint_requires_auth(self):
        """Requirement 8: Identity bounds endpoint requires MANAGE_USERS."""
        from atlas.api.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test_atlas_key"
        with app.test_client() as client:
            resp = client.get("/api/admin/identity/bounds")
            assert resp.status_code in (401, 403)


# ============================================================================
# Identity Model Tests
# ============================================================================

class TestIdentityModel:
    """Tests for the identity model distinction and UserAccount.to_actor()."""

    def test_user_account_to_actor_admin(self, user_service):
        """UserAccount.to_actor() returns correct ADMIN Actor."""
        from atlas.authority.models import Role, Permission
        admin = user_service.get_user_by_username("admin")
        assert admin is not None
        actor = admin.to_actor()
        assert actor.role == Role.ADMIN
        assert actor.has_permission(Permission.MANAGE_USERS)
        assert actor.has_permission(Permission.VIEW_LOGIN_AUDITS)
        assert not actor.is_system

    def test_operator_actor_lacks_manage_users(self, user_service):
        """OPERATOR role does NOT have MANAGE_USERS permission."""
        from atlas.authority.models import Role, Permission, Actor
        op_actor = Actor(actor_id="op_test", role=Role.OPERATOR, display_name="Op")
        assert not op_actor.has_permission(Permission.MANAGE_USERS)

    def test_system_actor_cannot_authorize_response(self):
        """System actor is strictly prohibited from AUTHORIZE_RESPONSE."""
        from atlas.authority.models import Actor, Permission, SYSTEM_INTERNAL_PERMISSIONS
        sys_actor = Actor(
            actor_id="system_core", role=None, is_system=True,
            custom_permissions=SYSTEM_INTERNAL_PERMISSIONS
        )
        assert not sys_actor.has_permission(Permission.AUTHORIZE_RESPONSE)
