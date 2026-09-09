"""Investigation Service and Evidence Trace for ATLAS Home Incidents.

Provides structured, read-only evidence and provenance snapshots answering:
"WHY DID ATLAS RAISE THIS INCIDENT?"

LOCKED PRINCIPLES:
- Read-only: Does not mutate incidents, execute LLM inference, or alter audit logs.
- Real provenance: Correlates solely through verified foreign keys and persisted DB records.
- Explicit availability: Clearly distinguishes AVAILABLE, MISSING, NOT_RECORDED, NOT_APPLICABLE.
- Zero client-side intelligence: Renders backend ground truth only.
"""

from __future__ import annotations

from enum import Enum
import logging
import threading
from typing import Any, Sequence

from atlas.alerts.service import get_alert_manager
from atlas.events.schema import ATLASEvent
from atlas.events.storage import EventStorage, get_event_storage
from atlas.incidents.schema import Incident
from atlas.orchestration import AtlasOrchestrator, get_orchestrator

logger = logging.getLogger("atlas.incidents.investigation")


class EvidenceAvailability(str, Enum):
    """Explicit availability states for investigation components."""
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    NOT_RECORDED = "NOT_RECORDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class InvestigationPhase(str, Enum):
    """Structured categories for evidence timeline entries."""
    OBSERVED = "OBSERVED"
    VERIFIED = "VERIFIED"
    RULE_EVALUATED = "RULE_EVALUATED"
    RISK_ASSESSED = "RISK_ASSESSED"
    INCIDENT_CREATED = "INCIDENT_CREATED"
    INCIDENT_UPDATED = "INCIDENT_UPDATED"
    ALERT_CORRELATED = "ALERT_CORRELATED"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    HUMAN_ACTION = "HUMAN_ACTION"
    CONTEXT_FUSED = "CONTEXT_FUSED"


class InvestigationService:
    """Service to assemble comprehensive read-only incident investigation snapshots."""

    def __init__(
        self,
        storage: EventStorage | None = None,
        orchestrator: AtlasOrchestrator | None = None,
    ) -> None:
        self.storage = storage or get_event_storage()
        self.orchestrator = orchestrator or get_orchestrator()

    def build_investigation(self, incident_id: str) -> dict[str, Any] | None:
        """Assemble full evidence provenance trace for a specific incident.
        
        Returns None if incident does not exist in SQLite storage.
        """
        incident = self.storage.get_incident(incident_id)
        if incident is None:
            return None

        # 1. Supporting Perceptual Events
        source_event_ids = list(incident.source_event_ids or [])
        events: list[ATLASEvent] = []
        missing_event_ids: list[str] = []

        if source_event_ids:
            found_events = self.storage.get_events_by_ids(source_event_ids)
            found_map = {e.event_id: e for e in found_events}
            for eid in source_event_ids:
                if eid in found_map:
                    events.append(found_map[eid])
                else:
                    # Fallback single lookup
                    single = self.storage.get_event(eid)
                    if single:
                        events.append(single)
                    else:
                        missing_event_ids.append(eid)

        supporting_events_data = [e.model_dump() for e in events]

        # 2. Persisted Intelligence Analysis (LLM Verification, Rules, Risk)
        analysis_data: dict[str, Any] | None = None
        analysis_status = EvidenceAvailability.NOT_RECORDED

        if incident.analysis_id:
            analysis_data = self.storage.get_analysis(incident.analysis_id)
            if analysis_data:
                analysis_status = EvidenceAvailability.AVAILABLE
            else:
                # If analysis_id is set but not found in DB
                analysis_status = EvidenceAvailability.MISSING
        else:
            # Fallback to latest analysis if its timestamp matches or is close
            analysis_status = EvidenceAvailability.NOT_RECORDED

        # Parse LLM verification
        llm_data: dict[str, Any]
        if analysis_data and "llm_verification" in analysis_data:
            llm_raw = analysis_data["llm_verification"]
            llm_data = {
                "status": EvidenceAvailability.AVAILABLE.value,
                "provider": "ollama",
                "model": llm_raw.get("model_used") or "qwen2.5:3b",
                "verification_id": llm_raw.get("verification_id"),
                "timestamp": llm_raw.get("timestamp"),
                "situation": llm_raw.get("situation", "N/A"),
                "interpretation": llm_raw.get("interpretation", "N/A"),
                "confidence": float(llm_raw.get("confidence", 0.0)),
                "uncertainty": llm_raw.get("uncertainty", "high"),
                "verification_required": bool(llm_raw.get("verification_required", True)),
                "supporting_event_ids": list(llm_raw.get("supporting_event_ids", [])),
                "contradicting_evidence": list(llm_raw.get("contradicting_evidence", [])),
                "possible_conditions": list(llm_raw.get("possible_conditions", [])),
                "llm_status": llm_raw.get("status", "completed"),
                "error_message": llm_raw.get("error_message"),
            }
        elif analysis_status == EvidenceAvailability.MISSING:
            llm_data = {
                "status": EvidenceAvailability.MISSING.value,
                "message": f"Referenced analysis '{incident.analysis_id}' was not found in storage.",
                "supporting_event_ids": [],
            }
        else:
            llm_data = {
                "status": EvidenceAvailability.NOT_RECORDED.value,
                "message": "No structured LLM verification was linked to this incident.",
                "supporting_event_ids": [],
            }

        # Parse Rule Evaluations
        rules_evaluations: list[dict[str, Any]] = []
        rules_status = EvidenceAvailability.NOT_RECORDED
        if analysis_data and "rule_evaluations" in analysis_data:
            rules_evaluations = list(analysis_data.get("rule_evaluations", []))
            rules_status = EvidenceAvailability.AVAILABLE
        elif analysis_status == EvidenceAvailability.MISSING:
            rules_status = EvidenceAvailability.MISSING

        # Parse Risk Assessment
        risk_data: dict[str, Any]
        risk_status = EvidenceAvailability.NOT_RECORDED
        if analysis_data and "risk" in analysis_data:
            r = analysis_data["risk"]
            risk_data = {
                "status": EvidenceAvailability.AVAILABLE.value,
                "risk_id": r.get("risk_id", incident.risk_id),
                "timestamp": r.get("timestamp"),
                "score": float(r.get("score", incident.risk_score)),
                "level": r.get("level", incident.severity),
                "uncertainty": r.get("uncertainty", incident.uncertainty),
                "reason": r.get("reason", "Deterministic safety rules triggered."),
                "human_verification_required": bool(r.get("human_verification_required", True)),
                "supporting_rule_ids": list(r.get("supporting_rule_ids", incident.triggering_rule_ids)),
                "supporting_event_ids": list(r.get("supporting_event_ids", source_event_ids)),
                "details": r.get("details", {}),
            }
            risk_status = EvidenceAvailability.AVAILABLE
        elif incident.risk_id:
            # Fallback to incident-embedded risk metrics
            risk_data = {
                "status": EvidenceAvailability.AVAILABLE.value,
                "risk_id": incident.risk_id,
                "score": incident.risk_score,
                "level": incident.severity,
                "uncertainty": incident.uncertainty,
                "reason": f"Incident created with risk score {incident.risk_score}",
                "human_verification_required": True,
                "supporting_rule_ids": incident.triggering_rule_ids,
                "supporting_event_ids": source_event_ids,
            }
            risk_status = EvidenceAvailability.AVAILABLE
        else:
            risk_data = {
                "status": EvidenceAvailability.NOT_RECORDED.value,
                "message": "No distinct risk assessment record found.",
            }

        # 3. Context / Memory Snapshot
        context_data: dict[str, Any]
        if analysis_data and "evidence" in analysis_data:
            ctx_meta = analysis_data["evidence"]
            context_data = {
                "status": EvidenceAvailability.AVAILABLE.value,
                "active_persons_count": ctx_meta.get("active_persons_count", 0),
                "active_objects_count": ctx_meta.get("active_objects_count", 0),
                "context_window_seconds": ctx_meta.get("context_window_seconds", 120),
                "associated_event_ids": ctx_meta.get("event_ids", source_event_ids),
            }
        else:
            context_data = {
                "status": EvidenceAvailability.NOT_RECORDED.value,
                "message": "Situational memory snapshot not persisted with this incident.",
            }

        # 4. Correlated Internal Alert
        alert = self.storage.get_alert_for_incident(incident_id)
        if alert:
            alert_data = {
                "status": EvidenceAvailability.AVAILABLE.value,
                "alert_id": alert.alert_id,
                "severity": alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity),
                "alert_status": alert.status.value if hasattr(alert.status, "value") else str(alert.status),
                "is_acknowledged": alert.is_acknowledged,
                "is_escalated": alert.is_escalated,
                "created_at": alert.created_at,
                "updated_at": alert.updated_at,
            }
        else:
            alert_data = {
                "status": EvidenceAvailability.NOT_RECORDED.value,
                "message": "No active or historical internal alert found for this incident.",
            }

        # 5. Orchestrator Evaluation
        orch_eval = self.orchestrator.evaluate_incident_eligibility(incident_id)
        orchestrator_data = {
            "status": EvidenceAvailability.AVAILABLE.value,
            "decision": orch_eval.decision.value,
            "reason": orch_eval.reason,
            "requires_human_authorization": orch_eval.requires_human_authorization,
            "escalation_state": incident.escalation_state.value if hasattr(incident.escalation_state, "value") else str(incident.escalation_state),
            "external_notifications": "0 / DISABLED",
            "details": orch_eval.details,
        }

        # 6. Immutable Audit Trail for Incident
        # Query audit_logs for target_id = incident_id and target_id = f"incident/{incident_id}"
        audit_raw = self.storage.get_audit_records_for_target(incident_id, limit=100)
        slash_target = f"incident/{incident_id}"
        audit_slash = self.storage.get_audit_records_for_target(slash_target, limit=100)
        
        combined_audit = list({a.audit_id: a for a in (audit_raw + audit_slash)}.values())
        # Sort chronologically ascending
        combined_audit.sort(key=lambda a: a.timestamp)

        audit_history: list[dict[str, Any]] = []
        for a in combined_audit:
            audit_history.append({
                "audit_id": a.audit_id,
                "timestamp": a.timestamp,
                "actor_id": a.actor_id,
                "action": a.action.value if hasattr(a.action, "value") else str(a.action),
                "target_type": a.target_type,
                "target_id": a.target_id,
                "previous_state": a.previous_state,
                "new_state": a.new_state,
                "reason": a.reason,
            })

        # 7. Chronological Evidence Timeline Construction
        # Merge all verified timestamped facts without synthetic points
        timeline_items: list[dict[str, Any]] = []

        # (a) Observed Supporting Events
        for ev in events:
            timeline_items.append({
                "timestamp": ev.timestamp,
                "phase": InvestigationPhase.OBSERVED.value,
                "title": f"Observed: {ev.event_type}",
                "description": f"Source device: {ev.source_device}, confidence: {ev.confidence if ev.confidence is not None else 'N/A'}",
                "details": {
                    "event_id": ev.event_id,
                    "event_type": ev.event_type,
                    "person_id": ev.person_id,
                    "object_id": ev.object_id,
                    "severity": ev.severity,
                },
            })

        # (b) LLM Verification
        if analysis_data and "llm_verification" in analysis_data:
            llm_v = analysis_data["llm_verification"]
            llm_ts = llm_v.get("timestamp") or analysis_data.get("timestamp")
            if llm_ts:
                timeline_items.append({
                    "timestamp": llm_ts,
                    "phase": InvestigationPhase.VERIFIED.value,
                    "title": f"LLM Verification: {llm_v.get('situation', 'Completed')}",
                    "description": f"{llm_v.get('interpretation', '')} (Confidence: {llm_v.get('confidence')}, Uncertainty: {llm_v.get('uncertainty')})",
                    "details": {
                        "analysis_id": analysis_data.get("analysis_id"),
                        "model_used": llm_v.get("model_used"),
                        "grounded_event_ids": llm_v.get("supporting_event_ids", []),
                    },
                })

        # (c) Deterministic Safety Rules
        for r_res in rules_evaluations:
            r_ts = r_res.get("timestamp") or (analysis_data.get("timestamp") if analysis_data else None)
            if r_ts:
                cond = "SATISFIED" if r_res.get("condition_satisfied") else "NOT SATISFIED"
                timeline_items.append({
                    "timestamp": r_ts,
                    "phase": InvestigationPhase.RULE_EVALUATED.value,
                    "title": f"Rule Evaluated: {r_res.get('rule_name', r_res.get('rule_id'))}",
                    "description": f"Condition: {cond} (Confidence: {r_res.get('confidence')})",
                    "details": r_res,
                })

        # (d) Risk Assessment
        if risk_status == EvidenceAvailability.AVAILABLE and risk_data.get("timestamp"):
            timeline_items.append({
                "timestamp": risk_data["timestamp"],
                "phase": InvestigationPhase.RISK_ASSESSED.value,
                "title": f"Risk Assessed: {risk_data['level']} ({risk_data['score']})",
                "description": risk_data.get("reason", ""),
                "details": {
                    "score": risk_data["score"],
                    "level": risk_data["level"],
                    "uncertainty": risk_data["uncertainty"],
                    "human_verification_required": risk_data["human_verification_required"],
                },
            })

        # (e) Incident Lifecycle Creation
        timeline_items.append({
            "timestamp": incident.created_at,
            "phase": InvestigationPhase.INCIDENT_CREATED.value,
            "title": f"Incident Created: {incident.incident_type.value if hasattr(incident.incident_type, 'value') else incident.incident_type}",
            "description": f"Initial Severity: {incident.severity}, Risk: {incident.risk_score}",
            "details": {
                "incident_id": incident.incident_id,
                "status": incident.status.value if hasattr(incident.status, "value") else incident.status,
                "source_events_count": len(source_event_ids),
            },
        })

        # (f) Correlated Alert
        if alert:
            timeline_items.append({
                "timestamp": alert.created_at,
                "phase": InvestigationPhase.ALERT_CORRELATED.value,
                "title": f"Internal Alert Correlated ({alert.severity.value if hasattr(alert.severity, 'value') else alert.severity})",
                "description": f"Alert ID: {alert.alert_id}, Status: {alert.status.value if hasattr(alert.status, 'value') else alert.status}",
                "details": {"alert_id": alert.alert_id},
            })

        # (g) Audit Records / Operator Actions
        for rec in audit_history:
            timeline_items.append({
                "timestamp": rec["timestamp"],
                "phase": InvestigationPhase.HUMAN_ACTION.value,
                "title": f"{rec['action']} by {rec['actor_id']}",
                "description": rec.get("reason") or f"State: {rec.get('previous_state')} -> {rec.get('new_state')}",
                "details": rec,
            })

        # 8. Camera Evidence (Step 6.4 Privacy-Bounded Event-Triggered Capture)
        evidence_records = self.storage.get_evidence_for_incident(incident_id)
        captured_records = [
            r for r in evidence_records
            if (hasattr(r.capture_status, "value") and r.capture_status.value == "CAPTURED")
            or str(r.capture_status) == "CAPTURED"
        ]
        failed_records = [
            r for r in evidence_records
            if (hasattr(r.capture_status, "value") and r.capture_status.value == "FAILED")
            or str(r.capture_status) == "FAILED"
        ]

        from pathlib import Path
        from atlas.evidence.vault import get_evidence_vault
        storage_dir = Path(self.storage.db_path).parent / "evidence"
        vault = get_evidence_vault(storage=self.storage, storage_dir=storage_dir)

        if captured_records:
            artifacts_list = []
            for r in captured_records:
                integ_status, _ = vault.verify_integrity(r.evidence_id)
                status_str = integ_status.value if hasattr(integ_status, "value") else str(integ_status)
                tr_val = (
                    r.temporal_relation.value
                    if hasattr(r.temporal_relation, "value")
                    else str(r.temporal_relation or "TIMESTAMP_UNAVAILABLE")
                )
                src_evt_ts = getattr(r, "source_event_timestamp", None) or "UNAVAILABLE"
                src_frm_ts = getattr(r, "source_frame_timestamp", None) or "UNAVAILABLE"

                art_dict = {
                    "evidence_id": r.evidence_id,
                    "incident_id": r.incident_id,
                    "source_event_id": r.source_event_id,
                    "source_event_timestamp": src_evt_ts,
                    "source_frame_timestamp": src_frm_ts,
                    "temporal_relation": tr_val,
                    "captured_at": r.captured_at,
                    "artifact_type": r.artifact_type,
                    "mime_type": r.mime_type,
                    "file_size": r.file_size,
                    "sha256": r.sha256,
                    "width": r.width,
                    "height": r.height,
                    "capture_status": r.capture_status.value if hasattr(r.capture_status, "value") else str(r.capture_status),
                    "retention_status": r.retention_status.value if hasattr(r.retention_status, "value") else str(r.retention_status),
                    "integrity_status": status_str,
                }
                artifacts_list.append(art_dict)
                timeline_items.append({
                    "timestamp": r.captured_at,
                    "phase": "EVIDENCE_CAPTURED",
                    "title": f"Camera Evidence ({tr_val})",
                    "description": f"Provenance: {tr_val}, Event TS: {src_evt_ts}, Captured: {r.captured_at}",
                    "details": art_dict,
                })

            primary_art = artifacts_list[0] if artifacts_list else None
            camera_evidence = {
                "status": EvidenceAvailability.AVAILABLE.value,
                "temporal_relation": primary_art["temporal_relation"] if primary_art else None,
                "source_event_id": primary_art["source_event_id"] if primary_art else None,
                "source_event_timestamp": primary_art["source_event_timestamp"] if primary_art else None,
                "source_frame_timestamp": primary_art["source_frame_timestamp"] if primary_art else None,
                "captured_at": primary_art["captured_at"] if primary_art else None,
                "artifacts": artifacts_list,
            }
        elif failed_records:
            r_fail = failed_records[0]
            reason_str = r_fail.failure_reason or "Camera frame unavailable during incident creation."
            fail_tr = getattr(r_fail, "temporal_relation", None)
            fail_tr_val = fail_tr.value if hasattr(fail_tr, "value") else str(fail_tr or "TIMESTAMP_UNAVAILABLE")
            src_evt_ts = getattr(r_fail, "source_event_timestamp", None) or "UNAVAILABLE"
            src_frm_ts = getattr(r_fail, "source_frame_timestamp", None) or "UNAVAILABLE"

            camera_evidence = {
                "status": "CAPTURE_FAILED",
                "temporal_relation": fail_tr_val,
                "source_event_id": r_fail.source_event_id,
                "source_event_timestamp": src_evt_ts,
                "source_frame_timestamp": src_frm_ts,
                "captured_at": r_fail.captured_at,
                "artifacts": [],
                "reason": reason_str,
            }
            timeline_items.append({
                "timestamp": r_fail.captured_at,
                "phase": "EVIDENCE_CAPTURE_FAILED",
                "title": "Camera Evidence Capture Failed",
                "description": f"Reason: {reason_str}",
                "details": {
                    "evidence_id": r_fail.evidence_id,
                    "temporal_relation": fail_tr_val,
                    "reason": reason_str,
                },
            })
        else:
            camera_evidence = {
                "status": EvidenceAvailability.NOT_RECORDED.value,
                "temporal_relation": None,
                "source_event_id": None,
                "source_event_timestamp": None,
                "source_frame_timestamp": None,
                "captured_at": None,
                "artifacts": [],
                "message": "Camera evidence unavailable for this incident.",
                "details": {
                    "explanation": "No event-triggered evidence was captured for this incident.",
                },
            }

        # 8b. Step 6.5 Multi-Source Context & Evidence Fusion Integration
        from atlas.fusion.sources import get_source_registry
        from atlas.fusion.engine import get_fusion_engine

        source_registry = get_source_registry()
        source_coverage = source_registry.get_sources_status()

        fusion_engine = get_fusion_engine(storage=self.storage)
        latest_fusion = fusion_engine.get_latest_fusion()
        fused_situation_data: dict[str, Any] | None = None
        if latest_fusion:
            fused_situation_data = latest_fusion.model_dump()
            timeline_items.append({
                "timestamp": latest_fusion.created_at,
                "phase": InvestigationPhase.CONTEXT_FUSED.value,
                "title": f"Context Fused ({latest_fusion.relationship_state.value})",
                "description": latest_fusion.situation_summary,
                "details": {
                    "fusion_id": latest_fusion.fusion_id,
                    "relationship_state": latest_fusion.relationship_state.value,
                    "completeness": latest_fusion.completeness,
                    "aggregate_confidence": latest_fusion.aggregate_confidence,
                    "uncertainty": latest_fusion.uncertainty,
                    "source_types": [s.value for s in latest_fusion.source_types],
                    "observation_count": len(latest_fusion.observation_ids),
                },
            })

        # 8c. Step 7 Notifications & Escalation Integration
        incident_notifications = self.storage.get_notifications_for_incident(incident_id)
        escalation_record = self.storage.get_escalation_record(incident_id)
        for notif in incident_notifications:
            timeline_items.append({
                "timestamp": notif.sent_at or notif.created_at,
                "phase": "NOTIFICATION_DISPATCHED",
                "title": f"Notification {notif.status.value} ({notif.channel.value})",
                "description": f"Recipient: {notif.recipient_user_id} (Level {notif.escalation_level}, Status: {notif.status.value})",
                "details": notif.model_dump(),
            })

        # Sort chronological timeline by timestamp
        timeline_items.sort(key=lambda item: item.get("timestamp", ""))

        # 9. Evidence Summary
        summary = {
            "event_count": len(source_event_ids),
            "supporting_event_count": len(events),
            "missing_event_ids_count": len(missing_event_ids),
            "analysis_available": (analysis_status == EvidenceAvailability.AVAILABLE),
            "rule_results_available": (rules_status == EvidenceAvailability.AVAILABLE),
            "risk_available": (risk_status == EvidenceAvailability.AVAILABLE),
            "audit_entries": len(audit_history),
            "camera_evidence_status": camera_evidence["status"],
            "notifications_count": len(incident_notifications),
            "overall_integrity": "COMPLETE" if not missing_event_ids and analysis_status == EvidenceAvailability.AVAILABLE else "PARTIAL",
        }

        return {
            "incident": incident.model_dump(),
            "timeline": timeline_items,
            "supporting_events": supporting_events_data,
            "missing_event_ids": missing_event_ids,
            "context": context_data,
            "intelligence": llm_data,
            "rules": {
                "status": rules_status.value,
                "evaluations": rules_evaluations,
            },
            "risk": risk_data,
            "orchestrator": orchestrator_data,
            "alert": alert_data,
            "audit_history": audit_history,
            "camera_evidence": camera_evidence,
            "source_coverage": source_coverage,
            "fused_situation": fused_situation_data,
            "notifications": [n.model_dump() for n in incident_notifications],
            "escalation": escalation_record.model_dump() if escalation_record else None,
            "evidence_summary": summary,
        }


# Singleton pattern
_investigation_service: InvestigationService | None = None
_inv_lock = threading.Lock()


def get_investigation_service() -> InvestigationService:
    """Retrieve or create the global InvestigationService singleton."""
    global _investigation_service
    if _investigation_service is None:
        with _inv_lock:
            if _investigation_service is None:
                _investigation_service = InvestigationService()
    return _investigation_service
