"""ATLAS Home Notifications & Escalation Module (Step 7)."""

from atlas.notifications.schema import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    ProviderState,
    EscalationStage,
    ProviderResult,
    EscalationStateRecord,
)
from atlas.notifications.providers import (
    NotificationProvider,
    InAppNotificationProvider,
    WebPushNotificationProvider,
    EmailNotificationProvider,
    ProviderRegistry,
    get_provider_registry,
)
from atlas.notifications.escalation import (
    EscalationEngine,
    EscalationPolicy,
)
from atlas.notifications.service import (
    NotificationService,
    get_notification_service,
)

__all__ = [
    "Notification",
    "NotificationChannel",
    "NotificationStatus",
    "ProviderState",
    "EscalationStage",
    "ProviderResult",
    "EscalationStateRecord",
    "NotificationProvider",
    "InAppNotificationProvider",
    "WebPushNotificationProvider",
    "EmailNotificationProvider",
    "ProviderRegistry",
    "get_provider_registry",
    "EscalationEngine",
    "EscalationPolicy",
    "NotificationService",
    "get_notification_service",
]
