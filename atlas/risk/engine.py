"""ATLAS Transparent Deterministic Risk Assessment Engine."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
from typing import Any, Sequence

from atlas.intelligence.schemas import LLMVerificationResult
from atlas.risk.schema import RiskAssessment, RiskLevel
from atlas.rules.schema import RuleEvaluationResult

logger = logging.getLogger("atlas.risk.engine")

# Prototype safety-policy weights (explicit, configurable, transparent)
DEFAULT_RULE_WEIGHTS = {
    "rule_possible_fall": 0.85,
    "rule_unauthorized_object_removal": 0.65,
    "rule_unexpected_presence": 0.45,
    "rule_high_risk_interaction": 0.90,
}


class RiskEngine:
    """Consumes deterministic rule evaluations, LLM interpretation, and source health.
    
    LOCKED PRINCIPLE:
    Risk calculation is deterministic, transparent, and grounded in evidence.
    No LLM output directly determines the final risk level.
    High-impact risks enforce human_verification_required=True.
    """

    def __init__(
        self,
        rule_weights: dict[str, float] | None = None,
        low_threshold: float = 0.35,
        medium_threshold: float = 0.65,
        high_threshold: float = 0.85,
    ) -> None:
        self.rule_weights = rule_weights or dict(DEFAULT_RULE_WEIGHTS)
        self.low_threshold = low_threshold
        self.medium_threshold = medium_threshold
        self.high_threshold = high_threshold

        self._lock = threading.Lock()
        self._last_assessment: RiskAssessment | None = None
        self._history: list[RiskAssessment] = []

    def assess_risk(
        self,
        rule_evaluations: Sequence[RuleEvaluationResult],
        llm_verification: LLMVerificationResult | None = None,
        source_health: dict[str, Any] | None = None,
    ) -> RiskAssessment:
        """Compute transparent deterministic risk assessment."""
        now_iso = datetime.now(timezone.utc).isoformat()
        health = source_health or {}
        cam_connected = health.get("camera", {}).get("is_connected", True)
        perc_active = health.get("perception", {}).get("status", "active") == "active"

        # 1. Identify satisfied rules
        satisfied_rules = [r for r in rule_evaluations if r.condition_satisfied]

        # Gather supporting event IDs from rules & LLM
        supporting_events: set[str] = set()
        supporting_rule_ids: list[str] = []
        contradictions_count = 0

        for r in rule_evaluations:
            if r.condition_satisfied:
                supporting_rule_ids.append(r.rule_id)
                supporting_events.update(r.supporting_event_ids)
            contradictions_count += len(r.contradicting_evidence)

        if llm_verification:
            contradictions_count += len(llm_verification.contradicting_evidence)
            if llm_verification.status == "completed":
                supporting_events.update(llm_verification.supporting_event_ids)

        # 2. Compute Base Score
        if not satisfied_rules:
            # Baseline ambient home activity score
            raw_score = 0.10
            primary_reason = "No safety rules triggered; normal ambient activity."
        else:
            # Score contribution from highest weighted satisfied rule
            scores = []
            for r in satisfied_rules:
                weight = self.rule_weights.get(r.rule_id, 0.50)
                scores.append(r.confidence * weight)

            max_rule_score = max(scores)
            corroboration_bonus = 0.08 if len(satisfied_rules) > 1 else 0.0
            contradiction_discount = min(0.35, 0.15 * contradictions_count)

            raw_score = max(0.05, max_rule_score + corroboration_bonus - contradiction_discount)
            primary_reason = f"Satisfied conditions: {', '.join(r.rule_name for r in satisfied_rules)}."

        # 3. Source Health Adjustment
        source_degraded = not (cam_connected and perc_active)
        if source_degraded:
            primary_reason += " (Perception source health is degraded)."

        # 4. Uncertainty Assessment
        if source_degraded:
            uncertainty = "high"
        elif contradictions_count > 0:
            uncertainty = "high"
        elif llm_verification and llm_verification.uncertainty == "high":
            uncertainty = "high"
        elif not satisfied_rules:
            uncertainty = "low"
        else:
            uncertainty = "moderate"

        final_score = round(min(1.0, max(0.0, raw_score)), 2)

        # 5. Level Mapping
        if final_score < self.low_threshold:
            level = RiskLevel.LOW
        elif final_score < self.medium_threshold:
            level = RiskLevel.MEDIUM
        elif final_score < self.high_threshold:
            level = RiskLevel.HIGH
        else:
            level = RiskLevel.CRITICAL

        # 6. Human Verification Requirement (High-impact safety boundary)
        # Any elevated risk (MEDIUM/HIGH/CRITICAL) or high uncertainty mandates human review
        human_verification_required = (
            level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)
            or uncertainty == "high"
            or any(r.verification_required for r in satisfied_rules)
        )

        assessment = RiskAssessment(
            timestamp=now_iso,
            level=level,
            score=final_score,
            reason=primary_reason,
            supporting_rule_ids=supporting_rule_ids,
            supporting_event_ids=sorted(list(supporting_events)),
            uncertainty=uncertainty,
            human_verification_required=human_verification_required,
            details={
                "satisfied_rules_count": len(satisfied_rules),
                "contradictions_count": contradictions_count,
                "source_degraded": source_degraded,
                "llm_status": llm_verification.status if llm_verification else "not_evaluated",
            },
        )

        with self._lock:
            self._last_assessment = assessment
            self._history.append(assessment)
            if len(self._history) > 100:
                self._history.pop(0)

        return assessment

    def get_latest_assessment(self) -> RiskAssessment | None:
        with self._lock:
            return self._last_assessment

    def get_recent_assessments(self, limit: int = 20) -> list[RiskAssessment]:
        with self._lock:
            return list(self._history)[-limit:]


_global_risk_engine: RiskEngine | None = None


def get_risk_engine() -> RiskEngine:
    """Get or initialize singleton RiskEngine."""
    global _global_risk_engine
    if _global_risk_engine is None:
        _global_risk_engine = RiskEngine()
    return _global_risk_engine
