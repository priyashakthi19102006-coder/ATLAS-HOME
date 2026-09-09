"""Alert management service for internal alert state tracking."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING, Optional

from atlas.alerts.schema import Alert, AlertSeverity, AlertStatus
from atlas.events.storage import EventStorage, get_event_storage

if TYPE_CHECKING:
    from atlas.incidents.schema import Incident

logger = logging.getLogger("atlas.alerts.service")


class AlertManager:
    """Manages internal alert lifecycles and cooldowns without external dispatch."""

    def __init__(self, storage: EventStorage | None = None) -> None:
        self.storage = storage or get_event_storage()

    def create_or_update_for_incident(self, incident: Incident) -> Alert:
        """Create a new alert or correlate with an existing active alert for an incident."""
        existing_alert = self.storage.get_alert_for_incident(incident.incident_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        # Map incident risk level or severity to AlertSeverity
        sev_map = {
            "LOW": AlertSeverity.LOW,
            "MEDIUM": AlertSeverity.MEDIUM,
            "HIGH": AlertSeverity.HIGH,
            "CRITICAL": AlertSeverity.CRITICAL,
        }
        severity = sev_map.get(str(incident.severity).upper(), AlertSeverity.LOW)

        status_str = str(incident.status).upper()
        if existing_alert:
            existing_alert.severity = severity
            existing_alert.updated_at = now_iso
            existing_alert.is_acknowledged = incident.is_acknowledged
            esc_val = incident.escalation_state.value if hasattr(incident.escalation_state, "value") else str(incident.escalation_state)
            existing_alert.is_escalated = esc_val in ("AUTHORIZED", "ESCALATED")
            if incident.is_resolved or "RESOLVED" in status_str:
                existing_alert.status = AlertStatus.RESOLVED
            elif "DISMISSED" in status_str:
                existing_alert.status = AlertStatus.DISMISSED
            elif incident.is_acknowledged or "ACKNOWLEDGED" in status_str:
                existing_alert.status = AlertStatus.ACKNOWLEDGED
            self.storage.save_alert(existing_alert)
            return existing_alert

        init_status = AlertStatus.ACTIVE
        if incident.is_resolved or "RESOLVED" in status_str:
            init_status = AlertStatus.RESOLVED
        elif "DISMISSED" in status_str:
            init_status = AlertStatus.DISMISSED
        elif incident.is_acknowledged or "ACKNOWLEDGED" in status_str:
            init_status = AlertStatus.ACKNOWLEDGED

        esc_val = incident.escalation_state.value if hasattr(incident.escalation_state, "value") else str(incident.escalation_state)
        new_alert = Alert(
            incident_id=incident.incident_id,
            severity=severity,
            created_at=now_iso,
            updated_at=now_iso,
            status=init_status,
            is_acknowledged=incident.is_acknowledged,
            is_escalated=esc_val in ("AUTHORIZED", "ESCALATED"),
            cooldown_seconds=60.0,
        )
        self.storage.save_alert(new_alert)
        return new_alert

    def on_incident_acknowledged(self, incident_id: str) -> None:
        alert = self.storage.get_alert_for_incident(incident_id)
        if alert:
            alert.status = AlertStatus.ACKNOWLEDGED
            alert.is_acknowledged = True
            alert.updated_at = datetime.now(timezone.utc).isoformat()
            self.storage.save_alert(alert)

    def on_incident_resolved(self, incident_id: str) -> None:
        alert = self.storage.get_alert_for_incident(incident_id)
        if alert:
            alert.status = AlertStatus.RESOLVED
            alert.updated_at = datetime.now(timezone.utc).isoformat()
            self.storage.save_alert(alert)

    def on_incident_dismissed(self, incident_id: str) -> None:
        alert = self.storage.get_alert_for_incident(incident_id)
        if alert:
            alert.status = AlertStatus.DISMISSED
            alert.updated_at = datetime.now(timezone.utc).isoformat()
            self.storage.save_alert(alert)

    def get_active_alerts(self) -> list[Alert]:
        return self.storage.get_active_alerts()

    def get_all_alerts(self, limit: int = 50) -> list[Alert]:
        return self.storage.get_recent_alerts(limit=limit)


_global_alert_manager: AlertManager | None = None


def get_alert_manager(storage: EventStorage | None = None, reload: bool = False) -> AlertManager:
    """Singleton getter for AlertManager."""
    global _global_alert_manager
    if _global_alert_manager is None or reload or storage is not None:
        _global_alert_manager = AlertManager(storage=storage)
    return _global_alert_manager
