"""ATLAS Orchestration Boundary Module.

Establishes the controlled boundary between:
RISK ASSESSMENT / INCIDENTS
and
AUTHORIZED RESPONSE / ORCHESTRATION.

LOCKED PRINCIPLES:
- The orchestrator NEVER automatically triggers external emergency actions.
- The LLM can NEVER directly call this orchestrator or authorize external actions.
- This module evaluates ACTION ELIGIBILITY only; no SMS/drone/device actions are dispatched.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from atlas.authority.models import Actor, Permission
from atlas.authority.service import AuthorityService, get_authority_service
from atlas.events.storage import EventStorage, get_event_storage
from atlas.incidents.schema import EscalationState, Incident, IncidentStatus


class OrchestratorDecision(str, Enum):
    """Structured decision on whether an incident is eligible for an authorized response."""
    ACTION_ELIGIBLE = "ACTION_ELIGIBLE"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    ALREADY_RESOLVED = "ALREADY_RESOLVED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class OrchestratorEvaluation:
    """Structured response from the orchestration boundary."""
    incident_id: str
    decision: OrchestratorDecision
    reason: str
    requires_human_authorization: bool
    details: dict[str, Any]


class AtlasOrchestrator:
    """Evaluates action eligibility at the system boundary without external side effects."""

    def __init__(
        self,
        device_id: str = "atlas_home_01",
        storage: EventStorage | None = None,
        authority_service: AuthorityService | None = None,
    ) -> None:
        self.device_id = device_id
        self.storage = storage or get_event_storage()
        self.authority = authority_service or get_authority_service()

    def evaluate_incident_eligibility(
        self,
        incident_id: str,
        actor: Actor | None = None,
    ) -> OrchestratorEvaluation:
        """Evaluate if an incident is eligible for response, enforcing authority boundaries."""
        incident = self.storage.get_incident(incident_id)

        # 1. Incident existence check
        if incident is None:
            return OrchestratorEvaluation(
                incident_id=incident_id,
                decision=OrchestratorDecision.NOT_ELIGIBLE,
                reason=f"Incident '{incident_id}' not found.",
                requires_human_authorization=False,
                details={"status": "not_found"},
            )

        # 2. Resolved / Dismissed check
        if incident.status == IncidentStatus.RESOLVED:
            return OrchestratorEvaluation(
                incident_id=incident_id,
                decision=OrchestratorDecision.ALREADY_RESOLVED,
                reason="Incident has already been resolved.",
                requires_human_authorization=False,
                details={"status": incident.status.value, "resolved_by": incident.resolved_by},
            )

        if incident.status == IncidentStatus.DISMISSED:
            return OrchestratorEvaluation(
                incident_id=incident_id,
                decision=OrchestratorDecision.NOT_ELIGIBLE,
                reason="Incident was dismissed; no action allowed.",
                requires_human_authorization=False,
                details={"status": incident.status.value},
            )

        # 3. Evidence sufficiency check
        if not incident.source_event_ids:
            return OrchestratorEvaluation(
                incident_id=incident_id,
                decision=OrchestratorDecision.INSUFFICIENT_EVIDENCE,
                reason="Incident does not have supporting perceptual evidence.",
                requires_human_authorization=True,
                details={"source_event_ids_count": 0},
            )

        # 4. Authority & Escalation Authorization Check
        has_authorized_escalation = incident.escalation_state == EscalationState.AUTHORIZED
        is_system_actor = (getattr(actor, "is_system", False) or actor.actor_id == "system_core") if actor is not None else False
        actor_authorized = (
            actor is not None
            and not is_system_actor
            and actor.has_permission(Permission.AUTHORIZE_RESPONSE)
        )

        if not (has_authorized_escalation or actor_authorized):
            return OrchestratorEvaluation(
                incident_id=incident_id,
                decision=OrchestratorDecision.AUTHORIZATION_REQUIRED,
                reason="Human authorization required before response action can be eligible.",
                requires_human_authorization=True,
                details={
                    "escalation_state": incident.escalation_state.value,
                    "actor_has_authorize_permission": actor_authorized,
                },
            )

        # 5. Incident is active and authorized
        return OrchestratorEvaluation(
            incident_id=incident_id,
            decision=OrchestratorDecision.ACTION_ELIGIBLE,
            reason="Incident is active, grounded in evidence, and human authorization is present.",
            requires_human_authorization=False,
            details={
                "status": incident.status.value,
                "escalation_state": incident.escalation_state.value,
                "risk_score": incident.risk_score,
                "evidence_count": len(incident.source_event_ids),
            },
        )


_global_orchestrator: AtlasOrchestrator | None = None


def get_orchestrator() -> AtlasOrchestrator:
    """Singleton getter for AtlasOrchestrator."""
    global _global_orchestrator
    if _global_orchestrator is None:
        _global_orchestrator = AtlasOrchestrator()
    return _global_orchestrator
