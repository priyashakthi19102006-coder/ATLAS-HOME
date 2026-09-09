"""Notification and Escalation Service for ATLAS Home (Step 7).

LOCKED PRINCIPLES:
- The notification subsystem consumes already-verified Incident & Risk state.
- Notifications NEVER make safety decisions.
- LLM cannot autonomously send notifications.
- SYSTEM_ACTOR cannot be a human recipient.
- External channels report NOT_CONFIGURED honestly unless configured.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any, Optional, Sequence

from atlas.audit.schema import AuditAction
from atlas.audit.service import AuditLogger, get_audit_logger
from atlas.authority.auth import get_auth_service
from atlas.authority.models import Actor, Permission, Role
from atlas.authority.service import AuthorityService, get_authority_service, UnauthorizedError
from atlas.config.settings import Settings, get_settings
from atlas.events.storage import EventStorage, get_event_storage
from atlas.incidents.schema import Incident, IncidentStatus
from atlas.notifications.escalation import EscalationEngine, EscalationPolicy
from atlas.notifications.providers import ProviderRegistry, get_provider_registry
from atlas.notifications.schema import (
    EscalationStateRecord,
    Notification,
    NotificationChannel,
    NotificationStatus,
    ProviderState,
)

logger = logging.getLogger("atlas.notifications.service")


class NotificationService:
    """Manages notification dispatch, escalation progression, and audit logging."""

    def __init__(
        self,
        storage: EventStorage | None = None,
        audit_logger: AuditLogger | None = None,
        settings: Settings | None = None,
        provider_registry: ProviderRegistry | None = None,
        authority_service: AuthorityService | None = None,
    ) -> None:
        self.storage = storage or get_event_storage()
        self.audit = audit_logger or (AuditLogger(storage=self.storage) if storage is not None else get_audit_logger())
        self.settings = settings or get_settings()
        self.providers = provider_registry or get_provider_registry(self.settings)
        self.authority = authority_service or get_authority_service()
        self.escalation_engine = EscalationEngine(settings=self.settings)

    # ------------------------------------------------------------------
    # Incident Hook: Process Incoming / Updated Incident
    # ------------------------------------------------------------------
    def process_incident(
        self,
        incident: Incident,
        current_time_epoch: float | None = None,
    ) -> list[Notification]:
        """Evaluate incident and dispatch notifications according to severity and escalation state."""
        if not getattr(self.settings, "notifications_enabled", True):
            logger.debug("Notifications disabled in settings; skipping incident %s", incident.incident_id)
            return []

        # Terminal incidents do not generate new notifications
        if incident.is_resolved or incident.status in (IncidentStatus.RESOLVED, IncidentStatus.DISMISSED):
            return []

        # Retrieve or initialize escalation record
        record = self.storage.get_escalation_record(incident.incident_id)
        is_new_escalation = False
        if record is None:
            record = self.escalation_engine.init_escalation_record(incident, current_time_epoch=current_time_epoch)
            self.storage.save_escalation_record(record)
            if record.is_active:
                is_new_escalation = True
                self.audit.record(
                    actor_id="system_core",
                    action=AuditAction.ESCALATION_STARTED,
                    target_type="incident",
                    target_id=incident.incident_id,
                    previous_state="NOT_ESCALATED",
                    new_state=f"LEVEL_{record.current_level}",
                    reason=f"Escalation started for {incident.severity} incident.",
                    metadata={"severity": incident.severity, "level": record.current_level},
                )

        # Check if escalation should advance
        if not is_new_escalation and self.escalation_engine.should_escalate(incident, record, current_time_epoch):
            record = self.escalation_engine.advance_record(record, current_time_epoch)
            self.storage.save_escalation_record(record)
            self.audit.record(
                actor_id="system_core",
                action=AuditAction.ESCALATION_ADVANCED,
                target_type="incident",
                target_id=incident.incident_id,
                previous_state=f"LEVEL_{record.current_level - 1}",
                new_state=f"LEVEL_{record.current_level}",
                reason=f"Unresolved incident advanced to escalation level {record.current_level}.",
                metadata={"severity": incident.severity, "level": record.current_level},
            )

        # Determine eligible recipients and channels
        recipients = EscalationPolicy.get_eligible_recipients(incident.severity, storage=self.storage)
        channels = EscalationPolicy.get_channels_for_severity(incident.severity, self.settings)

        if not recipients:
            logger.debug("No eligible recipients for severity %s on incident %s", incident.severity, incident.incident_id)
            return []

        # Existing notifications for this incident to prevent duplicates
        existing_notifications = self.storage.get_notifications_for_incident(incident.incident_id)
        existing_keys = {
            (n.incident_id, n.escalation_level, n.recipient_user_id, n.channel)
            for n in existing_notifications
        }

        created_notifications: list[Notification] = []

        # Privacy-safe title & summary
        inc_type_str = incident.incident_type.value if hasattr(incident.incident_type, "value") else str(incident.incident_type)
        safe_title = f"ATLAS Alert: {incident.severity} - {inc_type_str}"
        safe_summary = f"High-risk ATLAS Home situation detected ({inc_type_str}). Open operational console to review verified evidence."

        for recipient in recipients:
            if recipient.is_system:
                continue

            for channel in channels:
                dedup_key = (incident.incident_id, record.current_level, recipient.actor_id, channel)
                if dedup_key in existing_keys:
                    logger.debug("Duplicate notification prevented: %s", dedup_key)
                    continue

                notification = Notification(
                    incident_id=incident.incident_id,
                    recipient_user_id=recipient.actor_id,
                    recipient_role=recipient.role or Role.VIEWER,
                    severity=str(incident.severity).upper(),
                    channel=channel,
                    status=NotificationStatus.PENDING,
                    title=safe_title,
                    summary=safe_summary,
                    escalation_level=record.current_level,
                    attempt_count=0,
                    max_retries=getattr(self.settings, "notification_retry_count", 3),
                )

                self.audit.record(
                    actor_id="system_core",
                    action=AuditAction.NOTIFICATION_CREATED,
                    target_type="notification",
                    target_id=notification.notification_id,
                    previous_state="NONE",
                    new_state=NotificationStatus.PENDING.value,
                    reason=f"Notification created for recipient {recipient.actor_id} via {channel.value}",
                    metadata={"recipient": recipient.actor_id, "channel": channel.value, "level": record.current_level},
                )

                # Dispatch via provider
                provider = self.providers.get_provider(channel)
                if provider is None or provider.get_state() == ProviderState.NOT_CONFIGURED:
                    notification.attempt_count = 1
                    notification.status = NotificationStatus.FAILED
                    notification.failure_reason = "MOBILE PUSH NOT CONFIGURED" if channel in (NotificationChannel.MOBILE_PUSH, NotificationChannel.WEB_PUSH) else f"Provider for channel {channel.value} is NOT_CONFIGURED."
                    self.storage.save_notification(notification)
                    self.audit.record(
                        actor_id="system_core",
                        action=AuditAction.NOTIFICATION_PROVIDER_UNAVAILABLE,
                        target_type="notification",
                        target_id=notification.notification_id,
                        reason=notification.failure_reason,
                        metadata={"channel": channel.value},
                    )
                    self.audit.record(
                        actor_id="system_core",
                        action=AuditAction.NOTIFICATION_FAILED,
                        target_type="notification",
                        target_id=notification.notification_id,
                        previous_state=NotificationStatus.PENDING.value,
                        new_state=NotificationStatus.FAILED.value,
                        reason=notification.failure_reason,
                        metadata={"recipient": recipient.actor_id, "channel": channel.value},
                    )
                    created_notifications.append(notification)
                    existing_keys.add(dedup_key)
                    continue

                # Attempt send
                notification.attempt_count += 1
                self.audit.record(
                    actor_id="system_core",
                    action=AuditAction.NOTIFICATION_ATTEMPTED,
                    target_type="notification",
                    target_id=notification.notification_id,
                    previous_state=NotificationStatus.PENDING.value,
                    new_state=NotificationStatus.PENDING.value,
                    reason=f"Attempting dispatch via {channel.value} to {recipient.actor_id}",
                    metadata={"recipient": recipient.actor_id, "channel": channel.value, "attempt": notification.attempt_count},
                )
                result = provider.send(notification, recipient)
                notification.sent_at = datetime.now(timezone.utc).isoformat()
                if result.success:
                    notification.status = NotificationStatus.DELIVERED
                    self.audit.record(
                        actor_id="system_core",
                        action=AuditAction.NOTIFICATION_SENT,
                        target_type="notification",
                        target_id=notification.notification_id,
                        previous_state=NotificationStatus.PENDING.value,
                        new_state=NotificationStatus.SENT.value,
                        reason=f"Notification sent via {channel.value}",
                        metadata={"recipient": recipient.actor_id, "channel": channel.value},
                    )
                    self.audit.record(
                        actor_id="system_core",
                        action=AuditAction.NOTIFICATION_DELIVERED,
                        target_type="notification",
                        target_id=notification.notification_id,
                        previous_state=NotificationStatus.SENT.value,
                        new_state=NotificationStatus.DELIVERED.value,
                        reason=f"Notification delivered via {channel.value}",
                        metadata={"recipient": recipient.actor_id, "channel": channel.value},
                    )
                else:
                    notification.status = NotificationStatus.FAILED
                    notification.failure_reason = result.failure_reason or "Provider delivery error."
                    self.audit.record(
                        actor_id="system_core",
                        action=AuditAction.NOTIFICATION_FAILED,
                        target_type="notification",
                        target_id=notification.notification_id,
                        previous_state=NotificationStatus.PENDING.value,
                        new_state=NotificationStatus.FAILED.value,
                        reason=notification.failure_reason,
                        metadata={"recipient": recipient.actor_id, "channel": channel.value},
                    )

                self.storage.save_notification(notification)
                created_notifications.append(notification)
                existing_keys.add(dedup_key)

        record.notifications_sent_count += len(created_notifications)
        self.storage.save_escalation_record(record)
        return created_notifications

    # ------------------------------------------------------------------
    # Incident Lifecycle Hooks
    # ------------------------------------------------------------------
    def on_incident_acknowledged(self, incident: Incident, actor: Actor | None = None) -> None:
        """Handle incident acknowledgement: stop active escalation and mark notifications."""
        record = self.storage.get_escalation_record(incident.incident_id)
        if record and record.is_active:
            record = self.escalation_engine.stop_record(
                record,
                reason=f"Incident acknowledged by {actor.actor_id if actor else 'operator'}.",
            )
            record.acknowledged_by = actor.actor_id if actor else None
            record.acknowledged_at = datetime.now(timezone.utc).isoformat()
            self.storage.save_escalation_record(record)
            self.audit.record(
                actor_id=actor.actor_id if actor else "system_core",
                action=AuditAction.ESCALATION_STOPPED,
                target_type="incident",
                target_id=incident.incident_id,
                reason="Escalation stopped due to incident acknowledgement.",
                metadata={"acknowledged_by": actor.actor_id if actor else "unknown"},
            )

    def on_incident_resolved(self, incident: Incident, actor: Actor | None = None) -> None:
        """Handle incident resolution: terminate escalation and cancel pending notifications."""
        record = self.storage.get_escalation_record(incident.incident_id)
        if record and record.is_active:
            record = self.escalation_engine.stop_record(
                record,
                reason=f"Incident resolved ({incident.resolution_reason}).",
            )
            self.storage.save_escalation_record(record)
            self.audit.record(
                actor_id=actor.actor_id if actor else "system_core",
                action=AuditAction.ESCALATION_STOPPED,
                target_type="incident",
                target_id=incident.incident_id,
                reason=f"Escalation stopped: incident resolved as {incident.resolution_reason}.",
            )

    def on_incident_dismissed(self, incident: Incident, actor: Actor | None = None) -> None:
        """Handle incident dismissal: terminate escalation."""
        record = self.storage.get_escalation_record(incident.incident_id)
        if record and record.is_active:
            record = self.escalation_engine.stop_record(record, reason="Incident dismissed.")
            self.storage.save_escalation_record(record)
            self.audit.record(
                actor_id=actor.actor_id if actor else "system_core",
                action=AuditAction.ESCALATION_STOPPED,
                target_type="incident",
                target_id=incident.incident_id,
                reason="Escalation stopped: incident dismissed.",
            )

    # ------------------------------------------------------------------
    # Notification Acknowledge Action
    # ------------------------------------------------------------------
    def acknowledge_notification(self, notification_id: str, actor: Actor) -> Notification:
        """Acknowledge a specific notification by an authorized human actor."""
        # Enforce RBAC permission
        self.authority.assert_permission(actor, Permission.ACKNOWLEDGE_NOTIFICATION)

        notification = self.storage.get_notification(notification_id)
        if notification is None:
            raise KeyError(f"Notification '{notification_id}' not found.")

        updated = self.storage.acknowledge_notification(notification_id, actor.actor_id)
        if updated is None:
            raise KeyError(f"Notification '{notification_id}' could not be updated.")

        self.audit.record(
            actor_id=actor.actor_id,
            action=AuditAction.NOTIFICATION_ACKNOWLEDGED,
            target_type="notification",
            target_id=notification_id,
            previous_state=notification.status.value,
            new_state=NotificationStatus.ACKNOWLEDGED.value,
            reason=f"Notification acknowledged by {actor.actor_id}",
            metadata={"incident_id": notification.incident_id},
        )

        # Stopping escalation according to policy on acknowledgement
        record = self.storage.get_escalation_record(notification.incident_id)
        if record and record.is_active:
            record = self.escalation_engine.stop_record(
                record,
                reason=f"Escalation stopped: notification acknowledged by {actor.actor_id}."
            )
            record.acknowledged_by = actor.actor_id
            record.acknowledged_at = datetime.now(timezone.utc).isoformat()
            self.storage.save_escalation_record(record)
            self.audit.record(
                actor_id=actor.actor_id,
                action=AuditAction.ESCALATION_STOPPED,
                target_type="incident",
                target_id=notification.incident_id,
                reason=f"Escalation stopped: notification acknowledged by {actor.actor_id}.",
                metadata={"notification_id": notification_id},
            )

        return updated

    # ------------------------------------------------------------------
    # Query & Summary APIs
    # ------------------------------------------------------------------
    def get_notifications(
        self,
        incident_id: str | None = None,
        recipient_user_id: str | None = None,
        actor: Actor | None = None,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[Notification]:
        # Role-based scoping: Non-admin users are strictly scoped to their own notifications
        scoped_recipient = recipient_user_id
        if actor is not None and actor.role != Role.ADMIN:
            try:
                from atlas.authority.user_service import get_user_management_service
                user_mgmt = get_user_management_service()
                u = user_mgmt.get_user(actor.actor_id) or user_mgmt.get_user_by_username(actor.actor_id)
                scoped_recipient = u.user_id if u else actor.actor_id
            except Exception:
                scoped_recipient = actor.actor_id

        return self.storage.get_notifications(
            incident_id=incident_id,
            recipient_user_id=scoped_recipient,
            unread_only=unread_only,
            limit=limit,
        )

    def get_notification(self, notification_id: str) -> Optional[Notification]:
        return self.storage.get_notification(notification_id)

    def get_status_summary(self) -> dict[str, Any]:
        """Aggregate summary for real-time dashboard display."""
        all_notifs = self.storage.get_notifications(limit=500)
        active_escalations = self.storage.get_active_escalations()
        providers = self.providers.list_providers()

        unread_count = sum(1 for n in all_notifs if n.status not in (NotificationStatus.ACKNOWLEDGED, NotificationStatus.CANCELLED))

        return {
            "total_notifications": len(all_notifs),
            "unread_count": unread_count,
            "active_escalations_count": len(active_escalations),
            "providers": providers,
            "recent_notifications": [n.model_dump() for n in all_notifs[:10]],
            "active_escalations": [e.model_dump() for e in active_escalations],
        }


_global_notification_service: Optional[NotificationService] = None


def get_notification_service(
    storage: EventStorage | None = None,
    audit_logger: AuditLogger | None = None,
    settings: Settings | None = None,
    reload: bool = False,
) -> NotificationService:
    global _global_notification_service
    if _global_notification_service is None or reload:
        _global_notification_service = NotificationService(
            storage=storage,
            audit_logger=audit_logger,
            settings=settings,
        )
    return _global_notification_service
