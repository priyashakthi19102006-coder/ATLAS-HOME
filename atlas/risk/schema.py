"""Schemas for ATLAS Risk Assessment Engine."""

from __future__ import annotations

from enum import Enum
import uuid
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from atlas.intelligence.schemas import LLMVerificationResult
from atlas.rules.schema import RuleEvaluationResult


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskAssessment(BaseModel):
    """Structured, transparent risk assessment result.
    
    LOCKED PRINCIPLE:
    Risk scoring is deterministic and transparent. The LLM does NOT assign the final risk level.
    High-impact situations enforce human_verification_required=True without autonomous external intervention.
    """
    model_config = ConfigDict(validate_assignment=True)

    risk_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str
    level: RiskLevel
    score: float = Field(..., ge=0.0, le=1.0)
    reason: str
    supporting_rule_ids: list[str] = Field(default_factory=list)
    supporting_event_ids: list[str] = Field(default_factory=list)
    uncertainty: Literal["low", "moderate", "high"]
    human_verification_required: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class DecisionContext(BaseModel):
    """Unified structured pipeline result container for future Orchestrator consumption (Step 19)."""
    model_config = ConfigDict(validate_assignment=True)

    analysis_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str
    evidence: dict[str, Any]
    llm_verification: LLMVerificationResult
    rule_evaluations: list[RuleEvaluationResult]
    risk: RiskAssessment
