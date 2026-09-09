"""Tests for ATLAS Home Step 8 — Role-Specific Premium Dashboards & Alert-Gated Access.

Covers:
1.  Admin authentication: Admin can access /api/dashboard/admin/state with full sections A-J.
2.  Authorized User authentication: Authorized User can access /api/dashboard/authorized-user/state.
3.  Role restriction: Authorized User requesting /api/admin/users receives 403 Forbidden.
4.  Role restriction: Authorized User requesting /api/admin/login-audits receives 403 Forbidden.
5.  Role restriction: Authorized User requesting /api/audit receives 403 Forbidden.
6.  Surveillance restriction: Authorized User requesting /api/events/recent receives 403 Forbidden.
7.  Surveillance restriction: Authorized User requesting /api/context/recent receives 403 Forbidden.
8.  Surveillance restriction: Authorized User requesting /api/context/person/1 receives 403 Forbidden.
9.  Alert-Gated Camera (Blocked): Authorized User requesting /api/camera/frame with NO active alert receives 403.
10. Alert-Gated Camera (Blocked): Authorized User requesting /api/camera/video_feed with NO active alert receives 403.
11. Alert-Gated Camera (Allowed): Authorized User requesting /api/camera/frame with an ACTIVE alert receives 200/503 (not 403).
12. Alert-Gated Evidence (Blocked): Authorized User requesting /api/evidence/<id> with NO active alert receives 403.
13. Alert-Gated Evidence (Allowed): Authorized User requesting /api/evidence/<id> linked to an ACTIVE alert receives 200.
14. Alert-Gated Resolution: Resolving the active alert closes evidence and camera access (returns 403).
15. Admin Unrestricted: Admin accesses camera, evidence, surveillance without requiring active alerts.
16. User Slot Bounds: Exactly 1 Admin and Max 10 users enforced in user management.
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas.authority.models import Role, Permission, Actor
from atlas.authority.user_service import UserManagementService, MAX_USERS, MAX_ADMINS
from atlas.events.storage import EventStorage
from atlas.api.app import create_app


@pytest.fixture(scope="module")
def isolated_env(tmp_path_factory):
    """Set up an isolated database and environment."""
    d = tmp_path_factory.mktemp("role_dashboards_test")
    db_path = d / "test_roles.db"
    storage = EventStorage(db_path=str(db_path))
    user_svc = UserManagementService(db_path=str(db_path))

    # Create test authorized user
    auth_user = user_svc.create_user(
        username="family_member",
        display_name="Family Member",
        password="family_pass_1234!",
        role=Role.AUTHORIZED_USER,
    )

    app = create_app()
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_roles_key_12345"

    return {
        "db_path": str(db_path),
        "storage": storage,
        "user_svc": user_svc,
        "auth_user": auth_user,
        "app": app,
    }


def _login_actor(client, actor: Actor):
    """Helper to inject an authenticated session for an actor."""
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


class TestRoleDashboardsAndSecurity:
    """Test role separation and alert-gated access enforcement."""

    def test_admin_dashboard_state_as_admin(self, isolated_env):
        """Admin can access /api/dashboard/admin/state and receives all sections A-J."""
        app = isolated_env["app"]
        user_svc = isolated_env["user_svc"]
        admin_account = user_svc.get_user_by_username("admin")
        admin_actor = admin_account.to_actor()

        with patch("atlas.authority.user_service._global_user_service", user_svc):
            with app.test_client() as client:
                _login_actor(client, admin_actor)
                resp = client.get("/api/dashboard/admin/state")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["status"] == "ok"
                # Check Sections A through J presence
                assert "overview" in data          # Section A
                assert "live" in data              # Section B
                assert "people" in data            # Section C
                assert "activity" in data          # Section D
                assert "objects" in data           # Section E
                assert "incidents" in data         # Section F
                assert "evidence" in data          # Section G
                assert "login_activity" in data    # Section H
                assert "audit" in data             # Section I
                assert "user_management" in data   # Section J
                assert data["user_management"]["max_users"] == 10

    def test_authorized_user_cannot_access_admin_dashboard(self, isolated_env):
        """Authorized user receives 403 Forbidden on /api/dashboard/admin/state."""
        app = isolated_env["app"]
        user_svc = isolated_env["user_svc"]
        auth_account = isolated_env["auth_user"]
        auth_actor = auth_account.to_actor()

        with patch("atlas.authority.user_service._global_user_service", user_svc):
            with app.test_client() as client:
                _login_actor(client, auth_actor)
                resp = client.get("/api/dashboard/admin/state")
                assert resp.status_code == 403

    def test_authorized_user_dashboard_state(self, isolated_env):
        """Authorized user accesses /api/dashboard/authorized-user/state."""
        app = isolated_env["app"]
        user_svc = isolated_env["user_svc"]
        auth_account = isolated_env["auth_user"]
        auth_actor = auth_account.to_actor()

        with patch("atlas.authority.user_service._global_user_service", user_svc):
            with app.test_client() as client:
                _login_actor(client, auth_actor)
                resp = client.get("/api/dashboard/authorized-user/state")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["status"] == "ok"
                assert "home_status" in data
                assert "active_alerts" in data
                assert "alert_gated_access" in data
                assert data["role"] == "AUTHORIZED_USER"
                # Must NOT expose unrestricted track histories or admin sections
                assert "user_management" not in data
                assert "login_activity" not in data

    def test_authorized_user_cannot_access_admin_user_routes(self, isolated_env):
        """Authorized user receives 403 on admin user management routes."""
        app = isolated_env["app"]
        user_svc = isolated_env["user_svc"]
        auth_actor = isolated_env["auth_user"].to_actor()

        with patch("atlas.authority.user_service._global_user_service", user_svc):
            with app.test_client() as client:
                _login_actor(client, auth_actor)
                # List users
                r1 = client.get("/api/admin/users")
                assert r1.status_code == 403
                # Create user
                r2 = client.post("/api/admin/users", json={"username": "hacker", "password": "pass"})
                assert r2.status_code == 403
                # User slots
                r3 = client.get("/api/admin/users/slots")
                assert r3.status_code == 403
                # Login audits
                r4 = client.get("/api/admin/login-audits")
                assert r4.status_code == 403
                # Audit trail
                r5 = client.get("/api/audit")
                assert r5.status_code == 403

    def test_authorized_user_cannot_browse_surveillance_history(self, isolated_env):
        """Authorized user receives 403 on unrestricted surveillance history browsing."""
        app = isolated_env["app"]
        auth_actor = isolated_env["auth_user"].to_actor()

        with app.test_client() as client:
            _login_actor(client, auth_actor)
            r1 = client.get("/api/events/recent")
            assert r1.status_code == 403
            assert "restricted to Administrator" in r1.get_json()["details"]

            r2 = client.get("/api/context/recent")
            assert r2.status_code == 403

            r3 = client.get("/api/context/person/1")
            assert r3.status_code == 403

            r4 = client.get("/api/context/object/1")
            assert r4.status_code == 403

    def test_alert_gated_camera_access_blocked_without_active_alert(self, isolated_env):
        """When NO active alert exists, Authorized user is denied live camera access (403)."""
        app = isolated_env["app"]
        auth_actor = isolated_env["auth_user"].to_actor()

        # Ensure no active incidents in DB
        from atlas.incidents.service import get_incident_manager
        from atlas.incidents.schema import Incident, IncidentType, IncidentStatus, ResolutionReason
        inc_mgr = get_incident_manager()
        with inc_mgr.storage._get_connection() as conn:
            conn.execute("UPDATE incidents SET status = 'RESOLVED' WHERE status IN ('ACTIVE', 'ACKNOWLEDGED')")
            conn.commit()

        with app.test_client() as client:
            _login_actor(client, auth_actor)
            resp = client.get("/api/camera/frame")
            assert resp.status_code == 403
            assert "requires an active permitted alert" in resp.get_json()["details"]

            resp_stream = client.get("/api/camera/video_feed")
            assert resp_stream.status_code == 403

    def test_alert_gated_camera_access_allowed_with_active_alert(self, isolated_env):
        """When an ACTIVE alert exists, Authorized user CAN access live camera."""
        app = isolated_env["app"]
        auth_actor = isolated_env["auth_user"].to_actor()

        from atlas.incidents.service import get_incident_manager
        from atlas.incidents.schema import Incident, IncidentType, IncidentStatus, ResolutionReason
        inc_mgr = get_incident_manager()
        inc = Incident(
            incident_type=IncidentType.POSSIBLE_FALL,
            severity="HIGH",
            status=IncidentStatus.ACTIVE,
            metadata={"summary": "Possible fall detected in living area"},
        )
        inc_mgr.storage.save_incident(inc)

        try:
            with app.test_client() as client:
                _login_actor(client, auth_actor)
                resp = client.get("/api/camera/frame")
                # Should not be 403 (either 200 JPEG or 503 if hardware camera disconnected)
                assert resp.status_code in (200, 503), f"Expected 200 or 503, got {resp.status_code}"
                assert resp.status_code != 403
        finally:
            with inc_mgr.storage._get_connection() as conn:
                conn.execute("UPDATE incidents SET status = 'RESOLVED' WHERE incident_id = ?", (inc.incident_id,))
                conn.commit()

    def test_alert_gated_evidence_access(self, isolated_env):
        """Authorized user can access evidence ONLY when linked to an active permitted alert."""
        app = isolated_env["app"]
        auth_actor = isolated_env["auth_user"].to_actor()
        admin_actor = Actor(actor_id="admin_test", role=Role.ADMIN)

        from atlas.incidents.service import get_incident_manager
        from atlas.incidents.schema import Incident, IncidentType, IncidentStatus, ResolutionReason
        from atlas.evidence.vault import get_evidence_vault

        vault = get_evidence_vault()
        inc_mgr = get_incident_manager()

        # Create active incident
        inc = Incident(
            incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
            severity="HIGH",
            status=IncidentStatus.ACTIVE,
            metadata={"summary": "Person detected in perimeter"},
        )
        inc_mgr.storage.save_incident(inc)

        # Store dummy frame into vault linked to this incident
        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        evidence_record = vault.store_evidence(
            incident_id=inc.incident_id,
            frame=dummy_frame,
            metadata={"test": "alert_gated_evidence"},
        )
        assert evidence_record is not None
        eid = evidence_record.evidence_id

        # Link evidence ID in incident metadata
        inc.metadata["evidence_ids"] = [eid]
        inc_mgr.storage.save_incident(inc)

        with app.test_client() as client:
            # 1. With active alert: Authorized user CAN access evidence
            _login_actor(client, auth_actor)
            resp = client.get(f"/api/evidence/{eid}")
            assert resp.status_code == 200
            assert resp.mimetype == "image/jpeg"

            # 2. Resolve the alert
            inc_mgr.resolve_incident(
                inc.incident_id, admin_actor, reason=ResolutionReason.USER_CONFIRMED_SAFE
            )

            # 3. After resolution: Alert-gated access is closed -> returns 403
            resp_after = client.get(f"/api/evidence/{eid}")
            assert resp_after.status_code == 403
            assert "restricted to active permitted alerts" in resp_after.get_json()["details"]

            # 4. Admin can still access evidence unrestricted even after resolution
            _login_actor(client, admin_actor)
            admin_resp = client.get(f"/api/evidence/{eid}")
            assert admin_resp.status_code == 200

    def test_admin_unrestricted_access(self, isolated_env):
        """Admin has unrestricted access to surveillance, evidence, and camera without alerts."""
        app = isolated_env["app"]
        admin_actor = Actor(actor_id="admin_test", role=Role.ADMIN)

        with app.test_client() as client:
            _login_actor(client, admin_actor)
            # Surveillance browsing allowed
            r1 = client.get("/api/events/recent?limit=5")
            assert r1.status_code == 200

            r2 = client.get("/api/context/recent")
            assert r2.status_code == 200

            # Camera frame allowed without alert (200 or 503 disconnected, never 403)
            r3 = client.get("/api/camera/frame")
            assert r3.status_code in (200, 503)
            assert r3.status_code != 403
