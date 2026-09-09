"""Step 5.1 Hardening Tests -- LLM Structured Output Normalization.

10 surgical, deterministic tests verifying:
- _normalize_raw_json() coerces integer/float/unknown uncertainty to valid literals
- _normalize_raw_json() coerces word/out-of-range confidence to valid floats
- _normalize_raw_json() wraps bare-string list fields into lists
- LLMVerifier returns status="completed" (not malformed_rejected) when model
  returns integer uncertainty or word-valued confidence
- Locked principle docstring is preserved in verifier module

All tests are fully deterministic -- zero live LLM calls.
"""
from __future__ import annotations

import pytest

from atlas.intelligence.verifier import LLMVerifier, _normalize_raw_json
from atlas.intelligence.provider import MockTestLLMProvider
from atlas.intelligence.schemas import EvidenceContext, LLMVerificationResult


# ---------------------------------------------------------------------------
# Minimal helpers
# ---------------------------------------------------------------------------

def _make_evidence() -> EvidenceContext:
    """Return a minimal, valid EvidenceContext for verifier tests."""
    return EvidenceContext(
        timestamp="2026-09-09T18:00:00Z",
        active_events=[
            {
                "event_id": "ev-h-001",
                "event_type": "PERSON_MOVING",
                "person_id": 1,
                "timestamp": "2026-09-09T18:00:00Z",
                "confidence": 0.85,
                "source": "webcam",
                "status": "ACTIVE",
            }
        ],
        recent_events=[],
        recent_context={"active_persons_count": 1, "active_objects_count": 0},
        persons=[{"person_id": 1}],
        objects=[],
        relationships=[],
        source_health={"camera": {"is_connected": True}},
    )


def _base_valid_response() -> dict:
    """Canonical structurally valid model response."""
    return {
        "situation": "One person is moving.",
        "interpretation": "Normal presence observed.",
        "supporting_event_ids": ["ev-h-001"],
        "evidence_summary": ["Person moving tracked."],
        "confidence": 0.80,
        "uncertainty": "moderate",
        "verification_required": True,
        "possible_conditions": ["normal_activity"],
        "contradicting_evidence": [],
    }


# ===========================================================================
# 1-7: _normalize_raw_json unit tests
# ===========================================================================

def test_1_normalize_uncertainty_int_0_to_high():
    """uncertainty=0 (integer) must be normalised to 'high'."""
    raw = {**_base_valid_response(), "uncertainty": 0}
    result = _normalize_raw_json(raw)
    assert result["uncertainty"] == "high", (
        f"Expected 'high', got {result['uncertainty']!r}"
    )


def test_2_normalize_uncertainty_int_1_to_moderate():
    """uncertainty=1 (integer) must be normalised to 'moderate'."""
    raw = {**_base_valid_response(), "uncertainty": 1}
    result = _normalize_raw_json(raw)
    assert result["uncertainty"] == "moderate", (
        f"Expected 'moderate', got {result['uncertainty']!r}"
    )


def test_3_normalize_uncertainty_int_2_to_low():
    """uncertainty=2 (integer) must be normalised to 'low'."""
    raw = {**_base_valid_response(), "uncertainty": 2}
    result = _normalize_raw_json(raw)
    assert result["uncertainty"] == "low", (
        f"Expected 'low', got {result['uncertainty']!r}"
    )


def test_4_normalize_uncertainty_unknown_string_to_moderate():
    """uncertainty with an unknown string value must be normalised to 'moderate'."""
    for bad_val in ("unknown", "none", "very_uncertain"):
        raw = {**_base_valid_response(), "uncertainty": bad_val}
        result = _normalize_raw_json(raw)
        assert result["uncertainty"] == "moderate", (
            f"Expected 'moderate' for {bad_val!r}, got {result['uncertainty']!r}"
        )


def test_5_normalize_confidence_word_string():
    """confidence with a word value must be mapped to the expected float."""
    cases = {
        "high": 0.9,
        "medium": 0.6,
        "moderate": 0.6,
        "low": 0.3,
        "very high": 0.95,
        "very low": 0.1,
    }
    for word, expected in cases.items():
        raw = {**_base_valid_response(), "confidence": word}
        result = _normalize_raw_json(raw)
        assert result["confidence"] == pytest.approx(expected, abs=1e-9), (
            f"confidence word '{word}' expected {expected}, got {result['confidence']}"
        )


def test_6_normalize_confidence_out_of_range_clamped():
    """confidence > 1.0 must be clamped to 1.0; confidence < 0.0 to 0.0."""
    raw_high = {**_base_valid_response(), "confidence": 1.5}
    raw_low = {**_base_valid_response(), "confidence": -0.2}
    assert _normalize_raw_json(raw_high)["confidence"] == pytest.approx(1.0)
    assert _normalize_raw_json(raw_low)["confidence"] == pytest.approx(0.0)


def test_7_normalize_bare_string_list_fields():
    """evidence_summary, possible_conditions, contradicting_evidence returned as bare
    strings must each be wrapped into a single-element list."""
    raw = {
        **_base_valid_response(),
        "evidence_summary": "Person detected moving.",
        "possible_conditions": "normal_activity",
        "contradicting_evidence": "No contradicting evidence.",
    }
    result = _normalize_raw_json(raw)
    assert result["evidence_summary"] == ["Person detected moving."]
    assert result["possible_conditions"] == ["normal_activity"]
    assert result["contradicting_evidence"] == ["No contradicting evidence."]


# ===========================================================================
# 8-9: Full LLMVerifier integration -- degenerate model output must complete
# ===========================================================================

def test_8_verifier_integer_uncertainty_yields_completed():
    """When the model returns integer uncertainty, verifier must return status='completed'
    (not 'malformed_rejected') because _normalize_raw_json handles the coercion."""
    bad_response = {**_base_valid_response(), "uncertainty": 0, "confidence": 1}
    provider = MockTestLLMProvider(default_response=bad_response)
    verifier = LLMVerifier(provider=provider, cooldown_seconds=0.0)

    result = verifier.verify_evidence(_make_evidence(), force=True)

    assert result.status == "completed", (
        f"Expected status='completed', got {result.status!r}. "
        f"error_message={result.error_message!r}"
    )
    assert result.uncertainty == "high"
    assert result.confidence == pytest.approx(1.0)
    assert result.error_message is None


def test_9_verifier_word_confidence_yields_completed():
    """When the model returns confidence='high' (word), verifier must return
    status='completed' with confidence=0.9."""
    bad_response = {**_base_valid_response(), "confidence": "high", "uncertainty": "low"}
    provider = MockTestLLMProvider(default_response=bad_response)
    verifier = LLMVerifier(provider=provider, cooldown_seconds=0.0)

    result = verifier.verify_evidence(_make_evidence(), force=True)

    assert result.status == "completed", (
        f"Expected status='completed', got {result.status!r}. "
        f"error_message={result.error_message!r}"
    )
    assert result.confidence == pytest.approx(0.9)
    assert result.uncertainty == "low"
    assert result.error_message is None


# ===========================================================================
# 10: LOCKED PRINCIPLE docstring preservation check
# ===========================================================================

def test_10_verifier_locked_principle_docstring_preserved():
    """The 'LOCKED PRINCIPLE' docstring must remain in LLMVerifier to document
    that verifier output cannot directly trigger emergency actions."""
    import atlas.intelligence.verifier as verifier_module
    class_doc = LLMVerifier.__doc__ or ""
    assert "LOCKED PRINCIPLE" in class_doc, (
        "LLMVerifier class docstring must preserve the 'LOCKED PRINCIPLE' statement."
    )
    assert hasattr(verifier_module, "_UNCERTAINTY_INT_MAP"), \
        "_UNCERTAINTY_INT_MAP must be defined at module level"
    assert hasattr(verifier_module, "_VALID_UNCERTAINTY"), \
        "_VALID_UNCERTAINTY must be defined at module level"
    assert hasattr(verifier_module, "_normalize_raw_json"), \
        "_normalize_raw_json must be exported from the verifier module"
