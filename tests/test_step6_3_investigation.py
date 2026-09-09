"""Tests for ATLAS Home Step 6.3: Evidence & Incident Investigation.

Verifies:
1. Investigation endpoint exists (GET /api/incidents/<id>/investigation)
2. Known incident returns valid investigation snapshot with 200 OK
3. Unknown incident returns 404 Not Found
4. Endpoint is strictly read-only (repeated calls cause no mutations or state changes)
5. Incident fields correlate correctly
6. Source event IDs correlate correctly
7. Supporting events are real persisted events matching IDs
8. Analysis ID correlates correctly
9. LLM decision is returned correctly from persisted analysis
10. Supporting event IDs in LLM verification are preserved
11. Rule results correlate correctly
12. Risk assessment correlates correctly
13. Orchestrator state correlates correctly
14. Alert correlation works
15. Audit history correlates correctly
16. Chronological timeline ordering is strictly verified
17. Missing evidence components are represented explicitly (MISSING / NOT_RECORDED)
18. No fabricated camera evidence (status NOT_RECORDED)
19. Viewer role can read investigation data
20. Unauthorized mutation remains impossible
21. Frontend investigation view loads in HTML
22. Frontend does not calculate risk or infer incidents
23. Frontend does not call Ollama directly
24. Security audit: No secrets, passwords, or tokens exposed in payload
"""

import json
from pathlib import Path
import pytest

from atlas.api.app import create_app
from atlas.audit.schema import AuditAction
from atlas.audit.service import AuditLogger
from atlas.authority.auth import AuthService
from atlas.authority.models import Role
from atlas.config import Settings
from atlas.events.schema import ATLASEvent, EventSeverity, EventStatus, EventType
from atlas.events.storage import EventStorage
from atlas.intelligence.schemas import LLMVerificationResult
from atlas.intelligence.pipeline import IntelligencePipeline
from atlas.incidents.investigation import EvidenceAvailability, InvestigationPhase, InvestigationService
from atlas.incidents.schema import EscalationState, Incident, IncidentStatus, IncidentType
from atlas.orchestration import AtlasOrchestrator
from atlas.risk.schema import DecisionContext, RiskAssessment, RiskLevel
from atlas.rules.schema import RuleEvaluationResult


@pytest.fixture
def isolated_env(tmp_path):
    """Create an isolated test environment with temporary SQLite storage and test client."""
    db_path = tmp_path / "test_atlas_events.db"
    storage = EventStorage(str(db_path))
    auth_service = AuthService()
    audit_logger = AuditLogger(storage=storage)
    orchestrator = AtlasOrchestrator(storage=storage)
    inv_service = InvestigationService(storage=storage, orchestrator=orchestrator)

    import atlas.events.storage as st_mod
    import atlas.authority.auth as auth_mod
    import atlas.incidents.investigation as inv_mod
    import atlas.orchestration as orch_mod
    import atlas.audit.service as aud_mod

    orig_st = st_mod._global_storage
    orig_auth = auth_mod._global_auth_service
    orig_inv = inv_mod._investigation_service
    orig_orch = orch_mod._global_orchestrator
    orig_aud = aud_mod._global_audit_logger

    st_mod._global_storage = storage
    auth_mod._global_auth_service = auth_service
    inv_mod._investigation_service = inv_service
    orch_mod._global_orchestrator = orchestrator
    aud_mod._global_audit_logger = audit_logger

    settings = Settings()
    app = create_app(settings)
    app.config["TESTING"] = True

    client = app.test_client()

    yield {
        "storage": storage,
        "auth_service": auth_service,
        "inv_service": inv_service,
        "audit_logger": audit_logger,
        "client": client,
        "db_path": db_path,
    }

    st_mod._global_storage = orig_st
    auth_mod._auth_service = orig_auth
    inv_mod._investigation_service = orig_inv
    orch_mod._global_orchestrator = orig_orch
    aud_mod._global_audit_logger = orig_aud


def _seed_verified_incident(env):
    """Helper to populate real verified event, analysis, risk, incident, and audit records."""
    storage = env["storage"]
    audit_logger = env["audit_logger"]

    # 1. Real Events
    ev1 = ATLASEvent(
        event_id="ev-test-001",
        event_type=EventType.PERSON_ENTERED.value,
        timestamp="2026-09-09T18:00:01.000Z",
        status=EventStatus.ACTIVE.value,
        severity=EventSeverity.INFO.value,
        confidence=0.92,
        person_id="p-1",
        source="integrated_webcam",
    )
    ev2 = ATLASEvent(
        event_id="ev-test-002",
        event_type=EventType.PERSON_STATIONARY.value,
        timestamp="2026-09-09T18:00:05.000Z",
        status=EventStatus.ACTIVE.value,
        severity=EventSeverity.NOTICE.value,
        confidence=0.88,
        person_id="p-1",
        source="integrated_webcam",
    )
    storage.save_event(ev1)
    storage.save_event(ev2)

    # 2. Persisted Analysis
    llm_verif = LLMVerificationResult(
        verification_id="verif-test-001",
        timestamp="2026-09-09T18:00:06.000Z",
        situation="Person standing motionless in monitored foyer",
        interpretation="Subject entered and remained stationary near doorway.",
        confidence=0.85,
        uncertainty="low",
        verification_required=True,
        supporting_event_ids=["ev-test-001", "ev-test-002"],
        status="completed",
        model_used="qwen2.5:3b",
    )
    rule_eval = RuleEvaluationResult(
        evaluation_id="rule-test-001",
        rule_id="UNEXPECTED_PERSON_PRESENCE",
        rule_name="UNEXPECTED_PERSON_PRESENCE",
        timestamp="2026-09-09T18:00:07.000Z",
        condition_satisfied=True,
        confidence=0.75,
        supporting_event_ids=["ev-test-001", "ev-test-002"],
    )
    risk_assessment = RiskAssessment(
        risk_id="risk-test-001",
        timestamp="2026-09-09T18:00:08.000Z",
        level=RiskLevel.LOW,
        score=0.32,
        reason="Satisfied conditions: UNEXPECTED_PERSON_PRESENCE.",
        uncertainty="low",
        human_verification_required=True,
        supporting_rule_ids=["UNEXPECTED_PERSON_PRESENCE"],
        supporting_event_ids=["ev-test-001", "ev-test-002"],
    )
    decision = DecisionContext(
        analysis_id="analysis-test-001",
        timestamp="2026-09-09T18:00:08.000Z",
        evidence={
            "event_ids": ["ev-test-001", "ev-test-002"],
            "active_persons_count": 1,
            "active_objects_count": 0,
            "context_window_seconds": 120,
        },
        llm_verification=llm_verif,
        rule_evaluations=[rule_eval],
        risk=risk_assessment,
    )
    storage.save_analysis(decision)

    # 3. Incident
    incident = Incident(
        incident_id="inc-test-001",
        created_at="2026-09-09T18:00:09.000Z",
        updated_at="2026-09-09T18:00:09.000Z",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="LOW",
        risk_score=0.32,
        uncertainty="low",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["ev-test-001", "ev-test-002"],
        analysis_id="analysis-test-001",
        risk_id="risk-test-001",
        triggering_rule_ids=["UNEXPECTED_PERSON_PRESENCE"],
        escalation_state=EscalationState.NOT_ESCALATED,
    )
    storage.save_incident(incident)

    # 4. Audit Log
    audit_logger.record(
        actor_id="system_core",
        action=AuditAction.INCIDENT_CREATED,
        target_type="incident",
        target_id="inc-test-001",
        new_state="ACTIVE",
        reason="Triggered by UNEXPECTED_PERSON_PRESENCE.",
    )

    return incident


def _login_actor(client, username, password):
    """Authenticate and return response session cookies."""
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.json["token"]


# ============================================================================
# Tests
# ============================================================================

def test_1_investigation_endpoint_exists(isolated_env):
    """1. Endpoint exists and returns 401 when called without credentials."""
    client = isolated_env["client"]
    resp = client.get("/api/incidents/inc-test-001/investigation")
    assert resp.status_code == 401
    assert "Authentication required" in resp.json["details"]


def test_2_known_incident_returns_investigation(isolated_env):
    """2. Known incident returns full investigation snapshot with 200 OK."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "operator", "operator123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    assert resp.status_code == 200
    data = resp.json
    assert data["status"] == "ok"
    assert "incident" in data
    assert "timeline" in data
    assert "supporting_events" in data
    assert "intelligence" in data
    assert "rules" in data
    assert "risk" in data
    assert "orchestrator" in data
    assert "audit_history" in data
    assert "evidence_summary" in data


def test_3_unknown_incident_returns_404(isolated_env):
    """3. Unknown incident returns 404 Not Found."""
    client = isolated_env["client"]
    _login_actor(client, "viewer", "viewer123")

    resp = client.get("/api/incidents/nonexistent-uuid/investigation")
    assert resp.status_code == 404
    assert resp.json["error"] == "Not Found"


def test_4_endpoint_is_strictly_read_only(isolated_env):
    """4. Repeated requests do not mutate incident state, audit logs, or storage."""
    client = isolated_env["client"]
    storage = isolated_env["storage"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "viewer", "viewer123")

    initial_audit_count = len(storage.get_audit_records_for_target(incident.incident_id))

    # Call endpoint 5 times
    for _ in range(5):
        resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
        assert resp.status_code == 200

    # Verify no state mutated
    rechecked = storage.get_incident(incident.incident_id)
    assert rechecked.status == IncidentStatus.ACTIVE
    assert rechecked.is_acknowledged is False
    assert len(storage.get_audit_records_for_target(incident.incident_id)) == initial_audit_count


def test_5_incident_fields_correlate_correctly(isolated_env):
    """5. Returned incident fields match real persisted values."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "operator", "operator123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    inc_data = resp.json["incident"]
    assert inc_data["incident_id"] == incident.incident_id
    assert inc_data["incident_type"] == "UNEXPECTED_PERSON_PRESENCE"
    assert inc_data["risk_score"] == 0.32
    assert inc_data["severity"] == "LOW"
    assert inc_data["uncertainty"] == "low"
    assert inc_data["status"] == "ACTIVE"


def test_6_and_7_supporting_events_are_real_persisted_events(isolated_env):
    """6 & 7. Supporting events correlate to real persisted events matching IDs."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "operator", "operator123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    events = resp.json["supporting_events"]
    assert len(events) == 2
    event_ids = [e["event_id"] for e in events]
    assert "ev-test-001" in event_ids
    assert "ev-test-002" in event_ids
    assert events[0]["event_type"] == "PERSON_ENTERED"
    assert events[1]["event_type"] == "PERSON_STATIONARY"


def test_8_and_9_analysis_and_llm_decision_returned_correctly(isolated_env):
    """8 & 9. Persisted analysis and LLM verification correlate correctly."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "operator", "operator123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    intel = resp.json["intelligence"]
    assert intel["status"] == "AVAILABLE"
    assert intel["model"] == "qwen2.5:3b"
    assert intel["situation"] == "Person standing motionless in monitored foyer"
    assert intel["confidence"] == 0.85
    assert intel["uncertainty"] == "low"
    assert intel["verification_required"] is True


def test_10_supporting_event_ids_in_llm_verification_preserved(isolated_env):
    """10. Event IDs grounding the LLM verification are preserved."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "operator", "operator123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    grounded = resp.json["intelligence"]["supporting_event_ids"]
    assert grounded == ["ev-test-001", "ev-test-002"]


def test_11_rule_results_correlate_correctly(isolated_env):
    """11. Rule results associated with analysis correlate correctly."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "operator", "operator123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    rules = resp.json["rules"]
    assert rules["status"] == "AVAILABLE"
    assert len(rules["evaluations"]) == 1
    rule = rules["evaluations"][0]
    assert rule["rule_name"] == "UNEXPECTED_PERSON_PRESENCE"
    assert rule["condition_satisfied"] is True
    assert rule["confidence"] == 0.75


def test_12_risk_assessment_correlates_correctly(isolated_env):
    """12. Deterministic risk assessment correlates correctly."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "operator", "operator123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    risk = resp.json["risk"]
    assert risk["status"] == "AVAILABLE"
    assert risk["score"] == 0.32
    assert risk["level"] == "LOW"
    assert risk["human_verification_required"] is True
    assert "UNEXPECTED_PERSON_PRESENCE" in risk["reason"]


def test_13_orchestrator_state_correlates_correctly(isolated_env):
    """13. Orchestrator boundary state correlates correctly."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "operator", "operator123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    orch = resp.json["orchestrator"]
    assert orch["status"] == "AVAILABLE"
    assert orch["decision"] == "AUTHORIZATION_REQUIRED"
    assert orch["requires_human_authorization"] is True
    assert orch["external_notifications"] == "0 / DISABLED"


def test_14_alert_correlation_works(isolated_env):
    """14. Alert correlation returns NOT_RECORDED when no alert exists, and AVAILABLE when present."""
    client = isolated_env["client"]
    storage = isolated_env["storage"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "operator", "operator123")

    # Before alert
    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    assert resp.json["alert"]["status"] == "NOT_RECORDED"

    # Add an alert
    from atlas.alerts.schema import Alert, AlertSeverity, AlertStatus
    alert = Alert(
        incident_id=incident.incident_id,
        severity=AlertSeverity.LOW,
        status=AlertStatus.ACTIVE,
    )
    storage.save_alert(alert)

    # After alert
    resp2 = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    assert resp2.json["alert"]["status"] == "AVAILABLE"
    assert resp2.json["alert"]["alert_id"] == alert.alert_id


def test_15_audit_history_correlates_correctly(isolated_env):
    """15. Audit history returns chronological immutable entries for incident."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    audit_logger = isolated_env["audit_logger"]

    audit_logger.record(
        actor_id="operator_01",
        action=AuditAction.INCIDENT_ACKNOWLEDGED,
        target_type="incident",
        target_id=incident.incident_id,
        previous_state="ACTIVE",
        new_state="ACKNOWLEDGED",
        reason="Manual operator review.",
    )

    _login_actor(client, "operator", "operator123")
    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    audit = resp.json["audit_history"]
    assert len(audit) == 2
    actions = [a["action"] for a in audit]
    assert "INCIDENT_CREATED" in actions
    assert "INCIDENT_ACKNOWLEDGED" in actions


def test_16_chronological_timeline_ordering(isolated_env):
    """16. Chronological timeline entries are strictly ascending by timestamp."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "operator", "operator123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    timeline = resp.json["timeline"]
    assert len(timeline) >= 4

    timestamps = [item["timestamp"] for item in timeline if item.get("timestamp")]
    assert timestamps == sorted(timestamps)


def test_17_missing_evidence_is_represented_explicitly(isolated_env):
    """17. If referenced event or analysis is missing, explicit status is returned."""
    client = isolated_env["client"]
    storage = isolated_env["storage"]

    # Create incident pointing to nonexistent analysis and missing event
    ghost_inc = Incident(
        incident_id="inc-ghost-001",
        incident_type=IncidentType.GENERAL_SAFETY_REVIEW,
        source_event_ids=["ev-nonexistent-999"],
        analysis_id="analysis-missing-888",
    )
    storage.save_incident(ghost_inc)

    _login_actor(client, "operator", "operator123")
    resp = client.get("/api/incidents/inc-ghost-001/investigation")
    data = resp.json
    assert data["status"] == "ok"
    assert "ev-nonexistent-999" in data["missing_event_ids"]
    assert data["intelligence"]["status"] == "MISSING"
    assert data["evidence_summary"]["overall_integrity"] == "PARTIAL"


def test_18_no_fabricated_camera_evidence(isolated_env):
    """18. Camera evidence status is explicitly NOT_RECORDED without mock footage."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "operator", "operator123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    cam = resp.json["camera_evidence"]
    assert cam["status"] == "NOT_RECORDED"
    assert "Camera evidence unavailable" in cam["message"]


def test_19_viewer_can_read_investigation_data(isolated_env):
    """19. Viewer role possesses VIEW_INCIDENTS and can read investigation snapshot."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "viewer", "viewer123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    assert resp.status_code == 200
    assert resp.json["incident"]["incident_id"] == incident.incident_id


def test_20_unauthorized_mutation_remains_impossible(isolated_env):
    """20. Viewer cannot perform consequential mutations from investigation context."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "viewer", "viewer123")

    # Viewer attempts acknowledge
    resp = client.post(f"/api/incidents/{incident.incident_id}/acknowledge")
    assert resp.status_code == 403

    # Viewer attempts resolve
    resp = client.post(f"/api/incidents/{incident.incident_id}/resolve", json={"reason": "SYSTEM_TEST"})
    assert resp.status_code == 403


def test_21_frontend_investigation_view_loads(isolated_env):
    """21. Frontend index.html contains #modal-investigation and investigation UI markup."""
    client = isolated_env["client"]
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="modal-investigation"' in html
    assert 'id="inv-timeline-container"' in html
    assert 'id="inv-events-container"' in html
    assert 'id="inv-why-raised"' in html


def test_22_frontend_does_not_calculate_risk():
    """22. Frontend static JS does not calculate risk or evaluate rules client-side."""
    app_js = Path("atlas/frontend/app.js").read_text(encoding="utf-8")
    assert "function assessRisk" not in app_js
    assert "calculateRisk" not in app_js
    assert "evaluateRule" not in app_js


def test_23_frontend_does_not_call_ollama():
    """23. Frontend static JS does not call Ollama directly."""
    app_js = Path("atlas/frontend/app.js").read_text(encoding="utf-8")
    assert "11434" not in app_js
    assert "api/generate" not in app_js
    assert "api/chat" not in app_js


def test_24_security_audit_no_secrets_exposed(isolated_env):
    """24. Investigation payload does not contain password, token, or secret leaks."""
    client = isolated_env["client"]
    incident = _seed_verified_incident(isolated_env)
    _login_actor(client, "admin", "admin123")

    resp = client.get(f"/api/incidents/{incident.incident_id}/investigation")
    payload_str = json.dumps(resp.json).lower()
    assert "password" not in payload_str
    assert "secret" not in payload_str
    assert "session_token" not in payload_str
    assert "api_key" not in payload_str
