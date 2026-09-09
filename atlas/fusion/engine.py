"""Multi-Source Context & Evidence Fusion Engine for ATLAS Home (Step 6.5).

Correlates normalized observations temporally and entity-wise, classifies evidence
agreement/contradiction conservatively, and aggregates multi-source context
without assuming all subsystems are connected or fabricating synthetic data.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Optional

from atlas.events.schema import ATLASEvent
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
from atlas.fusion.sources import SourceRegistry, get_source_registry

logger = logging.getLogger("atlas.fusion.engine")


class FusionEngine:
    """Bounded, multi-source context & evidence fusion engine."""

    def __init__(
        self,
        registry: SourceRegistry | None = None,
        storage: Any | None = None,
        max_observations: int = 300,
        max_fusions: int = 100,
        retention_window_seconds: float = 120.0,
    ) -> None:
        self.registry = registry or get_source_registry()
        self._storage = storage
        self.retention_window_seconds = retention_window_seconds
        self._lock = threading.Lock()
        self._observations: deque[Observation] = deque(maxlen=max_observations)
        self._fusions: deque[FusedSituation] = deque(maxlen=max_fusions)
        self._seen_obs_ids: set[str] = set()

    @property
    def storage(self) -> Any:
        if self._storage is None:
            from atlas.events.storage import get_event_storage
            self._storage = get_event_storage()
        return self._storage

    def ingest_event(self, event: ATLASEvent) -> Observation:
        """Adapt a standardized ATLASEvent into a normalized Observation."""
        event_id = getattr(event, "event_id", "")
        raw_ts = getattr(event, "timestamp", None)
        if raw_ts:
            if hasattr(raw_ts, "isoformat"):
                observed_at = raw_ts.isoformat()
            else:
                observed_at = str(raw_ts)
        else:
            observed_at = datetime.now(timezone.utc).isoformat()

        raw_ev = getattr(event, "evidence", None)
        if isinstance(raw_ev, dict):
            val_payload = dict(raw_ev)
        elif hasattr(raw_ev, "model_dump"):
            val_payload = raw_ev.model_dump()
        else:
            val_payload = {"severity": getattr(event, "severity", "INFO")}

        conf = getattr(event, "confidence", None)
        confidence = float(conf) if conf is not None else 0.85
        uncertainty = max(0.0, min(1.0, 1.0 - confidence))

        obs = Observation(
            observation_id=f"obs_evt_{event_id[:12]}",
            source_type=SourceType.CAMERA,
            source_id=getattr(event, "source", "camera_0") or "camera_0",
            observed_at=observed_at,
            received_at=datetime.now(timezone.utc).isoformat(),
            observation_type=str(getattr(event, "event_type", "PERCEPTION_EVENT")),
            value=val_payload,
            confidence=confidence,
            uncertainty=uncertainty,
            availability=SourceAvailability.AVAILABLE,
            event_id=event_id,
            track_id=getattr(event, "track_id", None),
            provenance={
                "source_device": getattr(event, "source_device", "atlas_home"),
                "event_id": event_id,
            },
            metadata={
                "severity": getattr(event, "severity", "INFO"),
                "status": getattr(event, "status", "ACTIVE"),
            },
        )
        self.ingest_observation(obs)
        return obs

    def ingest_observation(self, observation: Observation) -> None:
        """Ingest a validated observation with duplicate protection and bounded retention."""
        with self._lock:
            if observation.observation_id in self._seen_obs_ids:
                logger.debug("Duplicate observation %s ignored.", observation.observation_id)
                return

            self._observations.append(observation)
            self._seen_obs_ids.add(observation.observation_id)
            if len(self._seen_obs_ids) > 1000:
                # Keep cache bounded to currently stored observations
                current_ids = {o.observation_id for o in self._observations}
                self._seen_obs_ids = current_ids

    def get_recent_observations(
        self,
        window_seconds: float | None = None,
        limit: int = 50,
    ) -> list[Observation]:
        """Query recent normalized observations within temporal window."""
        win = window_seconds if window_seconds is not None else self.retention_window_seconds
        now_epoch = time.time()
        cutoff = now_epoch - win

        with self._lock:
            res: list[Observation] = []
            for obs in reversed(self._observations):
                try:
                    dt = datetime.fromisoformat(obs.observed_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    obs_epoch = dt.timestamp()
                except Exception:
                    obs_epoch = now_epoch

                if obs_epoch >= cutoff:
                    res.append(obs)
                if len(res) >= limit:
                    break
            return res

    def fuse_context(
        self,
        window_seconds: float = 5.0,
        reference_time: float | None = None,
    ) -> FusedSituation:
        """Perform deterministic temporal and entity context fusion across active observations."""
        ref_epoch = reference_time if reference_time is not None else time.time()
        ref_dt = datetime.fromtimestamp(ref_epoch, timezone.utc)
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._lock:
            candidates = list(self._observations)

        # 1. Bounded Temporal Correlation Window
        correlated_obs: list[Observation] = []
        temporal_rels: list[TemporalCorrelation] = []

        for obs in candidates:
            try:
                obs_dt = datetime.fromisoformat(obs.observed_at)
                if obs_dt.tzinfo is None:
                    obs_dt = obs_dt.replace(tzinfo=timezone.utc)
                obs_epoch = obs_dt.timestamp()
            except Exception:
                continue

            delta_sec = abs(obs_epoch - ref_epoch)
            is_within = delta_sec <= window_seconds

            if is_within:
                correlated_obs.append(obs)
                temporal_rels.append(
                    TemporalCorrelation(
                        source_timestamp=obs.observed_at,
                        received_timestamp=obs.received_at,
                        correlation_timestamp=now_iso,
                        temporal_delta_seconds=round(delta_sec, 4),
                        is_within_window=True,
                        source_provenance=obs.provenance,
                    )
                )

        # 2. Source Types & Subsystem Availability
        src_status_list = self.registry.get_sources_status()
        avail_snapshot: dict[str, SourceAvailability] = {
            s["source_type"]: SourceAvailability(s["availability"])
            for s in src_status_list
        }
        source_types = sorted(list({obs.source_type for obs in correlated_obs}), key=lambda x: x.value)

        # 3. Entity Correlation (ByteTrack track_id preservation)
        entity_correlations: list[EntityCorrelation] = []
        track_map: dict[int, list[Observation]] = {}
        for obs in correlated_obs:
            if obs.track_id is not None:
                track_map.setdefault(obs.track_id, []).append(obs)

        for tid, t_obs in track_map.items():
            # Check sources contributing to this track_id
            t_srcs = {o.source_type for o in t_obs}
            if len(t_srcs) > 1:
                # Explicit cross-sensor association verified
                assoc_status = EntityAssociationStatus.ASSOCIATED
                assoc_conf = min(o.confidence for o in t_obs)
            else:
                # Single sensor track or unverified cross-linkage
                assoc_status = EntityAssociationStatus.UNKNOWN
                assoc_conf = t_obs[0].confidence

            entity_correlations.append(
                EntityCorrelation(
                    track_id=tid,
                    source_id=t_obs[0].source_id,
                    association_status=assoc_status,
                    confidence=round(assoc_conf, 3),
                    details={"observation_count": len(t_obs), "source_types": [s.value for s in t_srcs]},
                )
            )

        # 4. Evidence Agreement / Contradiction Classification
        supporting: list[str] = []
        contradictory: list[str] = []
        relationship_state = EvidenceRelationship.INSUFFICIENT

        if not correlated_obs:
            relationship_state = EvidenceRelationship.INSUFFICIENT
            agg_conf = 0.0
            agg_uncertainty = 1.0
            completeness = "INSUFFICIENT"
            summary = "No observations available within correlation window."
        elif len(source_types) <= 1:
            # Single source domain (e.g. CAMERA only)
            relationship_state = (
                EvidenceRelationship.INDEPENDENT if len(correlated_obs) > 1 else EvidenceRelationship.INSUFFICIENT
            )
            supporting = [o.observation_id for o in correlated_obs]
            agg_conf = round(sum(o.confidence for o in correlated_obs) / len(correlated_obs), 3)
            agg_uncertainty = round(max(1.0 - agg_conf, sum(o.uncertainty for o in correlated_obs) / len(correlated_obs)), 3)
            completeness = "SINGLE_SOURCE"
            summary = f"Single-source context ({source_types[0].value if source_types else 'NONE'}) with {len(correlated_obs)} observation(s)."
        else:
            # Multi-source observations present
            # Deterministic conflict detection (e.g., stationary vs high acceleration)
            has_stationary = any(
                "stationary" in str(o.value).lower() or "sitting" in str(o.value).lower()
                for o in correlated_obs
            )
            has_high_motion = any(
                "acceleration" in str(o.value).lower() or "rapid" in str(o.value).lower()
                for o in correlated_obs
            )

            if has_stationary and has_high_motion:
                relationship_state = EvidenceRelationship.CONTRADICTS
                for o in correlated_obs:
                    if "stationary" in str(o.value).lower() or "sitting" in str(o.value).lower():
                        supporting.append(o.observation_id)
                    else:
                        contradictory.append(o.observation_id)
                summary = "Multi-source evidence contradiction detected between motion and stationary observations."
            else:
                relationship_state = EvidenceRelationship.AGREES
                supporting = [o.observation_id for o in correlated_obs]
                summary = f"Multi-source evidence agreement confirmed across {len(source_types)} source subsystems."

            agg_conf = round(sum(o.confidence for o in correlated_obs) / len(correlated_obs), 3)
            agg_uncertainty = round(sum(o.uncertainty for o in correlated_obs) / len(correlated_obs), 3)
            completeness = "COMPLETE" if len(source_types) >= 2 else "PARTIAL"

        fused = FusedSituation(
            created_at=now_iso,
            observation_ids=[o.observation_id for o in correlated_obs],
            source_types=source_types,
            correlated_entities=entity_correlations,
            temporal_relationships=temporal_rels,
            supporting_evidence=supporting,
            contradictory_evidence=contradictory,
            source_availability=avail_snapshot,
            aggregate_confidence=agg_conf,
            uncertainty=agg_uncertainty,
            relationship_state=relationship_state,
            completeness=completeness,
            provenance={
                "evaluated_at": now_iso,
                "window_seconds": window_seconds,
                "reference_timestamp": ref_dt.isoformat(),
                "observation_count": len(correlated_obs),
            },
            situation_summary=summary,
            metadata={
                "active_tracks_count": len(entity_correlations),
                "active_sources_count": len(source_types),
            },
        )

        with self._lock:
            self._fusions.append(fused)

        # Persist to SQLite if storage available
        try:
            if self.storage and hasattr(self.storage, "save_fused_situation"):
                self.storage.save_fused_situation(fused)
        except Exception as e:
            logger.warning("Error persisting FusedSituation to SQLite: %s", e)

        return fused

    def get_latest_fusion(self) -> FusedSituation | None:
        """Retrieve most recent FusedSituation snapshot."""
        with self._lock:
            if self._fusions:
                return self._fusions[-1]
        
        # Fallback to persistent storage
        try:
            if self.storage and hasattr(self.storage, "get_recent_fused_situations"):
                stored = self.storage.get_recent_fused_situations(limit=1)
                if stored:
                    return stored[0]
        except Exception:
            pass
        return None

    def get_recent_fusions(self, limit: int = 20) -> list[FusedSituation]:
        """Retrieve recent FusedSituation snapshots."""
        with self._lock:
            if len(self._fusions) >= limit:
                return list(self._fusions)[-limit:]

        try:
            if self.storage and hasattr(self.storage, "get_recent_fused_situations"):
                return self.storage.get_recent_fused_situations(limit=limit)
        except Exception:
            pass

        with self._lock:
            return list(self._fusions)


_global_fusion_engine: FusionEngine | None = None
_engine_lock = threading.Lock()


def get_fusion_engine(
    registry: SourceRegistry | None = None,
    storage: Any | None = None,
) -> FusionEngine:
    """Retrieve or initialize singleton FusionEngine."""
    global _global_fusion_engine
    if _global_fusion_engine is None:
        with _engine_lock:
            if _global_fusion_engine is None:
                _global_fusion_engine = FusionEngine(
                    registry=registry,
                    storage=storage,
                )
    return _global_fusion_engine


def reset_global_fusion_engine() -> None:
    """Reset global fusion engine for test isolation."""
    global _global_fusion_engine
    with _engine_lock:
        _global_fusion_engine = None
