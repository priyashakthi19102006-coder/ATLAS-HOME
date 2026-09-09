"""Tests for Step 5.2: Final Safety Integrity + Authority Hardening.

Covers:
1. System authority separation: system_core is NOT a human role and CANNOT authorize escalation.
2. Alert correlation and deduplication: 1 active incident -> 1 active alert invariant.
3. Orchestrator safety boundary: system_core cannot satisfy human authorization; no bypass.
4. Audit traceability: end-to-end chain reconstruction with real entity IDs.
"""

from __future__ import annotations

import pytest

from atlas.alerts.schema import AlertSeverity, AlertStatus
from atlas.alerts.service import AlertManager
from atlas.audit.schema import AuditAction
from atlas.audit.service import AuditLogger
from atlas.authority.models import Action, Actor, Permission, Role
from atlas.authority.service import DEFAULT_ADMIN, DEFAULT_OPERATOR, SYSTEM_ACTOR, AuthorityService, UnauthorizedError
from atlas.events.schema import ATLASEvent, EventType
from atlas.events.storage import EventStorage
from atlas.incidents.schema import EscalationState, Incident, IncidentStatus, IncidentType, ResolutionReason
from atlas.incidents.service import IncidentManager, InvalidTransitionError
from atlas.intelligence.schemas import LLMVerificationResult
from atlas.orchestration import AtlasOrchestrator, OrchestratorDecision
from atlas.risk.schema import DecisionContext, RiskAssessment, RiskLevel
from atlas.rules.schema import RuleEvaluationResult


def make_decision(
    analysis_id: str = "ana-step5-2",
    rule_id: str = "rule_unexpected_presence",
    rule_name: str = "UNEXPECTED_PERSON_PRESENCE",
    condition_satisfied: bool = True,
    risk_score: float = 0.45,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    uncertainty: str = "moderate",
    supporting_events: list[str] | None = None,
) -> DecisionContext:
    events = supporting_events or ["ev-real-501", "ev-real-502"]
    rule_eval = RuleEvaluationResult(
        evaluation_id="eval-501",
        rule_id=rule_id,
        rule_name=rule_name,
        timestamp="2026-09-09T22:00:00Z",
        condition_satisfied=condition_satisfied,
        confidence=0.88,
        supporting_event_ids=events,
        missing_evidence=[],
        contradicting_evidence=[],
    )
    risk = RiskAssessment(
        risk_id="risk-501",
        score=risk_score,
        level=risk_level,
        timestamp="2026-09-09T22:00:00Z",
        reason="Elevated risk detected in test scenario.",
        human_verification_required=True,
        uncertainty=uncertainty,
        contributing_factors=["Test factor"],
        supporting_event_ids=events,
        supporting_rule_ids=[rule_id],
    )
    llm_res = LLMVerificationResult(
        timestamp="2026-09-09T22:00:00Z",
        situation="Observed activity",
        interpretation="Normal presence",
        supporting_event_ids=events,
        confidence=0.85,
        uncertainty="moderate",
        verification_required=True,
        status="completed",
    )
    return DecisionContext(
        analysis_id=analysis_id,
        timestamp="2026-09-09T22:00:00Z",
        evidence={"event_ids": events},
        llm_verification=llm_res,
        rule_evaluations=[rule_eval],
        risk=risk,
    )


# ============================================================================
# Objective 1: System Authority Separation Tests
# ============================================================================

def test_1_system_core_is_not_human_role():
    """Verify that system_core has role=None and is_system=True, distinct from human roles."""
    assert SYSTEM_ACTOR.role is None
    assert SYSTEM_ACTOR.is_system is True
    assert SYSTEM_ACTOR.actor_id == "system_core"
    assert SYSTEM_ACTOR.role != Role.ADMIN
    assert SYSTEM_ACTOR.role != Role.OPERATOR
    assert SYSTEM_ACTOR.role != Role.VIEWER


def test_2_system_core_cannot_have_authorize_response_permission():
    """Verify that system_core can never possess AUTHORIZE_RESPONSE."""
    assert SYSTEM_ACTOR.has_permission(Permission.AUTHORIZE_RESPONSE) is False

    # Even if artificially placed into custom_permissions, Actor.has_permission blocks it
    tampered_system = Actor(
        actor_id="system_core",
        role=None,
        is_system=True,
        custom_permissions=frozenset({Permission.AUTHORIZE_RESPONSE}),
    )
    assert tampered_system.has_permission(Permission.AUTHORIZE_RESPONSE) is False


def test_3_system_core_has_internal_pipeline_permissions():
    """Verify system_core retains permissions necessary for internal pipeline operations."""
    assert SYSTEM_ACTOR.has_permission(Permission.VIEW_SYSTEM_STATUS) is True
    assert SYSTEM_ACTOR.has_permission(Permission.VIEW_EVENTS) is True
    assert SYSTEM_ACTOR.has_permission(Permission.VIEW_INCIDENTS) is True
    assert SYSTEM_ACTOR.has_permission(Permission.VIEW_EVIDENCE) is True
    assert SYSTEM_ACTOR.has_permission(Permission.VIEW_AUDIT_LOG) is True


def test_4_system_core_cannot_authorize_escalation(tmp_path):
    """Verify that system_core attempting to authorize escalation raises UnauthorizedError."""
    storage = EventStorage(db_path=str(tmp_path / "test_sys_no_esc.db"))
    inc_mgr = IncidentManager(storage=storage)

    decision = make_decision()
    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    with pytest.raises(UnauthorizedError) as exc_info:
        inc_mgr.authorize_escalation(incident.incident_id, actor=SYSTEM_ACTOR)
    assert "System authority cannot authorize" in str(exc_info.value) or "lacks permission" in str(exc_info.value)


def test_5_human_admin_can_authorize_escalation(tmp_path):
    """Verify that an authentic human ADMIN can authorize response escalation."""
    storage = EventStorage(db_path=str(tmp_path / "test_admin_esc.db"))
    inc_mgr = IncidentManager(storage=storage)

    decision = make_decision()
    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    escalated = inc_mgr.authorize_escalation(incident.incident_id, actor=DEFAULT_ADMIN, reason="Admin verified emergency.")
    assert escalated.status == IncidentStatus.ESCALATED
    assert escalated.escalation_state == EscalationState.AUTHORIZED
    assert escalated.escalated_by == DEFAULT_ADMIN.actor_id


def test_6_human_operator_cannot_authorize_escalation(tmp_path):
    """Verify that an OPERATOR role cannot authorize escalation."""
    storage = EventStorage(db_path=str(tmp_path / "test_op_no_esc.db"))
    inc_mgr = IncidentManager(storage=storage)

    decision = make_decision()
    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    with pytest.raises(UnauthorizedError):
        inc_mgr.authorize_escalation(incident.incident_id, actor=DEFAULT_OPERATOR)


# ============================================================================
# Objective 2: Alert Correlation & Deduplication Tests
# ============================================================================

def test_7_first_eligible_incident_creates_one_alert(tmp_path):
    """Verify first eligible incident creates exactly one associated alert."""
    storage = EventStorage(db_path=str(tmp_path / "test_alert_one.db"))
    inc_mgr = IncidentManager(storage=storage)

    decision = make_decision(risk_score=0.55)
    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    active_alerts = storage.get_active_alerts()
    assert len(active_alerts) == 1
    assert active_alerts[0].incident_id == incident.incident_id
    assert active_alerts[0].status == AlertStatus.ACTIVE


def test_8_repeated_evaluation_does_not_create_duplicate_alert(tmp_path):
    """Verify repeated evaluations of the same active incident update the existing alert."""
    storage = EventStorage(db_path=str(tmp_path / "test_alert_dedup.db"))
    inc_mgr = IncidentManager(storage=storage)

    decision1 = make_decision(analysis_id="ana-1", risk_score=0.45)
    inc1 = inc_mgr.evaluate_decision_context(decision1)
    assert inc1 is not None

    alert1 = storage.get_alert_for_incident(inc1.incident_id)
    assert alert1 is not None
    original_alert_id = alert1.alert_id

    # Evaluate again with updated observations for the same incident type
    decision2 = make_decision(analysis_id="ana-2", risk_score=0.75, risk_level=RiskLevel.HIGH)
    inc2 = inc_mgr.evaluate_decision_context(decision2)
    assert inc2.incident_id == inc1.incident_id

    # Must still have only 1 alert, with the exact same alert_id
    recent_alerts = storage.get_recent_alerts()
    assert len(recent_alerts) == 1
    assert recent_alerts[0].alert_id == original_alert_id
    assert recent_alerts[0].severity == AlertSeverity.HIGH


def test_9_resolved_incident_updates_alert_to_resolved(tmp_path):
    """Verify resolving an incident transitions its associated alert to RESOLVED."""
    storage = EventStorage(db_path=str(tmp_path / "test_alert_resolve.db"))
    inc_mgr = IncidentManager(storage=storage)

    decision = make_decision()
    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    inc_mgr.resolve_incident(
        incident.incident_id,
        actor=DEFAULT_OPERATOR,
        reason=ResolutionReason.FALSE_POSITIVE,
        notes="Verified safe by operator.",
    )

    alert = storage.get_alert_for_incident(incident.incident_id)
    assert alert is not None
    assert alert.status == AlertStatus.RESOLVED

    # Active alerts should now be empty
    active_alerts = storage.get_active_alerts()
    assert len(active_alerts) == 0


def test_10_dismissed_incident_updates_alert_to_dismissed(tmp_path):
    """Verify dismissing an incident transitions its associated alert to DISMISSED."""
    storage = EventStorage(db_path=str(tmp_path / "test_alert_dismiss.db"))
    inc_mgr = IncidentManager(storage=storage)

    decision = make_decision()
    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    inc_mgr.dismiss_incident(
        incident.incident_id,
        actor=DEFAULT_OPERATOR,
        reason="Ambient noise dismissed.",
    )

    alert = storage.get_alert_for_incident(incident.incident_id)
    assert alert is not None
    assert alert.status == AlertStatus.DISMISSED

    # Active alerts should now be empty
    active_alerts = storage.get_active_alerts()
    assert len(active_alerts) == 0


def test_11_historical_alerts_remain_queryable(tmp_path):
    """Verify resolved/dismissed alerts remain preserved in historical storage queries."""
    storage = EventStorage(db_path=str(tmp_path / "test_hist_alerts.db"))
    inc_mgr = IncidentManager(storage=storage)

    decision = make_decision()
    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    inc_mgr.resolve_incident(
        incident.incident_id,
        actor=DEFAULT_OPERATOR,
        reason=ResolutionReason.CONDITION_CLEARED,
        notes="All clear.",
    )

    # Historical query still sees the alert
    all_alerts = storage.get_recent_alerts(limit=10)
    assert len(all_alerts) == 1
    assert all_alerts[0].status == AlertStatus.RESOLVED


# ============================================================================
# Objective 3: Orchestrator Safety Boundary Tests
# ============================================================================

def test_12_orchestrator_cannot_bypass_human_authorization_with_system_actor(tmp_path):
    """Verify passing SYSTEM_ACTOR to orchestrator yields AUTHORIZATION_REQUIRED, not ACTION_ELIGIBLE."""
    storage = EventStorage(db_path=str(tmp_path / "test_orch_sys.db"))
    inc_mgr = IncidentManager(storage=storage)
    orchestrator = AtlasOrchestrator(storage=storage)

    decision = make_decision()
    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    eval_result = orchestrator.evaluate_incident_eligibility(
        incident_id=incident.incident_id,
        actor=SYSTEM_ACTOR,
    )
    assert eval_result.decision == OrchestratorDecision.AUTHORIZATION_REQUIRED
    assert eval_result.requires_human_authorization is True


def test_13_orchestrator_action_eligible_only_with_authorized_human_admin(tmp_path):
    """Verify orchestrator returns ACTION_ELIGIBLE when authentic human ADMIN authorizes."""
    storage = EventStorage(db_path=str(tmp_path / "test_orch_admin.db"))
    inc_mgr = IncidentManager(storage=storage)
    orchestrator = AtlasOrchestrator(storage=storage)

    decision = make_decision()
    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    eval_result = orchestrator.evaluate_incident_eligibility(
        incident_id=incident.incident_id,
        actor=DEFAULT_ADMIN,
    )
    assert eval_result.decision == OrchestratorDecision.ACTION_ELIGIBLE
    assert eval_result.requires_human_authorization is False


def test_14_orchestrator_without_actor_requires_authorization(tmp_path):
    """Verify evaluating an active un-escalated incident without an actor yields AUTHORIZATION_REQUIRED."""
    storage = EventStorage(db_path=str(tmp_path / "test_orch_no_actor.db"))
    inc_mgr = IncidentManager(storage=storage)
    orchestrator = AtlasOrchestrator(storage=storage)

    decision = make_decision()
    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    eval_result = orchestrator.evaluate_incident_eligibility(incident_id=incident.incident_id, actor=None)
    assert eval_result.decision == OrchestratorDecision.AUTHORIZATION_REQUIRED
    assert eval_result.requires_human_authorization is True


# ============================================================================
# Objective 4: Audit Traceability Tests
# ============================================================================

def test_15_audit_traceability_reconstructs_complete_chain(tmp_path):
    """Verify complete end-to-end traceability from evidence events to incident, alert, and audit logs."""
    storage = EventStorage(db_path=str(tmp_path / "test_trace.db"))
    inc_mgr = IncidentManager(storage=storage)

    event_ids = ["ev-real-701", "ev-real-702"]
    decision = make_decision(
        analysis_id="ana-trace-001",
        rule_id="rule_unexpected_presence",
        supporting_events=event_ids,
        risk_score=0.60,
    )

    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    # Verify incident links back to source events, analysis, and risk
    assert set(incident.source_event_ids) == set(event_ids)
    assert incident.analysis_id == "ana-trace-001"
    assert incident.risk_id == "risk-501"
    assert "rule_unexpected_presence" in incident.triggering_rule_ids

    # Verify alert links back to incident
    alert = storage.get_alert_for_incident(incident.incident_id)
    assert alert is not None
    assert alert.incident_id == incident.incident_id

    # Verify audit logs record the incident creation with system_core actor
    audit_logger = AuditLogger(storage=storage)
    logs = audit_logger.get_recent_records(limit=10)
    creation_logs = [l for l in logs if l.action == AuditAction.INCIDENT_CREATED]
    assert len(creation_logs) == 1
    assert creation_logs[0].target_id == incident.incident_id
    assert creation_logs[0].actor_id == "system_core"
