"""Unit and Integration Tests for Step 6.1: Real-Time Operational Dashboard Foundation.

Validates:
1. Unified read-only endpoint GET /api/dashboard/state schema and contract.
2. Real backend state propagation (events, context, intelligence, rules, risk, incidents, alerts, orchestrator).
3. Zero client-side computation: data contract purely reflects backend metrics.
4. Empty state handling (empty events, no active incidents).
5. Read-only safety boundary: dashboard endpoint causes zero side effects.
6. Frontend static assets serving (index.html, app.js, style.css).
"""

from __future__ import annotations

import json
import pytest
from flask import Flask

from atlas.alerts.schema import Alert, AlertSeverity, AlertStatus
from atlas.alerts.service import AlertManager, get_alert_manager
from atlas.api.app import create_app
from atlas.config.settings import Settings
from atlas.context.memory import ContextManager, get_context_manager
from atlas.events.schema import ATLASEvent, EventSeverity, EventStatus, EventType
from atlas.events.storage import EventStorage, get_event_storage
from atlas.incidents.schema import EscalationState, Incident, IncidentStatus, IncidentType
from atlas.incidents.service import IncidentManager, get_incident_manager
from atlas.intelligence.schemas import LLMVerificationResult
from atlas.orchestration import AtlasOrchestrator, OrchestratorDecision
from atlas.risk.schema import DecisionContext, RiskAssessment, RiskLevel
from atlas.rules.schema import RuleEvaluationResult


@pytest.fixture
def client(tmp_path):
    """Create Flask test client with an isolated temporary database."""
    db_file = tmp_path / "test_dashboard.db"
    storage = EventStorage(str(db_file))
    
    # Initialize isolated singletons for test
    ctx = ContextManager(storage=storage)
    inc_mgr = IncidentManager(storage=storage)
    alert_mgr = AlertManager(storage=storage)

    # Patch global singletons
    import atlas.events.storage as st_mod
    import atlas.context.memory as ctx_mod
    import atlas.incidents.service as inc_mod
    import atlas.alerts.service as al_mod

    orig_st = st_mod._global_storage
    orig_ctx = ctx_mod._global_context_manager
    orig_inc = inc_mod._global_incident_manager
    orig_al = al_mod._global_alert_manager

    st_mod._global_storage = storage
    ctx_mod._global_context_manager = ctx
    inc_mod._global_incident_manager = inc_mgr
    al_mod._global_alert_manager = alert_mgr

    settings = Settings()
    app = create_app(settings)
    app.config["TESTING"] = True

    with app.test_client() as test_client:
        yield test_client

    # Restore globals
    st_mod._global_storage = orig_st
    ctx_mod._global_context_manager = orig_ctx
    inc_mod._global_incident_manager = orig_inc
    al_mod._global_alert_manager = orig_al


def test_1_dashboard_state_schema_and_contract(client):
    """Verify /api/dashboard/state returns 200 and all required top-level contract keys."""
    resp = client.get("/api/dashboard/state")
    assert resp.status_code == 200
    data = resp.get_json()

    required_keys = [
        "timestamp",
        "device_id",
        "environment",
        "system_status",
        "subsystems",
        "camera_status",
        "perception_status",
        "active_people_count",
        "active_object_count",
        "latest_events",
        "active_events",
        "latest_analysis",
        "latest_rule_results",
        "latest_risk",
        "active_incidents",
        "active_alerts",
        "orchestrator_state",
        "authorization_state",
        "source_health",
    ]

    for key in required_keys:
        assert key in data, f"Missing required dashboard contract key: {key}"


def test_2_dashboard_state_subsystems_dictionary(client):
    """Verify subsystems dictionary contains all required subsystem statuses."""
    resp = client.get("/api/dashboard/state")
    data = resp.get_json()
    subsystems = data["subsystems"]

    expected_subs = [
        "backend", "camera", "perception", "intelligence",
        "rules", "risk", "incidents", "alerts", "orchestrator"
    ]
    for sub in expected_subs:
        assert sub in subsystems
        assert isinstance(subsystems[sub], str)


def test_3_dashboard_state_reflects_active_events_and_counts(client):
    """Verify that when real events are recorded, dashboard reflects them."""
    ctx = get_context_manager()
    ev = ATLASEvent(
        event_type=EventType.PERSON_ENTERED.value,
        timestamp="2026-09-09T22:30:00Z",
        source="device_0",
        person_id="1",
        confidence=0.92,
        status=EventStatus.ACTIVE.value,
        severity=EventSeverity.INFO.value,
    )
    ctx.record_event(ev)

    resp = client.get("/api/dashboard/state")
    data = resp.get_json()

    assert data["active_people_count"] >= 1
    assert len(data["active_events"]) >= 1
    assert data["active_events"][0]["event_id"] == ev.event_id
    assert data["active_events"][0]["confidence"] == 0.92


def test_4_dashboard_state_empty_events_handled_gracefully(client):
    """Verify empty events return empty lists without crashing."""
    resp = client.get("/api/dashboard/state")
    data = resp.get_json()
    assert isinstance(data["latest_events"], list)
    assert isinstance(data["active_events"], list)
    assert data["active_people_count"] == 0


def test_5_dashboard_state_reflects_persisted_analysis(client):
    """Verify dashboard state includes latest analysis and risk from persistent storage."""
    storage = get_event_storage()

    rule_eval = RuleEvaluationResult(
        evaluation_id="eval-dash-1",
        rule_id="rule_unexpected_presence",
        rule_name="UNEXPECTED_PERSON_PRESENCE",
        timestamp="2026-09-09T22:30:00Z",
        condition_satisfied=True,
        confidence=0.88,
        supporting_event_ids=["ev-test-1"],
        missing_evidence=[],
        contradicting_evidence=[],
    )
    risk = RiskAssessment(
        risk_id="risk-dash-1",
        score=0.42,
        level=RiskLevel.MEDIUM,
        timestamp="2026-09-09T22:30:00Z",
        reason="Rule UNEXPECTED_PERSON_PRESENCE satisfied.",
        human_verification_required=True,
        uncertainty="moderate",
        contributing_factors=["Test"],
        supporting_event_ids=["ev-test-1"],
        supporting_rule_ids=["rule_unexpected_presence"],
    )
    llm_res = LLMVerificationResult(
        timestamp="2026-09-09T22:30:00Z",
        situation="Person observed standing in corridor.",
        interpretation="Normal presence observed.",
        supporting_event_ids=["ev-test-1"],
        confidence=0.85,
        uncertainty="moderate",
        verification_required=True,
        status="completed",
        model_used="qwen2.5:3b",
    )
    decision = DecisionContext(
        analysis_id="ana-dash-1",
        timestamp="2026-09-09T22:30:00Z",
        evidence={"event_ids": ["ev-test-1"]},
        llm_verification=llm_res,
        rule_evaluations=[rule_eval],
        risk=risk,
    )
    storage.save_analysis(decision)

    resp = client.get("/api/dashboard/state")
    data = resp.get_json()

    assert data["latest_analysis"] is not None
    assert data["latest_analysis"]["situation"] == "Person observed standing in corridor."
    assert data["latest_analysis"]["confidence"] == 0.85
    assert data["latest_risk"] is not None
    assert data["latest_risk"]["score"] == 0.42
    assert data["latest_risk"]["level"] == "MEDIUM"


def test_6_dashboard_state_empty_incidents_handled(client):
    """Verify empty incidents return empty list without crashing."""
    resp = client.get("/api/dashboard/state")
    data = resp.get_json()

    assert data["active_incidents"] == []
    assert data["active_alerts"] == []
    assert data["orchestrator_state"]["decision"] == "STANDBY"
    assert data["orchestrator_state"]["requires_human_authorization"] is False


def test_7_dashboard_state_reflects_active_incidents_and_orchestrator(client):
    """Verify active incidents and orchestrator decision are reflected in dashboard."""
    inc_mgr = get_incident_manager()
    storage = get_event_storage()

    rule_eval = RuleEvaluationResult(
        evaluation_id="eval-dash-2",
        rule_id="rule_unexpected_presence",
        rule_name="UNEXPECTED_PERSON_PRESENCE",
        timestamp="2026-09-09T22:31:00Z",
        condition_satisfied=True,
        confidence=0.88,
        supporting_event_ids=["ev-test-2"],
        missing_evidence=[],
        contradicting_evidence=[],
    )
    risk = RiskAssessment(
        risk_id="risk-dash-2",
        score=0.45,
        level=RiskLevel.MEDIUM,
        timestamp="2026-09-09T22:31:00Z",
        reason="Elevated presence detected.",
        human_verification_required=True,
        uncertainty="moderate",
        contributing_factors=["Test"],
        supporting_event_ids=["ev-test-2"],
        supporting_rule_ids=["rule_unexpected_presence"],
    )
    llm_res = LLMVerificationResult(
        timestamp="2026-09-09T22:31:00Z",
        situation="Person detected.",
        interpretation="Normal presence.",
        supporting_event_ids=["ev-test-2"],
        confidence=0.80,
        uncertainty="moderate",
        verification_required=True,
        status="completed",
    )
    decision = DecisionContext(
        analysis_id="ana-dash-2",
        timestamp="2026-09-09T22:31:00Z",
        evidence={"event_ids": ["ev-test-2"]},
        llm_verification=llm_res,
        rule_evaluations=[rule_eval],
        risk=risk,
    )

    incident = inc_mgr.evaluate_decision_context(decision)
    assert incident is not None

    resp = client.get("/api/dashboard/state")
    data = resp.get_json()

    assert len(data["active_incidents"]) == 1
    assert data["active_incidents"][0]["incident_id"] == incident.incident_id
    assert len(data["active_alerts"]) == 1
    assert data["orchestrator_state"]["decision"] == "AUTHORIZATION_REQUIRED"
    assert data["orchestrator_state"]["requires_human_authorization"] is True
    assert data["authorization_state"]["human_authorization_required"] is True


def test_8_dashboard_endpoint_is_strictly_read_only(client):
    """Verify multiple GET calls to /api/dashboard/state cause zero mutations."""
    resp1 = client.get("/api/dashboard/state")
    data1 = resp1.get_json()

    resp2 = client.get("/api/dashboard/state")
    data2 = resp2.get_json()

    assert len(data1["active_incidents"]) == len(data2["active_incidents"])
    assert len(data1["active_alerts"]) == len(data2["active_alerts"])
    assert data1["orchestrator_state"]["decision"] == data2["orchestrator_state"]["decision"]


def test_9_frontend_serves_html_and_static_assets(client):
    """Verify root / serves index.html and /app.js serves the operational dashboard code."""
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert b"ATLAS HOME" in res_root.data
    assert b"OPERATIONAL SAFETY BOUNDARY" in res_root.data

    res_js = client.get("/app.js")
    assert res_js.status_code == 200
    assert b"fetchDashboardState" in res_js.data

    res_css = client.get("/style.css")
    assert res_css.status_code == 200
    assert b"--bg-dark" in res_css.data


def test_10_camera_frame_returns_valid_frame_or_503(client):
    """Verify /api/camera/frame returns real JPEG frame (200) or HTTP 503 if disconnected (Zero-Mock policy)."""
    resp = client.get("/api/camera/frame")
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        assert resp.mimetype == "image/jpeg"
        assert len(resp.data) > 0
    else:
        data = resp.get_json()
        assert "error" in data
        assert "No real camera frame available" in data["error"]
