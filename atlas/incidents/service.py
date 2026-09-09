"""Incident management engine and lifecycle coordinator for ATLAS Home."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING, Any, Sequence

from atlas.alerts.service import AlertManager, get_alert_manager
from atlas.audit.schema import AuditAction
from atlas.audit.service import AuditLogger, get_audit_logger
from atlas.authority.models import Actor, Permission
from atlas.authority.service import AuthorityService, UnauthorizedError, get_authority_service
from atlas.events.storage import EventStorage, get_event_storage
from atlas.incidents.schema import (
    EscalationState,
    Incident,
    IncidentStatus,
    IncidentType,
    ResolutionReason,
)

if TYPE_CHECKING:
    from atlas.risk.schema import DecisionContext

logger = logging.getLogger("atlas.incidents.service")


class InvalidTransitionError(ValueError):
    """Raised when an illegal lifecycle state transition is attempted."""
    def __init__(self, current_status: IncidentStatus, target_status: IncidentStatus) -> None:
        msg = f"Cannot transition incident from '{current_status.value}' to '{target_status.value}'."
        super().__init__(msg)
        self.current_status = current_status
        self.target_status = target_status


# Deterministic state machine transition rules
VALID_STATUS_TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    IncidentStatus.OBSERVED: {IncidentStatus.ACTIVE, IncidentStatus.DISMISSED},
    IncidentStatus.ACTIVE: {
        IncidentStatus.ACKNOWLEDGED,
        IncidentStatus.ESCALATED,
        IncidentStatus.RESOLVED,
        IncidentStatus.DISMISSED,
    },
    IncidentStatus.ACKNOWLEDGED: {
        IncidentStatus.ESCALATED,
        IncidentStatus.RESOLVED,
        IncidentStatus.DISMISSED,
    },
    IncidentStatus.ESCALATED: {
        IncidentStatus.RESOLVED,
        IncidentStatus.DISMISSED,
    },
    # Terminal states: cannot transition out
    IncidentStatus.RESOLVED: set(),
    IncidentStatus.DISMISSED: set(),
}


# Rule name to incident type deterministic mapping
RULE_INCIDENT_TYPE_MAP: dict[str, IncidentType] = {
    "rule_possible_fall": IncidentType.POSSIBLE_FALL,
    "POSSIBLE_FALL": IncidentType.POSSIBLE_FALL,
    "rule_unauthorized_object_removal": IncidentType.POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL,
    "POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL": IncidentType.POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL,
    "rule_unexpected_presence": IncidentType.UNEXPECTED_PERSON_PRESENCE,
    "UNEXPECTED_PERSON_PRESENCE": IncidentType.UNEXPECTED_PERSON_PRESENCE,
    "rule_high_risk_interaction": IncidentType.POSSIBLE_HIGH_RISK_INTERACTION,
    "POSSIBLE_HIGH_RISK_INTERACTION": IncidentType.POSSIBLE_HIGH_RISK_INTERACTION,
}


class IncidentManager:
    """Manages Incident lifecycle, correlation, deduplication, and authorization."""

    def __init__(
        self,
        storage: EventStorage | None = None,
        authority_service: AuthorityService | None = None,
        audit_logger: AuditLogger | None = None,
        alert_manager: AlertManager | None = None,
        evidence_vault: Any | None = None,
        notification_service: Any | None = None,
        min_incident_risk_score: float = 0.30,
        correlation_window_seconds: float = 120.0,
    ) -> None:
        self.storage = storage or get_event_storage()
        self.authority = authority_service or get_authority_service()
        self.audit = audit_logger or (AuditLogger(storage=self.storage) if storage is not None else get_audit_logger())
        self.alerts = alert_manager or (AlertManager(storage=self.storage) if storage is not None else get_alert_manager())
        self._evidence_vault = evidence_vault
        self._notifications = notification_service
        self.min_incident_risk_score = min_incident_risk_score
        self.correlation_window_seconds = correlation_window_seconds

    @property
    def evidence_vault(self):
        if self._evidence_vault is None:
            from pathlib import Path
            from atlas.evidence.vault import get_evidence_vault
            storage_dir = Path(self.storage.db_path).parent / "evidence"
            self._evidence_vault = get_evidence_vault(storage=self.storage, audit=self.audit, storage_dir=storage_dir)
        return self._evidence_vault

    @property
    def notifications(self):
        if self._notifications is None:
            from atlas.notifications.service import get_notification_service
            self._notifications = get_notification_service(storage=self.storage, audit_logger=self.audit)
        return self._notifications

    def evaluate_decision_context(self, decision: DecisionContext) -> Incident | None:
        """Evaluate pipeline DecisionContext and create or correlate an Incident if threshold is met."""
        # 1. Determine if an incident condition is met
        triggered_rules = [r for r in decision.rule_evaluations if r.condition_satisfied]
        is_elevated_risk = decision.risk.score >= self.min_incident_risk_score

        if not triggered_rules and not is_elevated_risk:
            # Baseline ambient activity: do not create incident
            return None

        # 2. Determine primary IncidentType
        primary_type = IncidentType.GENERAL_SAFETY_REVIEW
        triggering_rule_ids = []

        if triggered_rules:
            # Pick highest priority / first triggered rule
            first_rule = triggered_rules[0]
            triggering_rule_ids = [r.rule_id for r in triggered_rules]
            primary_type = RULE_INCIDENT_TYPE_MAP.get(
                first_rule.rule_id,
                RULE_INCIDENT_TYPE_MAP.get(first_rule.rule_name, IncidentType.GENERAL_SAFETY_REVIEW),
            )

        source_event_ids = list(decision.risk.supporting_event_ids)

        # 3. Check for deduplication / correlation with existing ACTIVE or ACKNOWLEDGED incident
        existing_active = self.storage.get_active_incidents()
        for inc in existing_active:
            if inc.incident_type == primary_type and inc.status in (IncidentStatus.ACTIVE, IncidentStatus.ACKNOWLEDGED):
                # Correlate and update existing incident
                now_iso = datetime.now(timezone.utc).isoformat()
                combined_events = sorted(list(set(inc.source_event_ids + source_event_ids)))

                inc.updated_at = now_iso
                inc.risk_score = max(inc.risk_score, decision.risk.score)
                inc.severity = decision.risk.level.value
                inc.uncertainty = decision.risk.uncertainty
                inc.source_event_ids = combined_events
                inc.analysis_id = decision.analysis_id
                inc.risk_id = decision.risk.risk_id
                inc.triggering_rule_ids = sorted(list(set(inc.triggering_rule_ids + triggering_rule_ids)))

                self.storage.save_incident(inc)
                self.alerts.create_or_update_for_incident(inc)
                self.audit.record(
                    actor_id="system_core",
                    action=AuditAction.INCIDENT_UPDATED,
                    target_type="incident",
                    target_id=inc.incident_id,
                    new_state=inc.status.value,
                    reason=f"Correlated new observations with active incident: {decision.risk.reason}",
                    metadata={"risk_score": inc.risk_score, "analysis_id": decision.analysis_id},
                )
                return inc

        # 4. Create new Incident
        now_iso = datetime.now(timezone.utc).isoformat()
        incident = Incident(
            incident_type=primary_type,
            severity=decision.risk.level.value,
            risk_score=decision.risk.score,
            uncertainty=decision.risk.uncertainty,
            status=IncidentStatus.ACTIVE,
            source_event_ids=source_event_ids,
            analysis_id=decision.analysis_id,
            risk_id=decision.risk.risk_id,
            triggering_rule_ids=triggering_rule_ids,
            created_at=now_iso,
            updated_at=now_iso,
            metadata={
                "reason": decision.risk.reason,
                "human_verification_required": decision.risk.human_verification_required,
            },
        )

        self.storage.save_incident(incident)
        self.alerts.create_or_update_for_incident(incident)

        self.audit.record(
            actor_id="system_core",
            action=AuditAction.INCIDENT_CREATED,
            target_type="incident",
            target_id=incident.incident_id,
            previous_state=IncidentStatus.OBSERVED.value,
            new_state=IncidentStatus.ACTIVE.value,
            reason=decision.risk.reason,
            metadata={
                "incident_type": primary_type.value,
                "risk_score": decision.risk.score,
                "analysis_id": decision.analysis_id,
            },
        )

        # Step 6.4: Privacy-bounded event-triggered evidence capture (Fail-Safe)
        try:
            primary_event_id = source_event_ids[0] if source_event_ids else None
            self.evidence_vault.capture_for_incident(incident=incident, source_event_id=primary_event_id)
        except Exception as e:
            logger.warning("Evidence capture hook failed safely for incident %s: %s", incident.incident_id, e)

        # Step 7: Notifications & Escalation Dispatch (Fail-Safe)
        try:
            self.notifications.process_incident(incident)
        except Exception as e:
            logger.warning("Notification dispatch failed safely for incident %s: %s", incident.incident_id, e)

        return incident

    def _validate_transition(self, current: IncidentStatus, target: IncidentStatus) -> None:
        """Enforce valid deterministic state transitions."""
        allowed = VALID_STATUS_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidTransitionError(current, target)

    def acknowledge_incident(self, incident_id: str, actor: Actor) -> Incident:
        """Acknowledge an active incident by an authorized actor."""
        self.authority.assert_permission(actor, Permission.ACKNOWLEDGE_INCIDENT)

        incident = self.storage.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"Incident '{incident_id}' not found.")

        self._validate_transition(incident.status, IncidentStatus.ACKNOWLEDGED)

        prev_status = incident.status.value
        now_iso = datetime.now(timezone.utc).isoformat()

        incident.status = IncidentStatus.ACKNOWLEDGED
        incident.is_acknowledged = True
        incident.acknowledged_at = now_iso
        incident.acknowledged_by = actor.actor_id
        incident.updated_at = now_iso

        self.storage.save_incident(incident)
        self.alerts.on_incident_acknowledged(incident.incident_id)

        try:
            self.notifications.on_incident_acknowledged(incident, actor=actor)
        except Exception as e:
            logger.warning("Notification on_incident_acknowledged failed safely: %s", e)

        self.audit.record(
            actor_id=actor.actor_id,
            action=AuditAction.INCIDENT_ACKNOWLEDGED,
            target_type="incident",
            target_id=incident.incident_id,
            previous_state=prev_status,
            new_state=IncidentStatus.ACKNOWLEDGED.value,
            reason=f"Acknowledged by {actor.display_name or actor.actor_id}",
        )

        return incident

    def resolve_incident(
        self,
        incident_id: str,
        actor: Actor,
        reason: ResolutionReason | str,
        notes: str | None = None,
    ) -> Incident:
        """Resolve an incident with mandatory classified resolution reason."""
        self.authority.assert_permission(actor, Permission.RESOLVE_INCIDENT)

        incident = self.storage.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"Incident '{incident_id}' not found.")

        # Validate resolution reason
        if not reason:
            raise ValueError("Resolution reason is required and cannot be empty.")
        reason_enum = reason if isinstance(reason, ResolutionReason) else ResolutionReason(reason)

        self._validate_transition(incident.status, IncidentStatus.RESOLVED)

        prev_status = incident.status.value
        now_iso = datetime.now(timezone.utc).isoformat()

        incident.status = IncidentStatus.RESOLVED
        incident.is_resolved = True
        incident.resolved_at = now_iso
        incident.resolved_by = actor.actor_id
        incident.resolution_reason = reason_enum
        incident.resolution_notes = notes
        incident.updated_at = now_iso

        self.storage.save_incident(incident)
        self.alerts.on_incident_resolved(incident.incident_id)

        try:
            self.notifications.on_incident_resolved(incident, actor=actor)
        except Exception as e:
            logger.warning("Notification on_incident_resolved failed safely: %s", e)

        self.audit.record(
            actor_id=actor.actor_id,
            action=AuditAction.INCIDENT_RESOLVED,
            target_type="incident",
            target_id=incident.incident_id,
            previous_state=prev_status,
            new_state=IncidentStatus.RESOLVED.value,
            reason=f"Resolved as {reason_enum.value}: {notes or ''}".strip(),
            metadata={"resolution_reason": reason_enum.value, "notes": notes},
        )

        return incident

    def dismiss_incident(
        self,
        incident_id: str,
        actor: Actor,
        reason: str,
    ) -> Incident:
        """Dismiss an incident without deleting its history."""
        self.authority.assert_permission(actor, Permission.DISMISS_INCIDENT)

        if not reason or not reason.strip():
            raise ValueError("Dismissal reason is required.")

        incident = self.storage.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"Incident '{incident_id}' not found.")

        self._validate_transition(incident.status, IncidentStatus.DISMISSED)

        prev_status = incident.status.value
        now_iso = datetime.now(timezone.utc).isoformat()

        incident.status = IncidentStatus.DISMISSED
        incident.updated_at = now_iso
        incident.metadata["dismissal_reason"] = reason.strip()
        incident.metadata["dismissed_by"] = actor.actor_id

        self.storage.save_incident(incident)
        self.alerts.on_incident_dismissed(incident.incident_id)

        try:
            self.notifications.on_incident_dismissed(incident, actor=actor)
        except Exception as e:
            logger.warning("Notification on_incident_dismissed failed safely: %s", e)

        self.audit.record(
            actor_id=actor.actor_id,
            action=AuditAction.INCIDENT_DISMISSED,
            target_type="incident",
            target_id=incident.incident_id,
            previous_state=prev_status,
            new_state=IncidentStatus.DISMISSED.value,
            reason=f"Dismissed: {reason.strip()}",
        )

        return incident

    def request_escalation(
        self,
        incident_id: str,
        actor: Actor,
        reason: str,
    ) -> Incident:
        """Request response escalation for an incident (requires authorization)."""
        incident = self.storage.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"Incident '{incident_id}' not found.")

        if incident.status in (IncidentStatus.RESOLVED, IncidentStatus.DISMISSED):
            raise InvalidTransitionError(incident.status, IncidentStatus.ESCALATED)

        now_iso = datetime.now(timezone.utc).isoformat()
        incident.escalation_state = EscalationState.PENDING_AUTHORIZATION
        incident.updated_at = now_iso
        self.storage.save_incident(incident)

        self.audit.record(
            actor_id=actor.actor_id,
            action=AuditAction.AUTHORIZATION_REQUESTED,
            target_type="incident",
            target_id=incident.incident_id,
            previous_state=EscalationState.NOT_ESCALATED.value,
            new_state=EscalationState.PENDING_AUTHORIZATION.value,
            reason=reason,
        )

        return incident

    def authorize_escalation(
        self,
        incident_id: str,
        actor: Actor,
        reason: str | None = None,
    ) -> Incident:
        """Authorize response escalation (restricted to human ADMIN with AUTHORIZE_RESPONSE)."""
        if getattr(actor, "is_system", False) or actor.actor_id == "system_core":
            raise UnauthorizedError(
                actor_id=actor.actor_id,
                permission=Permission.AUTHORIZE_RESPONSE,
                message="System authority cannot authorize response escalation. Human administrative authorization is required.",
            )
        self.authority.assert_permission(actor, Permission.AUTHORIZE_RESPONSE)

        incident = self.storage.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"Incident '{incident_id}' not found.")

        if incident.status in (IncidentStatus.RESOLVED, IncidentStatus.DISMISSED):
            raise InvalidTransitionError(incident.status, IncidentStatus.ESCALATED)

        self._validate_transition(incident.status, IncidentStatus.ESCALATED)

        prev_status = incident.status.value
        now_iso = datetime.now(timezone.utc).isoformat()

        incident.status = IncidentStatus.ESCALATED
        incident.escalation_state = EscalationState.AUTHORIZED
        incident.escalated_at = now_iso
        incident.escalated_by = actor.actor_id
        incident.updated_at = now_iso

        self.storage.save_incident(incident)
        self.alerts.create_or_update_for_incident(incident)

        try:
            self.notifications.process_incident(incident)
        except Exception as e:
            logger.warning("Notification dispatch failed safely for escalated incident %s: %s", incident.incident_id, e)

        self.audit.record(
            actor_id=actor.actor_id,
            action=AuditAction.AUTHORIZATION_GRANTED,
            target_type="incident",
            target_id=incident.incident_id,
            previous_state=EscalationState.PENDING_AUTHORIZATION.value,
            new_state=EscalationState.AUTHORIZED.value,
            reason=reason or "Response escalation authorized by Administrator.",
        )
        self.audit.record(
            actor_id=actor.actor_id,
            action=AuditAction.INCIDENT_ESCALATED,
            target_type="incident",
            target_id=incident.incident_id,
            previous_state=prev_status,
            new_state=IncidentStatus.ESCALATED.value,
            reason=reason or "Incident state escalated.",
        )

        return incident

    def get_incident(self, incident_id: str) -> Incident | None:
        return self.storage.get_incident(incident_id)

    def get_active_incidents(self) -> list[Incident]:
        return self.storage.get_active_incidents()

    def get_recent_incidents(self, limit: int = 50) -> list[Incident]:
        return self.storage.get_recent_incidents(limit=limit)


_global_incident_manager: IncidentManager | None = None


def get_incident_manager(storage: EventStorage | None = None, reload: bool = False) -> IncidentManager:
    """Singleton getter for IncidentManager."""
    global _global_incident_manager
    if _global_incident_manager is None or reload or storage is not None:
        _global_incident_manager = IncidentManager(storage=storage)
    return _global_incident_manager
