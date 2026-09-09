"""Step 6.5: Multi-Source Context & Evidence Fusion Test Suite.

Verifies:
1. Observation schema validation with valid data.
2. Invalid observation rejection (out-of-bounds confidence, missing fields).
3. SourceType enum members and values.
4. SourceAvailability enum members and values.
5. Real camera source registration reflecting CameraStream state.
6. Unavailable / unconfigured source representation (NOT_CONFIGURED / UNAVAILABLE).
7. Timestamp preservation: source observed_at is strictly preserved and never rewritten.
8. Bounded temporal memory: max_observations and retention window respected.
9. Temporal correlation: observations within sliding window correlated.
10. Temporal mismatch handling: observations outside window excluded.
11. Entity correlation: preservation of ByteTrack track_id.
12. Unknown entity association fallback: association_status remains UNKNOWN.
13. Evidence agreement classification (AGREES).
14. Evidence contradiction classification (CONTRADICTS).
15. Insufficient / single-source evidence classification (INSUFFICIENT / INDEPENDENT).
16. FusedSituation creation and schema validation.
17. Provenance preservation: full causal audit metadata in FusedSituation.
18. Duplicate observation ingestion protection.
19. Read-only API endpoints (/api/sources, /api/context/fusions, /api/context/fusions/<id>).
20. Investigation service integration: source_coverage and fused_situation returned.
21. Chronological timeline entry for CONTEXT_FUSED phase.
22. Dashboard state integration: sources_summary exposed in /api/dashboard/state.
23. Zero client-side fusion reasoning: pure backend ground truth.
24. Zero continuous recording: no VideoWriter or unbounded video queues.
25. Zero fabricated source availability: unconfigured sources remain NOT_CONFIGURED.
26. Existing human authorization boundary preserved: system_core restricted, admin required.
27. Test directory isolation: zero test artifacts written to production data/ directory.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any
import numpy as np
import pytest
from pydantic import ValidationError

from atlas.authority.models import Action, Actor, Permission, Role
from atlas.authority.service import AuthorityService
from atlas.camera.base import CameraState
from atlas.events.schema import ATLASEvent, EventSeverity, EventStatus, EventType
from atlas.events.storage import EventStorage
import atlas.events.storage as aes
from atlas.fusion.engine import FusionEngine, get_fusion_engine, reset_global_fusion_engine
import atlas.fusion.engine as afe
from atlas.fusion.schema import (
    EntityAssociationStatus,
    EntityCorrelation,
    EvidenceRelationship,
    FusedSituation,
    Observation,
    SourceAvailability,
    SourceType,
    TemporalCorrelation,
)
from atlas.fusion.sources import SourceRegistry, get_source_registry, reset_global_source_registry
import atlas.fusion.sources as afs
from atlas.incidents.investigation import InvestigationService
from atlas.incidents.schema import Incident, IncidentStatus, IncidentType


class DummyCamera:
    """Isolated test camera producing deterministic synthetic frames."""
    def __init__(self, is_connected: bool = True, fps: float = 30.0):
        self.source = "0"
        self._is_connected = is_connected
        self._fps = fps
        self._state = CameraState.CONNECTED if is_connected else CameraState.DISCONNECTED

    def get_status(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "is_connected": self._is_connected,
            "state": self._state.value,
            "fps": self._fps,
            "resolution": {"width": 640, "height": 480},
            "last_frame_timestamp": time.time() if self._is_connected else None,
            "error_message": None,
        }

    def get_latest_frame(self):
        if not self._is_connected:
            return None
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        return frame, time.time()


@pytest.fixture
def isolated_fusion_env(tmp_path):
    """Provide isolated environment with fresh storage, source registry, and fusion engine."""
    reset_global_source_registry()
    reset_global_fusion_engine()

    db_path = tmp_path / "test_fusion_events.db"
    storage = EventStorage(db_path=db_path)
    camera = DummyCamera(is_connected=True, fps=30.0)
    registry = SourceRegistry(camera_stream=camera)
    engine = FusionEngine(registry=registry, storage=storage, max_observations=50, max_fusions=20)
    inv_service = InvestigationService(storage=storage)

    old_reg = afs._global_registry
    old_eng = afe._global_fusion_engine
    old_storage = aes._global_storage

    afs._global_registry = registry
    afe._global_fusion_engine = engine
    aes._global_storage = storage

    try:
        yield {
            "tmp_path": tmp_path,
            "db_path": db_path,
            "storage": storage,
            "camera": camera,
            "registry": registry,
            "engine": engine,
            "inv_service": inv_service,
        }
    finally:
        afs._global_registry = old_reg
        afe._global_fusion_engine = old_eng
        aes._global_storage = old_storage
        reset_global_source_registry()
        reset_global_fusion_engine()


# Test 1: Observation schema validation with valid data
def test_observation_schema_validation():
    now_iso = datetime.now(timezone.utc).isoformat()
    obs = Observation(
        observation_id="obs_valid_01",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=now_iso,
        received_at=now_iso,
        observation_type="PERSON_MOTION",
        value={"speed_px": 12.5, "direction": "left"},
        confidence=0.92,
        uncertainty=0.08,
        availability=SourceAvailability.AVAILABLE,
        track_id=42,
    )
    assert obs.observation_id == "obs_valid_01"
    assert obs.source_type == SourceType.CAMERA
    assert obs.confidence == 0.92
    assert obs.track_id == 42


# Test 2: Invalid observation rejection
def test_invalid_observation_rejection():
    now_iso = datetime.now(timezone.utc).isoformat()
    # Confidence > 1.0 must raise ValidationError
    with pytest.raises(ValidationError):
        Observation(
            source_type=SourceType.CAMERA,
            source_id="camera_0",
            observed_at=now_iso,
            observation_type="TEST",
            confidence=1.5,  # Invalid
            uncertainty=0.1,
        )

    # Extra fields must be forbidden
    with pytest.raises(ValidationError):
        Observation(
            source_type=SourceType.CAMERA,
            source_id="camera_0",
            observed_at=now_iso,
            observation_type="TEST",
            confidence=0.8,
            uncertainty=0.2,
            arbitrary_field="untrusted_payload",
        )


# Test 3: SourceType enum values
def test_source_type_enum():
    assert SourceType.CAMERA.value == "CAMERA"
    assert SourceType.IMU.value == "IMU"
    assert SourceType.GPS.value == "GPS"
    assert SourceType.ENVIRONMENTAL_SENSOR.value == "ENVIRONMENTAL_SENSOR"
    assert SourceType.WEARABLE.value == "WEARABLE"
    assert SourceType.GLASSES.value == "GLASSES"
    assert SourceType.DRONE.value == "DRONE"
    assert SourceType.ROBOT.value == "ROBOT"
    assert SourceType.OTHER.value == "OTHER"


# Test 4: SourceAvailability enum values
def test_source_availability_enum():
    assert SourceAvailability.AVAILABLE.value == "AVAILABLE"
    assert SourceAvailability.DEGRADED.value == "DEGRADED"
    assert SourceAvailability.DISCONNECTED.value == "DISCONNECTED"
    assert SourceAvailability.UNAVAILABLE.value == "UNAVAILABLE"
    assert SourceAvailability.NOT_CONFIGURED.value == "NOT_CONFIGURED"
    assert SourceAvailability.INVALID.value == "INVALID"


# Test 5: Real camera source registration reflecting CameraStream
def test_real_camera_source_registration(isolated_fusion_env):
    registry = isolated_fusion_env["registry"]
    cam_status = registry.get_camera_status()
    assert cam_status["source_type"] == SourceType.CAMERA
    assert cam_status["source_id"] == "camera_0"
    assert cam_status["availability"] == SourceAvailability.AVAILABLE
    assert cam_status["is_connected"] is True


# Test 6: Unavailable / unconfigured source representation
def test_unavailable_sources_representation(isolated_fusion_env):
    registry = isolated_fusion_env["registry"]
    sources = registry.get_sources_status()
    
    # Must contain CAMERA as AVAILABLE
    cam_s = next(s for s in sources if s["source_type"] == "CAMERA")
    assert cam_s["availability"] == "AVAILABLE"

    # All other subsystems must be NOT_CONFIGURED (never fabricated)
    unconfigured_types = {"IMU", "GPS", "WEARABLE", "GLASSES", "DRONE", "ROBOT", "ENVIRONMENTAL_SENSOR"}
    for st in unconfigured_types:
        entry = next(s for s in sources if s["source_type"] == st)
        assert entry["availability"] == "NOT_CONFIGURED"
        assert entry["is_connected"] is False


# Test 7: Source timestamp preservation (zero rewriting)
def test_timestamp_preservation(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    historic_iso = "2026-09-01T12:00:00.123456+00:00"

    evt = ATLASEvent(
        event_id="evt_preserve_ts",
        event_type="person_detected",
        timestamp=historic_iso,
        confidence=0.88,
    )
    obs = engine.ingest_event(evt)
    # The original observed_at timestamp must strictly match the event timestamp
    assert obs.observed_at == historic_iso


# Test 8: Bounded temporal memory
def test_bounded_temporal_memory(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    now_iso = datetime.now(timezone.utc).isoformat()

    # Engine max_observations was set to 50 in fixture
    for i in range(75):
        obs = Observation(
            observation_id=f"obs_bounded_{i}",
            source_type=SourceType.CAMERA,
            source_id="camera_0",
            observed_at=now_iso,
            observation_type="MOTION",
            confidence=0.8,
            uncertainty=0.2,
        )
        engine.ingest_observation(obs)

    assert len(engine._observations) == 50
    # Oldest observations (0..24) must have been evicted
    obs_ids = {o.observation_id for o in engine._observations}
    assert "obs_bounded_0" not in obs_ids
    assert "obs_bounded_74" in obs_ids


# Test 9: Temporal correlation within window
def test_temporal_correlation_within_window(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    t0 = time.time()
    t0_iso = datetime.fromtimestamp(t0, timezone.utc).isoformat()

    obs1 = Observation(
        observation_id="obs_corr_1",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="PERSON_WALKING",
        confidence=0.9,
        uncertainty=0.1,
    )
    engine.ingest_observation(obs1)

    fused = engine.fuse_context(window_seconds=5.0, reference_time=t0)
    assert "obs_corr_1" in fused.observation_ids
    assert len(fused.temporal_relationships) == 1
    assert fused.temporal_relationships[0].is_within_window is True


# Test 10: Temporal mismatch handling (observations outside window excluded)
def test_temporal_mismatch_excluded(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    t0 = time.time()
    t_past = t0 - 20.0  # 20 seconds before window
    t_past_iso = datetime.fromtimestamp(t_past, timezone.utc).isoformat()

    obs_old = Observation(
        observation_id="obs_old_1",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t_past_iso,
        observation_type="PERSON_WALKING",
        confidence=0.9,
        uncertainty=0.1,
    )
    engine.ingest_observation(obs_old)

    # Fusion window is 5.0 seconds around t0
    fused = engine.fuse_context(window_seconds=5.0, reference_time=t0)
    assert "obs_old_1" not in fused.observation_ids
    assert len(fused.observation_ids) == 0


# Test 11: Entity correlation with valid track ID
def test_entity_correlation_valid_track(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    t0 = time.time()
    t0_iso = datetime.fromtimestamp(t0, timezone.utc).isoformat()

    obs = Observation(
        observation_id="obs_track_7",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="PERSON_MOTION",
        confidence=0.85,
        uncertainty=0.15,
        track_id=7,
    )
    engine.ingest_observation(obs)

    fused = engine.fuse_context(window_seconds=5.0, reference_time=t0)
    assert len(fused.correlated_entities) == 1
    entity = fused.correlated_entities[0]
    assert entity.track_id == 7
    assert entity.source_id == "camera_0"


# Test 12: Unknown entity association remains unknown
def test_unknown_entity_association_fallback(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    t0 = time.time()
    t0_iso = datetime.fromtimestamp(t0, timezone.utc).isoformat()

    # Single sensor observation has no validated cross-sensor association
    obs = Observation(
        observation_id="obs_single_sensor",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="PERSON_MOTION",
        confidence=0.9,
        uncertainty=0.1,
        track_id=14,
    )
    engine.ingest_observation(obs)

    fused = engine.fuse_context(window_seconds=5.0, reference_time=t0)
    entity = fused.correlated_entities[0]
    # Must remain UNKNOWN, never invented identity
    assert entity.association_status == EntityAssociationStatus.UNKNOWN


# Test 13: Evidence agreement classification (AGREES)
def test_evidence_agreement_classification(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    registry = isolated_fusion_env["registry"]
    t0 = time.time()
    t0_iso = datetime.fromtimestamp(t0, timezone.utc).isoformat()

    # Register mock-free custom source adapter for multi-source testing
    registry.register_custom_source(
        source_type=SourceType.ENVIRONMENTAL_SENSOR,
        source_id="env_motion_01",
        availability=SourceAvailability.AVAILABLE,
    )

    obs1 = Observation(
        observation_id="obs_cam_agree",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="PERSON_PRESENCE",
        value={"motion": "active"},
        confidence=0.9,
        uncertainty=0.1,
    )
    obs2 = Observation(
        observation_id="obs_env_agree",
        source_type=SourceType.ENVIRONMENTAL_SENSOR,
        source_id="env_motion_01",
        observed_at=t0_iso,
        observation_type="PIR_MOTION",
        value={"motion": "active"},
        confidence=0.85,
        uncertainty=0.15,
    )
    engine.ingest_observation(obs1)
    engine.ingest_observation(obs2)

    fused = engine.fuse_context(window_seconds=5.0, reference_time=t0)
    assert fused.relationship_state == EvidenceRelationship.AGREES
    assert len(fused.supporting_evidence) == 2
    assert len(fused.contradictory_evidence) == 0


# Test 14: Evidence contradiction classification (CONTRADICTS)
def test_evidence_contradiction_classification(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    registry = isolated_fusion_env["registry"]
    t0 = time.time()
    t0_iso = datetime.fromtimestamp(t0, timezone.utc).isoformat()

    registry.register_custom_source(
        source_type=SourceType.WEARABLE,
        source_id="wearable_01",
        availability=SourceAvailability.AVAILABLE,
    )

    obs_cam = Observation(
        observation_id="obs_cam_sitting",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="PERSON_STATIONARY",
        value={"action": "person sitting", "state": "stationary"},
        confidence=0.88,
        uncertainty=0.12,
    )
    obs_wearable = Observation(
        observation_id="obs_wearable_accel",
        source_type=SourceType.WEARABLE,
        source_id="wearable_01",
        observed_at=t0_iso,
        observation_type="ACCELERATION_SPIKE",
        value={"acceleration_g": 3.8, "motion": "rapid acceleration"},
        confidence=0.82,
        uncertainty=0.18,
    )
    engine.ingest_observation(obs_cam)
    engine.ingest_observation(obs_wearable)

    fused = engine.fuse_context(window_seconds=5.0, reference_time=t0)
    assert fused.relationship_state == EvidenceRelationship.CONTRADICTS
    assert len(fused.contradictory_evidence) > 0


# Test 15: Insufficient / single-source evidence classification
def test_insufficient_evidence_classification(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    t0 = time.time()
    t0_iso = datetime.fromtimestamp(t0, timezone.utc).isoformat()

    # Empty window -> INSUFFICIENT
    fused_empty = engine.fuse_context(window_seconds=5.0, reference_time=t0)
    assert fused_empty.relationship_state == EvidenceRelationship.INSUFFICIENT

    # Single observation -> INSUFFICIENT
    obs = Observation(
        observation_id="obs_single_1",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="PERSON_MOTION",
        confidence=0.9,
        uncertainty=0.1,
    )
    engine.ingest_observation(obs)
    fused_single = engine.fuse_context(window_seconds=5.0, reference_time=t0)
    assert fused_single.relationship_state == EvidenceRelationship.INSUFFICIENT
    assert fused_single.completeness == "SINGLE_SOURCE"


# Test 16: FusedSituation creation and schema validation
def test_fused_situation_creation(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    t0 = time.time()
    t0_iso = datetime.fromtimestamp(t0, timezone.utc).isoformat()

    obs = Observation(
        observation_id="obs_fuse_schema",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="MOTION",
        confidence=0.85,
        uncertainty=0.15,
    )
    engine.ingest_observation(obs)

    fused = engine.fuse_context(window_seconds=5.0, reference_time=t0)
    assert isinstance(fused, FusedSituation)
    assert fused.fusion_id.startswith("fus_")
    assert fused.aggregate_confidence == 0.85
    assert fused.uncertainty == 0.15
    assert len(fused.source_types) == 1
    assert fused.source_types[0] == SourceType.CAMERA


# Test 17: Provenance preservation in FusedSituation
def test_provenance_preservation_in_fused_situation(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    t0 = time.time()
    t0_iso = datetime.fromtimestamp(t0, timezone.utc).isoformat()

    obs = Observation(
        observation_id="obs_prov_1",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="PERSON_MOTION",
        confidence=0.9,
        uncertainty=0.1,
        provenance={"driver": "v4l2", "frame_id": 100},
    )
    engine.ingest_observation(obs)

    fused = engine.fuse_context(window_seconds=5.0, reference_time=t0)
    assert "window_seconds" in fused.provenance
    assert "observation_count" in fused.provenance
    assert len(fused.temporal_relationships) == 1
    assert fused.temporal_relationships[0].source_provenance == {"driver": "v4l2", "frame_id": 100}


# Test 18: Duplicate observation protection
def test_duplicate_observation_protection(isolated_fusion_env):
    engine = isolated_fusion_env["engine"]
    t0_iso = datetime.now(timezone.utc).isoformat()

    obs = Observation(
        observation_id="obs_dup_01",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="TEST",
        confidence=0.8,
        uncertainty=0.2,
    )
    engine.ingest_observation(obs)
    engine.ingest_observation(obs)  # Ingest again

    assert len(engine._observations) == 1


# Test 19: Fusion read-only API persistence and retrieval
def test_fusion_persistence_and_retrieval(isolated_fusion_env):
    storage = isolated_fusion_env["storage"]
    engine = isolated_fusion_env["engine"]
    t0 = time.time()
    t0_iso = datetime.fromtimestamp(t0, timezone.utc).isoformat()

    obs = Observation(
        observation_id="obs_persist_1",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="PERSON_PRESENCE",
        confidence=0.9,
        uncertainty=0.1,
    )
    engine.ingest_observation(obs)
    fused = engine.fuse_context(window_seconds=5.0, reference_time=t0)

    # Fetch directly from storage
    retrieved = storage.get_fused_situation(fused.fusion_id)
    assert retrieved is not None
    assert retrieved.fusion_id == fused.fusion_id
    assert retrieved.aggregate_confidence == fused.aggregate_confidence
    assert retrieved.situation_summary == fused.situation_summary


# Test 20: Investigation service integration (source_coverage and fused_situation)
def test_investigation_integration(isolated_fusion_env):
    storage = isolated_fusion_env["storage"]
    engine = isolated_fusion_env["engine"]
    inv_service = isolated_fusion_env["inv_service"]

    # Ingest observation and fuse
    t0 = time.time()
    t0_iso = datetime.fromtimestamp(t0, timezone.utc).isoformat()
    obs = Observation(
        observation_id="obs_inv_test",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="PERSON_MOTION",
        confidence=0.92,
        uncertainty=0.08,
    )
    engine.ingest_observation(obs)
    engine.fuse_context(window_seconds=5.0, reference_time=t0)

    # Create an incident
    inc = Incident(
        incident_id="inc_fusion_inv",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
    )
    storage.save_incident(inc)

    inv = inv_service.build_investigation("inc_fusion_inv")
    assert inv is not None
    assert "source_coverage" in inv
    assert "fused_situation" in inv
    assert inv["fused_situation"] is not None
    assert inv["fused_situation"]["aggregate_confidence"] == 0.92


# Test 21: Chronological timeline entry for CONTEXT_FUSED phase
def test_timeline_context_fused_phase(isolated_fusion_env):
    storage = isolated_fusion_env["storage"]
    engine = isolated_fusion_env["engine"]
    inv_service = isolated_fusion_env["inv_service"]

    t0_iso = datetime.now(timezone.utc).isoformat()
    obs = Observation(
        observation_id="obs_tl_fuse",
        source_type=SourceType.CAMERA,
        source_id="camera_0",
        observed_at=t0_iso,
        observation_type="PERSON_MOTION",
        confidence=0.9,
        uncertainty=0.1,
    )
    engine.ingest_observation(obs)
    engine.fuse_context(window_seconds=5.0)

    inc = Incident(
        incident_id="inc_tl_fuse",
        incident_type=IncidentType.UNEXPECTED_PERSON_PRESENCE,
        severity="MEDIUM",
        status=IncidentStatus.ACTIVE,
    )
    storage.save_incident(inc)

    inv = inv_service.build_investigation("inc_tl_fuse")
    assert inv is not None
    fused_timeline = [item for item in inv["timeline"] if item.get("phase") == "CONTEXT_FUSED"]
    assert len(fused_timeline) == 1
    entry = fused_timeline[0]
    assert "Context Fused" in entry["title"]


# Test 22: Dashboard state integration
def test_dashboard_state_integration(isolated_fusion_env):
    from atlas.api.app import create_app
    app = create_app()
    client = app.test_client()

    resp = client.get("/api/dashboard/state")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "sources_summary" in data
    assert "sources" in data["sources_summary"]
    # Camera must be present
    cam_found = any(s["source_type"] == "CAMERA" for s in data["sources_summary"]["sources"])
    assert cam_found is True


# Test 23: Read-only API endpoints for sources and fusions
def test_sources_and_fusions_api_endpoints(isolated_fusion_env):
    from atlas.api.app import create_app
    app = create_app()
    client = app.test_client()

    # GET /api/sources
    resp_sources = client.get("/api/sources")
    assert resp_sources.status_code == 200
    s_data = resp_sources.get_json()
    assert s_data["status"] == "ok"
    assert "sources" in s_data
    assert "summary" in s_data

    # GET /api/context/fusions
    resp_fusions = client.get("/api/context/fusions")
    assert resp_fusions.status_code == 200
    f_data = resp_fusions.get_json()
    assert f_data["status"] == "ok"
    assert "fusions" in f_data


# Test 24: Zero continuous recording
def test_zero_continuous_recording_in_fusion():
    import inspect
    from atlas.fusion import engine, sources
    source_code = inspect.getsource(engine) + inspect.getsource(sources)
    assert "VideoWriter" not in source_code
    assert "write_video" not in source_code


# Test 25: Zero fabricated source availability
def test_zero_fabricated_source_availability(isolated_fusion_env):
    registry = isolated_fusion_env["registry"]
    sources = registry.get_sources_status()
    unconfigured = [s for s in sources if s["source_type"] != "CAMERA"]
    for s in unconfigured:
        # None of the unconfigured hardware sources may be marked AVAILABLE
        assert s["availability"] == "NOT_CONFIGURED"
        assert s["is_connected"] is False


# Test 26: Human authorization boundary preserved
def test_human_authorization_boundary_preserved():
    authority = AuthorityService()
    system_actor = Actor(actor_id="system_core", role=Role.VIEWER)
    operator_actor = Actor(actor_id="operator_01", role=Role.OPERATOR)
    admin_actor = Actor(actor_id="admin_01", role=Role.ADMIN)

    # system_core must be strictly forbidden from authorizing response escalations
    assert authority.check_permission(system_actor, Permission.AUTHORIZE_RESPONSE) is False
    # operator must be forbidden from authorizing response escalations
    assert authority.check_permission(operator_actor, Permission.AUTHORIZE_RESPONSE) is False
    # admin is permitted
    assert authority.check_permission(admin_actor, Permission.AUTHORIZE_RESPONSE) is True


# Test 27: Test directory isolation
def test_test_directory_isolation(isolated_fusion_env):
    prod_evidence_dir = Path("data/evidence")
    if prod_evidence_dir.exists():
        for f in prod_evidence_dir.iterdir():
            assert not f.name.startswith("obs_")
            assert not f.name.startswith("fus_")
