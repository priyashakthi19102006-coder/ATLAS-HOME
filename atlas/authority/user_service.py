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
MAX_USERS = MAX_ADMINS + MAX_AUTHORIZED_USERS  # Strict 11 total website accounts (1 Admin + 10 Authorized Users)


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
    enrollment_status: str = "PENDING_ENROLLMENT"  # PENDING_ENROLLMENT | ENROLLED
    phone_number: str | None = None
    relationship: str | None = None  # Friend, Family, Parent, Sibling, Caregiver, Employee, Other
    last_login: str | None = None

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
            "enrollment_status": self.enrollment_status,
            "phone_number": self.phone_number,
            "relationship": self.relationship,
            "last_login": self.last_login,
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
        self._enrollment_sessions: dict[str, list[list[float]]] = {}
        self._admin_reauth_timestamps: dict[str, float] = {}
        self._migrate_schema()
        self._ensure_default_admin()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate_schema(self) -> None:
        """Ensure tables exist and non-destructive migration for columns."""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_accounts (
                        user_id TEXT PRIMARY KEY,
                        username TEXT UNIQUE NOT NULL,
                        display_name TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        permissions_json TEXT,
                        enrolled_embedding_json TEXT,
                        enrolled_image_path TEXT,
                        enrollment_status TEXT DEFAULT 'PENDING_ENROLLMENT',
                        phone_number TEXT,
                        relationship TEXT,
                        last_login TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_user_username ON user_accounts(username)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_user_role ON user_accounts(role)")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS login_audits (
                        audit_id TEXT PRIMARY KEY,
                        timestamp TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        username TEXT NOT NULL,
                        role TEXT NOT NULL,
                        status TEXT NOT NULL,
                        face_confidence REAL,
                        session_token_hash TEXT,
                        ip_address TEXT,
                        user_agent TEXT,
                        evidence_path TEXT,
                        details_json TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_login_audit_user ON login_audits(user_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_login_audit_ts ON login_audits(timestamp)")

                for col, defn in [
                    ("enrollment_status", "TEXT DEFAULT 'PENDING_ENROLLMENT'"),
                    ("phone_number", "TEXT"),
                    ("relationship", "TEXT"),
                    ("last_login", "TEXT"),
                ]:
                    try:
                        conn.execute(f"ALTER TABLE user_accounts ADD COLUMN {col} {defn}")
                    except Exception:
                        pass
                try:
                    conn.execute("UPDATE user_accounts SET enrollment_status = 'ENROLLED' WHERE enrolled_embedding_json IS NOT NULL")
                except Exception:
                    pass
                conn.commit()

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
                                enrollment_status, phone_number, relationship, last_login,
                                created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, 1, NULL, NULL, NULL, 'PENDING_ENROLLMENT', NULL, NULL, NULL, ?, ?)""",
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

        keys = row.keys()
        enr_status = row["enrollment_status"] if "enrollment_status" in keys and row["enrollment_status"] else ("ENROLLED" if embedding is not None else "PENDING_ENROLLMENT")
        phone = row["phone_number"] if "phone_number" in keys else None
        relationship = row["relationship"] if "relationship" in keys else None
        last_log = row["last_login"] if "last_login" in keys else None

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
            enrollment_status=enr_status,
            phone_number=phone,
            relationship=relationship,
            last_login=last_log,
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
        phone_number: str | None = None,
        relationship: str | None = None,
        is_active: bool | None = None,
    ) -> UserAccount:
        """Create a new user account. Enforces identity bounds.
        
        Strict Identity Limit:
        - Exactly 1 Admin.
        - Maximum 10 Authorized Users (Admin is NOT counted inside the 10 slots).
        - Maximum 11 total website accounts.
        
        Authorized Users start in PENDING_ENROLLMENT (is_active = False)
        until face biometric enrollment is completed.
        """
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
            raise ValueError(f"Maximum of {MAX_USERS} total accounts ({MAX_ADMINS} Admin + {MAX_AUTHORIZED_USERS} Authorized Users) permitted.")

        if self.get_user_by_username(clean_user):
            raise ValueError(f"Username '{clean_user}' already exists.")

        user_id = f"user_{secrets.token_hex(8)}"
        pw_hash = generate_password_hash(password, method="scrypt")
        now = datetime.now(timezone.utc).isoformat()

        if is_active is None:
            initial_active = 0 if role == Role.AUTHORIZED_USER else 1
        else:
            initial_active = 1 if is_active else 0

        enr_status = "ENROLLED" if initial_active == 1 else "PENDING_ENROLLMENT"

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """INSERT INTO user_accounts
                       (user_id, username, display_name, password_hash, role, is_active,
                        permissions_json, enrolled_embedding_json, enrolled_image_path,
                        enrollment_status, phone_number, relationship, last_login,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, NULL, ?, ?)""",
                    (user_id, clean_user, display_name or clean_user,
                     pw_hash, role.value, initial_active, enr_status, phone_number,
                     relationship.strip() if relationship else None, now, now),
                )
                conn.commit()

        account = self.get_user_by_id(user_id)
        logger.info("Created user account: %s (role=%s, active=%s, status=%s)",
                    clean_user, role.value, initial_active, enr_status)
        return account

    def update_user_status(self, user_id: str, is_active: bool) -> UserAccount:
        """Enable or disable a user account. Cannot disable the last active admin."""
        account = self.get_user_by_id(user_id)
        if account is None:
            raise KeyError(f"User '{user_id}' not found.")

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

        if not is_active:
            try:
                from atlas.authority.auth import get_auth_service
                get_auth_service().invalidate_sessions_for_user(user_id)
            except Exception as exc:
                logger.warning("Could not invalidate sessions for user %s: %s", user_id, exc)

        return self.get_user_by_id(user_id)

    def delete_user(self, user_id: str) -> None:
        """Delete a user account. Cannot delete the last admin. Invalidates active sessions."""
        account = self.get_user_by_id(user_id)
        if account is None:
            raise KeyError(f"User '{user_id}' not found.")
        if account.role == Role.ADMIN:
            admins = self._list_by_role(Role.ADMIN)
            if len(admins) <= 1:
                raise ValueError("Cannot delete the last admin account.")
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM user_accounts WHERE user_id = ?", (user_id,))
                conn.commit()
        self.clear_enrollment_session(user_id)

        try:
            from atlas.authority.auth import get_auth_service
            get_auth_service().invalidate_sessions_for_user(user_id)
        except Exception as exc:
            logger.warning("Could not invalidate sessions for deleted user %s: %s", user_id, exc)

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

    def update_user(
        self,
        user_id: str,
        display_name: Optional[str] = None,
        username: Optional[str] = None,
        role: Optional[Role] = None,
        phone_number: Optional[str] = None,
        relationship: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> UserAccount:
        """Update user details. Enforces unique username and role escalation boundaries."""
        account = self.get_user_by_id(user_id)
        if account is None:
            raise KeyError(f"User '{user_id}' not found.")
        
        updates = []
        params = []
        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name.strip())
        if username is not None:
            clean_u = username.strip().lower()
            if not clean_u:
                raise ValueError("Username cannot be empty.")
            if clean_u != account.username:
                existing = self.get_user_by_username(clean_u)
                if existing and existing.user_id != user_id:
                    raise ValueError(f"Username '{clean_u}' is already taken.")
                updates.append("username = ?")
                params.append(clean_u)
        if phone_number is not None:
            updates.append("phone_number = ?")
            params.append(phone_number.strip() if phone_number else None)
        if relationship is not None:
            updates.append("relationship = ?")
            params.append(relationship.strip() if relationship else None)
        if role is not None:
            if role == Role.ADMIN and account.role != Role.ADMIN:
                existing_admins = self._list_by_role(Role.ADMIN)
                if len(existing_admins) >= MAX_ADMINS:
                    raise ValueError(f"Cannot promote to Administrator. Only {MAX_ADMINS} admin account is permitted.")
            elif account.role == Role.ADMIN and role != Role.ADMIN:
                existing_admins = self._list_by_role(Role.ADMIN)
                if len(existing_admins) <= 1:
                    raise ValueError("Cannot demote the last active admin.")
            updates.append("role = ?")
            params.append(role.value)
        if is_active is not None:
            if account.role == Role.ADMIN and not is_active:
                active_admins = [u for u in self._list_by_role(Role.ADMIN) if u.is_active]
                if len(active_admins) <= 1:
                    raise ValueError("Cannot disable the last active admin account.")
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)
        
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

        if is_active is False:
            try:
                from atlas.authority.auth import get_auth_service
                get_auth_service().invalidate_sessions_for_user(user_id)
            except Exception as exc:
                logger.warning("Could not invalidate sessions for user %s: %s", user_id, exc)

        return self.get_user_by_id(user_id)

    def update_admin_display_name(self, display_name: str) -> UserAccount:
        """Update display name of the primary Admin account."""
        clean_name = (display_name or "").strip()
        if not clean_name:
            raise ValueError("Display name cannot be empty.")
        admins = self._list_by_role(Role.ADMIN)
        if not admins:
            raise KeyError("Admin account not found.")
        admin = admins[0]
        return self.update_user(admin.user_id, display_name=clean_name)

    def update_last_login(self, user_id: str) -> None:
        """Update last login timestamp for user."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                try:
                    conn.execute("UPDATE user_accounts SET last_login = ? WHERE user_id = ?", (now, user_id))
                    conn.commit()
                except Exception:
                    pass

    def get_user_slots_summary(self) -> dict:
        """Return slot statistics for the 10 Authorized User limit.
        
        Admin is NOT counted inside the 10 Authorized User slots.
        Returns exactly 10 slots for Authorized Users.
        """
        all_users = self.list_users()
        admin_count = sum(1 for u in all_users if u.role == Role.ADMIN)
        authorized_users = [u for u in all_users if u.role != Role.ADMIN]
        authorized_count = len(authorized_users)
        active_count = sum(1 for u in all_users if u.is_active)
        return {
            "max_users": MAX_AUTHORIZED_USERS,
            "max_slots": MAX_AUTHORIZED_USERS,
            "max_admins": MAX_ADMINS,
            "max_authorized_users": MAX_AUTHORIZED_USERS,
            "total_capacity": MAX_ADMINS + MAX_AUTHORIZED_USERS,
            "total_users": len(all_users),
            "occupied_slots": authorized_count,
            "admin_count": admin_count,
            "authorized_user_count": authorized_count,
            "active_count": active_count,
            "remaining_slots": max(0, MAX_AUTHORIZED_USERS - authorized_count),
            "remaining_authorized_slots": max(0, MAX_AUTHORIZED_USERS - authorized_count),
            "slots": [
                {
                    "slot_number": i + 1,
                    "status": "OCCUPIED" if i < authorized_count else "EMPTY",
                    "occupied": i < authorized_count,
                    "user": authorized_users[i].public_dict() if i < authorized_count else None,
                }
                for i in range(MAX_AUTHORIZED_USERS)
            ]
        }

    def purge_synthetic_test_users(self) -> int:
        """Remove leftover synthetic test accounts matching 'kawin_17890%' from database."""
        with self._lock:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT user_id, username FROM user_accounts WHERE username LIKE 'kawin_17890%'").fetchall()
                count = len(rows)
                if count > 0:
                    conn.execute("DELETE FROM user_accounts WHERE username LIKE 'kawin_17890%'")
                    conn.commit()
                    logger.info("Purged %d synthetic test user accounts from user_accounts table", count)
                return count

    # =========================================================================
    # Face Enrollment & Multi-Sample Capture
    # =========================================================================

    def add_enrollment_sample(self, user_id: str, embedding: list[float]) -> int:
        """Accumulate a validated 1856-D sample in the active enrollment session."""
        with self._lock:
            if user_id not in self._enrollment_sessions:
                self._enrollment_sessions[user_id] = []
            self._enrollment_sessions[user_id].append(embedding)
            return len(self._enrollment_sessions[user_id])

    def get_enrollment_samples(self, user_id: str) -> list[list[float]]:
        """Retrieve collected samples for a user's enrollment session."""
        with self._lock:
            return list(self._enrollment_sessions.get(user_id, []))

    def clear_enrollment_session(self, user_id: str) -> None:
        """Clear temporary enrollment session samples."""
        with self._lock:
            self._enrollment_sessions.pop(user_id, None)

    def enroll_face(
        self,
        user_id: str,
        embedding: list,
        image_path: str | None = None,
    ) -> None:
        """Store face biometric template and activate the user account."""
        account = self.get_user_by_id(user_id)
        if account is None:
            raise KeyError(f"User '{user_id}' not found.")

        embedding_json = json.dumps(embedding)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """UPDATE user_accounts
                       SET enrolled_embedding_json = ?, enrolled_image_path = ?,
                           enrollment_status = 'ENROLLED', is_active = 1, updated_at = ?
                       WHERE user_id = ?""",
                    (embedding_json, image_path, now, user_id),
                )
                conn.commit()
            self._enrollment_sessions.pop(user_id, None)
        logger.info("Face template enrolled and account activated for user %s (%d-D embedding)",
                    user_id, len(embedding))

    def remove_face_enrollment(self, user_id: str) -> None:
        """Remove face biometric template and deactivate Authorized User until re-enrolled."""
        account = self.get_user_by_id(user_id)
        if account is None:
            raise KeyError(f"User '{user_id}' not found.")
        now = datetime.now(timezone.utc).isoformat()
        # Non-admin accounts require biometric enrollment to be active
        new_active = 1 if account.role == Role.ADMIN else 0
        new_status = "PENDING_ENROLLMENT"

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """UPDATE user_accounts
                       SET enrolled_embedding_json = NULL, enrolled_image_path = NULL,
                           enrollment_status = ?, is_active = ?, updated_at = ?
                       WHERE user_id = ?""",
                    (new_status, new_active, now, user_id),
                )
                conn.commit()
            self._enrollment_sessions.pop(user_id, None)

        try:
            from atlas.authority.auth import get_auth_service
            get_auth_service().invalidate_sessions_for_user(user_id)
        except Exception as exc:
            logger.warning("Could not invalidate sessions for user with removed face %s: %s", user_id, exc)

    # =========================================================================
    # Admin Sensitive Actions Re-Authentication Tracker
    # =========================================================================

    def record_admin_reauth(self, admin_user_id: str) -> None:
        """Record timestamp of verified Admin password + face re-authentication."""
        with self._lock:
            self._admin_reauth_timestamps[admin_user_id] = time.time()

    def is_admin_reauthenticated(self, admin_user_id: str, max_age_seconds: float = 300.0) -> bool:
        """Check if Admin has verified sensitive re-authentication within max_age_seconds."""
        with self._lock:
            t_auth = self._admin_reauth_timestamps.get(admin_user_id)
            if t_auth is None:
                return False
            return (time.time() - t_auth) <= max_age_seconds

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
