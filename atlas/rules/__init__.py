"""ATLAS Deterministic Safety Rule Engine Module."""

from atlas.rules.schema import RuleEvaluationResult
from atlas.rules.definitions import (
    BaseSafetyRule,
    PossibleFallRule,
    UnauthorizedObjectRemovalRule,
    UnexpectedPersonPresenceRule,
    HighRiskInteractionRule,
)
from atlas.rules.engine import DeterministicRuleEngine, get_rule_engine

__all__ = [
    "RuleEvaluationResult",
    "BaseSafetyRule",
    "PossibleFallRule",
    "UnauthorizedObjectRemovalRule",
    "UnexpectedPersonPresenceRule",
    "HighRiskInteractionRule",
    "DeterministicRuleEngine",
    "get_rule_engine",
]
