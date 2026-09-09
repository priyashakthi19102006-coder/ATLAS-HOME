"""Authority domain models for ATLAS Home.

Establishes internal domain authorization concepts:
- Actor
- Role
- Permission
- Action

NOTE: This is the domain authorization and permission enforcement boundary.
It does NOT claim to be a secure production authentication provider (no passwords/JWT/OAuth).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Set


class Role(str, Enum):
    """Predefined domain authorization roles."""
    ADMIN = "ADMIN"
    AUTHORIZED_USER = "AUTHORIZED_USER"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class Permission(str, Enum):
    """Domain action permissions."""
    VIEW_SYSTEM_STATUS = "VIEW_SYSTEM_STATUS"
    VIEW_EVENTS = "VIEW_EVENTS"
    VIEW_INCIDENTS = "VIEW_INCIDENTS"
    VIEW_EVIDENCE = "VIEW_EVIDENCE"
    ACKNOWLEDGE_INCIDENT = "ACKNOWLEDGE_INCIDENT"
    RESOLVE_INCIDENT = "RESOLVE_INCIDENT"
    DISMISS_INCIDENT = "DISMISS_INCIDENT"
    AUTHORIZE_RESPONSE = "AUTHORIZE_RESPONSE"
    VIEW_AUDIT_LOG = "VIEW_AUDIT_LOG"
    VIEW_NOTIFICATIONS = "VIEW_NOTIFICATIONS"
    ACKNOWLEDGE_NOTIFICATION = "ACKNOWLEDGE_NOTIFICATION"
    MANAGE_NOTIFICATIONS = "MANAGE_NOTIFICATIONS"
    MANAGE_USERS = "MANAGE_USERS"
    VIEW_LOGIN_AUDITS = "VIEW_LOGIN_AUDITS"


class Action(str, Enum):
    """Domain operations requiring authorization."""
    VIEW_STATUS = "VIEW_STATUS"
    VIEW_EVENTS = "VIEW_EVENTS"
    VIEW_INCIDENTS = "VIEW_INCIDENTS"
    VIEW_EVIDENCE = "VIEW_EVIDENCE"
    ACKNOWLEDGE = "ACKNOWLEDGE"
    RESOLVE = "RESOLVE"
    DISMISS = "DISMISS"
    REQUEST_ESCALATION = "REQUEST_ESCALATION"
    AUTHORIZE_ESCALATION = "AUTHORIZE_ESCALATION"
    VIEW_AUDIT = "VIEW_AUDIT"
    VIEW_NOTIFICATIONS = "VIEW_NOTIFICATIONS"
    ACKNOWLEDGE_NOTIFICATION = "ACKNOWLEDGE_NOTIFICATION"
    MANAGE_USERS = "MANAGE_USERS"
    VIEW_LOGIN_AUDITS = "VIEW_LOGIN_AUDITS"


ROLE_PERMISSIONS: dict[Role, FrozenSet[Permission]] = {
    Role.ADMIN: frozenset({
        Permission.VIEW_SYSTEM_STATUS,
        Permission.VIEW_EVENTS,
        Permission.VIEW_INCIDENTS,
        Permission.VIEW_EVIDENCE,
        Permission.ACKNOWLEDGE_INCIDENT,
        Permission.RESOLVE_INCIDENT,
        Permission.DISMISS_INCIDENT,
        Permission.AUTHORIZE_RESPONSE,
        Permission.VIEW_AUDIT_LOG,
        Permission.VIEW_NOTIFICATIONS,
        Permission.ACKNOWLEDGE_NOTIFICATION,
        Permission.MANAGE_NOTIFICATIONS,
        Permission.MANAGE_USERS,
        Permission.VIEW_LOGIN_AUDITS,
    }),
    Role.AUTHORIZED_USER: frozenset({
        Permission.VIEW_SYSTEM_STATUS,
        Permission.VIEW_EVENTS,
        Permission.VIEW_INCIDENTS,
        Permission.VIEW_EVIDENCE,
        Permission.ACKNOWLEDGE_INCIDENT,
        Permission.VIEW_NOTIFICATIONS,
        Permission.ACKNOWLEDGE_NOTIFICATION,
    }),
    Role.OPERATOR: frozenset({
        Permission.VIEW_SYSTEM_STATUS,
        Permission.VIEW_EVENTS,
        Permission.VIEW_INCIDENTS,
        Permission.VIEW_EVIDENCE,
        Permission.ACKNOWLEDGE_INCIDENT,
        Permission.RESOLVE_INCIDENT,
        Permission.DISMISS_INCIDENT,
        Permission.VIEW_AUDIT_LOG,
        Permission.VIEW_NOTIFICATIONS,
        Permission.ACKNOWLEDGE_NOTIFICATION,
    }),
    Role.VIEWER: frozenset({
        Permission.VIEW_SYSTEM_STATUS,
        Permission.VIEW_EVENTS,
        Permission.VIEW_INCIDENTS,
        Permission.VIEW_EVIDENCE,
        Permission.VIEW_NOTIFICATIONS,
    }),
}

SYSTEM_INTERNAL_PERMISSIONS: FrozenSet[Permission] = frozenset({
    Permission.VIEW_SYSTEM_STATUS,
    Permission.VIEW_EVENTS,
    Permission.VIEW_INCIDENTS,
    Permission.VIEW_EVIDENCE,
    Permission.VIEW_AUDIT_LOG,
    Permission.VIEW_NOTIFICATIONS,
})


@dataclass(frozen=True)
class Actor:
    """Represents an active entity performing domain actions.
    
    Human actors possess a Role (ADMIN, OPERATOR, VIEWER).
    Internal system actors have is_system=True and no human administrative role.
    System actors can NEVER possess Permission.AUTHORIZE_RESPONSE.
    """
    actor_id: str
    role: Role | None = Role.VIEWER
    display_name: str = ""
    custom_permissions: FrozenSet[Permission] = field(default_factory=frozenset)
    is_system: bool = False

    def has_permission(self, permission: Permission) -> bool:
        """Verify if actor possesses the specified permission.
        
        System actors are strictly prohibited from possessing AUTHORIZE_RESPONSE.
        """
        if self.is_system and permission == Permission.AUTHORIZE_RESPONSE:
            return False
        role_perms = ROLE_PERMISSIONS.get(self.role, frozenset()) if self.role is not None else frozenset()
        return permission in role_perms or permission in self.custom_permissions
