"""Deterministic Safety Rule Definitions for ATLAS Home."""

from __future__ import annotations

import abc
from datetime import datetime, timezone
import logging
from typing import Any, Sequence

from atlas.events.schema import ATLASEvent, EventType
from atlas.intelligence.schemas import LLMVerificationResult
from atlas.rules.schema import RuleEvaluationResult

logger = logging.getLogger("atlas.rules.definitions")


class BaseSafetyRule(abc.ABC):
    """Abstract base class for all deterministic safety rules."""

    def __init__(self, rule_id: str, rule_name: str, enabled: bool = True) -> None:
        self.rule_id = rule_id
        self.rule_name = rule_name
        self.enabled = enabled

    @abc.abstractmethod
    def evaluate(
        self,
        events: Sequence[ATLASEvent],
        context: dict[str, Any],
        llm_verification: LLMVerificationResult | None = None,
    ) -> RuleEvaluationResult:
        """Evaluate the rule against deterministic event facts, context, and LLM verification."""
        pass


class PossibleFallRule(BaseSafetyRule):
    """Evaluates fall-like safety conditions.
    
    Sequence evaluation:
    - Triggered by PERSON_FALL_LIKE observation.
    - Verified by post-event persistence in stationary or low-motion posture.
    - Contradicted if the person promptly recovers into normal walking.
    """

    def __init__(
        self,
        min_fall_confidence: float = 0.60,
        recovery_window_seconds: float = 10.0,
    ) -> None:
        super().__init__(
            rule_id="rule_possible_fall",
            rule_name="POSSIBLE_FALL",
            enabled=True,
        )
        self.min_fall_confidence = min_fall_confidence
        self.recovery_window_seconds = recovery_window_seconds

    def evaluate(
        self,
        events: Sequence[ATLASEvent],
        context: dict[str, Any],
        llm_verification: LLMVerificationResult | None = None,
    ) -> RuleEvaluationResult:
        now_iso = datetime.now(timezone.utc).isoformat()

        # Find all fall-like events
        fall_events = [
            e for e in events
            if e.event_type == EventType.PERSON_FALL_LIKE.value
            and (e.confidence or 0.0) >= self.min_fall_confidence
        ]

        if not fall_events:
            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                timestamp=now_iso,
                condition_satisfied=False,
                confidence=0.0,
                supporting_event_ids=[],
                missing_evidence=["PERSON_FALL_LIKE observation exceeding confidence threshold"],
                contradicting_evidence=[],
                verification_required=False,
                details={"status": "No fall-like events observed"},
            )

        latest_fall = fall_events[-1]
        target_pid = latest_fall.person_id
        supporting_ids = [latest_fall.event_id]
        contradicting = []
        missing = []

        # Analyze subsequent events for the same person track
        subsequent_events = [
            e for e in events
            if e.person_id == target_pid
            and e.timestamp > latest_fall.timestamp
        ]

        # Check for recovery: walking or normal standing
        has_recovered = False
        for sub in subsequent_events:
            if sub.event_type in (EventType.PERSON_WALKING.value, EventType.PERSON_STANDING.value):
                has_recovered = True
                contradicting.append(
                    f"Person track {target_pid} resumed {sub.event_type} shortly after fall-like motion."
                )
                supporting_ids.append(sub.event_id)

        # Check for persistence: stationary or sitting
        is_persistent = False
        for sub in subsequent_events:
            if sub.event_type in (EventType.PERSON_STATIONARY.value, EventType.PERSON_SITTING.value):
                is_persistent = True
                supporting_ids.append(sub.event_id)

        if not subsequent_events:
            missing.append("Post-fall temporal persistence observations")

        # LLM opinion corroboration check (without letting LLM make decision)
        llm_corroboration = False
        if llm_verification and llm_verification.status == "completed":
            if "possible_fall" in llm_verification.possible_conditions:
                llm_corroboration = True

        # Deterministic condition satisfaction
        if has_recovered:
            # Clear contradiction observed: person got back up
            condition_satisfied = False
            confidence = 0.20
        elif is_persistent or (not subsequent_events and (latest_fall.confidence or 0.0) >= 0.80):
            condition_satisfied = True
            confidence = min(1.0, (latest_fall.confidence or 0.70) + (0.10 if llm_corroboration else 0.0))
        else:
            condition_satisfied = False
            confidence = 0.40
            missing.append("Sufficient post-event persistence verification")

        return RuleEvaluationResult(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            timestamp=now_iso,
            condition_satisfied=condition_satisfied,
            confidence=round(confidence, 2),
            supporting_event_ids=supporting_ids,
            missing_evidence=missing,
            contradicting_evidence=contradicting,
            verification_required=condition_satisfied or bool(fall_events),
            details={
                "target_person_id": target_pid,
                "fall_confidence": latest_fall.confidence,
                "has_recovered": has_recovered,
                "is_persistent": is_persistent,
            },
        )


class UnauthorizedObjectRemovalRule(BaseSafetyRule):
    """Evaluates possible unauthorized object removal.
    
    Terminology: Always 'POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL', never 'THEFT_CONFIRMED'.
    Requires:
    - Object was observed stationary, then moved (OBJECT_MOVED).
    - Associated with nearby person track.
    - Subsequent departure (PERSON_LEFT or OBJECT_LEFT).
    """

    def __init__(self) -> None:
        super().__init__(
            rule_id="rule_unauthorized_object_removal",
            rule_name="POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL",
            enabled=True,
        )

    def evaluate(
        self,
        events: Sequence[ATLASEvent],
        context: dict[str, Any],
        llm_verification: LLMVerificationResult | None = None,
    ) -> RuleEvaluationResult:
        now_iso = datetime.now(timezone.utc).isoformat()

        # Find object moved events with person association
        moved_events = [
            e for e in events
            if e.event_type == EventType.OBJECT_MOVED.value
            and e.person_id is not None
        ]

        if not moved_events:
            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                timestamp=now_iso,
                condition_satisfied=False,
                confidence=0.0,
                supporting_event_ids=[],
                missing_evidence=["OBJECT_MOVED event associated with a person track"],
                contradicting_evidence=[],
                verification_required=False,
                details={"status": "No associated object movements observed"},
            )

        latest_moved = moved_events[-1]
        obj_id = latest_moved.object_id
        person_id = latest_moved.person_id
        supporting_ids = [latest_moved.event_id]
        missing = []
        contradicting = []

        # Check if person or object subsequently left the monitored space
        departure_events = [
            e for e in events
            if (e.event_type == EventType.PERSON_LEFT.value and e.person_id == person_id)
            or (e.event_type == EventType.OBJECT_LEFT.value and e.object_id == obj_id)
        ]

        has_departure = bool(departure_events)
        for dep in departure_events:
            supporting_ids.append(dep.event_id)

        # Check for stationary return (contradiction: object was put back down)
        stationary_returns = [
            e for e in events
            if e.event_type == EventType.OBJECT_STATIONARY.value
            and e.object_id == obj_id
            and e.timestamp > latest_moved.timestamp
        ]
        if stationary_returns:
            for st in stationary_returns:
                contradicting.append(f"Object {obj_id} returned to stationary state.")
                supporting_ids.append(st.event_id)

        if not has_departure:
            missing.append("Entity departure from scene (person or object left)")

        # Satisfaction logic
        if stationary_returns:
            condition_satisfied = False
            confidence = 0.20
        elif has_departure:
            condition_satisfied = True
            confidence = 0.75
        else:
            condition_satisfied = False
            confidence = 0.45

        return RuleEvaluationResult(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            timestamp=now_iso,
            condition_satisfied=condition_satisfied,
            confidence=round(confidence, 2),
            supporting_event_ids=supporting_ids,
            missing_evidence=missing,
            contradicting_evidence=contradicting,
            verification_required=condition_satisfied or (has_departure and not stationary_returns),
            details={
                "object_id": obj_id,
                "associated_person_id": person_id,
                "has_departure": has_departure,
                "has_stationary_return": bool(stationary_returns),
            },
        )


class UnexpectedPersonPresenceRule(BaseSafetyRule):
    """Evaluates unexpected presence in the monitored home environment.
    
    Terminology: Always 'UNEXPECTED_PERSON_PRESENCE', never 'INTRUDER_CONFIRMED'.
    """

    def __init__(self) -> None:
        super().__init__(
            rule_id="rule_unexpected_presence",
            rule_name="UNEXPECTED_PERSON_PRESENCE",
            enabled=True,
        )

    def evaluate(
        self,
        events: Sequence[ATLASEvent],
        context: dict[str, Any],
        llm_verification: LLMVerificationResult | None = None,
    ) -> RuleEvaluationResult:
        now_iso = datetime.now(timezone.utc).isoformat()

        # Check for person presence in active context or recent enter events
        enter_events = [e for e in events if e.event_type == EventType.PERSON_ENTERED.value]
        active_persons_count = context.get("active_persons_count", len([
            p for p in context.get("persons", []) if p.get("active", True)
        ]))

        if active_persons_count == 0 and not enter_events:
            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                timestamp=now_iso,
                condition_satisfied=False,
                confidence=0.0,
                supporting_event_ids=[],
                missing_evidence=["Active person presence in monitored zone"],
                contradicting_evidence=[],
                verification_required=False,
                details={"status": "No persons present"},
            )

        supporting_ids = [e.event_id for e in enter_events[-3:]]
        # In current prototype stage before authentication layer, presence is flagged for verification
        return RuleEvaluationResult(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            timestamp=now_iso,
            condition_satisfied=True,
            confidence=0.70,
            supporting_event_ids=supporting_ids,
            missing_evidence=["User authentication / identity verification (Step 5 pending)"],
            contradicting_evidence=[],
            verification_required=True,
            details={
                "active_persons_count": active_persons_count,
                "note": "Unauthenticated presence requires human verification.",
            },
        )


class HighRiskInteractionRule(BaseSafetyRule):
    """Conservative framework for assessing high-risk interaction between entities.
    
    LOCKED PRINCIPLE:
    DO NOT declare abuse, violence, or criminal acts from ambiguous visual detections.
    If evidence is insufficient, condition_satisfied remains False.
    """

    def __init__(self) -> None:
        super().__init__(
            rule_id="rule_high_risk_interaction",
            rule_name="POSSIBLE_HIGH_RISK_INTERACTION",
            enabled=True,
        )

    def evaluate(
        self,
        events: Sequence[ATLASEvent],
        context: dict[str, Any],
        llm_verification: LLMVerificationResult | None = None,
    ) -> RuleEvaluationResult:
        now_iso = datetime.now(timezone.utc).isoformat()

        active_persons = context.get("persons", [])
        # Conservative requirement: multiple persons with corroborated fall-like / abnormal motion
        fall_events = [e for e in events if e.event_type == EventType.PERSON_FALL_LIKE.value]

        if len(active_persons) < 2 or not fall_events:
            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                timestamp=now_iso,
                condition_satisfied=False,
                confidence=0.0,
                supporting_event_ids=[],
                missing_evidence=[
                    "Multi-entity close interaction with concurrent corroborated abnormal impact"
                ],
                contradicting_evidence=[],
                verification_required=False,
                details={"status": "Conservative threshold not met; no evidence of high-risk interaction."},
            )

        # Even with multi-entity + fall, visual perception alone is inconclusive
        return RuleEvaluationResult(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            timestamp=now_iso,
            condition_satisfied=False,
            confidence=0.35,
            supporting_event_ids=[e.event_id for e in fall_events],
            missing_evidence=["Acoustic or wearable corroboration (camera perception inconclusive)"],
            contradicting_evidence=[],
            verification_required=True,
            details={"status": "Ambiguous interaction; human verification required."},
        )
