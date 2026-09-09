"""ATLAS Authority and Domain Authorization Module."""

from atlas.authority.models import Action, Actor, Permission, Role, ROLE_PERMISSIONS
from atlas.authority.service import (
    AuthorityService,
    DEFAULT_ADMIN,
    DEFAULT_OPERATOR,
    DEFAULT_VIEWER,
    SYSTEM_ACTOR,
    UnauthorizedError,
    get_authority_service,
)
from atlas.authority.auth import AuthService, get_auth_service, DEV_ACCOUNTS

__all__ = [
    "Action",
    "Actor",
    "Permission",
    "Role",
    "ROLE_PERMISSIONS",
    "AuthorityService",
    "UnauthorizedError",
    "SYSTEM_ACTOR",
    "DEFAULT_ADMIN",
    "DEFAULT_OPERATOR",
    "DEFAULT_VIEWER",
    "get_authority_service",
    "AuthService",
    "get_auth_service",
    "DEV_ACCOUNTS",
]
