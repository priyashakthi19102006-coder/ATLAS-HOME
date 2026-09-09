"""ATLAS Multi-Source Context & Evidence Fusion Layer (Step 6.5)."""

from atlas.fusion.engine import (
    FusionEngine,
    get_fusion_engine,
    reset_global_fusion_engine,
)
from atlas.fusion.schema import (
    EntityAssociationStatus,
    EntityCorrelation,
    EvidenceRelationship,
    FusedSituation,
    Observation,
    SourceAvailability,
    SourceType,
    TemporalCorrelation,
)
from atlas.fusion.sources import (
    SourceRegistry,
    get_source_registry,
    reset_global_source_registry,
)

__all__ = [
    "SourceType",
    "SourceAvailability",
    "EvidenceRelationship",
    "EntityAssociationStatus",
    "Observation",
    "TemporalCorrelation",
    "EntityCorrelation",
    "FusedSituation",
    "SourceRegistry",
    "get_source_registry",
    "reset_global_source_registry",
    "FusionEngine",
    "get_fusion_engine",
    "reset_global_fusion_engine",
]
