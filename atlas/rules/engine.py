"""ATLAS Deterministic Safety Rule Engine."""

from __future__ import annotations

import logging
import threading
from typing import Any, Sequence

from atlas.events.schema import ATLASEvent
from atlas.intelligence.schemas import LLMVerificationResult
from atlas.rules.definitions import (
    BaseSafetyRule,
    HighRiskInteractionRule,
    PossibleFallRule,
    UnauthorizedObjectRemovalRule,
    UnexpectedPersonPresenceRule,
)
from atlas.rules.schema import RuleEvaluationResult

logger = logging.getLogger("atlas.rules.engine")


class DeterministicRuleEngine:
    """Manages and deterministically evaluates configured safety rules.
    
    LOCKED PRINCIPLE:
    Rules are hardcoded or deterministically configured; the LLM cannot rewrite or dynamically fabricate safety rules.
    """

    def __init__(self, rules: list[BaseSafetyRule] | None = None) -> None:
        if rules is not None:
            self.rules = rules
        else:
            self.rules = [
                PossibleFallRule(),
                UnauthorizedObjectRemovalRule(),
                UnexpectedPersonPresenceRule(),
                HighRiskInteractionRule(),
            ]

        self._lock = threading.Lock()
        self._last_evaluations: list[RuleEvaluationResult] = []

    def evaluate_all(
        self,
        events: Sequence[ATLASEvent],
        context: dict[str, Any],
        llm_verification: LLMVerificationResult | None = None,
    ) -> list[RuleEvaluationResult]:
        """Evaluate all active deterministic rules against real events and context."""
        results: list[RuleEvaluationResult] = []

        for rule in self.rules:
            if not rule.enabled:
                continue
            try:
                eval_res = rule.evaluate(
                    events=events,
                    context=context,
                    llm_verification=llm_verification,
                )
                results.append(eval_res)
            except Exception as e:
                logger.error("Error evaluating rule %s: %s", rule.rule_name, e)

        with self._lock:
            self._last_evaluations = results

        return results

    def get_latest_evaluations(self) -> list[RuleEvaluationResult]:
        with self._lock:
            return list(self._last_evaluations)

    def get_rule_status(self) -> list[dict[str, Any]]:
        with self._lock:
            latest_map = {r.rule_id: r for r in self._last_evaluations}
            return [
                {
                    "rule_id": rule.rule_id,
                    "rule_name": rule.rule_name,
                    "enabled": rule.enabled,
                    "condition_satisfied": latest_map[rule.rule_id].condition_satisfied if rule.rule_id in latest_map else False,
                    "confidence": latest_map[rule.rule_id].confidence if rule.rule_id in latest_map else 0.0,
                    "verification_required": latest_map[rule.rule_id].verification_required if rule.rule_id in latest_map else False,
                }
                for rule in self.rules
            ]


_global_rule_engine: DeterministicRuleEngine | None = None


def get_rule_engine() -> DeterministicRuleEngine:
    """Get or initialize singleton DeterministicRuleEngine."""
    global _global_rule_engine
    if _global_rule_engine is None:
        _global_rule_engine = DeterministicRuleEngine()
    return _global_rule_engine
