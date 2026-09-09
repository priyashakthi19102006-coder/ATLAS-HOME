"""Audit logger service for recording append-only audit events."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Sequence

from atlas.audit.schema import AuditAction, AuditRecord
from atlas.events.storage import EventStorage, get_event_storage

logger = logging.getLogger("atlas.audit.service")


class AuditLogger:
    """Service for appending immutable audit records to persistent SQLite storage."""

    def __init__(self, storage: EventStorage | None = None) -> None:
        self.storage = storage or get_event_storage()

    def record(
        self,
        actor_id: str,
        action: AuditAction | str,
        target_type: str,
        target_id: str,
        previous_state: str | None = None,
        new_state: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Create and persist an immutable audit record."""
        action_enum = action if isinstance(action, AuditAction) else AuditAction(action)
        entry = AuditRecord(
            actor_id=actor_id,
            action=action_enum,
            target_type=target_type,
            target_id=target_id,
            previous_state=previous_state,
            new_state=new_state,
            reason=reason,
            metadata=metadata or {},
        )

        try:
            self.storage.save_audit_record(entry)
        except Exception as exc:
            logger.error("Failed to persist audit log entry: %s", exc)

        return entry

    def get_records_for_target(self, target_id: str, limit: int = 50) -> list[AuditRecord]:
        """Query audit trail for a specific target entity."""
        return self.storage.get_audit_records_for_target(target_id, limit=limit)

    def get_recent_records(self, limit: int = 50) -> list[AuditRecord]:
        """Query recent system-wide audit records."""
        return self.storage.get_recent_audit_records(limit=limit)


_global_audit_logger: AuditLogger | None = None


def get_audit_logger(storage: EventStorage | None = None, reload: bool = False) -> AuditLogger:
    """Singleton getter for AuditLogger."""
    global _global_audit_logger
    if _global_audit_logger is None or reload or storage is not None:
        _global_audit_logger = AuditLogger(storage=storage)
    return _global_audit_logger
