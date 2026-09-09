"""Step 6.4: Comprehensive Evidence Vault and Event-Triggered Capture Tests.

Strictly follows:
- Zero-mock policy for production: tests run in isolated temporary environments.
- 30 required test cases for privacy-bounded, event-triggered evidence capture.
- Path traversal defenses and read-only endpoints.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import time
import numpy as np
import cv2
import pytest

from atlas.authority.auth import AuthService, get_auth_service
from atlas.authority.models import Action, Actor, Permission, Role
from atlas.authority.service import AuthorityService
from atlas.audit.service import AuditLogger
from atlas.audit.schema import AuditAction
from atlas.camera.base import BaseCameraStream, CameraState
from atlas.events.schema import ATLASEvent, EventSeverity, EventStatus, EventType
from atlas.events.storage import EventStorage
from atlas.evidence.policy import EvidencePolicy, get_default_evidence_policy
from atlas.evidence.schema import CaptureStatus, EvidenceRecord, IntegrityStatus, RetentionStatus
from atlas.evidence.vault import EvidenceVault, reset_global_vault
from atlas.incidents.investigation import EvidenceAvailability, InvestigationService
from atlas.incidents.schema import Incident, IncidentStatus, IncidentType
from atlas.incidents.service import IncidentManager
from atlas.intelligence.schemas import LLMVerificationResult
from atlas.intelligence.pipeline import IntelligencePipeline
from atlas.risk.schema import DecisionContext, RiskAssessment, RiskLevel
from atlas.rules.schema import RuleEvaluationResult


class DummyCamera:
    """Isolated test camera producing deterministic synthetic frames in memory."""
    def __init__(self, frame=None):
        self.source = "test_0"
        self._state = CameraState.CONNECTED
        if frame is None:
            # 100x100 blue frame
            self._frame = np.zeros((100, 100, 3), dtype=np.uint8)
            self._frame[:, :] = (255, 0, 0)
        else:
            self._frame = frame
        self._last_ts = time.time()

    def get_latest_frame(self):
        if self._frame is None:
            return None
        return self._frame.copy(), self._last_ts

    @property
    def is_connected(self) -> bool:
        return self._frame is not None

    @property
    def connection_state(self) -> CameraState:
        return self._state

    @property
    def resolution(self) -> tuple[int, int] | None:
        return (100, 100) if self._frame is not None else None

    @property
    def fps(self) -> float:
        return 30.0

    @property
    def last_frame_timestamp(self) -> float | None:
        return self._last_ts

    @property
    def error_message(self) -> str | None:
        return None

    def get_status(self) -> dict:
        return {
            "source": self.source,
            "is_connected": self.is_connected,
            "state": self._state.value,
            "fps": self.fps,
            "resolution": {"width": 100, "height": 100},
            "last_frame_timestamp": self._last_ts,
            "error_message": None,
        }


@pytest.fixture
def isolated_env(tmp_path):
    """Provide completely isolated storage, vault, and authority services."""
    reset_global_vault()
    db_path = tmp_path / "test_events.db"
    storage_dir = tmp_path / "evidence"
    storage_dir.mkdir(parents=True, exist_ok=True)

    storage = EventStorage(db_path=db_path)
    audit = AuditLogger(storage=storage)
    authority = AuthorityService()
    auth_svc = AuthService()
    camera = DummyCamera()

    policy = EvidencePolicy(
        storage_dir=storage_dir,
        max_frames_per_incident=1,
        jpeg_quality=80,
    )
    vault = EvidenceVault(
        storage_dir=storage_dir,
        policy=policy,
        storage=storage,
        camera=camera,
        audit=audit,
    )
    inc_mgr = IncidentManager(
        storage=storage,
        authority_service=authority,
        audit_logger=audit,
        evidence_vault=vault,
    )
    inv_svc = InvestigationService(storage=storage)

    import atlas.evidence.vault as av
    import atlas.events.storage as aes
    old_vault = av._global_vault
    old_storage = aes._global_storage
    av._global_vault = vault
    aes._global_storage = storage

    try:
        yield {
            "tmp_path": tmp_path,
            "db_path": db_path,
            "storage_dir": storage_dir,
            "storage": storage,
            "audit": audit,
            "authority": authority,
            "auth_svc": auth_svc,
            "camera": camera,
            "policy": policy,
            "vault": vault,
            "inc_mgr": inc_mgr,
            "inv_svc": inv_svc,
        }
    finally:
        av._global_vault = old_vault
        aes._global_storage = old_storage
        reset_global_vault()


def _make_decision_context(incident_type: str, score: float = 0.85):
    """Helper to construct a valid pipeline DecisionContext."""
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rule_name = f"rule_{incident_type.lower()}"
    return DecisionContext(
        analysis_id="test_analysis_01",
        timestamp=now_iso,
        evidence={"event_ids": ["evt_01"]},
        risk=RiskAssessment(
            risk_id="test_risk_01",
            timestamp=now_iso,
            level=RiskLevel.HIGH,
            score=score,
            reason=f"Detected {incident_type}",
            human_verification_required=True,
            uncertainty="low",
            contributing_factors=[incident_type],
        ),
        llm_verification=LLMVerificationResult(
            timestamp=now_iso,
            situation=f"Observed {incident_type}",
            interpretation="Elevated safety concern",
            confidence=0.85,
            uncertainty="low",
            verification_required=True,
            supporting_event_ids=["evt_01"],
            model_used="qwen2.5:3b",
        ),
        rule_evaluations=[
            RuleEvaluationResult(
                rule_id=rule_name,
                rule_name=rule_name,
                timestamp=now_iso,
                condition_satisfied=True,
                confidence=0.95,
                supporting_event_ids=["evt_01"],
                details={
                    "risk_level": "HIGH",
                    "risk_score": score,
                    "reason": f"Triggered {incident_type}",
                },
            )
        ],
    )


# ============================================================================
# 1. evidence policy loads correctly
# ============================================================================
def test_evidence_policy_loads_correctly(isolated_env):
    policy = isolated_env["policy"]
    assert policy.max_frames_per_incident == 1
    assert policy.jpeg_quality == 80
    assert "UNEXPECTED_PERSON_PRESENCE" in policy.qualifying_incident_types
    assert "POSSIBLE_FALL" in policy.qualifying_incident_types
    assert policy.is_qualifying("POSSIBLE_FALL") is True


# ============================================================================
# 2. qualifying incident triggers capture
# ============================================================================
def test_qualifying_incident_triggers_capture(isolated_env):
    inc_mgr = isolated_env["inc_mgr"]
    vault = isolated_env["vault"]

    decision = _make_decision_context("POSSIBLE_FALL", score=0.88)
    inc = inc_mgr.evaluate_decision_context(decision)
    assert inc is not None

    evidence = vault.get_evidence_for_incident(inc.incident_id)
    assert len(evidence) == 1
    assert evidence[0].capture_status == CaptureStatus.CAPTURED
    assert evidence[0].incident_id == inc.incident_id


# ============================================================================
# 3. non-qualifying event does not trigger capture
# ============================================================================
def test_non_qualifying_event_does_not_trigger_capture(isolated_env):
    vault = isolated_env["vault"]
    # GENERAL_SAFETY_REVIEW is not in default qualifying list
    inc = Incident(
        incident_type=IncidentType.GENERAL_SAFETY_REVIEW,
        severity="LOW",
        risk_score=0.35,
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_02"],
    )
    res = vault.capture_for_incident(inc)
    assert res is None
    evidence = vault.get_evidence_for_incident(inc.incident_id)
    assert len(evidence) == 0


# ============================================================================
# 4. evidence receives stable ID
# ============================================================================
def test_evidence_receives_stable_id(isolated_env):
    vault = isolated_env["vault"]
    inc = Incident(
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="HIGH",
        risk_score=0.82,
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_03"],
    )
    rec = vault.capture_for_incident(inc)
    assert rec is not None
    assert rec.evidence_id.startswith("ev_")
    # Query back
    stored = vault.get_evidence_record(rec.evidence_id)
    assert stored is not None
    assert stored.evidence_id == rec.evidence_id


# ============================================================================
# 5. evidence links to correct incident
# ============================================================================
def test_evidence_links_to_correct_incident(isolated_env):
    vault = isolated_env["vault"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        risk_score=0.90,
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_fall_01"],
    )
    rec = vault.capture_for_incident(inc)
    assert rec.incident_id == inc.incident_id


# ============================================================================
# 6. evidence links to correct source event
# ============================================================================
def test_evidence_links_to_correct_source_event(isolated_env):
    vault = isolated_env["vault"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL,
        severity="HIGH",
        risk_score=0.75,
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_source_42"],
    )
    rec = vault.capture_for_incident(inc, source_event_id="evt_source_42")
    assert rec.source_event_id == "evt_source_42"


# ============================================================================
# 7. artifact is persisted
# ============================================================================
def test_artifact_is_persisted(isolated_env):
    vault = isolated_env["vault"]
    storage_dir = isolated_env["storage_dir"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        risk_score=0.88,
        status=IncidentStatus.ACTIVE,
    )
    rec = vault.capture_for_incident(inc)
    file_on_disk = storage_dir / rec.file_path
    assert file_on_disk.exists()
    assert file_on_disk.stat().st_size > 0
    assert rec.file_size == file_on_disk.stat().st_size


# ============================================================================
# 8. SHA-256 is persisted
# ============================================================================
def test_sha256_is_persisted(isolated_env):
    vault = isolated_env["vault"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        risk_score=0.88,
        status=IncidentStatus.ACTIVE,
    )
    rec = vault.capture_for_incident(inc)
    assert len(rec.sha256) == 64
    # Re-read from SQLite
    stored = vault.get_evidence_record(rec.evidence_id)
    assert stored.sha256 == rec.sha256


# ============================================================================
# 9. integrity verification succeeds
# ============================================================================
def test_integrity_verification_succeeds(isolated_env):
    vault = isolated_env["vault"]
    inc = Incident(
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="HIGH",
        risk_score=0.88,
        status=IncidentStatus.ACTIVE,
    )
    rec = vault.capture_for_incident(inc)
    status, err = vault.verify_integrity(rec.evidence_id)
    assert status == IntegrityStatus.INTEGRITY_VERIFIED
    assert err is None


# ============================================================================
# 10. integrity failure is detected
# ============================================================================
def test_integrity_failure_is_detected(isolated_env):
    vault = isolated_env["vault"]
    storage_dir = isolated_env["storage_dir"]
    inc = Incident(
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="HIGH",
        risk_score=0.88,
        status=IncidentStatus.ACTIVE,
    )
    rec = vault.capture_for_incident(inc)
    # Tamper with file
    file_path = storage_dir / rec.file_path
    file_path.write_bytes(b"tampered_corrupt_content")

    status, err = vault.verify_integrity(rec.evidence_id)
    assert status == IntegrityStatus.INTEGRITY_FAILED
    assert "SHA-256 mismatch" in err


# ============================================================================
# 11. duplicate capture is prevented
# ============================================================================
def test_duplicate_capture_is_prevented(isolated_env):
    vault = isolated_env["vault"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        risk_score=0.90,
        status=IncidentStatus.ACTIVE,
    )
    rec1 = vault.capture_for_incident(inc)
    rec2 = vault.capture_for_incident(inc)
    assert rec1.evidence_id == rec2.evidence_id
    records = vault.get_evidence_for_incident(inc.incident_id)
    assert len(records) == 1


# ============================================================================
# 12. capture failure does not fail incident creation
# ============================================================================
def test_capture_failure_does_not_fail_incident_creation(isolated_env):
    # Set camera frame to None to simulate failure
    isolated_env["camera"]._frame = None
    inc_mgr = isolated_env["inc_mgr"]
    vault = isolated_env["vault"]

    decision = _make_decision_context("POSSIBLE_FALL", score=0.92)
    # Incident creation must NOT raise exception
    inc = inc_mgr.evaluate_decision_context(decision)
    assert inc is not None
    assert inc.status == IncidentStatus.ACTIVE

    # Check evidence record shows FAILED
    evs = vault.get_evidence_for_incident(inc.incident_id)
    assert len(evs) == 1
    assert evs[0].capture_status == CaptureStatus.FAILED
    assert evs[0].failure_reason == "CAMERA_FRAME_UNAVAILABLE"


# ============================================================================
# 13. missing artifact returns correct state
# ============================================================================
def test_missing_artifact_returns_correct_state(isolated_env):
    vault = isolated_env["vault"]
    status, err = vault.verify_integrity("ev_nonexistent_99")
    assert status == IntegrityStatus.ARTIFACT_MISSING


# ============================================================================
# 14. investigation API exposes AVAILABLE evidence
# ============================================================================
def test_investigation_api_exposes_available_evidence(isolated_env):
    inc_mgr = isolated_env["inc_mgr"]
    inv_svc = isolated_env["inv_svc"]

    decision = _make_decision_context("POSSIBLE_FALL", score=0.88)
    inc = inc_mgr.evaluate_decision_context(decision)

    snapshot = inv_svc.build_investigation(inc.incident_id)
    assert snapshot is not None
    cam_ev = snapshot["camera_evidence"]
    assert cam_ev["status"] == "AVAILABLE"
    assert len(cam_ev["artifacts"]) == 1
    art = cam_ev["artifacts"][0]
    assert art["integrity_status"] == "INTEGRITY_VERIFIED"
    assert art["incident_id"] == inc.incident_id


# ============================================================================
# 15. investigation API exposes CAPTURE_FAILED state
# ============================================================================
def test_investigation_api_exposes_capture_failed_state(isolated_env):
    isolated_env["camera"]._frame = None
    inc_mgr = isolated_env["inc_mgr"]
    inv_svc = isolated_env["inv_svc"]

    decision = _make_decision_context("POSSIBLE_FALL", score=0.88)
    inc = inc_mgr.evaluate_decision_context(decision)

    snapshot = inv_svc.build_investigation(inc.incident_id)
    cam_ev = snapshot["camera_evidence"]
    assert cam_ev["status"] == "CAPTURE_FAILED"
    assert cam_ev["artifacts"] == []
    assert "CAMERA_FRAME_UNAVAILABLE" in cam_ev["reason"]


# ============================================================================
# 16. investigation API preserves NOT_RECORDED state when appropriate
# ============================================================================
def test_investigation_api_preserves_not_recorded_state(isolated_env):
    storage = isolated_env["storage"]
    inv_svc = isolated_env["inv_svc"]

    # Manually save an incident without triggering evidence vault
    inc = Incident(
        incident_type=IncidentType.GENERAL_SAFETY_REVIEW,
        severity="LOW",
        risk_score=0.20,
        status=IncidentStatus.ACTIVE,
    )
    storage.save_incident(inc)

    snapshot = inv_svc.build_investigation(inc.incident_id)
    cam_ev = snapshot["camera_evidence"]
    assert cam_ev["status"] == "NOT_RECORDED"
    assert cam_ev["artifacts"] == []


# ============================================================================
# 17. evidence endpoint requires authentication
# ============================================================================
def test_evidence_endpoint_requires_authentication(isolated_env):
    from atlas.api.app import create_app
    app = create_app()
    client = app.test_client()

    resp = client.get("/api/evidence/ev_test_123")
    assert resp.status_code == 401


# ============================================================================
# 18. viewer access follows existing permission model
# ============================================================================
def test_viewer_access_follows_existing_permission_model(isolated_env):
    from atlas.api.app import create_app
    from atlas.authority.auth import get_auth_service
    app = create_app()
    client = app.test_client()

    # Create evidence in global vault for this test
    vault = isolated_env["vault"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        risk_score=0.85,
        status=IncidentStatus.ACTIVE,
    )
    rec = vault.capture_for_incident(inc)

    # Login viewer
    auth_svc = get_auth_service()
    login_res = auth_svc.authenticate("viewer", "viewer123")
    assert login_res is not None
    token, actor = login_res

    # Provide token
    resp = client.get(
        f"/api/evidence/{rec.evidence_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert len(resp.data) > 0


# ============================================================================
# 19. operator access works
# ============================================================================
def test_operator_access_works(isolated_env):
    from atlas.api.app import create_app
    from atlas.authority.auth import get_auth_service
    app = create_app()
    client = app.test_client()

    vault = isolated_env["vault"]
    inc = Incident(
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="HIGH",
        risk_score=0.85,
        status=IncidentStatus.ACTIVE,
    )
    rec = vault.capture_for_incident(inc)

    auth_svc = get_auth_service()
    token, _ = auth_svc.authenticate("operator", "operator123")
    resp = client.get(
        f"/api/evidence/{rec.evidence_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.headers["X-Evidence-Integrity"] == "INTEGRITY_VERIFIED"


# ============================================================================
# 20. admin access works
# ============================================================================
def test_admin_access_works(isolated_env):
    from atlas.api.app import create_app
    from atlas.authority.auth import get_auth_service
    app = create_app()
    client = app.test_client()

    vault = isolated_env["vault"]
    inc = Incident(
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="HIGH",
        risk_score=0.85,
        status=IncidentStatus.ACTIVE,
    )
    rec = vault.capture_for_incident(inc)

    auth_svc = get_auth_service()
    token, _ = auth_svc.authenticate("admin", "admin123")
    resp = client.get(
        f"/api/evidence/{rec.evidence_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200


# ============================================================================
# 21. arbitrary filesystem access is impossible
# ============================================================================
def test_arbitrary_filesystem_access_is_impossible(isolated_env):
    from atlas.api.app import create_app
    from atlas.authority.auth import get_auth_service
    app = create_app()
    client = app.test_client()

    auth_svc = get_auth_service()
    token, _ = auth_svc.authenticate("admin", "admin123")

    # Try requesting arbitrary files
    resp = client.get(
        "/api/evidence/app.py",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code in (400, 404)


# ============================================================================
# 22. path traversal is rejected
# ============================================================================
def test_path_traversal_is_rejected(isolated_env):
    from atlas.api.app import create_app
    from atlas.authority.auth import get_auth_service
    app = create_app()
    client = app.test_client()

    auth_svc = get_auth_service()
    token, _ = auth_svc.authenticate("admin", "admin123")

    # URL path traversal attempts
    resp1 = client.get(
        "/api/evidence/..%2F..%2F..%2Fwindows%2Fwin.ini",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp1.status_code in (400, 404)

    resp2 = client.get(
        "/api/evidence/..%5C..%5Cdata%5Catlas_events.db",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp2.status_code in (400, 404)


# ============================================================================
# 23. evidence endpoint is read-only
# ============================================================================
def test_evidence_endpoint_is_read_only(isolated_env):
    from atlas.api.app import create_app
    from atlas.authority.auth import get_auth_service
    app = create_app()
    client = app.test_client()

    auth_svc = get_auth_service()
    token, _ = auth_svc.authenticate("admin", "admin123")

    resp_post = client.post(
        "/api/evidence/ev_test_123",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp_post.status_code == 405

    resp_delete = client.delete(
        "/api/evidence/ev_test_123",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp_delete.status_code == 405


# ============================================================================
# 24. audit record is created for capture
# ============================================================================
def test_audit_record_created_for_capture(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        risk_score=0.90,
        status=IncidentStatus.ACTIVE,
    )
    rec = vault.capture_for_incident(inc)
    assert rec is not None

    audits = storage.get_audit_records_for_target(inc.incident_id)
    actions = [a.action for a in audits]
    assert "EVIDENCE_CAPTURED" in actions or AuditAction.EVIDENCE_CAPTURED in actions


# ============================================================================
# 25. test evidence never enters production evidence directory
# ============================================================================
def test_evidence_never_enters_production_evidence_directory(isolated_env):
    project_root = Path(__file__).resolve().parent.parent
    prod_evidence_dir = project_root / "data" / "evidence"

    # Capture in isolated fixture
    vault = isolated_env["vault"]
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        risk_score=0.90,
        status=IncidentStatus.ACTIVE,
    )
    rec = vault.capture_for_incident(inc)

    # Confirm record file is in isolated tmp storage, NOT in prod dir
    test_file = isolated_env["storage_dir"] / rec.file_path
    assert test_file.exists()
    if prod_evidence_dir.exists():
        assert not (prod_evidence_dir / rec.file_path).exists()


# ============================================================================
# 26. frontend renders actual evidence reference
# ============================================================================
def test_frontend_renders_actual_evidence_reference():
    js_path = Path(__file__).resolve().parent.parent / "atlas" / "frontend" / "app.js"
    content = js_path.read_text(encoding="utf-8")
    assert "/api/evidence/" in content
    assert "invEvidenceImg.src" in content


# ============================================================================
# 27. frontend never accesses filesystem directly
# ============================================================================
def test_frontend_never_accesses_filesystem_directly():
    js_path = Path(__file__).resolve().parent.parent / "atlas" / "frontend" / "app.js"
    content = js_path.read_text(encoding="utf-8")
    assert "fs." not in content
    assert "require('fs')" not in content
    assert "C:\\" not in content


# ============================================================================
# 28. frontend does not calculate integrity
# ============================================================================
def test_frontend_does_not_calculate_integrity():
    js_path = Path(__file__).resolve().parent.parent / "atlas" / "frontend" / "app.js"
    content = js_path.read_text(encoding="utf-8")
    # Frontend must only read integrity_status from backend
    assert "crypto.subtle" not in content
    assert "createHash" not in content
    assert "sha256(" not in content


# ============================================================================
# 29. no continuous recording is active
# ============================================================================
def test_no_continuous_recording_is_active():
    stream_path = Path(__file__).resolve().parent.parent / "atlas" / "camera" / "stream.py"
    content = stream_path.read_text(encoding="utf-8")
    # Must NOT have VideoWriter continuous loops
    assert "cv2.VideoWriter" not in content


# ============================================================================
# 30. no mock operational evidence exists in production paths
# ============================================================================
def test_no_mock_operational_evidence_exists_in_production_paths():
    prod_evidence_dir = Path(__file__).resolve().parent.parent / "data" / "evidence"
    if prod_evidence_dir.exists():
        for f in prod_evidence_dir.glob("*"):
            assert not f.name.startswith("mock_")
            assert not f.name.startswith("fake_")
            assert not f.name.startswith("synthetic_")
