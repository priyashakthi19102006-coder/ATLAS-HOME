"""Pydantic schemas for ATLAS LLM Verification and Interpretation layer."""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class EvidenceContext(BaseModel):
    """Structured evidence snapshot provided to the LLM Verifier."""
    model_config = ConfigDict(validate_assignment=True)

    timestamp: str
    active_events: list[dict[str, Any]] = Field(default_factory=list)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    recent_context: dict[str, Any] = Field(default_factory=dict)
    persons: list[dict[str, Any]] = Field(default_factory=list)
    objects: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    source_health: dict[str, Any] = Field(default_factory=dict)
    fused_situation: Optional[dict[str, Any]] = None


class LLMVerificationResult(BaseModel):
    """Structured, validated output from the LLM Verifier.
    
    LOCKED PRINCIPLE:
    This structured interpretation informs downstream safety rules and risk evaluation.
    It CANNOT directly trigger emergency actions or declare definitive legal/medical conclusions.
    """
    model_config = ConfigDict(validate_assignment=True)

    verification_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str
    situation: str = Field(..., description="High-level description of recent scene dynamic.")
    interpretation: str = Field(..., description="Cautious, evidence-grounded interpretation.")
    supporting_event_ids: list[str] = Field(default_factory=list, description="Event IDs grounding this interpretation.")
    evidence_summary: list[str] = Field(default_factory=list, description="Key factual observations from evidence.")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence of the interpretation (0.0 to 1.0).")
    uncertainty: Literal["low", "moderate", "high"] = Field(..., description="Explicit degree of uncertainty.")
    verification_required: bool = Field(default=True, description="Whether deterministic rule/human verification is needed.")
    possible_conditions: list[str] = Field(default_factory=list, description="Hypothesized candidate conditions.")
    contradicting_evidence: list[str] = Field(default_factory=list, description="Observations that weaken or contradict the hypothesis.")
    status: Literal["completed", "unavailable", "uncertain", "malformed_rejected"] = "completed"
    model_used: Optional[str] = None
    error_message: Optional[str] = None
