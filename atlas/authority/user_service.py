"""ATLAS Home User Management Service (Step 8 — Premium Identity & Authentication).

Manages the persistent user account store backed by the SQLite user_accounts table:
- Exactly ONE Admin account enforced at all times.
- Maximum 10 active Authorized Users (OPERATOR/VIEWER roles).
- Face biometric enrollment per user.
- Immutable login audit trail.
- Zero plaintext password storage.

Identity distinction:
  WEBSITE USERS:  ADMIN | OPERATOR | VIEWER (accounts in user_accounts table)
  OBSERVED PEOPLE: AUTHORIZED_PERSON | UNKNOWN (camera perimeter tracking, NOT website users)
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from werkzeug.security import generate_password_hash, check_password_hash

from atlas.authority.models import Actor, Role, Permission, ROLE_PERMISSIONS

logger = logging.getLogger("atlas.authority.user_service")

MAX_ADMINS = 1
MAX_AUTHORIZED_USERS = 10
MAX_USERS = 10  # Legacy total bound alias


@dataclass
class UserAccount:
    """Persistent user account record from the SQLite store."""
    user_id: str
    username: str
    display_name: str
    password_hash: str
    role: Role
    is_active: bool
    enrolled_embedding: list | None  # 1856-D face feature vector or None if not enrolled
    enrolled_image_path: str | None
    created_at: str
    updated_at: str

    def to_actor(self) -> Actor:
        """Convert to domain Actor for authorization checks."""
        return Actor(
            actor_id=self.user_id,
            role=self.role,
            display_name=self.display_name,
        )

    def public_dict(self) -> dict:
        """Serializable summary (no password hash, no raw embedding)."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role.value,
            "is_active": self.is_active,
            "face_enrolled": self.enrolled_embedding is not None,
            "enrolled_image_path": self.enrolled_image_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class LoginAuditRecord:
    """Immutable login verification event record."""
    audit_id: str
    timestamp: str
    user_id: str
    username: str
    role: str
    status: str  # CREDENTIALS_OK | CREDENTIALS_FAILED | FACE_VERIFIED | FACE_DENIED | SESSION_GRANTED | LOGOUT
    face_confidence: float | None
    session_token_hash: str | None
    ip_address: str | None
    user_agent: str | None
    evidence_path: str | None
    details: dict

    def to_dict(self) -> dict:
        return {
            "audit_id": self.audit_id,
            "timestamp": self.timestamp,
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role,
            "status": self.status,
            "face_confidence": self.face_confidence,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "evidence_path": self.evidence_path,
            "details": self.details,
        }


class UserManagementService:
    """Manages persistent ATLAS Home user accounts and login audit trail."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            project_root = Path(__file__).resolve().parent.parent.parent
            self.db_path = project_root / "data" / "atlas_events.db"
        else:
            self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._ensure_default_admin()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_default_admin(self) -> None:
        """Seed default admin account if no admin exists yet."""
        try:
            existing_admins = self._list_by_role(Role.ADMIN)
            if not existing_admins:
                admin_id = f"user_{secrets.token_hex(8)}"
                pw_hash = generate_password_hash("atlas_admin_2024!", method="scrypt")
                now = datetime.now(timezone.utc).isoformat()
                with self._lock:
                    with self._get_connection() as conn:
                        conn.execute(
                            """INSERT OR IGNORE INTO user_accounts
                               (user_id, username, display_name, password_hash, role, is_active,
                                permissions_json, enrolled_embedding_json, enrolled_image_path,
                                created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, 1, NULL, NULL, NULL, ?, ?)""",
                            (admin_id, "admin", "ATLAS Administrator", pw_hash,
                             Role.ADMIN.value, now, now),
                        )
                        conn.commit()
                logger.info("Default admin account seeded (user_id=%s)", admin_id)
        except Exception as exc:
            logger.warning("Could not seed default admin: %s", exc)

    def _row_to_account(self, row: sqlite3.Row) -> UserAccount:
        embedding = None
        if row["enrolled_embedding_json"]:
            try:
                embedding = json.loads(row["enrolled_embedding_json"])
            except Exception:
                embedding = None
        return UserAccount(
            user_id=row["user_id"],
            username=row["username"],
            display_name=row["display_name"],
            password_hash=row["password_hash"],
            role=Role(row["role"]),
            is_active=bool(row["is_active"]),
            enrolled_embedding=embedding,
            enrolled_image_path=row["enrolled_image_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _list_by_role(self, role: Role) -> list:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM user_accounts WHERE role = ?", (role.value,)
            ).fetchall()
        return [self._row_to_account(r) for r in rows]

    # =========================================================================
    # User Lookup
    # =========================================================================

    def get_user_by_username(self, username: str) -> Optional[UserAccount]:
        """Retrieve user account by username (case-insensitive)."""
        clean = (username or "").strip().lower()
        with self._lock:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM user_accounts WHERE LOWER(username) = ?", (clean,)
                ).fetchone()
        return self._row_to_account(row) if row else None

    def get_user_by_id(self, user_id: str) -> Optional[UserAccount]:
        """Retrieve user account by user_id."""
        with self._lock:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM user_accounts WHERE user_id = ?", (user_id,)
                ).fetchone()
        return self._row_to_account(row) if row else None

    def get_user(self, identifier: str) -> Optional[UserAccount]:
        """Retrieve user account by user_id or username."""
        return self.get_user_by_id(identifier) or self.get_user_by_username(identifier)

    def list_users(self) -> list:
        """List all user accounts."""
        with self._lock:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM user_accounts ORDER BY created_at"
                ).fetchall()
        return [self._row_to_account(r) for r in rows]

    # =========================================================================
    # Authentication
    # =========================================================================

    def verify_password(self, username: str, password: str) -> Optional[UserAccount]:
        """Verify username + password. Returns account if valid and active, else None."""
        account = self.get_user_by_username(username)
        if account is None:
            logger.warning("Credentials check: unknown user '%s'", username)
            return None
        if not account.is_active:
            logger.warning("Credentials check: user '%s' is disabled", username)
            return None
        if not check_password_hash(account.password_hash, password):
            logger.warning("Credentials check: wrong password for '%s'", username)
            return None
        return account

    # =========================================================================
    # User Management (Admin only — enforced at API layer)
    # =========================================================================

    def create_user(
        self,
        username: str,
        display_name: str,
        password: str,
        role: Role,
    ) -> UserAccount:
        """Create a new user account. Enforces identity bounds."""
        clean_user = (username or "").strip().lower()
        if not clean_user or not password:
            raise ValueError("Username and password are required.")

        if role == Role.ADMIN:
            existing_admins = self._list_by_role(Role.ADMIN)
            if len(existing_admins) >= MAX_ADMINS:
                raise ValueError(f"Only {MAX_ADMINS} admin account is permitted.")
        else:
            existing_auth = [u for u in self.list_users() if u.role != Role.ADMIN]
            if len(existing_auth) >= MAX_AUTHORIZED_USERS:
                raise ValueError(f"Maximum of {MAX_AUTHORIZED_USERS} Authorized Users permitted.")

        all_users = self.list_users()
        if len(all_users) >= MAX_USERS:
            raise ValueError(f"Maximum of {MAX_USERS} user accounts permitted.")

        if self.get_user_by_username(clean_user):
            raise ValueError(f"Username '{clean_user}' already exists.")

        user_id = f"user_{secrets.token_hex(8)}"
        pw_hash = generate_password_hash(password, method="scrypt")
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """INSERT INTO user_accounts
                       (user_id, username, display_name, password_hash, role, is_active,
                        permissions_json, enrolled_embedding_json, enrolled_image_path,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 1, NULL, NULL, NULL, ?, ?)""",
                    (user_id, clean_user, display_name or clean_user,
                     pw_hash, role.value, now, now),
                )
                conn.commit()

        account = self.get_user_by_id(user_id)
        logger.info("Created user account: %s (role=%s)", clean_user, role.value)
        return account

    def update_user_status(self, user_id: str, is_active: bool) -> UserAccount:
        """Enable or disable a user account. Cannot disable the last active admin."""
        account = self.get_user_by_id(user_id)
        if account is None:
            raise KeyError(f"User \'{user_id}\' not found.")

        if account.role == Role.ADMIN and not is_active:
            active_admins = [u for u in self._list_by_role(Role.ADMIN) if u.is_active]
            if len(active_admins) <= 1:
                raise ValueError("Cannot disable the last active admin account.")

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE user_accounts SET is_active = ?, updated_at = ? WHERE user_id = ?",
                    (1 if is_active else 0, now, user_id),
                )
                conn.commit()
        return self.get_user_by_id(user_id)

    def delete_user(self, user_id: str) -> None:
        """Delete a user account. Cannot delete the last admin."""
        account = self.get_user_by_id(user_id)
        if account is None:
            raise KeyError(f"User \'{user_id}\' not found.")
        if account.role == Role.ADMIN:
            admins = self._list_by_role(Role.ADMIN)
            if len(admins) <= 1:
                raise ValueError("Cannot delete the last admin account.")
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM user_accounts WHERE user_id = ?", (user_id,))
                conn.commit()
        logger.info("Deleted user account: %s", user_id)

    def change_password(self, user_id: str, new_password: str) -> None:
        """Update password hash for a user."""
        if not new_password or len(new_password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        pw_hash = generate_password_hash(new_password, method="scrypt")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                rows_affected = conn.execute(
                    "UPDATE user_accounts SET password_hash = ?, updated_at = ? WHERE user_id = ?",
                    (pw_hash, now, user_id),
                ).rowcount
                conn.commit()
        if rows_affected == 0:
            raise KeyError(f"User '{user_id}' not found.")

    def update_user(self, user_id: str, display_name: Optional[str] = None, role: Optional[Role] = None) -> UserAccount:
        """Update display name or role for an existing user."""
        account = self.get_user_by_id(user_id)
        if account is None:
            raise KeyError(f"User '{user_id}' not found.")
        
        updates = []
        params = []
        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name.strip())
        if role is not None:
            if role == Role.ADMIN and account.role != Role.ADMIN:
                existing_admins = self._list_by_role(Role.ADMIN)
                if len(existing_admins) >= MAX_ADMINS:
                    raise ValueError(f"Only {MAX_ADMINS} admin account is permitted.")
            elif account.role == Role.ADMIN and role != Role.ADMIN:
                existing_admins = self._list_by_role(Role.ADMIN)
                if len(existing_admins) <= 1:
                    raise ValueError("Cannot demote the last active admin.")
            updates.append("role = ?")
            params.append(role.value)
        
        if updates:
            now = datetime.now(timezone.utc).isoformat()
            updates.append("updated_at = ?")
            params.append(now)
            params.append(user_id)
            sql = f"UPDATE user_accounts SET {', '.join(updates)} WHERE user_id = ?"
            with self._lock:
                with self._get_connection() as conn:
                    conn.execute(sql, tuple(params))
                    conn.commit()
        return self.get_user_by_id(user_id)

    def get_user_slots_summary(self) -> dict:
        """Return slot statistics for the 10-user limit."""
        all_users = self.list_users()
        admin_count = sum(1 for u in all_users if u.role == Role.ADMIN)
        authorized_count = sum(1 for u in all_users if u.role != Role.ADMIN)
        active_count = sum(1 for u in all_users if u.is_active)
        return {
            "max_users": MAX_USERS,
            "max_admins": MAX_ADMINS,
            "max_authorized_users": MAX_AUTHORIZED_USERS,
            "total_users": len(all_users),
            "occupied_slots": len(all_users),
            "admin_count": admin_count,
            "authorized_user_count": authorized_count,
            "active_count": active_count,
            "remaining_slots": max(0, MAX_USERS - len(all_users)),
            "remaining_authorized_slots": max(0, MAX_AUTHORIZED_USERS - authorized_count),
            "slots": [
                {
                    "slot_number": i + 1,
                    "occupied": i < len(all_users),
                    "user": all_users[i].public_dict() if i < len(all_users) else None,
                }
                for i in range(MAX_USERS)
            ]
        }

    # =========================================================================
    # Face Enrollment
    # =========================================================================

    def enroll_face(
        self,
        user_id: str,
        embedding: list,
        image_path: str | None = None,
    ) -> None:
        """Store face biometric template for a user."""
        account = self.get_user_by_id(user_id)
        if account is None:
            raise KeyError(f"User \'{user_id}\' not found.")

        embedding_json = json.dumps(embedding)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """UPDATE user_accounts
                       SET enrolled_embedding_json = ?, enrolled_image_path = ?, updated_at = ?
                       WHERE user_id = ?""",
                    (embedding_json, image_path, now, user_id),
                )
                conn.commit()
        logger.info("Face template enrolled for user %s (%d-D embedding)", user_id, len(embedding))

    def remove_face_enrollment(self, user_id: str) -> None:
        """Remove face biometric template from user."""
        account = self.get_user_by_id(user_id)
        if account is None:
            raise KeyError(f"User \'{user_id}\' not found.")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE user_accounts SET enrolled_embedding_json = NULL, enrolled_image_path = NULL, updated_at = ? WHERE user_id = ?",
                    (now, user_id),
                )
                conn.commit()

    # =========================================================================
    # Login Audit Trail
    # =========================================================================

    def log_login_event(
        self,
        user_id: str,
        username: str,
        role: str,
        status: str,
        face_confidence: float | None = None,
        session_token: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        evidence_path: str | None = None,
        details: dict | None = None,
    ) -> str:
        """Write an immutable login audit record. Returns audit_id."""
        audit_id = f"audit_{secrets.token_hex(12)}"
        now_iso = datetime.now(timezone.utc).isoformat()
        now_epoch = time.time()

        token_hash = None
        if session_token:
            token_hash = hashlib.sha256(session_token.encode()).hexdigest()

        details_json = json.dumps(details or {})

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """INSERT INTO login_audits
                       (audit_id, timestamp, user_id, username, role, status,
                        face_confidence, session_token_hash, ip_address, user_agent,
                        evidence_path, details_json, created_at_epoch)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (audit_id, now_iso, user_id, username, role, status,
                     face_confidence, token_hash, ip_address, user_agent,
                     evidence_path, details_json, now_epoch),
                )
                conn.commit()
        return audit_id

    def get_login_audits(
        self,
        user_id: str | None = None,
        limit: int = 50,
    ) -> list:
        """Retrieve login audit records, optionally filtered by user_id."""
        with self._lock:
            with self._get_connection() as conn:
                if user_id:
                    rows = conn.execute(
                        "SELECT * FROM login_audits WHERE user_id = ? ORDER BY created_at_epoch DESC LIMIT ?",
                        (user_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM login_audits ORDER BY created_at_epoch DESC LIMIT ?",
                        (limit,),
                    ).fetchall()

        records = []
        for r in rows:
            details = {}
            if r["details_json"]:
                try:
                    details = json.loads(r["details_json"])
                except Exception:
                    pass
            records.append(LoginAuditRecord(
                audit_id=r["audit_id"],
                timestamp=r["timestamp"],
                user_id=r["user_id"],
                username=r["username"],
                role=r["role"],
                status=r["status"],
                face_confidence=r["face_confidence"],
                session_token_hash=r["session_token_hash"],
                ip_address=r["ip_address"],
                user_agent=r["user_agent"],
                evidence_path=r["evidence_path"],
                details=details,
            ))
        return records


# Global singleton
_global_user_service: Optional[UserManagementService] = None
_user_service_lock = threading.Lock()


def get_user_management_service() -> UserManagementService:
    """Retrieve or initialize the singleton UserManagementService."""
    global _global_user_service
    if _global_user_service is None:
        with _user_service_lock:
            if _global_user_service is None:
                _global_user_service = UserManagementService()
    return _global_user_service
