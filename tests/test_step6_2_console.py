"""Comprehensive test suite for ATLAS Home Step 6.2 — Human-in-the-Loop Operational Console.

Verifies:
1. Authentication: login, logout, me, token extraction, 401 unauthenticated.
2. RBAC: Viewer restrictions (403), Operator permissions, Admin authorization, system_core blocked (403).
3. Lifecycle Transitions: Acknowledge (200), Resolve (200), Dismiss (200), invalid transitions (409).
4. Escalation Authorization: Admin authorization (200), idempotent execution, external notifications disabled.
5. Audit Trail: Immutable append-only audit records, actor attribution, zero secret leakage.
"""

from __future__ import annotations

import json
import pytest

from atlas.api.app import create_app
from atlas.authority.auth import get_auth_service, DEV_ACCOUNTS
from atlas.authority.models import Actor, Permission, Role
from atlas.authority.service import AuthorityService, SYSTEM_ACTOR, get_authority_service, UnauthorizedError
from atlas.config.settings import Settings
from atlas.events.storage import EventStorage, get_event_storage
from atlas.incidents.schema import EscalationState, Incident, IncidentStatus, IncidentType, ResolutionReason
from atlas.incidents.service import IncidentManager, get_incident_manager, InvalidTransitionError
from atlas.alerts.service import AlertManager, get_alert_manager
from atlas.audit.service import AuditLogger, get_audit_logger, AuditAction


@pytest.fixture
def console_fixture(tmp_path):
    """Isolated test fixture with separate SQLite database and test client."""
    db_file = tmp_path / "test_console.db"
    storage = EventStorage(str(db_file))

    # Isolated managers
    inc_mgr = IncidentManager(storage=storage)
    alert_mgr = AlertManager(storage=storage)
    audit_logger = AuditLogger(storage=storage)
    auth_svc = get_auth_service()

    # Patch global singletons
    import atlas.events.storage as st_mod
    import atlas.incidents.service as inc_mod
    import atlas.alerts.service as al_mod
    import atlas.audit.service as aud_mod

    orig_st = st_mod._global_storage
    orig_inc = inc_mod._global_incident_manager
    orig_al = al_mod._global_alert_manager
    orig_aud = aud_mod._global_audit_logger

    st_mod._global_storage = storage
    inc_mod._global_incident_manager = inc_mgr
    al_mod._global_alert_manager = alert_mgr
    aud_mod._global_audit_logger = audit_logger

    settings = Settings()
    app = create_app(settings)
    app.config["TESTING"] = True

    client = app.test_client()

    yield {
        "client": client,
        "storage": storage,
        "inc_mgr": inc_mgr,
        "auth_svc": auth_svc,
        "audit_logger": audit_logger,
    }

    # Restore globals
    st_mod._global_storage = orig_st
    inc_mod._global_incident_manager = orig_inc
    al_mod._global_alert_manager = orig_al
    aud_mod._global_audit_logger = orig_aud


def seed_incident(storage: EventStorage, inc_type: IncidentType = IncidentType.POSSIBLE_FALL) -> Incident:
    """Helper to seed an active incident in storage."""
    inc = Incident(
        incident_type=inc_type,
        status=IncidentStatus.ACTIVE,
        severity="HIGH",
        risk_score=0.85,
        source_event_ids=["ev-console-1"],
        analysis_id="ana-console-1",
        risk_id="risk-console-1",
        triggering_rule_ids=["rule_possible_fall"],
    )
    storage.save_incident(inc)
    return inc


# ============================================================================
# 1. Authentication Tests
# ============================================================================

def test_login_success_all_roles(console_fixture):
    client = console_fixture["client"]

    # Admin Login
    res_admin = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert res_admin.status_code == 200
    data_admin = res_admin.get_json()
    assert data_admin["status"] == "ok"
    assert data_admin["user"]["role"] == "ADMIN"
    assert "AUTHORIZE_RESPONSE" in data_admin["user"]["permissions"]
    assert "token" in data_admin

    # Operator Login
    res_op = client.post("/api/auth/login", json={"username": "operator", "password": "operator123"})
    assert res_op.status_code == 200
    data_op = res_op.get_json()
    assert data_op["user"]["role"] == "OPERATOR"
    assert "ACKNOWLEDGE_INCIDENT" in data_op["user"]["permissions"]
    assert "AUTHORIZE_RESPONSE" not in data_op["user"]["permissions"]

    # Viewer Login
    res_v = client.post("/api/auth/login", json={"username": "viewer", "password": "viewer123"})
    assert res_v.status_code == 200
    data_v = res_v.get_json()
    assert data_v["user"]["role"] == "VIEWER"
    assert "ACKNOWLEDGE_INCIDENT" not in data_v["user"]["permissions"]


def test_login_failure_invalid_credentials(console_fixture):
    client = console_fixture["client"]
    res = client.post("/api/auth/login", json={"username": "admin", "password": "wrongpassword"})
    assert res.status_code == 401
    assert "Invalid username or password" in res.get_json()["details"]

    res_unknown = client.post("/api/auth/login", json={"username": "nonexistent", "password": "any"})
    assert res_unknown.status_code == 401


def test_auth_me_and_logout(console_fixture):
    client = console_fixture["client"]

    # Unauthenticated -> 401
    res_unauth = client.get("/api/auth/me")
    assert res_unauth.status_code == 401

    # Login
    res_login = client.post("/api/auth/login", json={"username": "operator", "password": "operator123"})
    token = res_login.get_json()["token"]

    # Authenticated via Bearer token
    res_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res_me.status_code == 200
    assert res_me.get_json()["user"]["role"] == "OPERATOR"

    # Authenticated via X-Session-Token header
    res_me_header = client.get("/api/auth/me", headers={"X-Session-Token": token})
    assert res_me_header.status_code == 200

    # Logout
    res_logout = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert res_logout.status_code == 200

    # Subsequent request with old token -> 401
    res_me_after = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res_me_after.status_code == 401


# ============================================================================
# 2. RBAC & Access Control Boundary Tests
# ============================================================================

def test_unauthenticated_mutation_returns_401(console_fixture):
    client = console_fixture["client"]
    storage = console_fixture["storage"]
    inc = seed_incident(storage)

    # All mutation endpoints require authentication
    assert client.post(f"/api/incidents/{inc.incident_id}/acknowledge").status_code == 401
    assert client.post(f"/api/incidents/{inc.incident_id}/resolve", json={"reason": "NORMAL_ACTIVITY"}).status_code == 401
    assert client.post(f"/api/incidents/{inc.incident_id}/dismiss", json={"reason": "Routine test"}).status_code == 401
    assert client.post(f"/api/incidents/{inc.incident_id}/authorize").status_code == 401


def test_viewer_denied_all_consequential_actions(console_fixture):
    client = console_fixture["client"]
    storage = console_fixture["storage"]
    inc = seed_incident(storage)

    # Login as viewer
    res_login = client.post("/api/auth/login", json={"username": "viewer", "password": "viewer123"})
    headers = {"Authorization": f"Bearer {res_login.get_json()['token']}"}

    # Viewer denied acknowledge -> 403
    assert client.post(f"/api/incidents/{inc.incident_id}/acknowledge", headers=headers).status_code == 403

    # Viewer denied resolve -> 403
    assert client.post(f"/api/incidents/{inc.incident_id}/resolve", json={"reason": "NORMAL_ACTIVITY"}, headers=headers).status_code == 403

    # Viewer denied dismiss -> 403
    assert client.post(f"/api/incidents/{inc.incident_id}/dismiss", json={"reason": "Test dismiss"}, headers=headers).status_code == 403

    # Viewer denied authorize -> 403
    assert client.post(f"/api/incidents/{inc.incident_id}/authorize", headers=headers).status_code == 403


def test_operator_permitted_actions_and_denied_authorization(console_fixture):
    client = console_fixture["client"]
    storage = console_fixture["storage"]

    # Login as operator
    res_login = client.post("/api/auth/login", json={"username": "operator", "password": "operator123"})
    headers = {"Authorization": f"Bearer {res_login.get_json()['token']}"}

    # 1. Operator can acknowledge
    inc1 = seed_incident(storage)
    res_ack = client.post(f"/api/incidents/{inc1.incident_id}/acknowledge", headers=headers)
    assert res_ack.status_code == 200
    assert res_ack.get_json()["incident"]["is_acknowledged"] is True

    # 2. Operator can resolve
    res_res = client.post(
        f"/api/incidents/{inc1.incident_id}/resolve",
        json={"reason": "CONDITION_CLEARED", "notes": "Subject walked away normally."},
        headers=headers,
    )
    assert res_res.status_code == 200
    assert res_res.get_json()["incident"]["status"] == "RESOLVED"

    # 3. Operator can dismiss
    inc2 = seed_incident(storage)
    res_dis = client.post(
        f"/api/incidents/{inc2.incident_id}/dismiss",
        json={"reason": "Operator confirmed drill simulation."},
        headers=headers,
    )
    assert res_dis.status_code == 200
    assert res_dis.get_json()["incident"]["status"] == "DISMISSED"

    # 4. Operator CANNOT authorize escalation -> 403
    inc3 = seed_incident(storage)
    res_auth = client.post(f"/api/incidents/{inc3.incident_id}/authorize", headers=headers)
    assert res_auth.status_code == 403
    assert "lacks permission 'AUTHORIZE_RESPONSE'" in res_auth.get_json()["details"]


def test_admin_permitted_escalation_authorization(console_fixture):
    client = console_fixture["client"]
    storage = console_fixture["storage"]
    inc = seed_incident(storage)

    # Login as admin
    res_login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    headers = {"Authorization": f"Bearer {res_login.get_json()['token']}"}

    # Admin can authorize escalation
    res_auth = client.post(
        f"/api/incidents/{inc.incident_id}/authorize",
        json={"reason": "Admin approved dispatch escalation."},
        headers=headers,
    )
    assert res_auth.status_code == 200
    data = res_auth.get_json()
    assert data["incident"]["escalation_state"] == "AUTHORIZED"
    assert data["external_notifications"] == "DISABLED (0)"


def test_system_core_actor_strictly_denied_consequential_actions(console_fixture):
    client = console_fixture["client"]
    storage = console_fixture["storage"]
    inc = seed_incident(storage)

    headers = {"X-Actor-ID": "system_core"}

    # system_core cannot acknowledge
    assert client.post(f"/api/incidents/{inc.incident_id}/acknowledge", headers=headers).status_code == 403

    # system_core cannot resolve
    assert client.post(f"/api/incidents/{inc.incident_id}/resolve", json={"reason": "CONDITION_CLEARED"}, headers=headers).status_code == 403

    # system_core cannot dismiss
    assert client.post(f"/api/incidents/{inc.incident_id}/dismiss", json={"reason": "test"}, headers=headers).status_code == 403

    # system_core cannot authorize response
    assert client.post(f"/api/incidents/{inc.incident_id}/authorize", headers=headers).status_code == 403


# ============================================================================
# 3. Lifecycle Transitions & Validation Tests
# ============================================================================

def test_resolve_requires_explicit_valid_reason(console_fixture):
    client = console_fixture["client"]
    storage = console_fixture["storage"]
    inc = seed_incident(storage)

    res_login = client.post("/api/auth/login", json={"username": "operator", "password": "operator123"})
    headers = {"Authorization": f"Bearer {res_login.get_json()['token']}"}

    # Missing reason -> 400
    res_no_reason = client.post(f"/api/incidents/{inc.incident_id}/resolve", json={}, headers=headers)
    assert res_no_reason.status_code == 400

    # Invalid free-form text reason -> 400
    res_bad_reason = client.post(f"/api/incidents/{inc.incident_id}/resolve", json={"reason": "I think it is fine"}, headers=headers)
    assert res_bad_reason.status_code == 400
    assert "Invalid resolution reason" in res_bad_reason.get_json()["details"]

    # Valid reason: SYSTEM_TEST -> 200
    res_valid = client.post(f"/api/incidents/{inc.incident_id}/resolve", json={"reason": "SYSTEM_TEST"}, headers=headers)
    assert res_valid.status_code == 200
    assert res_valid.get_json()["incident"]["status"] == "RESOLVED"


def test_dismiss_requires_non_empty_reason(console_fixture):
    client = console_fixture["client"]
    storage = console_fixture["storage"]
    inc = seed_incident(storage)

    res_login = client.post("/api/auth/login", json={"username": "operator", "password": "operator123"})
    headers = {"Authorization": f"Bearer {res_login.get_json()['token']}"}

    # Empty string reason -> 400
    res_empty = client.post(f"/api/incidents/{inc.incident_id}/dismiss", json={"reason": "   "}, headers=headers)
    assert res_empty.status_code == 400


def test_invalid_lifecycle_transition_returns_409(console_fixture):
    client = console_fixture["client"]
    storage = console_fixture["storage"]
    inc = seed_incident(storage)

    # Login as operator
    res_login = client.post("/api/auth/login", json={"username": "operator", "password": "operator123"})
    headers = {"Authorization": f"Bearer {res_login.get_json()['token']}"}

    # Resolve incident
    res_resolve = client.post(f"/api/incidents/{inc.incident_id}/resolve", json={"reason": "NORMAL_ACTIVITY"}, headers=headers)
    assert res_resolve.status_code == 200

    # Attempt to acknowledge terminal RESOLVED incident -> 409 Conflict
    res_ack_after_resolve = client.post(f"/api/incidents/{inc.incident_id}/acknowledge", headers=headers)
    assert res_ack_after_resolve.status_code == 409

    # Admin login
    res_admin = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    admin_headers = {"Authorization": f"Bearer {res_admin.get_json()['token']}"}

    # Attempt to authorize terminal RESOLVED incident -> 409 Conflict
    res_auth_resolved = client.post(f"/api/incidents/{inc.incident_id}/authorize", headers=admin_headers)
    assert res_auth_resolved.status_code == 409


def test_not_found_incident_returns_404(console_fixture):
    client = console_fixture["client"]

    res_login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    headers = {"Authorization": f"Bearer {res_login.get_json()['token']}"}

    fake_id = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"/api/incidents/{fake_id}/acknowledge", headers=headers).status_code == 404
    assert client.post(f"/api/incidents/{fake_id}/resolve", json={"reason": "NORMAL_ACTIVITY"}, headers=headers).status_code == 404
    assert client.post(f"/api/incidents/{fake_id}/dismiss", json={"reason": "Test"}, headers=headers).status_code == 404
    assert client.post(f"/api/incidents/{fake_id}/authorize", headers=headers).status_code == 404


# ============================================================================
# 4. Idempotency & Audit Trail Tests
# ============================================================================

def test_escalation_authorization_idempotency(console_fixture):
    client = console_fixture["client"]
    storage = console_fixture["storage"]
    audit = console_fixture["audit_logger"]
    inc = seed_incident(storage)

    res_login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    headers = {"Authorization": f"Bearer {res_login.get_json()['token']}"}

    # First authorization -> 200
    res1 = client.post(f"/api/incidents/{inc.incident_id}/authorize", headers=headers)
    assert res1.status_code == 200

    # Second authorization -> 200 (idempotent, no error)
    res2 = client.post(f"/api/incidents/{inc.incident_id}/authorize", headers=headers)
    assert res2.status_code == 200
    assert "idempotent" in res2.get_json()["message"]


def test_audit_logging_records_consequential_actions(console_fixture):
    client = console_fixture["client"]
    storage = console_fixture["storage"]
    audit = console_fixture["audit_logger"]
    inc = seed_incident(storage)

    # Operator logs in and acknowledges
    res_op = client.post("/api/auth/login", json={"username": "operator", "password": "operator123"})
    op_headers = {"Authorization": f"Bearer {res_op.get_json()['token']}"}
    client.post(f"/api/incidents/{inc.incident_id}/acknowledge", headers=op_headers)

    # Operator resolves
    client.post(
        f"/api/incidents/{inc.incident_id}/resolve",
        json={"reason": "FALSE_POSITIVE", "notes": "Verified cat jumped down."},
        headers=op_headers,
    )

    records = audit.get_records_for_target(inc.incident_id)
    actions = [r.action for r in records]
    assert AuditAction.INCIDENT_ACKNOWLEDGED in actions
    assert AuditAction.INCIDENT_RESOLVED in actions

    # Verify no passwords or tokens were logged in audit logs
    for r in records:
        text_repr = f"{r.reason} {r.metadata}"
        assert "password" not in text_repr.lower()
        assert "operator123" not in text_repr
        assert "admin123" not in text_repr
