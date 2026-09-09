"""ATLAS Intelligence & Safety Analysis Pipeline.

Coordinates:
Perception / Events / Context
        ↓
Evidence Context Builder
        ↓
LLM Verifier (Structured Interpretation)
        ↓
Deterministic Safety Rule Engine
        ↓
Risk Assessment Engine
        ↓
Unified DecisionContext
        ↓
Persistent SQLite Storage
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
from typing import TYPE_CHECKING, Any, Sequence

from atlas.camera.stream import get_camera_stream
from atlas.context.memory import ContextManager, get_context_manager
from atlas.events.schema import ATLASEvent
from atlas.events.storage import EventStorage, get_event_storage
from atlas.intelligence.schemas import EvidenceContext, LLMVerificationResult
from atlas.intelligence.verifier import LLMVerifier, get_verifier
from atlas.risk.engine import RiskEngine, get_risk_engine
from atlas.risk.schema import DecisionContext, RiskAssessment
from atlas.rules.engine import DeterministicRuleEngine, get_rule_engine
from atlas.rules.schema import RuleEvaluationResult

if TYPE_CHECKING:
    from atlas.incidents.service import IncidentManager

logger = logging.getLogger("atlas.intelligence.pipeline")


class IntelligencePipeline:
    """End-to-end Step 4 analysis and Step 5 incident coordinator."""

    def __init__(
        self,
        verifier: LLMVerifier | None = None,
        rule_engine: DeterministicRuleEngine | None = None,
        risk_engine: RiskEngine | None = None,
        context_manager: ContextManager | None = None,
        storage: EventStorage | None = None,
        incident_manager: IncidentManager | None = None,
    ) -> None:
        self.verifier = verifier or get_verifier()
        self.rule_engine = rule_engine or get_rule_engine()
        self.risk_engine = risk_engine or get_risk_engine()
        self.context_manager = context_manager or get_context_manager()
        self.storage = storage or get_event_storage()
        if incident_manager is not None:
            self.incident_manager = incident_manager
        else:
            from atlas.incidents.service import get_incident_manager
            self.incident_manager = get_incident_manager()

        self._lock = threading.Lock()
        self._latest_decision: DecisionContext | None = None

    def build_evidence_context(
        self,
        events: Sequence[ATLASEvent] | None = None,
        context_data: dict[str, Any] | None = None,
        source_health: dict[str, Any] | None = None,
    ) -> tuple[EvidenceContext, list[ATLASEvent], dict[str, Any], dict[str, Any]]:
        """Build structured evidence context from memory, storage, and health checks."""
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Retrieve events
        if events is None:
            active_evs = self.context_manager.get_active_events()
            recent_evs = self.context_manager.get_recent_events(limit=30)
            combined_events = list({e.event_id: e for e in (recent_evs + active_evs)}.values())
        else:
            combined_events = list(events)
            active_evs = [e for e in combined_events if getattr(e, "status", "") == "ACTIVE"]
            recent_evs = combined_events

        # 2. Retrieve situational context
        if context_data is None:
            situational_context = self.context_manager.get_recent_context(window_seconds=120.0)
        else:
            situational_context = dict(context_data)

        # 3. Retrieve source health
        if source_health is None:
            cam = get_camera_stream()
            cam_stat = cam.get_status()
            health = {
                "camera": {
                    "is_connected": cam_stat["is_connected"],
                    "state": cam_stat["state"],
                    "source": str(cam_stat["source"]),
                    "fps": cam_stat["fps"],
                },
                "perception": {
                    "status": "active",
                },
            }
        else:
            health = dict(source_health)

        active_events_dump = [
            e.model_dump() if hasattr(e, "model_dump") else dict(e)
            for e in active_evs
        ]
        recent_events_dump = [
            e.model_dump() if hasattr(e, "model_dump") else dict(e)
            for e in recent_evs[-20:]
        ]

        # Step 6.5: Multi-source Context Fusion
        fused_situation_dict = None
        try:
            from atlas.fusion.engine import get_fusion_engine
            fusion_engine = get_fusion_engine(storage=self.storage)
            latest_fused = fusion_engine.get_latest_fusion()
            if latest_fused:
                fused_situation_dict = latest_fused.model_dump()
        except Exception:
            pass

        evidence = EvidenceContext(
            timestamp=now_iso,
            active_events=active_events_dump,
            recent_events=recent_events_dump,
            recent_context=situational_context,
            persons=situational_context.get("persons", []),
            objects=situational_context.get("objects", []),
            relationships=[],
            source_health=health,
            fused_situation=fused_situation_dict,
        )

        return evidence, combined_events, situational_context, health

    def run_analysis(
        self,
        events: Sequence[ATLASEvent] | None = None,
        context_data: dict[str, Any] | None = None,
        source_health: dict[str, Any] | None = None,
        force_llm: bool = False,
    ) -> DecisionContext:
        """Execute the full Step 4 intelligence, rules, and risk evaluation pipeline."""
        evidence, all_events, ctx_data, health = self.build_evidence_context(
            events=events,
            context_data=context_data,
            source_health=source_health,
        )

        # 1. LLM Verification
        llm_result = self.verifier.verify_evidence(evidence, force=force_llm)

        # 2. Deterministic Safety Rules
        rule_evals = self.rule_engine.evaluate_all(
            events=all_events,
            context=ctx_data,
            llm_verification=llm_result,
        )

        # 3. Deterministic Risk Assessment
        risk_assessment = self.risk_engine.assess_risk(
            rule_evaluations=rule_evals,
            llm_verification=llm_result,
            source_health=health,
        )

        # 4. Construct Unified Decision Context
        now_iso = datetime.now(timezone.utc).isoformat()
        decision = DecisionContext(
            timestamp=now_iso,
            evidence={
                "event_ids": [e.event_id for e in all_events],
                "active_persons_count": ctx_data.get("active_persons_count", 0),
                "active_objects_count": ctx_data.get("active_objects_count", 0),
                "context_window_seconds": 120,
            },
            llm_verification=llm_result,
            rule_evaluations=rule_evals,
            risk=risk_assessment,
        )

        # 5. Persist to SQLite
        try:
            self.storage.save_analysis(decision)
        except Exception as e:
            logger.error("Failed to persist analysis to SQLite: %s", e)

        # 6. Step 5: Evaluate Incident creation/correlation
        try:
            self.incident_manager.evaluate_decision_context(decision)
        except Exception as e:
            logger.error("Failed to evaluate incident from decision context: %s", e)

        with self._lock:
            self._latest_decision = decision

        return decision

    def get_latest_decision(self) -> DecisionContext | None:
        with self._lock:
            return self._latest_decision


_global_pipeline: IntelligencePipeline | None = None


def get_intelligence_pipeline() -> IntelligencePipeline:
    """Get or initialize singleton IntelligencePipeline."""
    global _global_pipeline
    if _global_pipeline is None:
        _global_pipeline = IntelligencePipeline()
    return _global_pipeline
