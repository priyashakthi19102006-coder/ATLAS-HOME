"""Schemas for ATLAS Deterministic Safety Rule Engine."""

from __future__ import annotations

import uuid
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class RuleEvaluationResult(BaseModel):
    """Structured evaluation output of an individual safety rule.
    
    LOCKED PRINCIPLE:
    Safety conditions are evaluated deterministically using real event facts,
    temporal sequences, and thresholds, not arbitrary LLM assumptions.
    """
    model_config = ConfigDict(validate_assignment=True)

    evaluation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rule_id: str
    rule_name: str
    timestamp: str
    condition_satisfied: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_event_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    verification_required: bool = True
    details: dict[str, Any] = Field(default_factory=dict)
