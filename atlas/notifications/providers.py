"""Notification provider abstractions and implementations for ATLAS Home (Final Stage).

LOCKED PRINCIPLES:
- ZERO FAKE DELIVERY: If mobile push or email is not configured, report NOT_CONFIGURED honestly.
- Do NOT fabricate FCM/APNs receipts, phone numbers, or push tokens.
- Never claim SENT, DELIVERED, or SUCCESS unless a genuine provider actually confirms it.
- In-App notifications are genuinely supported locally.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional
import urllib.request
import urllib.error

from atlas.authority.models import Actor
from atlas.config.settings import Settings, get_settings
from atlas.notifications.schema import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    ProviderResult,
    ProviderState,
)

logger = logging.getLogger("atlas.notifications.providers")


class NotificationProvider(ABC):
    """Abstract interface for delivery channels."""

    @property
    @abstractmethod
    def channel(self) -> NotificationChannel:
        """The channel handled by this provider."""

    @abstractmethod
    def get_state(self) -> ProviderState:
        """Current operational availability of this provider."""

    @abstractmethod
    def send(self, notification: Notification, recipient: Actor) -> ProviderResult:
        """Attempt delivery of notification to the specified recipient."""

    def get_display_status(self) -> str:
        """Human-readable display status for UI and dashboards."""
        state = self.get_state()
        if self.channel in (NotificationChannel.MOBILE_PUSH, NotificationChannel.WEB_PUSH):
            if state == ProviderState.NOT_CONFIGURED:
                return "MOBILE PUSH: NOT CONFIGURED"
            return f"MOBILE PUSH: {state.value}"
        return f"{self.channel.value}: {state.value}"


class InAppNotificationProvider(NotificationProvider):
    """Local in-app notification provider. Active and operational by default."""

    @property
    def channel(self) -> NotificationChannel:
        return NotificationChannel.IN_APP

    def get_state(self) -> ProviderState:
        return ProviderState.ACTIVE

    def send(self, notification: Notification, recipient: Actor) -> ProviderResult:
        if recipient.is_system or recipient.actor_id == "system_core":
            return ProviderResult(
                success=False,
                channel=self.channel,
                status=NotificationStatus.FAILED,
                provider_state=self.get_state(),
                failure_reason="SYSTEM_ACTOR cannot be a notification recipient: system_core is excluded.",
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        return ProviderResult(
            success=True,
            channel=self.channel,
            status=NotificationStatus.DELIVERED,
            provider_state=self.get_state(),
            delivered_at=now_iso,
            details={"recipient": recipient.actor_id, "role": recipient.role.value if recipient.role else "UNKNOWN"},
        )


class MobilePushNotificationProvider(NotificationProvider):
    """Genuine Mobile Push provider abstraction.

    CRITICAL HONESTY RULES:
    - If real push credentials / gateway endpoint are not configured,
      honestly report NOT_CONFIGURED.
    - NEVER claim SENT, DELIVERED, or SUCCESS unless a genuine external provider confirms it.
    - No fake push tokens, no simulated APNs/FCM receipts, no fake phone numbers.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def channel(self) -> NotificationChannel:
        return NotificationChannel.MOBILE_PUSH

    def get_state(self) -> ProviderState:
        endpoint = getattr(self.settings, "mobile_push_endpoint", "").strip()
        api_key = getattr(self.settings, "mobile_push_api_key", "").strip()
        enabled = getattr(self.settings, "mobile_push_enabled", False)

        # Genuine provider requires explicit enablement AND real gateway endpoint/key
        if not enabled or (not endpoint and not api_key):
            return ProviderState.NOT_CONFIGURED
        return ProviderState.ACTIVE

    def send(self, notification: Notification, recipient: Actor) -> ProviderResult:
        state = self.get_state()
        if state == ProviderState.NOT_CONFIGURED:
            logger.info("Mobile push delivery skipped: provider is NOT_CONFIGURED.")
            return ProviderResult(
                success=False,
                channel=self.channel,
                status=NotificationStatus.FAILED,
                provider_state=state,
                failure_reason="MOBILE PUSH NOT CONFIGURED",
            )

        endpoint = getattr(self.settings, "mobile_push_endpoint", "").strip()
        api_key = getattr(self.settings, "mobile_push_api_key", "").strip()

        # Attempt real transmission only if genuine endpoint exists
        payload = json.dumps({
            "recipient_id": recipient.actor_id,
            "title": notification.title,
            "body": notification.summary,
            "severity": notification.severity,
            "incident_id": notification.incident_id,
            "escalation_level": notification.escalation_level,
        }).encode("utf-8")

        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}" if api_key else "",
                "User-Agent": "ATLAS-Home-Notification/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status in (200, 201, 202, 204):
                    now_iso = datetime.now(timezone.utc).isoformat()
                    return ProviderResult(
                        success=True,
                        channel=self.channel,
                        status=NotificationStatus.DELIVERED,
                        provider_state=ProviderState.ACTIVE,
                        delivered_at=now_iso,
                        details={"status_code": resp.status, "recipient": recipient.actor_id},
                    )
                return ProviderResult(
                    success=False,
                    channel=self.channel,
                    status=NotificationStatus.FAILED,
                    provider_state=ProviderState.UNAVAILABLE,
                    failure_reason=f"Mobile push gateway returned HTTP {resp.status}.",
                )
        except Exception as exc:
            logger.warning("Real mobile push provider failed: %s", exc)
            return ProviderResult(
                success=False,
                channel=self.channel,
                status=NotificationStatus.FAILED,
                provider_state=ProviderState.UNAVAILABLE,
                failure_reason=f"Mobile push provider transmission error: {exc}",
            )


class WebPushNotificationProvider(MobilePushNotificationProvider):
    """Web push alias inheriting genuine MobilePush provider constraints."""

    @property
    def channel(self) -> NotificationChannel:
        return NotificationChannel.WEB_PUSH


class EmailNotificationProvider(NotificationProvider):
    """Email notification provider abstraction.

    CRITICAL RULE:
    If SMTP server is not configured, honestly report NOT_CONFIGURED.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def channel(self) -> NotificationChannel:
        return NotificationChannel.EMAIL

    def get_state(self) -> ProviderState:
        if not getattr(self.settings, "email_notifications_enabled", False):
            return ProviderState.NOT_CONFIGURED
        return ProviderState.UNAVAILABLE

    def send(self, notification: Notification, recipient: Actor) -> ProviderResult:
        state = self.get_state()
        if state == ProviderState.NOT_CONFIGURED:
            return ProviderResult(
                success=False,
                channel=self.channel,
                status=NotificationStatus.FAILED,
                provider_state=state,
                failure_reason="Email notification provider is not configured.",
            )

        return ProviderResult(
            success=False,
            channel=self.channel,
            status=NotificationStatus.FAILED,
            provider_state=state,
            failure_reason="Email SMTP gateway currently unavailable.",
        )


class ProviderRegistry:
    """Registry maintaining available notification providers."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._providers: dict[NotificationChannel, NotificationProvider] = {
            NotificationChannel.IN_APP: InAppNotificationProvider(),
            NotificationChannel.MOBILE_PUSH: MobilePushNotificationProvider(self.settings),
            NotificationChannel.WEB_PUSH: WebPushNotificationProvider(self.settings),
            NotificationChannel.EMAIL: EmailNotificationProvider(self.settings),
        }

    def get_provider(self, channel: NotificationChannel) -> Optional[NotificationProvider]:
        return self._providers.get(channel)

    def list_providers(self) -> list[dict[str, Any]]:
        results = []
        for ch, prov in self._providers.items():
            state = prov.get_state()
            results.append({
                "channel": ch.value,
                "state": state.value,
                "is_active": state == ProviderState.ACTIVE,
                "display": prov.get_display_status(),
            })
        return results


_global_provider_registry: Optional[ProviderRegistry] = None


def get_provider_registry(settings: Settings | None = None) -> ProviderRegistry:
    global _global_provider_registry
    if _global_provider_registry is None:
        _global_provider_registry = ProviderRegistry(settings=settings)
    return _global_provider_registry
