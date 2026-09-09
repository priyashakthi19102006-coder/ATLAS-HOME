"""Comprehensive Unit and Integration Tests for Step 5:
Authority, Incidents, Lifecycle, Alerts, Audit Logging, and Orchestrator Boundary.

All tests use deterministic local fixtures and mock actors.
Zero fake data injected into the live physical system.
"""

from __future__ import annotations

import pytest

from atlas.alerts.schema import AlertSeverity, AlertStatus
from atlas.alerts.service import AlertManager, get_alert_manager
from atlas.api.app import create_app
from atlas.audit.schema import AuditAction
from atlas.audit.service import AuditLogger, get_audit_logger
from atlas.authority.models import Action, Actor, Permission, Role
from atlas.authority.service import AuthorityService, UnauthorizedError
from atlas.config.settings import Settings
from atlas.events.schema import ATLASEvent, EventType
from atlas.events.storage import EventStorage, get_event_storage
from atlas.incidents.schema import (
    EscalationState,
    Incident,
    IncidentStatus,
    IncidentType,
    ResolutionReason,
)
from atlas.incidents.service import (
    IncidentManager,
    InvalidTransitionError,
    get_incident_manager,
)
from atlas.intelligence.schemas import LLMVerificationResult
from atlas.orchestration import AtlasOrchestrator, OrchestratorDecision
from atlas.risk.schema import DecisionContext, RiskAssessment, RiskLevel
from atlas.rules.schema import RuleEvaluationResult


def make_decision(
    analysis_id: str = "ana-001",
    rule_id: str = "rule_unexpected_presence",
    rule_name: str = "UNEXPECTED_PERSON_PRESENCE",
    condition_satisfied: bool = True,
    risk_score: float = 0.45,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    uncertainty: str = "moderate",
    supporting_events: list[str] | None = None,
) -> DecisionContext:
    events = supporting_events or ["ev-real-01", "ev-real-02"]
    rule_eval = RuleEvaluationResult(
        evaluation_id="eval-01",
        rule_id=rule_id,
        rule_name=rule_name,
        timestamp="2026-09-09T18:00:00Z",
        condition_satisfied=condition_satisfied,
        confidence=0.85,
        supporting_event_ids=events,
        missing_evidence=[],
        contradicting_evidence=[],
    )
    risk_ass = RiskAssessment(
        risk_id="risk-01",
        timestamp="2026-09-09T18:00:00Z",
        level=risk_level,
        score=risk_score,
        reason=f"Satisfied conditions: {rule_name}.",
        supporting_rule_ids=[rule_id] if condition_satisfied else [],
        supporting_event_ids=events if condition_satisfied else [],
        uncertainty=uncertainty,  # type: ignore
        human_verification_required=True,
    )
    llm_res = LLMVerificationResult(
        timestamp="2026-09-09T18:00:00Z",
        situation="Observed presence",
        interpretation="Normal presence",
        supporting_event_ids=events,
        confidence=0.75,
        uncertainty="moderate",
        verification_required=True,
        status="completed",
    )
    return DecisionContext(
        analysis_id=analysis_id,
        timestamp="2026-09-09T18:00:00Z",
        evidence={"event_ids": events},
        llm_verification=llm_res,
        rule_evaluations=[rule_eval],
        risk=risk_ass,
    )


# ============================================================================
# 1. Actor, Role, Permission Model
# ============================================================================
def test_1_actor_role_permission_model():
    admin = Actor(actor_id="admin_test", role=Role.ADMIN)
    operator = Actor(actor_id="op_test", role=Role.OPERATOR)
    viewer = Actor(actor_id="viewer_test", role=Role.VIEWER)

    # Admin has all permissions including response authorization
    assert admin.has_permission(Permission.VIEW_SYSTEM_STATUS)
    assert admin.has_permission(Permission.ACKNOWLEDGE_INCIDENT)
    assert admin.has_permission(Permission.RESOLVE_INCIDENT)
    assert admin.has_permission(Permission.AUTHORIZE_RESPONSE)
    assert admin.has_permission(Permission.VIEW_AUDIT_LOG)

    # Operator can acknowledge and resolve, but NOT authorize response
    assert operator.has_permission(Permission.ACKNOWLEDGE_INCIDENT)
    assert operator.has_permission(Permission.RESOLVE_INCIDENT)
    assert not operator.has_permission(Permission.AUTHORIZE_RESPONSE)

    # Viewer can only view
    assert viewer.has_permission(Permission.VIEW_INCIDENTS)
    assert not viewer.has_permission(Permission.ACKNOWLEDGE_INCIDENT)
    assert not viewer.has_permission(Permission.RESOLVE_INCIDENT)
    assert not viewer.has_permission(Permission.AUTHORIZE_RESPONSE)


# ============================================================================
# 2. Permission Enforcement
# ============================================================================
def test_2_permission_enforcement():
    auth_svc = AuthorityService()
    viewer = Actor(actor_id="v_1", role=Role.VIEWER)
    admin = Actor(actor_id="a_1", role=Role.ADMIN)

    # Check permission boolean
    assert not auth_svc.check_permission(viewer, Permission.ACKNOWLEDGE_INCIDENT)
    assert auth_svc.check_permission(admin, Permission.ACKNOWLEDGE_INCIDENT)

    # Assert permission raises UnauthorizedError
    with pytest.raises(UnauthorizedError) as exc_info:
        auth_svc.assert_permission(viewer, Permission.ACKNOWLEDGE_INCIDENT)
    assert "not authorized" in str(exc_info.value).lower() or "lacks permission" in str(exc_info.value).lower()

    with pytest.raises(UnauthorizedError):
        auth_svc.assert_permission(None, Permission.VIEW_INCIDENTS)

    # Admin succeeds without raising
    auth_svc.assert_permission(admin, Permission.AUTHORIZE_RESPONSE)


# ============================================================================
# 3. Incident Creation from Rule / Risk Output
# ============================================================================
def test_3_incident_creation_from_rule_and_risk(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_inc_create.db"))
    mgr = IncidentManager(storage=storage)

    # Case A: Elevated risk + triggered rule creates incident
    decision = make_decision(rule_name="UNEXPECTED_PERSON_PRESENCE", risk_score=0.45)
    incident = mgr.evaluate_decision_context(decision)

    assert incident is not None
    assert incident.incident_type == IncidentType.UNEXPECTED_PERSON_PRESENCE
    assert incident.status == IncidentStatus.ACTIVE
    assert incident.risk_score == 0.45
    assert incident.analysis_id == decision.analysis_id
    assert incident.risk_id == decision.risk.risk_id
    assert "ev-real-01" in incident.source_event_ids

    # Case B: Low ambient activity (no rules triggered, risk < threshold) does NOT create incident
    ambient_decision = make_decision(
        rule_name="AMBIENT",
        condition_satisfied=False,
        risk_score=0.10,
        risk_level=RiskLevel.LOW,
    )
    no_incident = mgr.evaluate_decision_context(ambient_decision)
    assert no_incident is None


# ============================================================================
# 4. Incident Persistence
# ============================================================================
def test_4_incident_persistence(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_inc_persist.db"))
    incident = Incident(
        incident_id="inc-persist-100",
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        risk_score=0.88,
        uncertainty="low",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["fall-ev-01"],
        analysis_id="ana-999",
        risk_id="risk-999",
    )
    storage.save_incident(incident)

    loaded = storage.get_incident("inc-persist-100")
    assert loaded is not None
    assert loaded.incident_id == "inc-persist-100"
    assert loaded.incident_type == IncidentType.POSSIBLE_FALL
    assert loaded.risk_score == 0.88
    assert loaded.status == IncidentStatus.ACTIVE
    assert loaded.source_event_ids == ["fall-ev-01"]


# ============================================================================
# 5. Incident Deduplication & Correlation
# ============================================================================
def test_5_incident_deduplication_and_correlation(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_inc_dedup.db"))
    mgr = IncidentManager(storage=storage)

    # First observation creates active incident
    dec1 = make_decision(analysis_id="ana-1", supporting_events=["ev-1"])
    inc1 = mgr.evaluate_decision_context(dec1)
    assert inc1 is not None
    inc1_id = inc1.incident_id

    # Second observation of same type within active window updates existing incident
    dec2 = make_decision(analysis_id="ana-2", supporting_events=["ev-2"], risk_score=0.55)
    inc2 = mgr.evaluate_decision_context(dec2)

    assert inc2 is not None
    assert inc2.incident_id == inc1_id  # Same incident ID correlated
    assert "ev-1" in inc2.source_event_ids
    assert "ev-2" in inc2.source_event_ids
    assert inc2.risk_score == 0.55

    # Total active incidents in storage must still be 1 (no spam)
    all_active = storage.get_active_incidents()
    assert len(all_active) == 1


# ============================================================================
# 6. Lifecycle Valid Transitions
# ============================================================================
def test_6_lifecycle_valid_transitions(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_lifecycle.db"))
    mgr = IncidentManager(storage=storage)
    op = Actor(actor_id="op_1", role=Role.OPERATOR)
    admin = Actor(actor_id="adm_1", role=Role.ADMIN)

    dec = make_decision()
    inc = mgr.evaluate_decision_context(dec)
    assert inc is not None
    assert inc.status == IncidentStatus.ACTIVE

    # ACTIVE -> ACKNOWLEDGED
    ack_inc = mgr.acknowledge_incident(inc.incident_id, op)
    assert ack_inc.status == IncidentStatus.ACKNOWLEDGED

    # ACKNOWLEDGED -> ESCALATED
    esc_inc = mgr.authorize_escalation(inc.incident_id, admin)
    assert esc_inc.status == IncidentStatus.ESCALATED

    # ESCALATED -> RESOLVED
    res_inc = mgr.resolve_incident(inc.incident_id, op, reason=ResolutionReason.CONDITION_CLEARED)
    assert res_inc.status == IncidentStatus.RESOLVED


# ============================================================================
# 7. Invalid Transitions Rejected
# ============================================================================
def test_7_invalid_transitions_rejected(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_invalid_transitions.db"))
    mgr = IncidentManager(storage=storage)
    op = Actor(actor_id="op_1", role=Role.OPERATOR)

    dec = make_decision()
    inc = mgr.evaluate_decision_context(dec)
    assert inc is not None

    # Resolve incident
    mgr.resolve_incident(inc.incident_id, op, reason=ResolutionReason.NORMAL_ACTIVITY)

    # RESOLVED is terminal: cannot acknowledge or escalate
    with pytest.raises(InvalidTransitionError):
        mgr.acknowledge_incident(inc.incident_id, op)

    admin = Actor(actor_id="adm_1", role=Role.ADMIN)
    with pytest.raises(InvalidTransitionError):
        mgr.authorize_escalation(inc.incident_id, admin)


# ============================================================================
# 8. Acknowledgement Authorization & Audit
# ============================================================================
def test_8_acknowledgement_authorization_and_audit(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_ack.db"))
    mgr = IncidentManager(storage=storage)
    audit = AuditLogger(storage=storage)
    op = Actor(actor_id="op_verified", role=Role.OPERATOR)

    dec = make_decision()
    inc = mgr.evaluate_decision_context(dec)
    assert inc is not None

    updated = mgr.acknowledge_incident(inc.incident_id, op)
    assert updated.is_acknowledged is True
    assert updated.acknowledged_by == "op_verified"
    assert updated.acknowledged_at is not None

    # Verify audit record
    audits = audit.get_records_for_target(inc.incident_id)
    actions = [a.action for a in audits]
    assert AuditAction.INCIDENT_ACKNOWLEDGED in actions


# ============================================================================
# 9. Resolution Authorization & Audit
# ============================================================================
def test_9_resolution_authorization_and_audit(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_resolve.db"))
    mgr = IncidentManager(storage=storage)
    audit = AuditLogger(storage=storage)
    op = Actor(actor_id="op_resolver", role=Role.OPERATOR)

    dec = make_decision()
    inc = mgr.evaluate_decision_context(dec)
    assert inc is not None

    # Empty reason must be rejected
    with pytest.raises(ValueError):
        mgr.resolve_incident(inc.incident_id, op, reason="")

    resolved = mgr.resolve_incident(
        inc.incident_id,
        op,
        reason=ResolutionReason.FALSE_POSITIVE,
        notes="Person was pet cat, verified safe.",
    )
    assert resolved.status == IncidentStatus.RESOLVED
    assert resolved.is_resolved is True
    assert resolved.resolution_reason == ResolutionReason.FALSE_POSITIVE
    assert resolved.resolved_by == "op_resolver"

    # Verify audit record
    audits = audit.get_records_for_target(inc.incident_id)
    actions = [a.action for a in audits]
    assert AuditAction.INCIDENT_RESOLVED in actions


# ============================================================================
# 10. Dismissal Authorization & Audit
# ============================================================================
def test_10_dismissal_authorization_and_audit(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_dismiss.db"))
    mgr = IncidentManager(storage=storage)
    audit = AuditLogger(storage=storage)
    op = Actor(actor_id="op_dismisser", role=Role.OPERATOR)

    dec = make_decision()
    inc = mgr.evaluate_decision_context(dec)
    assert inc is not None

    dismissed = mgr.dismiss_incident(inc.incident_id, op, reason="User acknowledged routine testing")
    assert dismissed.status == IncidentStatus.DISMISSED
    assert dismissed.metadata.get("dismissal_reason") == "User acknowledged routine testing"

    # History is retained in database (not deleted)
    retrieved = storage.get_incident(inc.incident_id)
    assert retrieved is not None
    assert retrieved.status == IncidentStatus.DISMISSED

    # Verify audit trail
    audits = audit.get_records_for_target(inc.incident_id)
    actions = [a.action for a in audits]
    assert AuditAction.INCIDENT_DISMISSED in actions


# ============================================================================
# 11. Escalation Authorization Boundary
# ============================================================================
def test_11_escalation_authorization_boundary(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_escalation.db"))
    mgr = IncidentManager(storage=storage)
    op = Actor(actor_id="op_1", role=Role.OPERATOR)
    admin = Actor(actor_id="adm_1", role=Role.ADMIN)

    dec = make_decision()
    inc = mgr.evaluate_decision_context(dec)
    assert inc is not None

    # 1. Operator requests escalation
    req_inc = mgr.request_escalation(inc.incident_id, op, reason="Potential high concern")
    assert req_inc.escalation_state == EscalationState.PENDING_AUTHORIZATION

    # 2. Operator CANNOT authorize escalation (requires ADMIN with AUTHORIZE_RESPONSE)
    with pytest.raises(UnauthorizedError):
        mgr.authorize_escalation(inc.incident_id, op)

    # 3. Admin authorizes escalation
    auth_inc = mgr.authorize_escalation(inc.incident_id, admin)
    assert auth_inc.status == IncidentStatus.ESCALATED
    assert auth_inc.escalation_state == EscalationState.AUTHORIZED
    assert auth_inc.escalated_by == "adm_1"


# ============================================================================
# 12. Audit Logging Append-Only & Target Query
# ============================================================================
def test_12_audit_logging_append_only(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_audit.db"))
    audit = AuditLogger(storage=storage)

    audit.record(
        actor_id="user_1",
        action=AuditAction.INCIDENT_CREATED,
        target_type="incident",
        target_id="target_abc",
        reason="Triggered",
    )
    audit.record(
        actor_id="user_2",
        action=AuditAction.INCIDENT_ACKNOWLEDGED,
        target_type="incident",
        target_id="target_abc",
        reason="Acknowledged",
    )

    records = audit.get_records_for_target("target_abc")
    assert len(records) == 2
    assert records[0].action == AuditAction.INCIDENT_CREATED
    assert records[1].action == AuditAction.INCIDENT_ACKNOWLEDGED


# ============================================================================
# 13. Risk -> Incident Mapping
# ============================================================================
def test_13_risk_to_incident_mapping(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_mapping.db"))
    mgr = IncidentManager(storage=storage)

    # Fall rule maps to POSSIBLE_FALL
    dec_fall = make_decision(rule_id="rule_possible_fall", rule_name="POSSIBLE_FALL", risk_score=0.80)
    inc_fall = mgr.evaluate_decision_context(dec_fall)
    assert inc_fall is not None
    assert inc_fall.incident_type == IncidentType.POSSIBLE_FALL

    # Unauthorized object removal maps to POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL
    dec_obj = make_decision(
        rule_id="rule_unauthorized_object_removal",
        rule_name="POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL",
        risk_score=0.65,
    )
    inc_obj = mgr.evaluate_decision_context(dec_obj)
    assert inc_obj is not None
    assert inc_obj.incident_type == IncidentType.POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL


# ============================================================================
# 14. Event -> Incident Traceability
# ============================================================================
def test_14_event_to_incident_traceability(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_traceability.db"))
    mgr = IncidentManager(storage=storage)

    ev_ids = ["ev_001_person_entered", "ev_002_person_stationary"]
    dec = make_decision(analysis_id="ana_trace_123", supporting_events=ev_ids)
    inc = mgr.evaluate_decision_context(dec)
    assert inc is not None

    # Incident stores exact analysis, risk, and event IDs
    assert inc.analysis_id == "ana_trace_123"
    assert inc.risk_id == "risk-01"
    assert inc.source_event_ids == ev_ids


# ============================================================================
# 15. API Incident Endpoints
# ============================================================================
def test_15_api_incident_endpoints(tmp_path):
    db_file = tmp_path / "test_api_incidents.db"
    storage = get_event_storage(db_path=str(db_file), reload=True)
    mgr = get_incident_manager(storage=storage, reload=True)
    get_alert_manager(storage=storage, reload=True)
    get_audit_logger(storage=storage, reload=True)

    try:
        # Seed an incident
        dec = make_decision()
        inc = mgr.evaluate_decision_context(dec)
        assert inc is not None

        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()

        # List incidents
        res = client.get("/api/incidents")
        assert res.status_code == 200
        data = res.get_json()
        assert data["count"] >= 1

        # Get by ID
        res_id = client.get(f"/api/incidents/{inc.incident_id}")
        assert res_id.status_code == 200
        assert res_id.get_json()["incident"]["incident_id"] == inc.incident_id

        # Acknowledge via API
        res_ack = client.post(
            f"/api/incidents/{inc.incident_id}/acknowledge",
            json={"actor_id": "operator_01"},
        )
        assert res_ack.status_code == 200
        assert res_ack.get_json()["incident"]["is_acknowledged"] is True

        # Check alerts & audit endpoints
        res_alerts = client.get("/api/alerts")
        assert res_alerts.status_code == 200
        res_audit = client.get(f"/api/audit/{inc.incident_id}")
        assert res_audit.status_code == 200
    finally:
        get_event_storage(reload=True)
        get_incident_manager(reload=True)
        get_alert_manager(reload=True)
        get_audit_logger(reload=True)


# ============================================================================
# 16. Unauthorized Action Rejection via API
# ============================================================================
def test_16_unauthorized_action_rejection_via_api(tmp_path):
    db_file = tmp_path / "test_api_unauth.db"
    storage = get_event_storage(db_path=str(db_file), reload=True)
    mgr = get_incident_manager(storage=storage, reload=True)
    get_alert_manager(storage=storage, reload=True)
    get_audit_logger(storage=storage, reload=True)

    try:
        dec = make_decision()
        inc = mgr.evaluate_decision_context(dec)
        assert inc is not None

        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()

        # Viewer attempts to acknowledge -> 403 Unauthorized
        res_ack = client.post(
            f"/api/incidents/{inc.incident_id}/acknowledge",
            json={"actor_id": "viewer_01"},
        )
        assert res_ack.status_code == 403

        # Operator attempts to authorize escalation -> 403 Unauthorized
        res_esc = client.post(
            f"/api/incidents/{inc.incident_id}/escalation/authorize",
            json={"actor_id": "operator_01"},
        )
        assert res_esc.status_code == 403
    finally:
        get_event_storage(reload=True)
        get_incident_manager(reload=True)
        get_alert_manager(reload=True)
        get_audit_logger(reload=True)



# ============================================================================
# 17. Resolved Incident Cannot Be Acknowledged
# ============================================================================
def test_17_resolved_incident_cannot_be_acknowledged(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_res_ack.db"))
    mgr = IncidentManager(storage=storage)
    op = Actor(actor_id="op_1", role=Role.OPERATOR)

    dec = make_decision()
    inc = mgr.evaluate_decision_context(dec)
    assert inc is not None

    mgr.resolve_incident(inc.incident_id, op, reason=ResolutionReason.NORMAL_ACTIVITY)

    with pytest.raises(InvalidTransitionError):
        mgr.acknowledge_incident(inc.incident_id, op)


# ============================================================================
# 18. Dismissed Incident Cannot Be Escalated
# ============================================================================
def test_18_dismissed_incident_cannot_be_escalated(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_dismiss_esc.db"))
    mgr = IncidentManager(storage=storage)
    op = Actor(actor_id="op_1", role=Role.OPERATOR)
    admin = Actor(actor_id="adm_1", role=Role.ADMIN)

    dec = make_decision()
    inc = mgr.evaluate_decision_context(dec)
    assert inc is not None

    mgr.dismiss_incident(inc.incident_id, op, reason="User test dismiss")

    with pytest.raises(InvalidTransitionError):
        mgr.request_escalation(inc.incident_id, op, reason="Try escalate")

    with pytest.raises(InvalidTransitionError):
        mgr.authorize_escalation(inc.incident_id, admin)


# ============================================================================
# 19. High Uncertainty Preserves Human Verification Requirement
# ============================================================================
def test_19_high_uncertainty_preserves_human_verification(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_high_uncertainty.db"))
    mgr = IncidentManager(storage=storage)

    dec = make_decision(uncertainty="high", risk_score=0.75)
    inc = mgr.evaluate_decision_context(dec)
    assert inc is not None

    assert inc.uncertainty == "high"
    assert inc.metadata.get("human_verification_required") is True

    # Orchestrator boundary confirms human authorization is mandatory
    orch = AtlasOrchestrator(storage=storage)
    eval_res = orch.evaluate_incident_eligibility(inc.incident_id)
    assert eval_res.requires_human_authorization is True
    assert eval_res.decision == OrchestratorDecision.AUTHORIZATION_REQUIRED


# ============================================================================
# 20. No External Notification Occurs
# ============================================================================
def test_20_no_external_notification_occurs(tmp_path):
    storage = EventStorage(db_path=str(tmp_path / "test_no_external_notification.db"))
    alert_mgr = AlertManager(storage=storage)
    mgr = IncidentManager(storage=storage, alert_manager=alert_mgr)

    dec = make_decision(risk_score=0.95, risk_level=RiskLevel.CRITICAL)
    inc = mgr.evaluate_decision_context(dec)
    assert inc is not None

    # Alert record created internally
    alert = storage.get_alert_for_incident(inc.incident_id)
    assert alert is not None
    assert alert.status == AlertStatus.ACTIVE
    assert alert.severity == AlertSeverity.CRITICAL

    # Notification count remains 0 (no SMS/email/push dispatched)
    assert alert.notification_count == 0
    assert alert.last_notified_at is None
