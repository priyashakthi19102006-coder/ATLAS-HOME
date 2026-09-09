"""Step 6.4.1: Evidence Temporal Grounding & Provenance Hardening Test Suite.

Verifies:
1. TemporalRelation enum and values.
2. EvidenceRecord schema with source_event_timestamp, source_frame_timestamp, and temporal_relation.
3. SQLite storage persistence and query of temporal fields.
4. Schema optionality and default behavior.
5. Migration safety when opening existing databases with Step 6.4 schema lacking Step 6.4.1 columns.
6. Causal accuracy: get_latest_frame() after event qualifies MUST be marked CAPTURED_AFTER_EVENT.
7. Exact source frame claim: is_exact_source_frame=True yields EXACT_SOURCE_FRAME.
8. Missing source event timestamp handling -> marks TIMESTAMP_UNAVAILABLE.
9. Camera capture failure handling -> sets TIMESTAMP_UNAVAILABLE and preserves event provenance.
10. Event evidence frame_timestamp extraction and ISO formatting.
11. Event without frame_timestamp sets source_frame_timestamp="UNAVAILABLE".
12. Temporal ordering verification (captured_at >= source_event_timestamp).
13. Negative delta / clock anomaly detection -> marks TIMESTAMP_UNAVAILABLE.
14. Audit trail logging for EVIDENCE_CAPTURED contains temporal provenance metadata.
15. Audit trail failure logging contains temporal provenance metadata.
16. Investigation API top-level camera_evidence exposes temporal provenance.
17. Investigation API artifacts array exposes temporal provenance per artifact.
18. Chronological timeline entry incorporates temporal relation.
19. Investigation API failure case surfaces temporal provenance metadata.
20. Frontend DOM elements exist in index.html for temporal provenance.
21. Test directory isolation: zero test artifacts written to production data/ directory.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import time
from typing import Any
import numpy as np
import pytest

from atlas.audit.schema import AuditAction
from atlas.audit.service import AuditLogger
from atlas.camera.base import CameraState
from atlas.events.schema import ATLASEvent, EvidencePayload
from atlas.events.storage import EventStorage
import atlas.events.storage as aes
from atlas.evidence.policy import EvidencePolicy
from atlas.evidence.schema import CaptureStatus, EvidenceRecord, IntegrityStatus, RetentionStatus, TemporalRelation
from atlas.evidence.vault import EvidenceVault, reset_global_vault
import atlas.evidence.vault as av
from atlas.incidents.investigation import EvidenceAvailability, InvestigationService
from atlas.incidents.schema import Incident, IncidentStatus, IncidentType
from atlas.incidents.service import IncidentManager


class DummyCamera:
    """Isolated test camera producing synthetic frames with customizable timestamp."""
    def __init__(self, frame: np.ndarray | None = None, ts: float | None = None):
        self.source = "test_device_0"
        self._state = CameraState.CONNECTED
        if frame is None:
            self._frame = np.zeros((100, 100, 3), dtype=np.uint8)
            self._frame[:, :] = (0, 255, 0)
        else:
            self._frame = frame
        self._last_ts = ts if ts is not None else time.time()

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

    def fps(self) -> float:
        return 30.0


@pytest.fixture
def isolated_env(tmp_path):
    """Provide completely isolated temporary test environment."""
    reset_global_vault()
    db_path = tmp_path / "test_atlas_events.db"
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    storage = EventStorage(db_path=db_path)
    audit = AuditLogger(storage=storage)
    camera = DummyCamera()
    policy = EvidencePolicy(
        storage_dir=evidence_dir,
        max_frames_per_incident=1,
        max_artifact_size_bytes=1024 * 1024,
        jpeg_quality=80,
        qualifying_incident_types=[
            "UNEXPECTED_PERSON_PRESENCE",
            "POSSIBLE_FALL",
            "SECURITY_BREACH",
        ],
    )
    vault = EvidenceVault(
        storage_dir=evidence_dir,
        policy=policy,
        storage=storage,
        camera=camera,
        audit=audit,
    )
    inv_service = InvestigationService(storage=storage)

    old_vault = av._global_vault
    old_storage = aes._global_storage
    av._global_vault = vault
    aes._global_storage = storage

    try:
        yield {
            "tmp_path": tmp_path,
            "db_path": db_path,
            "evidence_dir": evidence_dir,
            "storage": storage,
            "audit": audit,
            "policy": policy,
            "cam": camera,
            "vault": vault,
            "inv_service": inv_service,
        }
    finally:
        av._global_vault = old_vault
        aes._global_storage = old_storage
        reset_global_vault()


# Test 1: TemporalRelation enum values
def test_temporal_relation_enum_values():
    assert TemporalRelation.EXACT_SOURCE_FRAME.value == "EXACT_SOURCE_FRAME"
    assert TemporalRelation.NEAREST_AVAILABLE_FRAME.value == "NEAREST_AVAILABLE_FRAME"
    assert TemporalRelation.CAPTURED_AFTER_EVENT.value == "CAPTURED_AFTER_EVENT"
    assert TemporalRelation.TIMESTAMP_UNAVAILABLE.value == "TIMESTAMP_UNAVAILABLE"


# Test 2: EvidenceRecord schema serialization with temporal fields
def test_evidence_record_schema_serialization():
    now_iso = datetime.now(timezone.utc).isoformat()
    rec = EvidenceRecord(
        evidence_id="ev_test_123",
        incident_id="inc_test_123",
        source_event_id="evt_test_123",
        source_event_timestamp=now_iso,
        source_frame_timestamp=now_iso,
        temporal_relation=TemporalRelation.CAPTURED_AFTER_EVENT,
        captured_at=now_iso,
        artifact_type="IMAGE_FRAME",
        mime_type="image/jpeg",
        file_path="ev_test_123.jpg",
        file_size=1024,
        sha256="abcdef123456",
        width=640,
        height=480,
        capture_status=CaptureStatus.CAPTURED,
        retention_status=RetentionStatus.ACTIVE,
    )
    d = rec.model_dump()
    assert d["source_event_timestamp"] == now_iso
    assert d["source_frame_timestamp"] == now_iso
    assert d["temporal_relation"] == TemporalRelation.CAPTURED_AFTER_EVENT
    assert d["evidence_id"] == "ev_test_123"


# Test 3: SQLite storage persistence and query of temporal fields
def test_sqlite_storage_persistence_and_query(isolated_env):
    storage = isolated_env["storage"]
    now_iso = datetime.now(timezone.utc).isoformat()

    rec = EvidenceRecord(
        evidence_id="ev_store_001",
        incident_id="inc_store_001",
        source_event_id="evt_store_001",
        source_event_timestamp="2026-09-09T18:00:00+00:00",
        source_frame_timestamp="2026-09-09T17:59:59.980000+00:00",
        temporal_relation=TemporalRelation.CAPTURED_AFTER_EVENT,
        captured_at=now_iso,
        artifact_type="IMAGE_FRAME",
        mime_type="image/jpeg",
        file_path="ev_store_001.jpg",
        file_size=2048,
        sha256="deadbeefcafe",
        width=1280,
        height=720,
        capture_status=CaptureStatus.CAPTURED,
        retention_status=RetentionStatus.ACTIVE,
    )
    storage.save_evidence_record(rec)

    fetched = storage.get_evidence_record("ev_store_001")
    assert fetched is not None
    assert fetched.evidence_id == "ev_store_001"
    assert fetched.source_event_timestamp == "2026-09-09T18:00:00+00:00"
    assert fetched.source_frame_timestamp == "2026-09-09T17:59:59.980000+00:00"
    assert fetched.temporal_relation == TemporalRelation.CAPTURED_AFTER_EVENT


# Test 4: Schema default values and optional handling
def test_schema_defaults_and_optional_fields():
    rec = EvidenceRecord(
        evidence_id="ev_defaults",
        incident_id="inc_defaults",
        captured_at=datetime.now(timezone.utc).isoformat(),
        file_path="",
        file_size=0,
        sha256="",
    )
    assert rec.source_event_timestamp is None
    assert rec.source_frame_timestamp is None
    assert rec.temporal_relation == TemporalRelation.CAPTURED_AFTER_EVENT


# Test 5: Migration safety when opening older DB
def test_migration_safety_with_legacy_db(tmp_path):
    legacy_db_path = tmp_path / "legacy_atlas.db"
    conn = sqlite3.connect(str(legacy_db_path))
    # Create Step 6.4 table without Step 6.4.1 columns
    conn.execute("""
        CREATE TABLE evidence_records (
            evidence_id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL,
            source_event_id TEXT,
            captured_at TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            capture_status TEXT NOT NULL,
            retention_status TEXT NOT NULL,
            failure_reason TEXT,
            metadata_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    # Insert a legacy record
    conn.execute("""
        INSERT INTO evidence_records (
            evidence_id, incident_id, captured_at, artifact_type, mime_type,
            file_path, file_size, sha256, capture_status, retention_status, metadata_json, created_at
        ) VALUES ('ev_legacy_1', 'inc_legacy_1', '2026-09-09T00:00:00Z', 'IMAGE_FRAME', 'image/jpeg', 'legacy.jpg', 10, 'hash', 'CAPTURED', 'ACTIVE', '{}', 1757430000.0)
    """)
    conn.commit()
    conn.close()

    # Initialize EventStorage which runs automatic migrations
    storage = EventStorage(db_path=legacy_db_path)
    legacy_rec = storage.get_evidence_record("ev_legacy_1")
    assert legacy_rec is not None
    assert legacy_rec.evidence_id == "ev_legacy_1"
    assert legacy_rec.source_event_timestamp is None
    assert legacy_rec.source_frame_timestamp is None
    assert legacy_rec.temporal_relation == TemporalRelation.CAPTURED_AFTER_EVENT


# Test 6: get_latest_frame() after event MUST be marked CAPTURED_AFTER_EVENT
def test_capture_after_event_temporal_relation(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]
    cam = isolated_env["cam"]

    event_epoch = time.time()
    event_iso = datetime.fromtimestamp(event_epoch, timezone.utc).isoformat()
    evt = ATLASEvent(
        event_id="evt_person_1",
        event_type="person_detected",
        timestamp=event_iso,
        evidence={"frame_timestamp": event_epoch},
    )
    storage.save_event(evt)

    # Frame is captured 100ms after event
    cam._last_ts = event_epoch + 0.1

    inc = Incident(
        incident_id="inc_test_after",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_person_1"],
    )

    rec = vault.capture_for_incident(inc)
    assert rec is not None
    assert rec.capture_status == CaptureStatus.CAPTURED
    # Crucial assertion: Must NOT claim exact source frame!
    assert rec.temporal_relation == TemporalRelation.CAPTURED_AFTER_EVENT
    assert rec.source_event_timestamp == event_iso
    assert rec.source_frame_timestamp == event_iso


# Test 7: is_exact_source_frame=True yields EXACT_SOURCE_FRAME
def test_exact_source_frame_when_explicitly_proven(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]

    event_epoch = time.time()
    event_iso = datetime.fromtimestamp(event_epoch, timezone.utc).isoformat()
    evt = ATLASEvent(
        event_id="evt_person_exact",
        event_type="person_detected",
        timestamp=event_iso,
        evidence={"frame_timestamp": event_epoch},
    )
    storage.save_event(evt)

    inc = Incident(
        incident_id="inc_test_exact",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_person_exact"],
    )

    rec = vault.capture_for_incident(inc, is_exact_source_frame=True)
    assert rec is not None
    assert rec.capture_status == CaptureStatus.CAPTURED
    assert rec.temporal_relation == TemporalRelation.EXACT_SOURCE_FRAME


# Test 8: Missing source event timestamp -> marks TIMESTAMP_UNAVAILABLE
def test_missing_source_event_marks_unavailable(isolated_env):
    vault = isolated_env["vault"]

    inc = Incident(
        incident_id="inc_missing_evt",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="LOW",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["nonexistent_event_id"],
    )

    rec = vault.capture_for_incident(inc)
    assert rec is not None
    assert rec.capture_status == CaptureStatus.CAPTURED
    assert rec.source_event_timestamp == "UNAVAILABLE"
    assert rec.source_frame_timestamp == "UNAVAILABLE"
    assert rec.temporal_relation == TemporalRelation.TIMESTAMP_UNAVAILABLE


# Test 9: Camera frame unavailable (capture failure) sets failure and preserves event provenance
def test_camera_failure_preserves_provenance(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]
    cam = isolated_env["cam"]
    cam._frame = None  # Simulate camera stream disconnection

    event_epoch = time.time()
    event_iso = datetime.fromtimestamp(event_epoch, timezone.utc).isoformat()
    evt = ATLASEvent(
        event_id="evt_with_cam_fail",
        event_type="person_detected",
        timestamp=event_iso,
        evidence={"frame_timestamp": event_epoch},
    )
    storage.save_event(evt)

    inc = Incident(
        incident_id="inc_cam_fail",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="LOW",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_with_cam_fail"],
    )

    rec = vault.capture_for_incident(inc)
    assert rec is not None
    assert rec.capture_status == CaptureStatus.FAILED
    assert rec.failure_reason == "CAMERA_FRAME_UNAVAILABLE"
    assert rec.source_event_timestamp == event_iso
    assert rec.source_frame_timestamp == event_iso
    assert rec.temporal_relation == TemporalRelation.TIMESTAMP_UNAVAILABLE


# Test 10: Event evidence frame_timestamp extraction and ISO formatting
def test_event_evidence_frame_timestamp_extraction(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]

    epoch_ts = 1757430000.5
    expected_iso = datetime.fromtimestamp(epoch_ts, timezone.utc).isoformat()

    evt = ATLASEvent(
        event_id="evt_epoch_calc",
        event_type="fall_detected",
        timestamp=expected_iso,
        evidence={"frame_timestamp": epoch_ts},
    )
    storage.save_event(evt)

    inc = Incident(
        incident_id="inc_epoch_calc",
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_epoch_calc"],
    )

    rec = vault.capture_for_incident(inc)
    assert rec is not None
    assert rec.source_frame_timestamp == expected_iso


# Test 11: Event without frame_timestamp sets source_frame_timestamp="UNAVAILABLE"
def test_event_without_frame_timestamp(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]

    event_iso = datetime.now(timezone.utc).isoformat()
    evt = ATLASEvent(
        event_id="evt_no_frame_ts",
        event_type="person_detected",
        timestamp=event_iso,
        evidence={},
    )
    storage.save_event(evt)

    inc = Incident(
        incident_id="inc_no_frame_ts",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_no_frame_ts"],
    )

    rec = vault.capture_for_incident(inc)
    assert rec is not None
    assert rec.source_frame_timestamp == "UNAVAILABLE"
    assert rec.source_event_timestamp == event_iso
    assert rec.temporal_relation == TemporalRelation.CAPTURED_AFTER_EVENT


# Test 12: Temporal ordering verification (captured_at >= source_event_timestamp)
def test_temporal_ordering_verification(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]
    cam = isolated_env["cam"]

    t0 = time.time()
    evt_ts = datetime.fromtimestamp(t0, timezone.utc).isoformat()
    evt = ATLASEvent(
        event_id="evt_order_1",
        event_type="person_detected",
        timestamp=evt_ts,
    )
    storage.save_event(evt)

    # Simulate camera frame arriving 50ms later
    cam._last_ts = t0 + 0.05

    inc = Incident(
        incident_id="inc_order_1",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_order_1"],
    )

    rec = vault.capture_for_incident(inc)
    assert rec is not None

    dt_event = datetime.fromisoformat(rec.source_event_timestamp)
    dt_captured = datetime.fromisoformat(rec.captured_at)
    assert dt_captured >= dt_event


# Test 13: Negative delta / clock anomaly detection marks TIMESTAMP_UNAVAILABLE
def test_clock_anomaly_negative_delta_marked_unavailable(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]
    cam = isolated_env["cam"]

    # Source event recorded at current time
    t0 = time.time()
    evt_ts = datetime.fromtimestamp(t0, timezone.utc).isoformat()
    evt = ATLASEvent(
        event_id="evt_anomaly",
        event_type="person_detected",
        timestamp=evt_ts,
    )
    storage.save_event(evt)

    # Frame has timestamp 10 seconds in the past
    cam._last_ts = t0 - 10.0

    inc = Incident(
        incident_id="inc_anomaly",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_anomaly"],
    )

    rec = vault.capture_for_incident(inc)
    assert rec is not None
    assert rec.temporal_relation == TemporalRelation.TIMESTAMP_UNAVAILABLE


# Test 14: Audit trail logging for EVIDENCE_CAPTURED contains temporal provenance
def test_audit_trail_contains_temporal_provenance(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]

    now = time.time()
    evt_iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
    evt = ATLASEvent(
        event_id="evt_audit_prov",
        event_type="person_detected",
        timestamp=evt_iso,
    )
    storage.save_event(evt)

    inc = Incident(
        incident_id="inc_audit_prov",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_audit_prov"],
    )

    vault.capture_for_incident(inc)

    audits = storage.get_audit_records_for_target("inc_audit_prov")
    cap_audits = [a for a in audits if a.action == AuditAction.EVIDENCE_CAPTURED]
    assert len(cap_audits) == 1
    meta = cap_audits[0].metadata
    assert "temporal_relation" in meta
    assert meta["temporal_relation"] == "CAPTURED_AFTER_EVENT"
    assert meta["source_event_timestamp"] == evt_iso


# Test 15: Audit trail failure logging contains temporal provenance
def test_audit_trail_failure_logging_contains_provenance(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]
    cam = isolated_env["cam"]
    cam._frame = None

    evt_iso = datetime.now(timezone.utc).isoformat()
    evt = ATLASEvent(
        event_id="evt_audit_fail",
        event_type="person_detected",
        timestamp=evt_iso,
    )
    storage.save_event(evt)

    inc = Incident(
        incident_id="inc_audit_fail",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_audit_fail"],
    )

    vault.capture_for_incident(inc)

    audits = storage.get_audit_records_for_target("inc_audit_fail")
    fail_audits = [a for a in audits if a.action == AuditAction.EVIDENCE_CAPTURE_FAILED]
    assert len(fail_audits) == 1
    meta = fail_audits[0].metadata
    assert "temporal_relation" in meta
    assert meta["temporal_relation"] == "TIMESTAMP_UNAVAILABLE"
    assert meta["source_event_timestamp"] == evt_iso


# Test 16: Investigation API top-level camera_evidence exposes temporal provenance
def test_investigation_api_top_level_camera_evidence(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]
    inv_service = isolated_env["inv_service"]

    now = time.time()
    evt_iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
    evt = ATLASEvent(
        event_id="evt_inv_top",
        event_type="person_detected",
        timestamp=evt_iso,
    )
    storage.save_event(evt)

    inc = Incident(
        incident_id="inc_inv_top",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_inv_top"],
    )
    storage.save_incident(inc)

    vault.capture_for_incident(inc)

    inv_data = inv_service.build_investigation("inc_inv_top")
    assert inv_data is not None

    cam_ev = inv_data["camera_evidence"]
    assert cam_ev["status"] == "AVAILABLE"
    assert cam_ev["temporal_relation"] == "CAPTURED_AFTER_EVENT"
    assert cam_ev["source_event_id"] == "evt_inv_top"
    assert cam_ev["source_event_timestamp"] == evt_iso
    assert cam_ev["captured_at"] is not None


# Test 17: Investigation API artifacts array exposes temporal provenance
def test_investigation_api_artifacts_array_provenance(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]
    inv_service = isolated_env["inv_service"]

    now = time.time()
    evt_iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
    evt = ATLASEvent(
        event_id="evt_inv_art",
        event_type="person_detected",
        timestamp=evt_iso,
    )
    storage.save_event(evt)

    inc = Incident(
        incident_id="inc_inv_art",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_inv_art"],
    )
    storage.save_incident(inc)
    vault.capture_for_incident(inc)

    inv_data = inv_service.build_investigation("inc_inv_art")
    assert inv_data is not None

    artifacts = inv_data["camera_evidence"]["artifacts"]
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art["temporal_relation"] == "CAPTURED_AFTER_EVENT"
    assert art["source_event_timestamp"] == evt_iso
    assert "source_frame_timestamp" in art


# Test 18: Chronological timeline entry incorporates temporal relation
def test_timeline_incorporates_temporal_relation(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]
    inv_service = isolated_env["inv_service"]

    evt = ATLASEvent(
        event_id="evt_inv_tl",
        event_type="person_detected",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    storage.save_event(evt)

    inc = Incident(
        incident_id="inc_inv_tl",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_inv_tl"],
    )
    storage.save_incident(inc)
    vault.capture_for_incident(inc)

    inv_data = inv_service.build_investigation("inc_inv_tl")
    assert inv_data is not None

    ev_entries = [item for item in inv_data["timeline"] if item.get("phase") == "EVIDENCE_CAPTURED"]
    assert len(ev_entries) == 1
    entry = ev_entries[0]
    assert "CAPTURED_AFTER_EVENT" in entry["title"]
    assert "Provenance: CAPTURED_AFTER_EVENT" in entry["description"]


# Test 19: Investigation API failure case surfaces temporal provenance metadata
def test_investigation_api_failure_case_provenance(isolated_env):
    vault = isolated_env["vault"]
    storage = isolated_env["storage"]
    cam = isolated_env["cam"]
    inv_service = isolated_env["inv_service"]
    cam._frame = None

    evt_iso = datetime.now(timezone.utc).isoformat()
    evt = ATLASEvent(
        event_id="evt_inv_fail",
        event_type="person_detected",
        timestamp=evt_iso,
    )
    storage.save_event(evt)

    inc = Incident(
        incident_id="inc_inv_fail",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
        source_event_ids=["evt_inv_fail"],
    )
    storage.save_incident(inc)
    vault.capture_for_incident(inc)

    inv_data = inv_service.build_investigation("inc_inv_fail")
    assert inv_data is not None

    cam_ev = inv_data["camera_evidence"]
    assert cam_ev["status"] == "CAPTURE_FAILED"
    assert cam_ev["temporal_relation"] == "TIMESTAMP_UNAVAILABLE"
    assert cam_ev["source_event_timestamp"] == evt_iso


# Test 20: Frontend DOM elements exist in index.html
def test_frontend_dom_elements_exist():
    index_html_path = Path("atlas/frontend/index.html")
    assert index_html_path.exists()
    content = index_html_path.read_text(encoding="utf-8")
    assert 'id="inv-cam-provenance"' in content
    assert 'id="inv-cam-event-time"' in content
    assert 'id="inv-cam-frame-time"' in content


# Test 21: Test directory isolation: zero test artifacts in production data/
def test_directory_isolation(isolated_env):
    prod_evidence_dir = Path("data/evidence")
    if prod_evidence_dir.exists():
        prod_files = list(prod_evidence_dir.iterdir())
        for f in prod_files:
            assert not f.name.startswith("ev_test")
            assert not f.name.startswith("ev_store")
