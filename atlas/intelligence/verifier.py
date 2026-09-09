"""ATLAS LLM Verifier and Structured Interpretation Engine."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any

from atlas.config.settings import get_settings
from atlas.intelligence.prompts import SYSTEM_PROMPT, format_evidence_prompt
from atlas.intelligence.provider import (
    BaseLLMProvider,
    OpenAICompatibleProvider,
    OllamaProvider,
)
from atlas.intelligence.schemas import EvidenceContext, LLMVerificationResult

logger = logging.getLogger("atlas.intelligence.verifier")

# ---------------------------------------------------------------------------
# Uncertainty label map used by _normalize_raw_json.
# Models sometimes return integers (0/1/2) or floats (0.0/0.5/1.0) for
# the 'uncertainty' field instead of the required string literal.
# ---------------------------------------------------------------------------
_UNCERTAINTY_INT_MAP: dict[int, str] = {
    0: "high",      # 0 ≡ most uncertain
    1: "moderate",
    2: "low",
}
_UNCERTAINTY_FLOAT_THRESHOLDS = [
    (0.34, "high"),     # 0.0 – 0.33  → high
    (0.67, "moderate"), # 0.34 – 0.66 → moderate
    (1.01, "low"),      # 0.67 – 1.0  → low
]
_VALID_UNCERTAINTY = frozenset({"low", "moderate", "high"})

_CONFIDENCE_WORD_MAP: dict[str, float] = {
    "low": 0.3,
    "moderate": 0.6,
    "medium": 0.6,
    "high": 0.9,
    "very high": 0.95,
    "very low": 0.1,
}


def _normalize_raw_json(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce known LLM output degeneracies into Pydantic-valid values.

    This runs BEFORE Pydantic model construction.  It does NOT weaken the
    schema — it translates semantically-equivalent but type-mismatched model
    outputs into the exact types the schema expects.

    Specifically handles:
    - uncertainty: int / float  → "low" | "moderate" | "high" string
    - uncertainty: unknown str  → "moderate" (safe default)
    - confidence: word string   → mapped float
    - confidence: out-of-range  → clamped to [0.0, 1.0]
    - evidence_summary / possible_conditions / contradicting_evidence:
        bare string             → single-element list
    """
    result = dict(raw)

    # --- uncertainty ---
    unc = result.get("uncertainty")
    if unc is not None:
        if isinstance(unc, str):
            if unc.lower() in _VALID_UNCERTAINTY:
                result["uncertainty"] = unc.lower()
            else:
                logger.debug(
                    "LLM returned unknown uncertainty string %r, normalising to 'moderate'", unc
                )
                result["uncertainty"] = "moderate"
        elif isinstance(unc, int):
            result["uncertainty"] = _UNCERTAINTY_INT_MAP.get(unc, "moderate")
            logger.debug(
                "LLM returned integer uncertainty %r, normalised to %r",
                unc, result["uncertainty"],
            )
        elif isinstance(unc, float):
            for threshold, label in _UNCERTAINTY_FLOAT_THRESHOLDS:
                if unc < threshold:
                    result["uncertainty"] = label
                    break
            else:
                result["uncertainty"] = "moderate"
            logger.debug(
                "LLM returned float uncertainty %r, normalised to %r",
                unc, result["uncertainty"],
            )

    # --- confidence ---
    conf = result.get("confidence")
    if conf is not None:
        if isinstance(conf, str):
            mapped = _CONFIDENCE_WORD_MAP.get(conf.lower().strip())
            if mapped is not None:
                result["confidence"] = mapped
                logger.debug(
                    "LLM returned string confidence %r, normalised to %r", conf, mapped
                )
            else:
                # Try to parse it as a float string (e.g. "0.85")
                try:
                    result["confidence"] = max(0.0, min(1.0, float(conf)))
                except (ValueError, TypeError):
                    result["confidence"] = 0.5
                    logger.debug(
                        "LLM returned unparseable confidence string %r, defaulting to 0.5", conf
                    )
        elif isinstance(conf, (int, float)):
            result["confidence"] = max(0.0, min(1.0, float(conf)))

    # --- list fields that models sometimes return as bare strings ---
    for field_name in ("evidence_summary", "possible_conditions", "contradicting_evidence"):
        val = result.get(field_name)
        if isinstance(val, str):
            result[field_name] = [val] if val else []
            logger.debug(
                "LLM returned bare string for '%s', wrapped into list", field_name
            )

    return result


def create_llm_provider(settings: Any | None = None) -> BaseLLMProvider:
    """Instantiate appropriate LLM provider based on settings."""
    if settings is None:
        settings = get_settings()
    if getattr(settings, "llm_provider", "").lower() == "ollama":
        return OllamaProvider(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return OpenAICompatibleProvider(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


class LLMVerifier:
    """Consumes structured evidence, invokes LLM provider, and enforces structured output.
    
    LOCKED PRINCIPLE:
    The verifier output is an interpretation with explicit uncertainty, NOT an emergency directive.
    """

    def __init__(
        self,
        provider: BaseLLMProvider | None = None,
        cooldown_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        self.provider = provider or create_llm_provider(settings)
        self.cooldown_seconds = (
            cooldown_seconds if cooldown_seconds is not None else settings.analysis_cooldown_seconds
        )
        self._lock = threading.Lock()
        self._last_analysis_time: float = 0.0
        self._last_result: LLMVerificationResult | None = None
        self._history: list[LLMVerificationResult] = []

    def verify_evidence(
        self,
        evidence: EvidenceContext,
        force: bool = False,
    ) -> LLMVerificationResult:
        """Run LLM interpretation on structured evidence with debouncing and safe fallback."""
        now_ts = time.time()
        iso_now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            # Check cooldown unless forced
            if not force and (now_ts - self._last_analysis_time < self.cooldown_seconds):
                if self._last_result is not None:
                    return self._last_result

            self._last_analysis_time = now_ts

        # Collect valid known event IDs from input evidence for grounding check
        valid_event_ids = set()
        for ev in evidence.active_events:
            if isinstance(ev, dict) and "event_id" in ev:
                valid_event_ids.add(ev["event_id"])
        for ev in evidence.recent_events:
            if isinstance(ev, dict) and "event_id" in ev:
                valid_event_ids.add(ev["event_id"])

        # Check if provider is configured
        if not self.provider.is_configured:
            fallback = self._create_fallback_result(
                timestamp=iso_now,
                reason="LLM provider unconfigured (no API key provided). Operating in deterministic rule-only mode.",
                status="unavailable",
                valid_event_ids=valid_event_ids,
            )
            self._record_result(fallback)
            return fallback

        user_prompt = format_evidence_prompt(evidence)
        raw_json, error_msg = self.provider.generate(SYSTEM_PROMPT, user_prompt)

        if error_msg or raw_json is None:
            fallback = self._create_fallback_result(
                timestamp=iso_now,
                reason=f"LLM provider error: {error_msg or 'No response'}",
                status="unavailable",
                valid_event_ids=valid_event_ids,
            )
            self._record_result(fallback)
            return fallback

        # Validate against schema and ground event IDs
        try:
            if not isinstance(raw_json, dict):
                raise ValueError("LLM response is not a JSON object")

            # Check required fields exist in raw_json
            required_fields = ["situation", "interpretation", "confidence", "uncertainty"]
            for rf in required_fields:
                if rf not in raw_json or raw_json[rf] is None:
                    raise ValueError(f"Missing required field '{rf}' in LLM response")

            # Normalise type-mismatched but semantically valid LLM output fields
            # BEFORE constructing the Pydantic model.  This prevents spurious
            # malformed_rejected outcomes for integer/float uncertainty, word-
            # valued confidence, and bare-string list fields.
            normalised = _normalize_raw_json(raw_json)

            # Grounding: filter supporting_event_ids so only actual evidence IDs are retained
            claimed_ids = normalised.get("supporting_event_ids", [])
            if not isinstance(claimed_ids, list):
                claimed_ids = []
            grounded_ids = [eid for eid in claimed_ids if eid in valid_event_ids]

            result = LLMVerificationResult(
                timestamp=iso_now,
                situation=str(normalised["situation"]),
                interpretation=str(normalised["interpretation"]),
                supporting_event_ids=grounded_ids,
                evidence_summary=list(normalised.get("evidence_summary", [])),
                confidence=float(normalised["confidence"]),
                uncertainty=normalised["uncertainty"],
                verification_required=bool(normalised.get("verification_required", True)),
                possible_conditions=list(normalised.get("possible_conditions", [])),
                contradicting_evidence=list(normalised.get("contradicting_evidence", [])),
                status="completed",
                model_used=self.provider.provider_name,
            )
        except Exception as exc:
            logger.warning("LLM response failed schema validation: %s", exc)
            fallback = self._create_fallback_result(
                timestamp=iso_now,
                reason=f"LLM output rejected (schema validation failed: {exc})",
                status="malformed_rejected",
                valid_event_ids=valid_event_ids,
            )
            self._record_result(fallback)
            return fallback

        self._record_result(result)
        return result

    def _create_fallback_result(
        self,
        timestamp: str,
        reason: str,
        status: str,
        valid_event_ids: set[str],
    ) -> LLMVerificationResult:
        """Construct safe fallback verification maintaining explicit high uncertainty."""
        return LLMVerificationResult(
            timestamp=timestamp,
            situation="Automated LLM interpretation unavailable.",
            interpretation=f"Verification pending deterministic safety rules. {reason}",
            supporting_event_ids=list(valid_event_ids)[:5],
            evidence_summary=["Structured event stream active without LLM interpretation."],
            confidence=0.0,
            uncertainty="high",
            verification_required=True,
            possible_conditions=[],
            contradicting_evidence=[],
            status=status,  # type: ignore
            model_used=self.provider.provider_name,
            error_message=reason,
        )

    def _record_result(self, result: LLMVerificationResult) -> None:
        with self._lock:
            self._last_result = result
            self._history.append(result)
            if len(self._history) > 100:
                self._history.pop(0)

    def get_latest_result(self) -> LLMVerificationResult | None:
        with self._lock:
            return self._last_result

    def get_recent_results(self, limit: int = 20) -> list[LLMVerificationResult]:
        with self._lock:
            return list(self._history)[-limit:]


_global_verifier: LLMVerifier | None = None


def get_verifier() -> LLMVerifier:
    """Get or initialize singleton LLMVerifier."""
    global _global_verifier
    if _global_verifier is None:
        _global_verifier = LLMVerifier()
    return _global_verifier
