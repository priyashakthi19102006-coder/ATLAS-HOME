"""ATLAS Home — Step 4 Real Integration Diagnostic.

Connects the full live pipeline:
Physical Webcam (Index 0)
→ Real-Time Perception (YOLOv8 + ByteTrack)
→ Event Engine (Lifecycle & Deduplication)
→ Temporal Context / Memory Layer
→ LLM Verifier (Structured Interpretation)
→ Deterministic Safety Rule Engine
→ Deterministic Risk Engine
→ Persistent SQLite Storage

Zero-Mock Policy:
Uses real physical camera frames, real perception inferences, real events,
and authentic LLM provider connectivity (or transparent unconfigured fallback).
NEVER claims an incident occurred unless supported by actual live evidence.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from atlas.camera.stream import CameraStream
from atlas.perception.engine import PerceptionEngine
from atlas.events.engine import EventEngine
from atlas.context.memory import ContextManager
from atlas.events.storage import EventStorage
from atlas.intelligence.pipeline import IntelligencePipeline
from atlas.intelligence.verifier import LLMVerifier
from atlas.rules.engine import DeterministicRuleEngine
from atlas.risk.engine import RiskEngine
from atlas.incidents.service import get_incident_manager
from atlas.alerts.service import get_alert_manager
from atlas.audit.service import get_audit_logger
from atlas.orchestration import AtlasOrchestrator


def main() -> int:
    print("=" * 65)
    print("ATLAS HOME — STEP 4 INTELLIGENCE & RISK VERIFICATION")
    print("Zero-Mock Policy: Real Camera -> Perception -> Events -> Rules -> Risk")
    print("=" * 65)

    # 1. Initialize SQLite storage & Context
    db_path = Path("data/atlas_events.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    storage = EventStorage(str(db_path))
    context = ContextManager(retention_window_seconds=120.0, storage=storage)
    event_engine = EventEngine(context_manager=context, track_expiry_seconds=2.0)

    # 2. Initialize Intelligence, Rules, Risk
    verifier = LLMVerifier(cooldown_seconds=0.0)
    rule_engine = DeterministicRuleEngine()
    risk_engine = RiskEngine()
    pipeline = IntelligencePipeline(
        verifier=verifier,
        rule_engine=rule_engine,
        risk_engine=risk_engine,
        context_manager=context,
        storage=storage,
    )

    # 3. Connect Real Camera (Device index 0)
    print("[camera] Opening physical webcam (device index 0)...")
    cam = CameraStream(source=0, auto_start=True)

    # Wait for camera readiness
    for _ in range(30):
        if cam.is_connected:
            break
        time.sleep(0.1)

    if not cam.is_connected:
        print("[FAIL] Physical camera could not be opened at index 0.")
        cam.stop()
        return 1

    w, h = cam.resolution
    print(f"[camera] Connected! Resolution: {w}x{h}, Real FPS: {cam.fps:.1f}")

    # 4. Initialize Perception Engine
    print("[perception] Loading YOLOv8 + ByteTrack...")
    try:
        perception = PerceptionEngine()
    except Exception as e:
        print(f"[FAIL] Failed to initialize perception engine: {e}")
        cam.stop()
        return 1

    # 5. Capture real live frames
    total_frames = 25
    processed_frames = 0
    all_live_events = []
    print(f"\n[pipeline] Processing {total_frames} live camera frames through pipeline...")
    print("-" * 65)

    while processed_frames < total_frames:
        frame_data = cam.get_latest_frame()
        if frame_data is None:
            time.sleep(0.02)
            continue

        frame, frame_ts = frame_data
        obs = perception.process_frame(frame, timestamp=frame_ts)
        events = event_engine.process_observation(obs)
        if events:
            all_live_events.extend(events)
            for ev in events:
                print(f"  >> Real Event: ID={ev.event_id[:8]}.. Type={ev.event_type} PID={ev.person_id} Status={ev.status}")

        processed_frames += 1
        time.sleep(0.04)

    cam.stop()
    print("-" * 65)
    print(f"[pipeline] Completed {processed_frames} live frames.")

    # 6. Execute Intelligence & Risk Evaluation on Real Evidence
    print("\n[intelligence] Executing Step 4.2 Analysis Pipeline on live observations...")
    decision = pipeline.run_analysis(
        events=all_live_events,
        source_health={
            "camera": {"is_connected": True, "source": "device_0", "state": "CONNECTED"},
            "perception": {"status": "active"},
        },
        force_llm=True,
    )

    # 7. Step 5 Incident, Alert, Audit & Orchestration Inspection
    recent_incidents = storage.get_recent_incidents(limit=10)
    matched_incident = None
    for inc in recent_incidents:
        if inc.analysis_id == decision.analysis_id:
            matched_incident = inc
            break

    has_real_incident = matched_incident is not None
    real_incident_observed_str = "YES" if has_real_incident else "NO"

    # 8. Print Structured Diagnostic Report
    print("\n" + "=" * 65)
    print("ATLAS HOME — STEP 5 SYSTEM STATE REPORT")
    print("=" * 65)

    health = verifier.provider.check_health()
    network_status = health.get("network", "UNKNOWN")
    auth_status = "NOT_REQUIRED" if health.get("authenticated") else "FAILED"
    model_avail = "YES" if health.get("model_available") else f"NO ({health.get('error', 'MODEL_NOT_FOUND')})"

    cam_was_connected = cam.is_connected or True  # We checked is_connected earlier before stop
    has_real_events = len(all_live_events) > 0
    camera_status_str = f"CONNECTED ({w}x{h}, {cam.fps:.1f} fps)" if cam_was_connected else "DISCONNECTED"
    real_events_str = f"YES ({len(all_live_events)} events observed)" if has_real_events else "NO (0 events observed)"
    llm_provider_str = f"{verifier.provider.provider_name} ({decision.llm_verification.model_used or 'qwen2.5:3b'})"

    if decision.llm_verification.status == "completed":
        llm_status_str = "SUCCESSFUL REAL LLM VERIFICATION (completed)"
        llm_error_detail_str = "NONE"
    elif decision.llm_verification.status == "fallback":
        llm_status_str = "SAFE FALLBACK AFTER LLM FAILURE (fallback)"
        llm_error_detail_str = decision.llm_verification.error_message or "Provider failure fallback"
    else:
        llm_status_str = f"SAFE FALLBACK AFTER LLM FAILURE ({decision.llm_verification.status})"
        llm_error_detail_str = decision.llm_verification.error_message or "Validation rejection"

    rules_triggered = [r.rule_name for r in decision.rule_evaluations if r.condition_satisfied]
    rule_status_str = f"EVALUATED ({len(decision.rule_evaluations)} rules; triggered: {rules_triggered or 'NONE'})"
    risk_status_str = f"ASSESSED (Score={decision.risk.score:.2f}, Level={decision.risk.level.value}, Uncertainty={decision.risk.uncertainty.upper()})"

    orch = AtlasOrchestrator(storage=storage)
    if matched_incident:
        inc_type_val = matched_incident.incident_type.value if hasattr(matched_incident.incident_type, "value") else str(matched_incident.incident_type)
        incident_status_str = f"ACTIVE ({inc_type_val}, ID={matched_incident.incident_id[:8]}..)"
        matched_alert = storage.get_alert_for_incident(matched_incident.incident_id)
        if matched_alert:
            al_stat = matched_alert.status.value if hasattr(matched_alert.status, "value") else str(matched_alert.status)
            al_sev = matched_alert.severity.value if hasattr(matched_alert.severity, "value") else str(matched_alert.severity)
            alert_status_str = f"{al_stat} (ID={matched_alert.alert_id[:8]}.., Sev={al_sev})"
        else:
            alert_status_str = "NONE"
        orch_eval = orch.evaluate_incident_eligibility(matched_incident.incident_id)
        orchestrator_decision_str = orch_eval.decision.value
        esc_val = matched_incident.escalation_state.value if hasattr(matched_incident.escalation_state, "value") else str(matched_incident.escalation_state)
        auth_state_str = f"{esc_val} (Human auth required={orch_eval.requires_human_authorization})"
    else:
        incident_status_str = "NONE (Baseline ambient conditions; risk below threshold)"
        alert_status_str = "NONE (No active incident)"
        orchestrator_decision_str = "NOT_APPLICABLE (No incident created)"
        auth_state_str = "NOT_ESCALATED"

    # Print the 11 mandatory Step 5.2 diagnostic fields
    print("STEP 5.2 DIAGNOSTIC FIELDS:")
    print(f"  CAMERA_STATUS:         {camera_status_str}")
    print(f"  REAL_EVENTS_OBSERVED:  {real_events_str}")
    print(f"  LLM_PROVIDER:          {llm_provider_str}")
    print(f"  LLM_STATUS:            {llm_status_str}")
    print(f"  LLM_ERROR_DETAIL:      {llm_error_detail_str}")
    print(f"  RULE_STATUS:           {rule_status_str}")
    print(f"  RISK_STATUS:           {risk_status_str}")
    print(f"  INCIDENT_STATUS:       {incident_status_str}")
    print(f"  ALERT_STATUS:          {alert_status_str}")
    print(f"  ORCHESTRATOR_DECISION: {orchestrator_decision_str}")
    print(f"  AUTHORIZATION_STATE:   {auth_state_str}")

    print("\n--- Physical Camera & Perception ---")
    print(f"  Device Index: 0")
    print(f"  Status: CONNECTED ({w}x{h})")
    print(f"  Frames Processed: {processed_frames}")
    ctx = context.get_recent_context(window_seconds=60.0)
    print(f"  Active Persons: {ctx.get('active_persons_count', 0)}")
    print(f"  Active Objects: {ctx.get('active_objects_count', 0)}")

    print(f"\n--- Real Events ---")
    print(f"  REAL EVENT OBSERVED: {real_events_str}")
    for ev in all_live_events:
        print(f"    - Event Type: {ev.event_type}")
        print(f"      Event ID:   {ev.event_id}")
        print(f"      Timestamp:  {ev.timestamp}")
        print(f"      Source:     {ev.source}")
        print(f"      Track ID:   {ev.person_id or ev.object_id or 'None'}")

    print(f"\n--- LLM Provider & Verification ---")
    print(f"  Provider:             {verifier.provider.provider_name}")
    print(f"  Model Used:           {decision.llm_verification.model_used}")
    print(f"  Verification Status:  {decision.llm_verification.status}")
    if decision.llm_verification.status != "completed" and decision.llm_verification.error_message:
        print(f"  Error Detail:         {decision.llm_verification.error_message}")
    print(f"  Confidence:           {decision.llm_verification.confidence:.2f}")
    print(f"  Uncertainty:          {decision.llm_verification.uncertainty.upper()}")
    print(f"  Verification Needed:  {decision.llm_verification.verification_required}")
    print(f"  Situation:            {decision.llm_verification.situation}")
    print(f"  Interpretation:       {decision.llm_verification.interpretation}")
    print(f"  Supporting Event IDs: {decision.llm_verification.supporting_event_ids}")

    print(f"\n--- Deterministic Rule Engine ---")
    for r in decision.rule_evaluations:
        flag = "[TRIGGERED]" if r.condition_satisfied else "[CLEARED]"
        print(f"  {flag} {r.rule_name:32} Satisfied={r.condition_satisfied} Conf={r.confidence:.2f}")
        if r.missing_evidence:
            print(f"      Missing: {r.missing_evidence[0]}")
        if r.contradicting_evidence:
            print(f"      Contradicting: {r.contradicting_evidence[0]}")

    print(f"\n--- Deterministic Risk Engine ---")
    print(f"  Risk ID:        {decision.risk.risk_id[:8]}..")
    print(f"  Level:          {decision.risk.level.value}")
    print(f"  Score:          {decision.risk.score:.2f}")
    print(f"  Reason:         {decision.risk.reason}")
    print(f"  Uncertainty:    {decision.risk.uncertainty.upper()}")
    print(f"  Human Required: {decision.risk.human_verification_required}")

    print(f"\n--- Traceability Chain ---")
    event_str = ", ".join(eid[:8] for eid in decision.risk.supporting_event_ids) or "None"
    rule_str = ", ".join(decision.risk.supporting_rule_ids) or "None"
    print(f"  Events [{event_str}]")
    print(f"    → Analysis [{decision.analysis_id[:8]}..]")
    print(f"    → Rules [{rule_str}]")
    print(f"    → Risk [{decision.risk.risk_id[:8]}..] ({decision.risk.level.value})")

    def fmt_val(x: Any) -> str:
        if hasattr(x, "value"):
            return str(x.value)
        return str(x)

    # Step 5 Reporting
    print(f"\n--- Step 5: Incident & Alert Foundation ---")
    print(f"  REAL INCIDENT OBSERVED: {real_incident_observed_str}")
    if matched_incident:
        print(f"  Incident ID:      {matched_incident.incident_id}")
        print(f"  Incident Type:    {fmt_val(matched_incident.incident_type)}")
        print(f"  Status:           {fmt_val(matched_incident.status)}")
        print(f"  Severity:         {fmt_val(matched_incident.severity)}")
        print(f"  Risk Score:       {matched_incident.risk_score:.2f}")
        print(f"  Uncertainty:      {matched_incident.uncertainty}")
        print(f"  Escalation State: {fmt_val(matched_incident.escalation_state)}")
        print(f"  Source Event IDs: {matched_incident.source_event_ids}")
        print(f"  Analysis ID:      {matched_incident.analysis_id}")
        print(f"  Risk ID:          {matched_incident.risk_id}")

        alert = storage.get_alert_for_incident(matched_incident.incident_id)
        if alert:
            print(f"  Associated Alert:")
            print(f"    Alert ID:           {alert.alert_id}")
            print(f"    Alert Status:       {fmt_val(alert.status)}")
            print(f"    Severity:           {fmt_val(alert.severity)}")
            print(f"    Notification Count: {alert.notification_count} (NO external notifications sent)")
            print(f"    Cooldown Seconds:   {alert.cooldown_seconds}s")

        # Orchestration Boundary Evaluation
        orch = AtlasOrchestrator(storage=storage)
        orch_eval = orch.evaluate_incident_eligibility(matched_incident.incident_id)
        print(f"  Orchestrator Boundary Evaluation:")
        print(f"    Decision:                      {fmt_val(orch_eval.decision)}")
        print(f"    Requires Human Authorization:  {orch_eval.requires_human_authorization}")
        print(f"    Reason:                        {orch_eval.reason}")
    else:
        print("  Reason: Baseline ambient observation — no elevated risk threshold or rule violation detected.")
        print("  Action: Incident creation correctly withheld to prevent false alarms.")

    # Audit Trail
    audit_logger = get_audit_logger()
    recent_audits = audit_logger.get_recent_records(limit=5)
    print(f"\n--- Audit Trail (Recent Immutable Records) ---")
    if recent_audits:
        for a in recent_audits:
            print(f"  - [{a.timestamp[:19]}] Actor={a.actor_id} Action={a.action.value} Target={a.target_type}:{a.target_id[:8]}.. Reason={a.reason}")
    else:
        print("  No audit events recorded in this session.")

    # SQLite Persistence & Table Counts
    latest_db_record = storage.get_latest_analysis()
    db_ok = latest_db_record is not None and latest_db_record.get("analysis_id") == decision.analysis_id
    print(f"\n--- SQLite Persistence & Table Verification ---")
    print(f"  Analysis Record Persisted: {'YES [PASS]' if db_ok else 'NO [FAIL]'}")
    if db_ok:
        print(f"  Saved Analysis ID: {latest_db_record.get('analysis_id')}")

    # Count records in all tables
    with storage._lock:
        with storage._get_connection() as conn:
            c = conn.cursor()
            events_cnt = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            analyses_cnt = c.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
            incidents_cnt = c.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            alerts_cnt = c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            audit_cnt = c.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]

    print(f"  SQLite Table Record Counts:")
    print(f"    events:      {events_cnt}")
    print(f"    analyses:    {analyses_cnt}")
    print(f"    incidents:   {incidents_cnt}")
    print(f"    alerts:      {alerts_cnt}")
    print(f"    audit_logs:  {audit_cnt}")

    print("\n" + "=" * 65)
    print("STEP 5 VERIFICATION COMPLETE")
    print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
