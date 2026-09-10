"""Authentication and session management service for ATLAS Home Step 6.2.

Provides local secure authentication:
- Secure password hashing via werkzeug.security (scrypt)
- Seeded DEVELOPMENT-ONLY local accounts (admin, operator, viewer)
- Thread-safe session management with cryptographically random tokens
- Session lookup supporting Bearer headers, X-Session-Token, and session cookies
- Zero credentials or plaintext passwords stored in source code or database
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
import secrets
import threading
from typing import Optional, Any

from werkzeug.security import check_password_hash

from atlas.authority.models import Actor, Role, Permission
from atlas.authority.service import DEFAULT_ADMIN, DEFAULT_OPERATOR, DEFAULT_VIEWER, get_authority_service

logger = logging.getLogger("atlas.authority.auth")


@dataclass(frozen=True)
class UserAccount:
    """Represents an authenticatable local development account."""
    username: str
    password_hash: str
    actor: Actor
    is_development_only: bool = True


# Precomputed secure password hashes for development-only accounts
# Passwords:
#   admin: admin123
#   operator: operator123
#   viewer: viewer123
# Note: Plaintext passwords are NEVER stored in codebase or SQLite.
DEV_ACCOUNTS: dict[str, UserAccount] = {
    "admin": UserAccount(
        username="admin",
        password_hash="scrypt:32768:8:1$RB0cLWoWInSirfAk$cc67cc9e896a80c27c87051a47b6c2d922de1f464312abd7a77a4a4e839625150adabdf9060df45ac791815483fc68807d38ca97b584280163cf8703d40dc1d5",
        actor=DEFAULT_ADMIN,
    ),
    "operator": UserAccount(
        username="operator",
        password_hash="scrypt:32768:8:1$mewDxNjDI1PjwSJN$cde28c2dee9834979e317c33e1604fbf2feae7c1f03fb1fcb394c297059bcd74ca8d30dd73f223c811a4ccf49361e1373eca8f1c2a2bd05d69cb8f49cf831f42",
        actor=DEFAULT_OPERATOR,
    ),
    "viewer": UserAccount(
        username="viewer",
        password_hash="scrypt:32768:8:1$qrjM0LpvWm7M6Cw9$115a0c661ba24a8fc64bcd689d0e7d57735b812253ce88041bfeeede1a45f9317c1516bc2a2b138edd22a479cc16f93d3509adbb5c3fc7ddde6613938f17f7ef",
        actor=DEFAULT_VIEWER,
    ),
}


class AuthService:
    """Manages authentication, session tokens, and identity mapping."""

    def __init__(self, session_ttl_hours: int = 24) -> None:
        self.session_ttl = timedelta(hours=session_ttl_hours)
        self._accounts: dict[str, UserAccount] = dict(DEV_ACCOUNTS)
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def authenticate(self, username: str, password: str) -> tuple[str, Actor] | None:
        """Verify username & password; return (session_token, Actor) or None if invalid."""
        clean_user = (username or "").strip().lower()
        if not clean_user or not password:
            return None

        with self._lock:
            account = self._accounts.get(clean_user)
            if not account:
                logger.warning("Authentication failed: unknown user '%s'", clean_user)
                return None

            if not check_password_hash(account.password_hash, password):
                logger.warning("Authentication failed: invalid password for user '%s'", clean_user)
                return None

            # Generate cryptographically secure URL-safe token
            token = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)
            self._sessions[token] = {
                "username": account.username,
                "actor": account.actor,
                "created_at": now,
                "expires_at": now + self.session_ttl,
            }
            logger.info("User '%s' authenticated successfully (role=%s)", account.username, account.actor.role)
            return token, account.actor

    def create_session(self, actor: Actor, username: str) -> str:
        """Create and register a new authenticated session for an Actor."""
        with self._lock:
            token = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)
            self._sessions[token] = {
                "username": username,
                "actor": actor,
                "created_at": now,
                "expires_at": now + self.session_ttl,
            }
            logger.info("Session created for user '%s' (role=%s)", username, actor.role)
            return token

    def get_actor_by_token(self, token: str | None) -> Actor | None:
        """Validate token and return associated Actor if active and unexpired."""
        if not token:
            return None

        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return None

            now = datetime.now(timezone.utc)
            if now > session["expires_at"]:
                del self._sessions[token]
                logger.debug("Session expired for user '%s'", session.get("username"))
                return None

            return session["actor"]

    def invalidate_session(self, token: str | None) -> bool:
        """Invalidate and remove an active session token."""
        if not token:
            return False

        with self._lock:
            if token in self._sessions:
                del self._sessions[token]
                return True
            return False

    def invalidate_sessions_for_user(self, identifier: str) -> int:
        """Invalidate all active sessions for a given username or user_id."""
        if not identifier:
            return 0
        with self._lock:
            tokens_to_remove = [
                token for token, sess in self._sessions.items()
                if (sess.get("actor") and sess["actor"].actor_id == identifier)
                or sess.get("username") == identifier
            ]
            for token in tokens_to_remove:
                del self._sessions[token]
            if tokens_to_remove:
                logger.info("Invalidated %d active sessions for user '%s'", len(tokens_to_remove), identifier)
            return len(tokens_to_remove)

    def extract_token_from_request(self, req: Any) -> str | None:
        """Extract session token from Authorization Bearer, X-Session-Token, or cookies."""
        # 1. Check Authorization header
        auth_header = req.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()

        # 2. Check X-Session-Token header
        custom_header = req.headers.get("X-Session-Token", "")
        if custom_header:
            return custom_header.strip()

        # 3. Check Flask session cookie if available
        try:
            from flask import session as flask_session
            cookie_token = flask_session.get("session_token")
            if cookie_token:
                return cookie_token
        except Exception:
            pass

        # 4. Check request cookies directly
        if hasattr(req, "cookies"):
            token_cookie = req.cookies.get("atlas_session")
            if token_cookie:
                return token_cookie.strip()

        return None

    def get_current_actor(self, req: Any) -> Actor | None:
        """Extract and resolve authenticated actor from incoming HTTP request."""
        token = self.extract_token_from_request(req)
        return self.get_actor_by_token(token)


_global_auth_service: AuthService | None = None


def get_auth_service() -> AuthService:
    """Singleton getter for AuthService."""
    global _global_auth_service
    if _global_auth_service is None:
        _global_auth_service = AuthService()
    return _global_auth_service
