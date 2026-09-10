"""Integration tests for ATLAS Home Camera ON/OFF Control & Live Stream Fix."""

import pytest
import time
from atlas.config.settings import get_settings
from atlas.api.app import create_app
from atlas.camera.stream import get_camera_stream
from atlas.camera.base import CameraState
from atlas.authority.user_service import get_user_management_service
from atlas.authority.models import Role
from atlas.audit.service import get_audit_logger
from atlas.audit.schema import AuditAction
from atlas.incidents.service import get_incident_manager
from atlas.incidents.schema import Incident, IncidentType, IncidentStatus


@pytest.fixture
def app_client():
    settings = get_settings()
    app = create_app(settings)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client, app


def test_camera_stream_unit():
    """Verify CameraStream enable/disable and state transitions."""
    stream = get_camera_stream()
    assert stream.is_enabled is True

    # Disable camera
    stream.disable()
    assert stream.is_enabled is False
    assert stream.connection_state == CameraState.OFF
    assert stream.get_latest_frame() is None
    status = stream.get_status()
    assert status["enabled"] is False
    assert status["state"] == "off"

    # Re-enable camera
    stream.enable()
    assert stream.is_enabled is True
    assert stream.connection_state in (CameraState.CONNECTING, CameraState.CONNECTED)
    status = stream.get_status()
    assert status["enabled"] is True


def test_camera_control_rbac(app_client):
    """Test RBAC enforcement on POST /api/camera/control."""
    client, app = app_client

    # 1. Unauthenticated -> 401
    resp = client.post("/api/camera/control", json={"enabled": False})
    assert resp.status_code == 401

    # 2. Authorized User -> 403 Forbidden
    resp = client.post("/api/camera/control", json={"enabled": False}, headers={"X-Actor-ID": "sarah_resident"})
    assert resp.status_code == 403
    assert "Forbidden" in resp.get_json()["error"]

    # 3. Admin -> 200 OK
    resp = client.post("/api/camera/control", json={"enabled": False}, headers={"X-Actor-ID": "admin"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["camera_enabled"] is False
    assert data["camera_state"] == "off"

    # Re-enable via Admin
    resp = client.post("/api/camera/control", json={"enabled": True}, headers={"X-Actor-ID": "admin"})
    assert resp.status_code == 200
    assert resp.get_json()["camera_enabled"] is True


def test_camera_off_consequences(app_client):
    """When camera is OFF:
    - video_feed returns 503 CAMERA_OFF
    - camera/frame returns 503 CAMERA_OFF
    - /api/auth/login/face returns 503 CAMERA_UNAVAILABLE
    - existing active incidents remain untouched!
    """
    client, app = app_client
    stream = get_camera_stream()

    # Create an active incident to verify it is NOT deleted when camera is turned off
    inc_mgr = get_incident_manager()
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        status=IncidentStatus.ACTIVE,
        metadata={"summary": "Active incident before camera toggle"},
    )
    inc_mgr.storage.save_incident(inc)
    active_before = [i.incident_id for i in inc_mgr.get_active_incidents()]
    assert inc.incident_id in active_before

    # Turn camera OFF via Admin API
    resp = client.post("/api/camera/control", json={"enabled": False}, headers={"X-Actor-ID": "admin"})
    assert resp.status_code == 200

    # 1. video_feed -> 503
    resp_feed = client.get("/api/camera/video_feed", headers={"X-Actor-ID": "admin"})
    assert resp_feed.status_code == 503
    assert resp_feed.get_json()["status"] == "CAMERA_OFF"

    # 2. frame -> 503
    resp_frame = client.get("/api/camera/frame", headers={"X-Actor-ID": "admin"})
    assert resp_frame.status_code == 503
    assert resp_frame.get_json()["status"] == "CAMERA_OFF"

    # 3. Stage 1 login for admin, then face verification -> 503 CAMERA_UNAVAILABLE
    resp_login1 = client.post("/api/auth/login/credentials", json={"username": "admin", "password": "atlas_admin_2024!"})
    assert resp_login1.status_code == 200
    temp_token = resp_login1.get_json()["temp_token"]

    resp_face = client.post("/api/auth/login/face", json={"temp_token": temp_token})
    assert resp_face.status_code == 503
    data_face = resp_face.get_json()
    assert data_face["status"] == "CAMERA_UNAVAILABLE"
    assert "Camera is currently OFF" in data_face["details"]

    # 4. Verify active incidents are preserved intact
    active_after = [i.incident_id for i in inc_mgr.get_active_incidents()]
    assert inc.incident_id in active_after, "Active incident must NOT be removed when camera is turned off!"

    # 5. Audit trail records CAMERA_DISABLED
    audit_logger = get_audit_logger()
    records = audit_logger.get_recent_records(limit=10)
    actions = [r.action.value if hasattr(r, "action") else r["action"] for r in records]
    assert "CAMERA_DISABLED" in actions

    # Re-enable camera
    resp = client.post("/api/camera/control", json={"enabled": True}, headers={"X-Actor-ID": "admin"})
    assert resp.status_code == 200
    assert resp.get_json()["camera_enabled"] is True


def test_video_feed_authentication_and_alert_gating(app_client):
    """Test video_feed authentication and alert gating policy."""
    client, app = app_client
    stream = get_camera_stream()
    stream.enable()

    # 1. Unauthenticated without temp_token -> 401
    resp = client.get("/api/camera/video_feed")
    assert resp.status_code == 401

    # 2. Authorized User with NO active alert -> 403
    inc_mgr = get_incident_manager()
    with inc_mgr.storage._get_connection() as conn:
        conn.execute("UPDATE incidents SET status = 'RESOLVED' WHERE status IN ('ACTIVE', 'ACKNOWLEDGED')")
        conn.commit()

    resp = client.get("/api/camera/video_feed", headers={"X-Actor-ID": "sarah_resident"})
    assert resp.status_code == 403

    # 3. Authorized User WITH active alert -> 200 Stream
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        status=IncidentStatus.ACTIVE,
        metadata={"summary": "Fall detected in hall"},
    )
    inc_mgr.storage.save_incident(inc)

    resp = client.get("/api/camera/video_feed", headers={"X-Actor-ID": "sarah_resident"})
    assert resp.status_code == 200
    assert "multipart/x-mixed-replace" in resp.headers.get("Content-Type", "")

    # 4. Unauthenticated WITH valid Stage 1 temp_token -> 200 Stream (face preview during login)
    resp_l = client.post("/api/auth/login/credentials", json={"username": "admin", "password": "atlas_admin_2024!"})
    temp_token = resp_l.get_json()["temp_token"]

    resp_preview = client.get(f"/api/camera/video_feed?temp_token={temp_token}")
    assert resp_preview.status_code == 200
    assert "multipart/x-mixed-replace" in resp_preview.headers.get("Content-Type", "")
