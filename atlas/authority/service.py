"""Authority and permission enforcement service for ATLAS Home."""

from __future__ import annotations

import logging
from typing import Optional

from atlas.authority.models import Actor, Permission, Role, SYSTEM_INTERNAL_PERMISSIONS

logger = logging.getLogger("atlas.authority.service")


class UnauthorizedError(PermissionError):
    """Raised when an actor lacks permission for an attempted domain action."""
    def __init__(self, actor_id: str, permission: Permission, message: str | None = None) -> None:
        msg = message or f"Actor '{actor_id}' is not authorized for permission '{permission.value}'."
        super().__init__(msg)
        self.actor_id = actor_id
        self.permission = permission


# Predefined development and system actors
SYSTEM_ACTOR = Actor(
    actor_id="system_core",
    role=None,  # System authority is NOT a human role
    display_name="ATLAS Core System",
    is_system=True,
    custom_permissions=SYSTEM_INTERNAL_PERMISSIONS,
)

DEFAULT_ADMIN = Actor(
    actor_id="admin_01",
    role=Role.ADMIN,
    display_name="Default Administrator",
)

DEFAULT_OPERATOR = Actor(
    actor_id="operator_01",
    role=Role.OPERATOR,
    display_name="Default Security Operator",
)

DEFAULT_VIEWER = Actor(
    actor_id="viewer_01",
    role=Role.VIEWER,
    display_name="Default Guest Viewer",
)

PREDEFINED_ACTORS: dict[str, Actor] = {
    SYSTEM_ACTOR.actor_id: SYSTEM_ACTOR,
    DEFAULT_ADMIN.actor_id: DEFAULT_ADMIN,
    DEFAULT_OPERATOR.actor_id: DEFAULT_OPERATOR,
    DEFAULT_VIEWER.actor_id: DEFAULT_VIEWER,
}


class AuthorityService:
    """Manages domain actors and enforces authorization boundaries."""

    def __init__(self) -> None:
        self._actors: dict[str, Actor] = dict(PREDEFINED_ACTORS)

    def register_actor(self, actor: Actor) -> None:
        """Register or update an actor identity in the domain."""
        self._actors[actor.actor_id] = actor

    def get_actor(self, actor_id: str | None) -> Actor | None:
        """Retrieve actor by ID."""
        if not actor_id:
            return None
        return self._actors.get(actor_id)

    def get_actor_or_default(self, actor_id: str | None, default: Actor | None = None) -> Actor:
        """Retrieve actor by ID, fallback to provided default or DEFAULT_OPERATOR for dev testing."""
        actor = self.get_actor(actor_id)
        if actor is not None:
            return actor
        if default is not None:
            return default
        return DEFAULT_OPERATOR

    def check_permission(self, actor: Actor | None, permission: Permission) -> bool:
        """Check whether actor has permission without raising exception."""
        if actor is None:
            return False
        return actor.has_permission(permission)

    def assert_permission(self, actor: Actor | None, permission: Permission) -> None:
        """Assert that actor has permission; raise UnauthorizedError if missing."""
        if actor is None:
            raise UnauthorizedError(
                actor_id="anonymous",
                permission=permission,
                message=f"Anonymous actor cannot perform action requiring '{permission.value}'.",
            )
        if not actor.has_permission(permission):
            role_desc = actor.role.value if actor.role is not None else ("SYSTEM_CORE" if actor.is_system else "UNKNOWN")
            raise UnauthorizedError(
                actor_id=actor.actor_id,
                permission=permission,
                message=f"Actor '{actor.actor_id}' ({role_desc}) lacks permission '{permission.value}'.",
            )


_global_authority_service: AuthorityService | None = None


def get_authority_service() -> AuthorityService:
    """Singleton getter for AuthorityService."""
    global _global_authority_service
    if _global_authority_service is None:
        _global_authority_service = AuthorityService()
    return _global_authority_service
