"""ATLAS Intelligence Module.

Provides LLM-assisted verification and evidence interpretation.
"""

from atlas.intelligence.schemas import EvidenceContext, LLMVerificationResult
from atlas.intelligence.provider import (
    BaseLLMProvider,
    OpenAICompatibleProvider,
    OllamaProvider,
    MockTestLLMProvider,
)
from atlas.intelligence.verifier import LLMVerifier, get_verifier, create_llm_provider
from atlas.intelligence.pipeline import IntelligencePipeline, get_intelligence_pipeline

__all__ = [
    "EvidenceContext",
    "LLMVerificationResult",
    "BaseLLMProvider",
    "OpenAICompatibleProvider",
    "OllamaProvider",
    "MockTestLLMProvider",
    "LLMVerifier",
    "get_verifier",
    "create_llm_provider",
    "IntelligencePipeline",
    "get_intelligence_pipeline",
]
