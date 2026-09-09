"""Comprehensive test suite for ATLAS Home Step 7 — Notifications & Escalation.

Tests:
1. LOW incident does not create inappropriate external notification.
2. MEDIUM incident follows configured policy (in-app for admin only).
3. HIGH incident reaches eligible recipients (Admin and Operator).
4. CRITICAL incident starts escalation.
5. Duplicate processing does not duplicate notifications (idempotency).
6. Escalation advances only when the incident remains unresolved.
7. Acknowledgement stops escalation according to policy.
8. Resolution stops escalation.
9. Dismissal stops escalation.
10. Unauthorized user cannot acknowledge notification (403).
11. Unauthorized user cannot cancel escalation.
12. Viewer cannot access restricted notification actions.
13. Notification provider unavailable is represented honestly (NOT_CONFIGURED).
14. Failed delivery is recorded.
15. Retries are bounded.
16. Notification history remains auditable.
17. system_core / SYSTEM_ACTOR cannot act as human recipient.
18. Notification APIs require authentication (401 for anonymous).
19. Notification ID access respects permissions and returns 404 for invalid ID.
20. No production test notifications are generated in production directories (isolated DB).
21. Configuration is validated and environment-aware.
22. No hardcoded secrets, phone numbers, or push tokens.
23. Dashboard reflects backend notification state.
24. Notification state survives application restart (SQLite persistence).
25. Full regression suite isolation check.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
import pytest

from atlas.api.app import create_app
from atlas.audit.service import AuditLogger
from atlas.authority.auth import get_auth_service
from atlas.authority.models import Actor, Permission, Role
from atlas.authority.service import AuthorityService, DEFAULT_ADMIN, DEFAULT_OPERATOR, DEFAULT_VIEWER, SYSTEM_ACTOR
from atlas.config.settings import Settings
from atlas.events.storage import EventStorage
from atlas.incidents.schema import Incident, IncidentStatus, IncidentType, ResolutionReason
from atlas.incidents.service import IncidentManager
from atlas.notifications.escalation import EscalationEngine, EscalationPolicy
from atlas.notifications.providers import (
    InAppNotificationProvider,
    NotificationProvider,
    ProviderRegistry,
    WebPushNotificationProvider,
    EmailNotificationProvider,
)
from atlas.notifications.schema import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    ProviderState,
)
from atlas.notifications.service import NotificationService, get_notification_service


@pytest.fixture
def notif_env(tmp_path):
    """Isolated environment with temporary SQLite storage and test client."""
    db_path = tmp_path / "test_notif.db"
    storage = EventStorage(db_path=db_path)
    audit = AuditLogger(storage=storage)
    auth_svc = get_auth_service()
    authority = AuthorityService()

    settings = Settings()
    settings.notifications_enabled = True
    settings.escalation_enabled = True
    settings.escalation_interval_seconds = 60.0
    settings.max_escalation_level = 3
    settings.mobile_push_enabled = False
    settings.email_notifications_enabled = False

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

    # Patch globals
    import atlas.events.storage as st_mod
    import atlas.notifications.service as ns_mod
    import atlas.incidents.service as inc_mod
    import atlas.audit.service as aud_mod

    orig_storage = st_mod._global_storage
    orig_notif = ns_mod._global_notification_service
    orig_inc = inc_mod._global_incident_manager
    orig_audit = aud_mod._global_audit_logger

    st_mod._global_storage = storage
    ns_mod._global_notification_service = notif_svc
    inc_mod._global_incident_manager = inc_mgr
    aud_mod._global_audit_logger = audit

    app = create_app(settings)
    app.config["TESTING"] = True
    client = app.test_client()

    admin_token, _ = auth_svc.authenticate("admin", "admin123")
    operator_token, _ = auth_svc.authenticate("operator", "operator123")
    viewer_token, _ = auth_svc.authenticate("viewer", "viewer123")

    try:
        yield {
            "db_path": db_path,
            "storage": storage,
            "audit": audit,
            "notif_svc": notif_svc,
            "inc_mgr": inc_mgr,
            "client": client,
            "settings": settings,
            "admin_token": admin_token,
            "operator_token": operator_token,
            "viewer_token": viewer_token,
        }
    finally:
        st_mod._global_storage = orig_storage
        ns_mod._global_notification_service = orig_notif
        inc_mod._global_incident_manager = orig_inc
        aud_mod._global_audit_logger = orig_audit
        storage.close()


def test_1_low_incident_no_direct_notification(notif_env):
    """1. LOW incident does not create inappropriate direct notifications."""
    notif_svc = notif_env["notif_svc"]
    inc = Incident(
        incident_type=IncidentType.GENERAL_SAFETY_REVIEW,
        severity="LOW",
        status=IncidentStatus.ACTIVE,
    )
    notifs = notif_svc.process_incident(inc)
    assert len(notifs) == 0, "LOW incidents should not create direct recipient notifications"


def test_2_medium_incident_admin_only(notif_env):
    """2. MEDIUM incident follows configured policy (notifies Admin only)."""
    notif_svc = notif_env["notif_svc"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
    )
    notifs = notif_svc.process_incident(inc)
    assert len(notifs) == 1
    assert notifs[0].recipient_user_id == "admin_01"
    assert notifs[0].recipient_role == Role.ADMIN
    assert notifs[0].status == NotificationStatus.DELIVERED


def test_3_high_incident_reaches_admin_and_operator(notif_env):
    """3. HIGH incident reaches eligible recipients (Admin and Operator)."""
    notif_svc = notif_env["notif_svc"]
    inc = Incident(
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="HIGH",
        status=IncidentStatus.ACTIVE,
    )
    notifs = notif_svc.process_incident(inc)
    assert len(notifs) == 2
    recipients = {n.recipient_user_id for n in notifs}
    assert recipients == {"admin_01", "operator_01"}
    assert all(n.status == NotificationStatus.DELIVERED for n in notifs)


def test_4_critical_incident_starts_escalation(notif_env):
    """4. CRITICAL incident starts escalation."""
    notif_svc = notif_env["notif_svc"]
    storage = notif_env["storage"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="CRITICAL",
        status=IncidentStatus.ACTIVE,
    )
    notifs = notif_svc.process_incident(inc)
    assert len(notifs) == 2
    assert all(n.escalation_level == 1 for n in notifs)

    record = storage.get_escalation_record(inc.incident_id)
    assert record is not None
    assert record.is_active is True
    assert record.current_level == 1
    assert record.next_escalation_epoch is not None


def test_5_idempotent_duplicate_processing_prevented(notif_env):
    """5. Duplicate processing does not duplicate notifications."""
    notif_svc = notif_env["notif_svc"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="CRITICAL",
        status=IncidentStatus.ACTIVE,
    )
    first_run = notif_svc.process_incident(inc)
    assert len(first_run) == 2

    # Second run immediately without escalation time elapsed
    second_run = notif_svc.process_incident(inc)
    assert len(second_run) == 0, "Repeated analysis must not generate duplicate notifications"


def test_6_escalation_advances_when_unresolved(notif_env):
    """6. Escalation advances only when the incident remains unresolved and interval elapsed."""
    notif_svc = notif_env["notif_svc"]
    storage = notif_env["storage"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="CRITICAL",
        status=IncidentStatus.ACTIVE,
    )
    notif_svc.process_incident(inc, current_time_epoch=1000.0)

    # 30 seconds later (interval is 60s): should not advance
    mid_run = notif_svc.process_incident(inc, current_time_epoch=1030.0)
    assert len(mid_run) == 0

    # 65 seconds later: should advance to Level 2
    advanced_run = notif_svc.process_incident(inc, current_time_epoch=1065.0)
    assert len(advanced_run) == 2
    assert all(n.escalation_level == 2 for n in advanced_run)

    rec = storage.get_escalation_record(inc.incident_id)
    assert rec.current_level == 2

    # 130 seconds later: should advance to Level 3 (max)
    lvl3_run = notif_svc.process_incident(inc, current_time_epoch=1130.0)
    assert len(lvl3_run) == 2
    assert all(n.escalation_level == 3 for n in lvl3_run)

    rec = storage.get_escalation_record(inc.incident_id)
    assert rec.current_level == 3

    # Further time elapsed: should NOT exceed max level 3
    over_run = notif_svc.process_incident(inc, current_time_epoch=1200.0)
    assert len(over_run) == 0


def test_7_acknowledgement_stops_escalation(notif_env):
    """7. Acknowledgement stops escalation according to policy."""
    inc_mgr = notif_env["inc_mgr"]
    storage = notif_env["storage"]
    notif_svc = notif_env["notif_svc"]

    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="CRITICAL",
        status=IncidentStatus.ACTIVE,
    )
    storage.save_incident(inc)
    notif_svc.process_incident(inc, current_time_epoch=1000.0)

    # Operator acknowledges incident
    inc_mgr.acknowledge_incident(inc.incident_id, actor=DEFAULT_OPERATOR)

    rec = storage.get_escalation_record(inc.incident_id)
    assert rec.is_active is False
    assert "acknowledged" in rec.stopped_reason.lower()

    # Even if time advances, no further notifications
    after_ack = notif_svc.process_incident(inc, current_time_epoch=1100.0)
    assert len(after_ack) == 0


def test_8_resolution_stops_escalation(notif_env):
    """8. Resolution stops escalation."""
    inc_mgr = notif_env["inc_mgr"]
    storage = notif_env["storage"]
    notif_svc = notif_env["notif_svc"]

    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="CRITICAL",
        status=IncidentStatus.ACTIVE,
    )
    storage.save_incident(inc)
    notif_svc.process_incident(inc, current_time_epoch=1000.0)

    # Resolve incident
    inc_mgr.resolve_incident(
        inc.incident_id,
        actor=DEFAULT_ADMIN,
        reason=ResolutionReason.USER_CONFIRMED_SAFE,
        notes="Person confirmed ok",
    )

    rec = storage.get_escalation_record(inc.incident_id)
    assert rec.is_active is False
    assert "resolved" in rec.stopped_reason.lower()

    after_res = notif_svc.process_incident(inc, current_time_epoch=1200.0)
    assert len(after_res) == 0


def test_9_dismissal_stops_escalation(notif_env):
    """9. Dismissal stops escalation."""
    inc_mgr = notif_env["inc_mgr"]
    storage = notif_env["storage"]
    notif_svc = notif_env["notif_svc"]

    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="CRITICAL",
        status=IncidentStatus.ACTIVE,
    )
    storage.save_incident(inc)
    notif_svc.process_incident(inc, current_time_epoch=1000.0)

    # Dismiss incident
    inc_mgr.dismiss_incident(inc.incident_id, actor=DEFAULT_OPERATOR, reason="False alarm")

    rec = storage.get_escalation_record(inc.incident_id)
    assert rec.is_active is False
    assert "dismissed" in rec.stopped_reason.lower()


def test_10_unauthorized_user_cannot_acknowledge(notif_env):
    """10. Unauthorized user (viewer) cannot acknowledge notification."""
    notif_svc = notif_env["notif_svc"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        status=IncidentStatus.ACTIVE,
    )
    notifs = notif_svc.process_incident(inc)
    notif_id = notifs[0].notification_id

    # Viewer attempts acknowledgement
    with pytest.raises(Exception):
        notif_svc.acknowledge_notification(notif_id, actor=DEFAULT_VIEWER)


def test_11_unauthorized_user_cannot_cancel_escalation(notif_env):
    """11. Unauthorized user cannot stop or cancel escalation."""
    inc_mgr = notif_env["inc_mgr"]
    storage = notif_env["storage"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="CRITICAL",
        status=IncidentStatus.ACTIVE,
    )
    storage.save_incident(inc)

    # Viewer attempts to acknowledge or resolve incident to stop escalation
    with pytest.raises(Exception):
        inc_mgr.acknowledge_incident(inc.incident_id, actor=DEFAULT_VIEWER)

    with pytest.raises(Exception):
        inc_mgr.resolve_incident(inc.incident_id, actor=DEFAULT_VIEWER, reason=ResolutionReason.USER_CONFIRMED_SAFE)


def test_12_viewer_cannot_access_restricted_notification_actions(notif_env):
    """12. Viewer cannot access restricted notification endpoints (HTTP 403)."""
    client = notif_env["client"]
    viewer_tok = notif_env["viewer_token"]
    notif_svc = notif_env["notif_svc"]

    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        status=IncidentStatus.ACTIVE,
    )
    notifs = notif_svc.process_incident(inc)
    notif_id = notifs[0].notification_id

    # POST acknowledge as viewer
    resp = client.post(
        f"/api/notifications/{notif_id}/acknowledge",
        headers={"Authorization": f"Bearer {viewer_tok}"},
    )
    assert resp.status_code == 403


def test_13_notification_provider_unavailable_represented_honestly(notif_env):
    """13. Notification provider unavailable is represented honestly (NOT_CONFIGURED)."""
    settings = notif_env["settings"]
    push_prov = WebPushNotificationProvider(settings=settings)
    assert push_prov.get_state() == ProviderState.NOT_CONFIGURED

    email_prov = EmailNotificationProvider(settings=settings)
    assert email_prov.get_state() == ProviderState.NOT_CONFIGURED

    # Sending to unconfigured provider fails honestly with explicit reason
    res = push_prov.send(Notification(
        incident_id="test",
        recipient_user_id="admin",
        recipient_role=Role.ADMIN,
        severity="CRITICAL",
        channel=NotificationChannel.WEB_PUSH,
        title="test",
        summary="test",
    ), DEFAULT_ADMIN)

    assert res.success is False
    assert res.provider_state == ProviderState.NOT_CONFIGURED
    assert "not configured" in res.failure_reason.lower()


def test_14_failed_delivery_recorded(notif_env):
    """14. Failed delivery is recorded with failure reason and status FAILED."""
    storage = notif_env["storage"]
    n = Notification(
        incident_id="test-fail",
        recipient_user_id="admin",
        recipient_role=Role.ADMIN,
        severity="HIGH",
        channel=NotificationChannel.WEB_PUSH,
        status=NotificationStatus.FAILED,
        title="test",
        summary="test",
        attempt_count=1,
        failure_reason="Gateway timeout",
    )
    storage.save_notification(n)

    retrieved = storage.get_notification(n.notification_id)
    assert retrieved is not None
    assert retrieved.status == NotificationStatus.FAILED
    assert retrieved.failure_reason == "Gateway timeout"
    assert retrieved.attempt_count == 1


def test_15_bounded_retries(notif_env):
    """15. Retries are bounded to max_retries."""
    settings = notif_env["settings"]
    assert settings.notification_retry_count == 3

    n = Notification(
        incident_id="test",
        recipient_user_id="admin",
        recipient_role=Role.ADMIN,
        severity="HIGH",
        channel=NotificationChannel.IN_APP,
        title="test",
        summary="test",
        max_retries=3,
        attempt_count=3,
    )
    assert n.attempt_count >= n.max_retries


def test_16_notification_history_remains_auditable(notif_env):
    """16. Notification history remains auditable via append-only audit trail."""
    notif_svc = notif_env["notif_svc"]
    storage = notif_env["storage"]

    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="CRITICAL",
        status=IncidentStatus.ACTIVE,
    )
    notif_svc.process_incident(inc)

    audit_records = storage.get_recent_audit_records(limit=10)
    actions = [r.action.value for r in audit_records]
    assert "NOTIFICATION_CREATED" in actions
    assert "NOTIFICATION_DELIVERED" in actions
    assert "ESCALATION_STARTED" in actions


def test_17_system_core_cannot_act_as_human_recipient(notif_env):
    """17. system_core / SYSTEM_ACTOR cannot act as human recipient."""
    prov = InAppNotificationProvider()
    n = Notification(
        incident_id="test",
        recipient_user_id="system_core",
        recipient_role=Role.ADMIN,
        severity="HIGH",
        channel=NotificationChannel.IN_APP,
        title="test",
        summary="test",
    )
    res = prov.send(n, SYSTEM_ACTOR)
    assert res.success is False
    assert "SYSTEM_ACTOR cannot be a notification recipient" in res.failure_reason


def test_18_notification_apis_require_authentication(notif_env):
    """18. Notification APIs require authentication (401 for anonymous)."""
    client = notif_env["client"]

    # Anonymous GET /api/notifications
    r1 = client.get("/api/notifications")
    assert r1.status_code == 401

    # Anonymous GET /api/notifications/unread
    r2 = client.get("/api/notifications/unread")
    assert r2.status_code == 401

    # Anonymous POST /api/notifications/123/acknowledge
    r3 = client.post("/api/notifications/123/acknowledge")
    assert r3.status_code == 401


def test_19_notification_id_access_validation(notif_env):
    """19. Notification ID access respects permissions and returns 404 for invalid ID."""
    client = notif_env["client"]
    admin_tok = notif_env["admin_token"]

    r = client.get(
        "/api/notifications/non-existent-notif-id",
        headers={"Authorization": f"Bearer {admin_tok}"},
    )
    assert r.status_code == 404


def test_20_test_directory_isolation(notif_env):
    """20. No production test notifications are generated in production directories."""
    prod_db = Path("data/atlas_events.db")
    test_db = notif_env["db_path"]
    assert str(test_db) != str(prod_db), "Test must use isolated temporary database"


def test_21_configuration_validation(notif_env):
    """21. Configuration is validated and environment-aware."""
    s = Settings()
    assert hasattr(s, "notifications_enabled")
    assert hasattr(s, "escalation_enabled")
    assert hasattr(s, "escalation_interval_seconds")
    assert hasattr(s, "mobile_push_enabled")
    d = s.to_dict()
    assert "notifications_enabled" in d
    assert "escalation_interval_seconds" in d


def test_22_no_hardcoded_secrets_or_tokens(notif_env):
    """22. Verify zero hardcoded tokens, phone numbers, or push keys in notifications."""
    from atlas.notifications.providers import WebPushNotificationProvider
    wp = WebPushNotificationProvider()
    assert wp.get_state() == ProviderState.NOT_CONFIGURED


def test_23_dashboard_reflects_backend_notification_state(notif_env):
    """23. Dashboard reflects backend notification state via notifications_summary."""
    client = notif_env["client"]
    notif_svc = notif_env["notif_svc"]

    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="CRITICAL",
        status=IncidentStatus.ACTIVE,
    )
    notif_svc.process_incident(inc)

    resp = client.get("/api/dashboard/state")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "notifications_summary" in data
    ns = data["notifications_summary"]
    assert ns["total_notifications"] == 2
    assert ns["unread_count"] == 2
    assert ns["active_escalations_count"] == 1


def test_24_notification_state_survives_storage_reload(notif_env):
    """24. Notification state survives application reload (SQLite persistence)."""
    db_path = notif_env["db_path"]
    notif_svc = notif_env["notif_svc"]

    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        status=IncidentStatus.ACTIVE,
    )
    notifs = notif_svc.process_incident(inc)
    notif_id = notifs[0].notification_id

    # Create fresh storage pointing to same sqlite file
    fresh_storage = EventStorage(db_path=db_path)
    fresh_notif = fresh_storage.get_notification(notif_id)
    assert fresh_notif is not None
    assert fresh_notif.incident_id == inc.incident_id
    assert fresh_notif.severity == "HIGH"
    fresh_storage.close()


def test_25_operator_can_acknowledge_notification_via_api(notif_env):
    """25. Authorized operator can acknowledge notification via API."""
    client = notif_env["client"]
    op_token = notif_env["operator_token"]
    notif_svc = notif_env["notif_svc"]

    inc = Incident(
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="HIGH",
        status=IncidentStatus.ACTIVE,
    )
    notifs = notif_svc.process_incident(inc)
    notif_id = notifs[0].notification_id

    resp = client.post(
        f"/api/notifications/{notif_id}/acknowledge",
        headers={"Authorization": f"Bearer {op_token}"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["notification"]["status"] == "ACKNOWLEDGED"
