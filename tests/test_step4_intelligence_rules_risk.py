"""Comprehensive Unit Tests for Step 4:
LLM Verifier, Deterministic Safety Rule Engine, and Risk Assessment.

NOTE: All tests use deterministic test fixtures/providers (MockTestLLMProvider).
Test-only mocks never run in live execution.
"""

from __future__ import annotations

import time
import pytest

from atlas.events.schema import ATLASEvent, EventSeverity, EventStatus, EventType
from atlas.events.storage import EventStorage
from atlas.intelligence.pipeline import IntelligencePipeline
from atlas.intelligence.provider import MockTestLLMProvider
from atlas.intelligence.schemas import EvidenceContext, LLMVerificationResult
from atlas.intelligence.verifier import LLMVerifier
from atlas.risk.engine import RiskEngine
from atlas.risk.schema import RiskAssessment, RiskLevel
from atlas.rules.definitions import (
    HighRiskInteractionRule,
    PossibleFallRule,
    UnauthorizedObjectRemovalRule,
    UnexpectedPersonPresenceRule,
)
from atlas.rules.engine import DeterministicRuleEngine


def make_event(
    event_id: str,
    event_type: str,
    timestamp: str,
    person_id: str | None = None,
    object_id: str | None = None,
    confidence: float = 0.85,
    status: str = "ACTIVE",
    severity: str = "INFO",
) -> ATLASEvent:
    return ATLASEvent(
        event_id=event_id,
        event_type=event_type,
        source="atlas_home_camera",
        source_device="atlas_home",
        timestamp=timestamp,
        status=status,
        severity=severity,
        confidence=confidence,
        person_id=person_id,
        object_id=object_id,
    )


# ============================================================================
# 1. LLM Verifier & Schema Tests
# ============================================================================

def test_llm_schema_validation_success():
    mock_provider = MockTestLLMProvider(
        default_response={
            "situation": "Person observed in living area",
            "interpretation": "Standard daytime domestic presence",
            "supporting_event_ids": ["ev-1", "ev-2"],
            "evidence_summary": ["Person entered at 12:00"],
            "confidence": 0.88,
            "uncertainty": "low",
            "verification_required": False,
            "possible_conditions": ["normal_activity"],
            "contradicting_evidence": [],
        }
    )
    verifier = LLMVerifier(provider=mock_provider, cooldown_seconds=0.0)

    evidence = EvidenceContext(
        timestamp="2026-09-09T12:00:00Z",
        active_events=[{"event_id": "ev-1"}, {"event_id": "ev-2"}],
    )
    result = verifier.verify_evidence(evidence, force=True)

    assert result.status == "completed"
    assert result.confidence == 0.88
    assert result.uncertainty == "low"
    assert result.supporting_event_ids == ["ev-1", "ev-2"]
    assert result.verification_required is False


def test_llm_grounding_filters_hallucinated_ids():
    # Provider claims an event ID that is NOT in the input evidence
    mock_provider = MockTestLLMProvider(
        default_response={
            "situation": "Activity observed",
            "interpretation": "Interpretation claiming non-existent events",
            "supporting_event_ids": ["real-id", "hallucinated-id-999"],
            "confidence": 0.70,
            "uncertainty": "moderate",
            "verification_required": True,
        }
    )
    verifier = LLMVerifier(provider=mock_provider, cooldown_seconds=0.0)

    evidence = EvidenceContext(
        timestamp="2026-09-09T12:00:00Z",
        active_events=[{"event_id": "real-id"}],
    )
    result = verifier.verify_evidence(evidence, force=True)

    # Hallucinated ID must be filtered out
    assert "real-id" in result.supporting_event_ids
    assert "hallucinated-id-999" not in result.supporting_event_ids


def test_llm_malformed_response_handling():
    # Provider returns bad types (e.g. confidence > 1.0 or invalid uncertainty)
    mock_provider = MockTestLLMProvider(
        default_response={
            "situation": "Test",
            "confidence": 5.5,  # Invalid: must be <= 1.0
            "uncertainty": "unknown_uncertainty_level",
        }
    )
    verifier = LLMVerifier(provider=mock_provider, cooldown_seconds=0.0)
    evidence = EvidenceContext(timestamp="2026-09-09T12:00:00Z")

    result = verifier.verify_evidence(evidence, force=True)
    assert result.status == "malformed_rejected"
    assert result.uncertainty == "high"
    assert result.verification_required is True


def test_llm_provider_failure_fallback():
    mock_provider = MockTestLLMProvider(should_fail=True, error_message="Network Timeout")
    verifier = LLMVerifier(provider=mock_provider, cooldown_seconds=0.0)
    evidence = EvidenceContext(timestamp="2026-09-09T12:00:00Z")

    result = verifier.verify_evidence(evidence, force=True)
    assert result.status == "unavailable"
    assert result.uncertainty == "high"
    assert "Network Timeout" in (result.error_message or "")
    assert result.confidence == 0.0


def test_llm_provider_check_health():
    # 1. Mock provider healthy
    healthy_mock = MockTestLLMProvider()
    h = healthy_mock.check_health()
    assert h["network"] == "CONNECTED"
    assert h["authenticated"] is True
    assert h["status_code"] == 200

    # 2. Mock provider failing
    failing_mock = MockTestLLMProvider(should_fail=True, error_message="Service Down")
    h_fail = failing_mock.check_health()
    assert h_fail["network"] == "DISCONNECTED"
    assert h_fail["authenticated"] is False
    assert "Service Down" in h_fail["error"]

    # 3. Unconfigured OpenAI provider
    from atlas.intelligence.provider import OpenAICompatibleProvider
    unconfigured = OpenAICompatibleProvider(api_key=None)
    h_unconf = unconfigured.check_health()
    assert h_unconf["network"] == "UNCONFIGURED"
    assert h_unconf["authenticated"] is False


def test_ollama_provider_health_check_success_and_model_not_found():
    from unittest.mock import MagicMock, patch
    import io
    from atlas.intelligence.provider import OllamaProvider

    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:3b")
    assert provider.provider_name == "ollama(qwen2.5:3b)"
    assert provider.is_configured is True

    # Case A: Model found in tags
    mock_resp_success = MagicMock()
    mock_resp_success.getcode.return_value = 200
    mock_resp_success.read.return_value = b'{"models": [{"name": "qwen2.5:3b:latest", "model": "qwen2.5:3b"}]}'
    mock_resp_success.__enter__.return_value = mock_resp_success

    with patch("urllib.request.urlopen", return_value=mock_resp_success):
        h = provider.check_health()
        assert h["network"] == "CONNECTED"
        assert h["authenticated"] is True
        assert h["model_available"] is True
        assert h["error"] is None

    # Case B: Model NOT found in tags
    mock_resp_not_found = MagicMock()
    mock_resp_not_found.getcode.return_value = 200
    mock_resp_not_found.read.return_value = b'{"models": [{"name": "llama3:latest", "model": "llama3"}]}'
    mock_resp_not_found.__enter__.return_value = mock_resp_not_found

    with patch("urllib.request.urlopen", return_value=mock_resp_not_found):
        h2 = provider.check_health()
        assert h2["network"] == "CONNECTED"
        assert h2["model_available"] is False
        assert "MODEL_NOT_FOUND" in h2["error"]

    # Case C: Ollama service unavailable
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        h3 = provider.check_health()
        assert h3["network"] == "DISCONNECTED"
        assert h3["model_available"] is False
        assert "Connection refused" in h3["error"]


def test_ollama_generate_and_code_fence_stripping():
    from unittest.mock import MagicMock, patch
    from atlas.intelligence.provider import OllamaProvider, extract_and_parse_json

    # Test code fence stripping helper
    fenced = '```json\n{"situation": "walking", "confidence": 0.9}\n```'
    parsed = extract_and_parse_json(fenced)
    assert parsed["situation"] == "walking"
    assert parsed["confidence"] == 0.9

    # Test generate with code fence in Ollama message content
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:3b")
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.read.return_value = b'{"message": {"content": "```json\\n{\\"situation\\": \\"person sitting\\", \\"confidence\\": 0.85, \\"uncertainty\\": \\"low\\", \\"supporting_event_ids\\": [\\"ev-1\\"]}\\n```"}}'
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res, err = provider.generate("system prompt", "user prompt")
        assert err is None
        assert res is not None
        assert res["situation"] == "person sitting"
        assert res["confidence"] == 0.85


def test_ollama_verifier_integration_and_grounding():
    from unittest.mock import MagicMock, patch
    from atlas.intelligence.provider import OllamaProvider
    from atlas.intelligence.verifier import LLMVerifier
    from atlas.intelligence.schemas import EvidenceContext

    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:3b")
    verifier = LLMVerifier(provider=provider, cooldown_seconds=0.0)

    # Provider returns one real event and one hallucinated event
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.read.return_value = b'{"message": {"content": "{\\"situation\\": \\"person standing\\", \\"interpretation\\": \\"normal\\", \\"confidence\\": 0.88, \\"uncertainty\\": \\"low\\", \\"verification_required\\": false, \\"supporting_event_ids\\": [\\"real-ev-1\\", \\"fake-ev-99\\"]}"}}'
    mock_resp.__enter__.return_value = mock_resp

    evidence = EvidenceContext(
        timestamp="2026-09-09T16:00:00Z",
        active_events=[{"event_id": "real-ev-1"}],
    )

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = verifier.verify_evidence(evidence, force=True)
        assert res.status == "completed"
        assert res.confidence == 0.88
        assert "real-ev-1" in res.supporting_event_ids
        # Hallucinated ID must be filtered out by grounding
        assert "fake-ev-99" not in res.supporting_event_ids


def test_ollama_provider_configuration():
    from atlas.config.settings import Settings
    from atlas.intelligence.verifier import create_llm_provider

    s = Settings()
    s.llm_provider = "ollama"
    s.llm_base_url = "http://localhost:11434"
    s.llm_model = "qwen2.5:3b"
    s.llm_api_key = None

    provider = create_llm_provider(s)
    assert provider.provider_name == "ollama(qwen2.5:3b)"
    assert provider.is_configured is True
    d = s.to_dict()
    assert d["llm_api_key"] is None
    assert d["llm_configured"] is True


def test_ollama_timeout_handling():
    from unittest.mock import patch
    from atlas.intelligence.provider import OllamaProvider

    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:3b", timeout_seconds=1.0)
    with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
        res, err = provider.generate("sys", "user")
        assert res is None
        assert err is not None
        assert "timed out" in err.lower()


def test_ollama_malformed_missing_fields_rejection():
    from unittest.mock import MagicMock, patch
    from atlas.intelligence.provider import OllamaProvider
    from atlas.intelligence.verifier import LLMVerifier
    from atlas.intelligence.schemas import EvidenceContext

    provider = OllamaProvider()
    verifier = LLMVerifier(provider=provider, cooldown_seconds=0.0)

    # Missing required field 'interpretation'
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.read.return_value = b'{"message": {"content": "{\\"situation\\": \\"walking\\", \\"confidence\\": 0.8, \\"uncertainty\\": \\"low\\"}"}}'
    mock_resp.__enter__.return_value = mock_resp

    evidence = EvidenceContext(timestamp="2026-09-09T16:00:00Z")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = verifier.verify_evidence(evidence, force=True)
        assert res.status == "malformed_rejected"
        assert res.confidence == 0.0
        assert res.uncertainty == "high"
        assert res.verification_required is True


def test_ollama_failure_safe_fallback_in_pipeline(tmp_path):
    from unittest.mock import patch
    import urllib.error
    from atlas.intelligence.provider import OllamaProvider
    from atlas.intelligence.verifier import LLMVerifier
    from atlas.rules.engine import DeterministicRuleEngine
    from atlas.risk.engine import RiskEngine
    from atlas.events.storage import EventStorage
    from atlas.intelligence.pipeline import IntelligencePipeline

    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:3b")
    verifier = LLMVerifier(provider=provider, cooldown_seconds=0.0)
    rule_engine = DeterministicRuleEngine()
    risk_engine = RiskEngine()
    storage = EventStorage(db_path=str(tmp_path / "test_ollama_fail.db"))

    pipeline = IntelligencePipeline(
        verifier=verifier,
        rule_engine=rule_engine,
        risk_engine=risk_engine,
        storage=storage,
    )

    ev_fall = make_event("ev-fail-1", EventType.PERSON_FALL_LIKE.value, "2026-09-09T12:00:00Z", person_id="1", confidence=0.85)

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        # Pipeline must NOT crash when Ollama is unavailable
        decision = pipeline.run_analysis(
            events=[ev_fall],
            context_data={"active_persons_count": 1, "persons": [{"track_id": 1}]},
            force_llm=True,
        )

        assert decision is not None
        assert decision.llm_verification.status == "unavailable"
        assert decision.llm_verification.confidence == 0.0
        assert decision.llm_verification.uncertainty == "high"
        assert decision.llm_verification.verification_required is True

        assert len(decision.rule_evaluations) >= 4
        assert decision.risk is not None
        assert decision.risk.human_verification_required is True


def test_llm_cooldown_debouncing():
    mock_provider = MockTestLLMProvider(
        default_response={
            "situation": "Call 1",
            "interpretation": "First call",
            "confidence": 0.8,
            "uncertainty": "low",
        }
    )
    verifier = LLMVerifier(provider=mock_provider, cooldown_seconds=10.0)
    evidence = EvidenceContext(timestamp="2026-09-09T12:00:00Z")

    res1 = verifier.verify_evidence(evidence, force=False)
    # Second call within cooldown without force
    res2 = verifier.verify_evidence(evidence, force=False)

    # Provider should only have been invoked once
    assert mock_provider.call_count == 1
    assert res1.verification_id == res2.verification_id


# ============================================================================
# 2. Deterministic Safety Rule Engine Tests
# ============================================================================

def test_fall_rule_matching_with_persistence():
    rule = PossibleFallRule(min_fall_confidence=0.60)
    ev_fall = make_event("e-1", EventType.PERSON_FALL_LIKE.value, "2026-09-09T12:00:01Z", person_id="4", confidence=0.85)
    ev_stat = make_event("e-2", EventType.PERSON_STATIONARY.value, "2026-09-09T12:00:03Z", person_id="4", confidence=0.90)

    result = rule.evaluate([ev_fall, ev_stat], context={"persons": [{"track_id": 4}]})
    assert result.condition_satisfied is True
    assert result.confidence >= 0.70
    assert "e-1" in result.supporting_event_ids
    assert result.verification_required is True


def test_fall_rule_contradiction_on_rapid_recovery():
    rule = PossibleFallRule(min_fall_confidence=0.60)
    ev_fall = make_event("e-1", EventType.PERSON_FALL_LIKE.value, "2026-09-09T12:00:01Z", person_id="4", confidence=0.75)
    # Immediately after, person gets up and walks normally
    ev_walk = make_event("e-2", EventType.PERSON_WALKING.value, "2026-09-09T12:00:04Z", person_id="4", confidence=0.88)

    result = rule.evaluate([ev_fall, ev_walk], context={"persons": [{"track_id": 4}]})
    # Person recovered normally; condition is negated
    assert result.condition_satisfied is False
    assert len(result.contradicting_evidence) > 0
    assert "resumed" in result.contradicting_evidence[0]


def test_unauthorized_object_removal_rule():
    rule = UnauthorizedObjectRemovalRule()
    # 1. Object moved by person
    ev_move = make_event("e-1", EventType.OBJECT_MOVED.value, "2026-09-09T12:00:01Z", person_id="2", object_id="10")
    # 2. Person leaves
    ev_leave = make_event("e-2", EventType.PERSON_LEFT.value, "2026-09-09T12:00:05Z", person_id="2")

    result = rule.evaluate([ev_move, ev_leave], context={})
    assert result.condition_satisfied is True
    assert result.rule_name == "POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL"
    assert "e-1" in result.supporting_event_ids
    assert "e-2" in result.supporting_event_ids

    # If object was returned to stationary state, condition is negated
    ev_return = make_event("e-3", EventType.OBJECT_STATIONARY.value, "2026-09-09T12:00:06Z", object_id="10")
    result_with_return = rule.evaluate([ev_move, ev_leave, ev_return], context={})
    assert result_with_return.condition_satisfied is False
    assert len(result_with_return.contradicting_evidence) > 0


def test_unexpected_person_presence_rule():
    rule = UnexpectedPersonPresenceRule()
    ev_enter = make_event("e-1", EventType.PERSON_ENTERED.value, "2026-09-09T12:00:01Z", person_id="7")

    result = rule.evaluate([ev_enter], context={"active_persons_count": 1})
    assert result.condition_satisfied is True
    assert result.rule_name == "UNEXPECTED_PERSON_PRESENCE"
    assert result.verification_required is True


def test_high_risk_interaction_rule_conservative_behavior():
    rule = HighRiskInteractionRule()
    # Single person detection: rule must NOT trigger
    ev_fall = make_event("e-1", EventType.PERSON_FALL_LIKE.value, "2026-09-09T12:00:01Z", person_id="1")
    result = rule.evaluate([ev_fall], context={"persons": [{"track_id": 1}]})
    assert result.condition_satisfied is False
    assert "Conservative threshold not met" in result.details.get("status", "")


# ============================================================================
# 3. Risk Engine Tests
# ============================================================================

def test_risk_score_and_threshold_mapping():
    risk_engine = RiskEngine()
    rule_engine = DeterministicRuleEngine()

    # Case A: Ambient activity, no triggered rules
    evals_ambient = rule_engine.evaluate_all(events=[], context={"active_persons_count": 0, "persons": []})
    risk_ambient = risk_engine.assess_risk(evals_ambient)
    assert risk_ambient.level == RiskLevel.LOW
    assert risk_ambient.score < 0.35

    # Case B: Possible Fall triggered
    ev_fall = make_event("e-1", EventType.PERSON_FALL_LIKE.value, "2026-09-09T12:00:01Z", person_id="3", confidence=0.85)
    ev_stat = make_event("e-2", EventType.PERSON_STATIONARY.value, "2026-09-09T12:00:03Z", person_id="3", confidence=0.90)
    evals_fall = rule_engine.evaluate_all(events=[ev_fall, ev_stat], context={"active_persons_count": 1, "persons": [{"track_id": 3}]})
    risk_fall = risk_engine.assess_risk(evals_fall)

    assert risk_fall.level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert risk_fall.score >= 0.65
    assert risk_fall.human_verification_required is True
    assert "rule_possible_fall" in risk_fall.supporting_rule_ids


def test_risk_source_health_degradation_elevates_uncertainty():
    risk_engine = RiskEngine()
    degraded_health = {
        "camera": {"is_connected": False, "state": "DISCONNECTED"},
        "perception": {"status": "inactive"},
    }
    risk = risk_engine.assess_risk(rule_evaluations=[], source_health=degraded_health)
    assert risk.uncertainty == "high"
    assert risk.human_verification_required is True
    assert "degraded" in risk.reason


# ============================================================================
# 4. End-to-End Pipeline & SQLite Persistence Tests
# ============================================================================

def test_pipeline_execution_and_sqlite_persistence(tmp_path):
    db_file = tmp_path / "test_pipeline.db"
    storage = EventStorage(db_path=str(db_file))

    mock_provider = MockTestLLMProvider(
        default_response={
            "situation": "Living room fall-like posture detected",
            "interpretation": "Possible domestic fall requiring check",
            "supporting_event_ids": ["ev-100"],
            "evidence_summary": ["Person 9 went stationary"],
            "confidence": 0.82,
            "uncertainty": "moderate",
            "verification_required": True,
            "possible_conditions": ["possible_fall"],
            "contradicting_evidence": [],
        }
    )
    verifier = LLMVerifier(provider=mock_provider, cooldown_seconds=0.0)
    rule_engine = DeterministicRuleEngine()
    risk_engine = RiskEngine()

    pipeline = IntelligencePipeline(
        verifier=verifier,
        rule_engine=rule_engine,
        risk_engine=risk_engine,
        storage=storage,
    )

    ev_fall = make_event("ev-100", EventType.PERSON_FALL_LIKE.value, "2026-09-09T12:00:00Z", person_id="9", confidence=0.88)
    ev_stat = make_event("ev-101", EventType.PERSON_STATIONARY.value, "2026-09-09T12:00:03Z", person_id="9", confidence=0.90)

    decision = pipeline.run_analysis(
        events=[ev_fall, ev_stat],
        context_data={"active_persons_count": 1, "persons": [{"track_id": 9}]},
        force_llm=True,
    )

    assert decision.analysis_id is not None
    assert decision.risk.level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert decision.risk.human_verification_required is True

    # Check persistence in SQLite
    latest_stored = storage.get_latest_analysis()
    assert latest_stored is not None
    assert latest_stored["analysis_id"] == decision.analysis_id
    assert latest_stored["risk"]["level"] == decision.risk.level.value

    recent_stored = storage.get_recent_analyses(limit=5)
    assert len(recent_stored) == 1
