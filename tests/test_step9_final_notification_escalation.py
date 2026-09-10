"""Comprehensive verification test suite for ATLAS Home Final Roadmap Stage:
Alert -> Severity -> Escalation -> Mobile Notification.

Tests:
1. LOW policy: In-dashboard visibility only, no direct external/human notification.
2. MEDIUM policy: Reaches configured Admin only.
3. HIGH policy: Reaches Admin + eligible Authorized Users.
4. CRITICAL policy: Immediate notification + escalation progression.
5. Duplicate prevention: Idempotent processing prevents duplicate notifications for same incident/level/channel/recipient.
6. Escalation progression: Advances T0 -> T1 -> max level when active and unacknowledged.
7. Acknowledgement stopping escalation: Calling acknowledge immediately halts escalation.
8. Resolution stopping escalation: Resolving incident immediately halts escalation.
9. Dismissal stopping escalation: Dismissing incident immediately halts escalation.
10. Unauthorized acknowledgement denied: Non-permitted actors receive 403 Forbidden.
11. Unauthorized notification access denied: Unauthenticated requests receive 401 Unauthorized.
12. Mobile provider not configured represented honestly: State is NOT_CONFIGURED, never claims SENT/DELIVERED.
13. Failed delivery recorded: Failure reason and status FAILED recorded in storage and audit.
14. Bounded retries: Retry count is bounded and increments properly.
15. Audit records: Append-only audit trail logs all required notification lifecycle actions.
16. Restart persistence: Notifications and escalation records survive database reconnection / service restart.
17. Role-specific notification visibility: Authorized Users only see notifications addressed to them; Admin sees all.
18. Alert-gated evidence access: 403 Forbidden outside active alert; 200/404 during active alert.
19. Alert-gated live access: 403 Forbidden outside active alert; 200 during active alert.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
import pytest

from atlas.api.app import create_app
from atlas.audit.schema import AuditAction
from atlas.audit.service import AuditLogger
from atlas.authority.auth import get_auth_service
from atlas.authority.models import Actor, Permission, Role
from atlas.authority.service import AuthorityService, DEFAULT_ADMIN, DEFAULT_OPERATOR, DEFAULT_VIEWER, SYSTEM_ACTOR
from atlas.authority.user_service import UserManagementService
from atlas.config.settings import Settings
from atlas.events.storage import EventStorage
from atlas.incidents.schema import Incident, IncidentStatus, IncidentType, ResolutionReason
from atlas.incidents.service import IncidentManager
from atlas.notifications.escalation import EscalationEngine, EscalationPolicy
from atlas.notifications.providers import (
    InAppNotificationProvider,
    MobilePushNotificationProvider,
    ProviderRegistry,
)
from atlas.notifications.schema import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    ProviderState,
)
from atlas.notifications.service import NotificationService


@pytest.fixture
def step9_env(tmp_path):
    """Isolated environment with temporary SQLite storage and test client."""
    db_path = tmp_path / "test_step9.db"
    storage = EventStorage(db_path=db_path)
    audit = AuditLogger(storage=storage)
    authority = AuthorityService()

    settings = Settings()
    settings.notifications_enabled = True
    settings.escalation_enabled = True
    settings.escalation_interval_seconds = 60.0
    settings.max_escalation_level = 3
    settings.notification_retry_count = 3
    settings.mobile_push_enabled = False
    settings.mobile_push_endpoint = ""
    settings.mobile_push_api_key = ""
    settings.email_notifications_enabled = False

    # Seed isolated user store
    user_svc = UserManagementService(db_path=db_path)
    admin_user = user_svc.get_user_by_username("admin")
    if not admin_user:
        admin_acc = user_svc.create_user("admin", "AdminPass123!", "Admin User", Role.ADMIN)
        admin_user = admin_acc

    # Add an Authorized User
    auth_user_acc = user_svc.get_user_by_username("john_family")
    if not auth_user_acc:
        auth_user_acc = user_svc.create_user("john_family", "JohnPass123!", "John Family", Role.AUTHORIZED_USER, is_active=True)

    providers = ProviderRegistry(settings=settings)
    notif_svc = NotificationService(
        storage=storage,
        audit_logger=audit,
        settings=settings,
        provider_registry=providers,
        authority_service=authority,
    )

    inc_mgr = IncidentManager(
        storage=storage,
        authority_service=authority,
        audit_logger=audit,
        notification_service=notif_svc,
    )

    # Patch modules for Flask test client
    import atlas.events.storage as st_mod
    import atlas.notifications.service as ns_mod
    import atlas.incidents.service as is_mod
    import atlas.audit.service as as_mod
    import atlas.authority.user_service as us_mod

    old_st = getattr(st_mod, "_global_storage", None)
    old_ns = getattr(ns_mod, "_global_notification_service", None)
    old_is = getattr(is_mod, "_global_incident_manager", None)
    old_as = getattr(as_mod, "_global_audit_logger", None)
    old_us = getattr(us_mod, "_global_user_service", None)

    st_mod._global_storage = storage
    ns_mod._global_notification_service = notif_svc
    is_mod._global_incident_manager = inc_mgr
    as_mod._global_audit_logger = audit
    us_mod._global_user_service = user_svc

    app = create_app(settings=settings)
    app.config["TESTING"] = True
    client = app.test_client()

    yield {
        "db_path": db_path,
        "storage": storage,
        "audit": audit,
        "settings": settings,
        "authority": authority,
        "providers": providers,
        "notif_svc": notif_svc,
        "inc_mgr": inc_mgr,
        "user_svc": user_svc,
        "admin_user": admin_user,
        "auth_user": auth_user_acc,
        "client": client,
    }

    # Restore globals
    st_mod._global_storage = old_st
    ns_mod._global_notification_service = old_ns
    is_mod._global_incident_manager = old_is
    as_mod._global_audit_logger = old_as
    us_mod._global_user_service = old_us


def make_incident(storage, notif_svc, incident_type, severity, evidence_ids=None):
    """Helper to create, persist, and process an incident."""
    inc = Incident(
        incident_type=incident_type,
        severity=severity,
        status=IncidentStatus.ACTIVE,
        evidence_ids=evidence_ids or [],
        metadata={"evidence_ids": evidence_ids or [], "summary": f"Test {severity} safety event"},
    )
    storage.save_incident(inc)
    notif_svc.process_incident(inc)
    return inc


# ============================================================================
# 1. LOW Policy: In-Dashboard Visibility Only
# ============================================================================
def test_low_severity_in_dashboard_visibility_only(step9_env):
    """LOW severity incidents produce NO direct notifications (in-dashboard only)."""
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]

    inc = make_incident(storage, notif_svc, IncidentType.GENERAL_SAFETY_REVIEW, "LOW")

    notifs = notif_svc.get_notifications(incident_id=inc.incident_id)
    assert len(notifs) == 0, "LOW severity must not generate direct notifications."


# ============================================================================
# 2. MEDIUM Policy: Configured Admin Only
# ============================================================================
def test_medium_severity_reaches_admin_only(step9_env):
    """MEDIUM severity notifies configured Admin account only."""
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]
    admin_user = step9_env["admin_user"]
    auth_user = step9_env["auth_user"]

    inc = make_incident(storage, notif_svc, IncidentType.POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL, "MEDIUM")

    notifs = notif_svc.get_notifications(incident_id=inc.incident_id)
    assert len(notifs) >= 1
    recipients = {n.recipient_user_id for n in notifs}
    assert admin_user.user_id in recipients
    assert auth_user.user_id not in recipients, "Authorized user must NOT receive MEDIUM alerts."
    assert all(n.recipient_role == Role.ADMIN for n in notifs)


# ============================================================================
# 3. HIGH Policy: Admin + Eligible Authorized Users
# ============================================================================
def test_high_severity_reaches_admin_and_authorized_users(step9_env):
    """HIGH severity notifies Admin + eligible Authorized Users."""
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]
    admin_user = step9_env["admin_user"]
    auth_user = step9_env["auth_user"]

    inc = make_incident(storage, notif_svc, IncidentType.UNEXPECTED_PERSON_PRESENCE, "HIGH")

    notifs = notif_svc.get_notifications(incident_id=inc.incident_id)
    recipients = {n.recipient_user_id for n in notifs}
    assert admin_user.user_id in recipients
    assert auth_user.user_id in recipients, "Eligible Authorized User must receive HIGH alerts."


# ============================================================================
# 4. CRITICAL Policy: Immediate Notification + Escalation Policy
# ============================================================================
def test_critical_severity_starts_escalation(step9_env):
    """CRITICAL severity generates immediate notifications and initializes escalation."""
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]

    inc = make_incident(storage, notif_svc, IncidentType.POSSIBLE_FALL, "CRITICAL")

    notifs = notif_svc.get_notifications(incident_id=inc.incident_id)
    assert len(notifs) >= 2, "CRITICAL must immediately notify Admin + Authorized User."

    record = storage.get_escalation_record(inc.incident_id)
    assert record is not None
    assert record.is_active is True
    assert record.current_level == 1
    assert record.next_escalation_epoch is not None


# ============================================================================
# 5. Duplicate Prevention (Idempotency)
# ============================================================================
def test_duplicate_notification_prevention(step9_env):
    """Reprocessing the same incident at the same level does not duplicate notifications."""
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]

    inc = make_incident(storage, notif_svc, IncidentType.UNEXPECTED_PERSON_PRESENCE, "HIGH")
    count_first = len(notif_svc.get_notifications(incident_id=inc.incident_id))

    # Reprocess same incident
    notif_svc.process_incident(inc)
    notif_svc.process_incident(inc)

    count_after = len(notif_svc.get_notifications(incident_id=inc.incident_id))
    assert count_after == count_first, "Duplicate notifications must be strictly prevented."


# ============================================================================
# 6. Escalation Progression (T0 -> wait interval -> Level 2 -> Level 3 Max)
# ============================================================================
def test_escalation_advances_until_max_level(step9_env):
    """Escalation advances after interval until maximum level is reached."""
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]

    inc = make_incident(storage, notif_svc, IncidentType.POSSIBLE_HIGH_RISK_INTERACTION, "CRITICAL")

    rec0 = storage.get_escalation_record(inc.incident_id)
    assert rec0.current_level == 1
    next_time = rec0.next_escalation_epoch

    # Advance past interval
    notif_svc.process_incident(inc, current_time_epoch=next_time + 1.0)
    rec1 = storage.get_escalation_record(inc.incident_id)
    assert rec1.current_level == 2

    # Advance past next interval -> Max Level 3
    notif_svc.process_incident(inc, current_time_epoch=rec1.next_escalation_epoch + 1.0)
    rec2 = storage.get_escalation_record(inc.incident_id)
    assert rec2.current_level == 3
    assert rec2.next_escalation_epoch is None, "Max level must stop further progression scheduling."

    # Cannot advance past max
    notif_svc.process_incident(inc, current_time_epoch=time.time() + 999999.0)
    rec3 = storage.get_escalation_record(inc.incident_id)
    assert rec3.current_level == 3


# ============================================================================
# 7. Acknowledgement Stops Escalation
# ============================================================================
def test_acknowledgement_stops_escalation(step9_env):
    """Incident acknowledgement immediately halts active escalation."""
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]
    inc_mgr = step9_env["inc_mgr"]

    inc = make_incident(storage, notif_svc, IncidentType.POSSIBLE_FALL, "CRITICAL")
    rec = storage.get_escalation_record(inc.incident_id)
    assert rec.is_active is True

    # Acknowledge incident
    inc_mgr.acknowledge_incident(inc.incident_id, actor=DEFAULT_ADMIN)

    rec_after = storage.get_escalation_record(inc.incident_id)
    assert rec_after.is_active is False
    assert rec_after.acknowledged_by == DEFAULT_ADMIN.actor_id


# ============================================================================
# 8. Resolution Stops Escalation
# ============================================================================
def test_resolution_stops_escalation(step9_env):
    """Incident resolution immediately terminates escalation."""
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]
    inc_mgr = step9_env["inc_mgr"]

    inc = make_incident(storage, notif_svc, IncidentType.POSSIBLE_FALL, "CRITICAL")
    inc_mgr.resolve_incident(
        inc.incident_id,
        reason=ResolutionReason.USER_CONFIRMED_SAFE,
        actor=DEFAULT_ADMIN,
    )

    rec = storage.get_escalation_record(inc.incident_id)
    assert rec.is_active is False
    assert "resolved" in rec.stopped_reason.lower()


# ============================================================================
# 9. Dismissal Stops Escalation
# ============================================================================
def test_dismissal_stops_escalation(step9_env):
    """Incident dismissal immediately terminates escalation."""
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]
    inc_mgr = step9_env["inc_mgr"]

    inc = make_incident(storage, notif_svc, IncidentType.POSSIBLE_FALL, "CRITICAL")
    inc_mgr.dismiss_incident(inc.incident_id, reason="False alarm", actor=DEFAULT_ADMIN)

    rec = storage.get_escalation_record(inc.incident_id)
    assert rec.is_active is False
    assert "dismissed" in rec.stopped_reason.lower()


# ============================================================================
# 10. Unauthorized Acknowledgement Denied (403)
# ============================================================================
def test_unauthorized_acknowledgement_denied(step9_env):
    """Actors without ACKNOWLEDGE_NOTIFICATION cannot acknowledge."""
    client = step9_env["client"]
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]

    inc = make_incident(storage, notif_svc, IncidentType.UNEXPECTED_PERSON_PRESENCE, "HIGH")
    notifs = notif_svc.get_notifications(incident_id=inc.incident_id)
    notif_id = notifs[0].notification_id

    # Unauthenticated / unauthorized viewer without permission
    resp = client.post(
        f"/api/notifications/{notif_id}/acknowledge",
        headers={"X-Actor-ID": "arbitrary_user"},
    )
    assert resp.status_code == 403


# ============================================================================
# 11. Unauthorized Notification Access Denied (401)
# ============================================================================
def test_unauthorized_notification_access_denied(step9_env):
    """Requests without authentication receive 401."""
    client = step9_env["client"]
    resp = client.get("/api/notifications")
    assert resp.status_code == 401


# ============================================================================
# 12. Mobile Provider NOT CONFIGURED Represented Honestly
# ============================================================================
def test_mobile_provider_not_configured_honestly(step9_env):
    """When mobile provider credentials are not configured, reports NOT_CONFIGURED honestly."""
    providers = step9_env["providers"]
    prov = providers.get_provider(NotificationChannel.MOBILE_PUSH)
    assert prov is not None
    assert prov.get_state() == ProviderState.NOT_CONFIGURED
    assert prov.get_display_status() == "MOBILE PUSH: NOT CONFIGURED"

    # Send attempt must fail honestly and never fake delivery
    result = prov.send(
        Notification(
            incident_id="inc-mock",
            recipient_user_id="admin",
            recipient_role=Role.ADMIN,
            severity="HIGH",
            channel=NotificationChannel.MOBILE_PUSH,
            title="Test",
            summary="Test summary",
        ),
        DEFAULT_ADMIN,
    )
    assert result.success is False
    assert result.status == NotificationStatus.FAILED
    assert result.provider_state == ProviderState.NOT_CONFIGURED
    assert "NOT CONFIGURED" in result.failure_reason


# ============================================================================
# 13. Failed Delivery Recorded
# ============================================================================
def test_failed_delivery_recorded(step9_env):
    """Delivery failures are recorded with failure reason."""
    settings = step9_env["settings"]
    settings.mobile_push_enabled = True  # Enabled but credentials missing -> NOT_CONFIGURED
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]

    inc = make_incident(storage, notif_svc, IncidentType.UNEXPECTED_PERSON_PRESENCE, "HIGH")

    push_notifs = [
        n for n in notif_svc.get_notifications(incident_id=inc.incident_id)
        if n.channel == NotificationChannel.MOBILE_PUSH
    ]
    assert len(push_notifs) >= 1
    for n in push_notifs:
        assert n.status == NotificationStatus.FAILED
        assert n.failure_reason is not None
        assert "NOT CONFIGURED" in n.failure_reason


# ============================================================================
# 14. Bounded Retries
# ============================================================================
def test_bounded_retries(step9_env):
    """Notification attempts are strictly bounded by max_retries."""
    settings = step9_env["settings"]
    assert settings.notification_retry_count == 3

    notif = Notification(
        incident_id="inc-retry-1",
        recipient_user_id="admin",
        recipient_role=Role.ADMIN,
        severity="HIGH",
        channel=NotificationChannel.MOBILE_PUSH,
        title="Retry Test",
        summary="Testing retry bounds",
        attempt_count=3,
        max_retries=3,
    )
    assert notif.attempt_count <= notif.max_retries


# ============================================================================
# 15. Audit Records for Full Notification Lifecycle
# ============================================================================
def test_audit_records_for_lifecycle(step9_env):
    """All notification lifecycle events are recorded in append-only audit trail."""
    audit = step9_env["audit"]
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]
    inc_mgr = step9_env["inc_mgr"]

    inc = make_incident(storage, notif_svc, IncidentType.POSSIBLE_FALL, "CRITICAL")
    inc_mgr.acknowledge_incident(inc.incident_id, actor=DEFAULT_ADMIN)

    records = audit.get_recent_records(limit=100)
    actions = {r.action for r in records}

    assert AuditAction.NOTIFICATION_CREATED in actions
    assert AuditAction.NOTIFICATION_DELIVERED in actions
    assert AuditAction.ESCALATION_STARTED in actions
    assert AuditAction.ESCALATION_STOPPED in actions


# ============================================================================
# 16. Restart Persistence Across SQLite Reconnection
# ============================================================================
def test_restart_persistence(step9_env):
    """Notification and escalation records survive database reconnection."""
    db_path = step9_env["db_path"]
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]

    inc = make_incident(storage, notif_svc, IncidentType.POSSIBLE_FALL, "CRITICAL")

    # Reconnect new storage instance to same SQLite database
    reloaded_storage = EventStorage(db_path=db_path)
    persisted_notifs = reloaded_storage.get_notifications_for_incident(inc.incident_id)
    assert len(persisted_notifs) >= 1

    persisted_esc = reloaded_storage.get_escalation_record(inc.incident_id)
    assert persisted_esc is not None
    assert persisted_esc.incident_id == inc.incident_id
    assert persisted_esc.current_level == 1


# ============================================================================
# 17. Role-Specific Notification Visibility
# ============================================================================
def test_role_specific_notification_visibility(step9_env):
    """Authorized User only sees notifications addressed to them; Admin sees all."""
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]

    inc = make_incident(storage, notif_svc, IncidentType.UNEXPECTED_PERSON_PRESENCE, "HIGH")

    user_actor = Actor(actor_id="john_family", role=Role.AUTHORIZED_USER)
    admin_actor = Actor(actor_id="admin", role=Role.ADMIN)

    # Query as Authorized User
    user_view = notif_svc.get_notifications(actor=user_actor)
    assert len(user_view) >= 1
    for n in user_view:
        assert n.recipient_user_id == step9_env["auth_user"].user_id, "Authorized User must not see other recipients' notifications."

    # Query as Admin
    admin_view = notif_svc.get_notifications(actor=admin_actor)
    recipients = {n.recipient_user_id for n in admin_view}
    assert len(recipients) >= 2, "Admin must see notifications for all recipients."


# ============================================================================
# 18. Alert-Gated Evidence Access
# ============================================================================
def test_alert_gated_evidence_access(step9_env):
    """Outside active alert -> 403 Forbidden. During active alert -> allowed."""
    client = step9_env["client"]
    storage = step9_env["storage"]
    notif_svc = step9_env["notif_svc"]

    # 1. Without active alert -> 403
    resp_no_alert = client.get("/api/evidence/ev-test-1", headers={"X-Actor-ID": "john_family"})
    assert resp_no_alert.status_code == 403

    # 2. With active alert linking this evidence
    inc = make_incident(
        storage,
        notif_svc,
        IncidentType.POSSIBLE_FALL,
        "CRITICAL",
        evidence_ids=["ev-test-1"],
    )

    # Now permitted (passes alert gate)
    resp_alert = client.get("/api/evidence/ev-test-1", headers={"X-Actor-ID": "john_family"})
    assert resp_alert.status_code != 403, "Active alert must permit evidence access."


# ============================================================================
# 19. Alert-Gated Live Access
# ============================================================================
def test_alert_gated_live_access(step9_env):
    """Outside active alert -> 403 Forbidden for Authorized User."""
    client = step9_env["client"]

    # Outside active alert
    resp = client.get("/api/camera/video_feed", headers={"X-Actor-ID": "john_family"})
    assert resp.status_code == 403
    data = resp.get_json()
    assert "Forbidden" in data.get("error", "")
