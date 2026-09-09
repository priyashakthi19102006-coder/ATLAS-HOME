"""Real Ollama Provider Test Script (Harmless Schema & Connectivity Test).

Runs a real request against local Ollama (qwen2.5:3b at http://localhost:11434).
DO NOT claim real camera evidence was verified from this test.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from atlas.intelligence.provider import OllamaProvider
from atlas.intelligence.verifier import LLMVerifier
from atlas.intelligence.schemas import EvidenceContext


def main() -> int:
    print("=" * 65)
    print("REAL OLLAMA PROVIDER TEST")
    print("Diagnostic: Local Ollama (qwen2.5:3b) Connectivity & Schema Test")
    print("=" * 65)

    # 1. Health Check
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:3b")
    health = provider.check_health()

    reachable = health["network"] == "CONNECTED"
    model_available = health["model_available"]

    print(f"Ollama Reachable:    {'YES [PASS]' if reachable else 'NO [FAIL]'}")
    print(f"Model Available:     {'YES [PASS]' if model_available else 'NO [FAIL]'}")
    print(f"HTTP Status:         {health.get('status_code')}")
    if health.get("error"):
        print(f"Health Error:        {health['error']}")

    if not (reachable and model_available):
        print("\n[FAIL] Ollama is not reachable or model qwen2.5:3b is missing.")
        return 1

    # 2. Real Structured Evidence Request
    verifier = LLMVerifier(provider=provider, cooldown_seconds=0.0)
    now_iso = datetime.now(timezone.utc).isoformat()

    valid_event_id = "test-sample-ev-401"
    evidence = EvidenceContext(
        timestamp=now_iso,
        active_events=[
            {
                "event_id": valid_event_id,
                "event_type": "PERSON_ENTERED",
                "timestamp": now_iso,
                "person_id": "1",
                "status": "ACTIVE",
                "confidence": 0.92,
            }
        ],
        recent_events=[],
        recent_context={"active_persons_count": 1, "active_objects_count": 0},
        persons=[{"track_id": 1, "label": "person", "confidence": 0.92}],
        objects=[],
        relationships=[],
        source_health={"camera": {"is_connected": True}, "perception": {"status": "active"}},
    )

    print("\n[test] Sending structured EvidenceContext to local Ollama...")
    result = verifier.verify_evidence(evidence, force=True)

    request_succeeded = result.status == "completed"
    response_parsed = result.status in ("completed", "malformed_rejected")
    schema_validated = result.status == "completed"

    # Verify grounding: any supporting IDs in the result MUST belong to valid_event_id
    grounding_validated = all(eid == valid_event_id for eid in result.supporting_event_ids)

    print(f"Request Succeeded:   {'YES [PASS]' if request_succeeded else 'NO [FAIL]'}")
    print(f"Response Parsed:     {'YES [PASS]' if response_parsed else 'NO [FAIL]'}")
    print(f"Schema Validated:    {'YES [PASS]' if schema_validated else 'NO [FAIL]'}")
    print(f"Grounding Validated: {'YES [PASS]' if grounding_validated else 'NO [FAIL]'}")

    print("\n--- Ollama Verification Output ---")
    print(f"Status:                {result.status}")
    print(f"Situation:             {result.situation}")
    print(f"Interpretation:        {result.interpretation}")
    print(f"Confidence:            {result.confidence:.2f}")
    print(f"Uncertainty:           {result.uncertainty}")
    print(f"Verification Required: {result.verification_required}")
    print(f"Supporting Event IDs:  {result.supporting_event_ids}")
    print(f"Evidence Summary:      {result.evidence_summary}")
    print(f"Possible Conditions:   {result.possible_conditions}")
    print(f"Contradicting Ev:      {result.contradicting_evidence}")
    print(f"Model Used:            {result.model_used}")
    print("=" * 65)

    if request_succeeded and schema_validated and grounding_validated:
        print("REAL OLLAMA PROVIDER TEST: PASSED")
        return 0
    else:
        print("REAL OLLAMA PROVIDER TEST: FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
