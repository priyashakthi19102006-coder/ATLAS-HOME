"""SQLite Persistent Event History Storage for ATLAS Home.

Stores structured event records with indexed metadata.
Does NOT store raw webcam video or frames.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Sequence

from atlas.events.schema import ATLASEvent, EventStatus

logger = logging.getLogger("atlas.events.storage")


def _extract_val(val: Any) -> str:
    """Safely extract string value from an enum or primitive."""
    if hasattr(val, "value"):
        return str(val.value)
    return str(val) if val is not None else ""


class EventStorage:
    """Thread-safe SQLite storage for ATLAS persistent event history."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            project_root = Path(__file__).resolve().parent.parent.parent
            data_dir = project_root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = data_dir / "atlas_events.db"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create table and indexes if they do not exist."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        event_id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        source TEXT NOT NULL,
                        source_device TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        status TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        confidence REAL,
                        person_id TEXT,
                        object_id TEXT,
                        action TEXT,
                        location TEXT,
                        evidence_json TEXT,
                        metadata_json TEXT,
                        created_at REAL NOT NULL
                    )
                """)
                # Create performance indexes
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp DESC)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_status ON events(status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_person ON events(person_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_object ON events(object_id)")

                # Step 4: Persistent Intelligence & Risk Analyses table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS analyses (
                        analysis_id TEXT PRIMARY KEY,
                        timestamp TEXT NOT NULL,
                        situation TEXT NOT NULL,
                        risk_level TEXT NOT NULL,
                        risk_score REAL NOT NULL,
                        uncertainty TEXT NOT NULL,
                        human_verification_required INTEGER NOT NULL,
                        event_ids_json TEXT NOT NULL,
                        full_json TEXT NOT NULL,
                        created_at REAL NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_ts ON analyses(timestamp DESC)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_risk ON analyses(risk_level)")

                # Step 5: Persistent Incidents, Alerts, and Immutable Audit Trail
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS incidents (
                        incident_id TEXT PRIMARY KEY,
                        incident_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        risk_score REAL NOT NULL,
                        uncertainty TEXT NOT NULL,
                        escalation_state TEXT NOT NULL,
                        is_acknowledged INTEGER NOT NULL,
                        is_resolved INTEGER NOT NULL,
                        source_event_ids_json TEXT NOT NULL,
                        analysis_id TEXT,
                        risk_id TEXT,
                        triggering_rule_ids_json TEXT NOT NULL,
                        full_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_type ON incidents(incident_type)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at DESC)")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS alerts (
                        alert_id TEXT PRIMARY KEY,
                        incident_id TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        status TEXT NOT NULL,
                        is_acknowledged INTEGER NOT NULL,
                        is_escalated INTEGER NOT NULL,
                        full_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_incident ON alerts(incident_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC)")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        audit_id TEXT PRIMARY KEY,
                        timestamp TEXT NOT NULL,
                        actor_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        target_type TEXT NOT NULL,
                        target_id TEXT NOT NULL,
                        previous_state TEXT,
                        new_state TEXT,
                        reason TEXT,
                        metadata_json TEXT,
                        created_at REAL NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_logs(target_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(timestamp DESC)")

                # Step 6.4: Persistent Evidence Vault records
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS evidence_records (
                        evidence_id TEXT PRIMARY KEY,
                        incident_id TEXT NOT NULL,
                        source_event_id TEXT,
                        source_event_timestamp TEXT,
                        source_frame_timestamp TEXT,
                        captured_at TEXT NOT NULL,
                        temporal_relation TEXT NOT NULL DEFAULT 'CAPTURED_AFTER_EVENT',
                        artifact_type TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        file_size INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        width INTEGER,
                        height INTEGER,
                        capture_status TEXT NOT NULL,
                        retention_status TEXT NOT NULL,
                        failure_reason TEXT,
                        metadata_json TEXT NOT NULL,
                        created_at REAL NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_evidence_incident ON evidence_records(incident_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_evidence_status ON evidence_records(capture_status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_evidence_created ON evidence_records(created_at DESC)")

                # Step 6.4.1 Non-destructive migration for temporal provenance columns
                try:
                    cursor.execute("ALTER TABLE evidence_records ADD COLUMN source_event_timestamp TEXT")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE evidence_records ADD COLUMN source_frame_timestamp TEXT")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE evidence_records ADD COLUMN temporal_relation TEXT DEFAULT 'CAPTURED_AFTER_EVENT'")
                except Exception:
                    pass

                # Step 6.5: Persistent Multi-Source Fused Situations
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS fused_situations (
                        fusion_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        observation_ids_json TEXT NOT NULL,
                        source_types_json TEXT NOT NULL,
                        aggregate_confidence REAL NOT NULL,
                        uncertainty REAL NOT NULL,
                        relationship_state TEXT NOT NULL,
                        completeness TEXT NOT NULL,
                        situation_summary TEXT NOT NULL,
                        data_json TEXT NOT NULL,
                        created_at_epoch REAL NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_fused_created ON fused_situations(created_at_epoch DESC)")

                # Step 7: Persistent Notifications & Escalation records
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS notifications (
                        notification_id TEXT PRIMARY KEY,
                        incident_id TEXT NOT NULL,
                        recipient_user_id TEXT NOT NULL,
                        recipient_role TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        channel TEXT NOT NULL,
                        status TEXT NOT NULL,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        escalation_level INTEGER NOT NULL,
                        attempt_count INTEGER NOT NULL,
                        max_retries INTEGER NOT NULL,
                        failure_reason TEXT,
                        created_at TEXT NOT NULL,
                        sent_at TEXT,
                        acknowledged_at TEXT,
                        acknowledged_by TEXT,
                        metadata_json TEXT,
                        created_at_epoch REAL NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_notif_incident ON notifications(incident_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_notif_recipient ON notifications(recipient_user_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_notif_status ON notifications(status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at_epoch DESC)")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS escalation_records (
                        incident_id TEXT PRIMARY KEY,
                        severity TEXT NOT NULL,
                        current_level INTEGER NOT NULL,
                        is_active INTEGER NOT NULL,
                        started_at TEXT NOT NULL,
                        last_escalated_at TEXT,
                        next_escalation_epoch REAL,
                        stopped_reason TEXT,
                        notifications_sent_count INTEGER NOT NULL,
                        acknowledged_by TEXT,
                        acknowledged_at TEXT,
                        updated_at_epoch REAL NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_esc_active ON escalation_records(is_active)")

                # Step 8: Persistent User Accounts & Login Audits
                cursor.execute("""
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
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_username ON user_accounts(username)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_role ON user_accounts(role)")

                cursor.execute("""
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
                        details_json TEXT,
                        created_at_epoch REAL NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_login_audit_user ON login_audits(user_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_login_audit_time ON login_audits(created_at_epoch DESC)")

                # Non-destructive data normalization for legacy enum repr strings in SQLite
                cursor.execute("UPDATE alerts SET status = 'RESOLVED' WHERE status LIKE '%RESOLVED'")
                cursor.execute("UPDATE alerts SET status = 'DISMISSED' WHERE status LIKE '%DISMISSED'")
                cursor.execute("UPDATE alerts SET status = 'ACKNOWLEDGED' WHERE status LIKE '%ACKNOWLEDGED'")
                cursor.execute("UPDATE alerts SET status = 'ACTIVE' WHERE status LIKE '%ACTIVE'")
                cursor.execute("UPDATE alerts SET severity = 'LOW' WHERE severity LIKE '%LOW'")
                cursor.execute("UPDATE alerts SET severity = 'MEDIUM' WHERE severity LIKE '%MEDIUM'")
                cursor.execute("UPDATE alerts SET severity = 'HIGH' WHERE severity LIKE '%HIGH'")
                cursor.execute("UPDATE alerts SET severity = 'CRITICAL' WHERE severity LIKE '%CRITICAL'")
                cursor.execute("UPDATE incidents SET status = 'RESOLVED' WHERE status LIKE '%RESOLVED'")
                cursor.execute("UPDATE incidents SET status = 'DISMISSED' WHERE status LIKE '%DISMISSED'")
                cursor.execute("UPDATE incidents SET status = 'ACKNOWLEDGED' WHERE status LIKE '%ACKNOWLEDGED'")
                cursor.execute("UPDATE incidents SET status = 'ACTIVE' WHERE status LIKE '%ACTIVE'")
                cursor.execute("UPDATE incidents SET incident_type = 'POSSIBLE_FALL' WHERE incident_type LIKE '%POSSIBLE_FALL'")
                cursor.execute("UPDATE incidents SET incident_type = 'UNEXPECTED_PERSON_PRESENCE' WHERE incident_type LIKE '%UNEXPECTED_PERSON_PRESENCE'")
                cursor.execute("UPDATE incidents SET incident_type = 'POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL' WHERE incident_type LIKE '%POSSIBLE_UNAUTHORIZED_OBJECT_REMOVAL'")
                cursor.execute("UPDATE incidents SET incident_type = 'POSSIBLE_HIGH_RISK_INTERACTION' WHERE incident_type LIKE '%POSSIBLE_HIGH_RISK_INTERACTION'")

                conn.commit()

    def _row_to_event(self, row: sqlite3.Row) -> ATLASEvent:
        """Convert a database row into an ATLASEvent model."""
        evidence = json.loads(row["evidence_json"]) if row["evidence_json"] else None
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}

        return ATLASEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            source=row["source"],
            source_device=row["source_device"],
            timestamp=row["timestamp"],
            status=row["status"],
            severity=row["severity"],
            confidence=row["confidence"],
            person_id=row["person_id"],
            object_id=row["object_id"],
            action=row["action"],
            location=row["location"],
            evidence=evidence,
            metadata=metadata,
        )

    def save_event(self, event: ATLASEvent) -> None:
        """Persist an event record to SQLite."""
        evidence_str = json.dumps(event.evidence) if event.evidence else None
        metadata_str = json.dumps(event.metadata) if event.metadata else None
        now = time.time()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO events (
                        event_id, event_type, source, source_device, timestamp,
                        status, severity, confidence, person_id, object_id,
                        action, location, evidence_json, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        status = excluded.status,
                        severity = excluded.severity,
                        action = excluded.action,
                        evidence_json = excluded.evidence_json,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        event.event_id,
                        event.event_type,
                        event.source,
                        event.source_device,
                        event.timestamp,
                        event.status,
                        event.severity,
                        event.confidence,
                        str(event.person_id) if event.person_id is not None else None,
                        str(event.object_id) if event.object_id is not None else None,
                        event.action,
                        event.location,
                        evidence_str,
                        metadata_str,
                        now,
                    ),
                )
                conn.commit()

    def update_event_status(self, event_id: str, new_status: str) -> bool:
        """Update the lifecycle status of an event."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE events SET status = ? WHERE event_id = ?",
                    (new_status, event_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def get_event(self, event_id: str) -> ATLASEvent | None:
        """Retrieve a specific event by its ID."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM events WHERE event_id = ?", (event_id,))
                row = cursor.fetchone()
                return self._row_to_event(row) if row else None

    def get_events_by_ids(self, event_ids: Sequence[str]) -> list[ATLASEvent]:
        """Retrieve multiple events matching the given sequence of IDs."""
        if not event_ids:
            return []
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in event_ids)
                cursor.execute(
                    f"SELECT * FROM events WHERE event_id IN ({placeholders}) ORDER BY timestamp ASC",
                    tuple(event_ids),
                )
                rows = cursor.fetchall()
                return [self._row_to_event(r) for r in rows]

    def get_recent_events(self, limit: int = 50, event_type: str | None = None) -> list[ATLASEvent]:
        """Query recent events chronologically descending."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if event_type:
                    cursor.execute(
                        "SELECT * FROM events WHERE event_type = ? ORDER BY timestamp DESC LIMIT ?",
                        (event_type, limit),
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM events ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    )
                rows = cursor.fetchall()
                return [self._row_to_event(r) for r in rows]

    def get_active_events(self) -> list[ATLASEvent]:
        """Query currently active (unresolved) events."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM events WHERE status = ? ORDER BY timestamp DESC",
                    (EventStatus.ACTIVE.value,),
                )
                rows = cursor.fetchall()
                return [self._row_to_event(r) for r in rows]

    def get_events_for_track(self, track_id: str | int, is_person: bool = True) -> list[ATLASEvent]:
        """Query all events associated with a specific person or object track ID."""
        field = "person_id" if is_person else "object_id"
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT * FROM events WHERE {field} = ? ORDER BY timestamp DESC",
                    (str(track_id),),
                )
                rows = cursor.fetchall()
                return [self._row_to_event(r) for r in rows]

    def count_events(self) -> int:
        """Return total count of recorded events."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM events")
                return cursor.fetchone()[0]

    def save_analysis(self, decision_context: Any) -> None:
        """Persist structured intelligence, rule evaluation, and risk decision context."""
        now = time.time()
        # Handle Pydantic model or dict
        if hasattr(decision_context, "model_dump"):
            data = decision_context.model_dump()
        else:
            data = dict(decision_context)

        analysis_id = str(data.get("analysis_id", ""))
        timestamp = str(data.get("timestamp", ""))
        situation = str(data.get("llm_verification", {}).get("situation", "Observed scene activity"))
        risk = data.get("risk", {})
        risk_level = str(risk.get("level", "LOW"))
        risk_score = float(risk.get("score", 0.0))
        uncertainty = str(risk.get("uncertainty", "moderate"))
        human_verif = 1 if risk.get("human_verification_required", True) else 0

        event_ids = list(risk.get("supporting_event_ids", []))
        event_ids_json = json.dumps(event_ids)
        full_json = json.dumps(data)

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO analyses (
                        analysis_id, timestamp, situation, risk_level, risk_score,
                        uncertainty, human_verification_required, event_ids_json,
                        full_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis_id,
                        timestamp,
                        situation,
                        risk_level,
                        risk_score,
                        uncertainty,
                        human_verif,
                        event_ids_json,
                        full_json,
                        now,
                    ),
                )
                conn.commit()

    def get_latest_analysis(self) -> dict[str, Any] | None:
        """Retrieve the most recent decision analysis record."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT full_json FROM analyses ORDER BY created_at DESC LIMIT 1")
                row = cursor.fetchone()
                if row:
                    return json.loads(row["full_json"])
                return None

    def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        """Retrieve a specific decision analysis record by ID."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT full_json FROM analyses WHERE analysis_id = ?", (analysis_id,))
                row = cursor.fetchone()
                if row:
                    return json.loads(row["full_json"])
                return None

    def get_recent_analyses(self, limit: int = 20) -> list[dict[str, Any]]:
        """Retrieve recent decision analysis records."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT full_json FROM analyses ORDER BY created_at DESC LIMIT ?", (limit,))
                rows = cursor.fetchall()
                return [json.loads(r["full_json"]) for r in rows]



    # ------------------------------------------------------------------------
    # Incident Storage Methods
    # ------------------------------------------------------------------------
    def save_incident(self, incident: Any) -> None:
        """Persist or update an Incident record in SQLite."""
        if hasattr(incident, "model_dump"):
            data = incident.model_dump()
        else:
            data = dict(incident)

        now = time.time()
        incident_id = str(data["incident_id"])
        incident_type = _extract_val(data.get("incident_type", "GENERAL_SAFETY_REVIEW"))
        status = _extract_val(data.get("status", "ACTIVE"))
        severity = _extract_val(data.get("severity", "LOW"))
        risk_score = float(data.get("risk_score", 0.0))
        uncertainty = _extract_val(data.get("uncertainty", "moderate"))
        escalation_state = _extract_val(data.get("escalation_state", "NOT_ESCALATED"))
        is_acknowledged = 1 if data.get("is_acknowledged", False) else 0
        is_resolved = 1 if data.get("is_resolved", False) else 0
        source_event_ids_json = json.dumps(data.get("source_event_ids", []))
        analysis_id = data.get("analysis_id")
        risk_id = data.get("risk_id")
        triggering_rule_ids_json = json.dumps(data.get("triggering_rule_ids", []))
        full_json = json.dumps(data)

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO incidents (
                        incident_id, incident_type, status, severity, risk_score,
                        uncertainty, escalation_state, is_acknowledged, is_resolved,
                        source_event_ids_json, analysis_id, risk_id,
                        triggering_rule_ids_json, full_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(incident_id) DO UPDATE SET
                        status = excluded.status,
                        severity = excluded.severity,
                        risk_score = excluded.risk_score,
                        uncertainty = excluded.uncertainty,
                        escalation_state = excluded.escalation_state,
                        is_acknowledged = excluded.is_acknowledged,
                        is_resolved = excluded.is_resolved,
                        source_event_ids_json = excluded.source_event_ids_json,
                        analysis_id = excluded.analysis_id,
                        risk_id = excluded.risk_id,
                        triggering_rule_ids_json = excluded.triggering_rule_ids_json,
                        full_json = excluded.full_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        incident_id,
                        incident_type,
                        status,
                        severity,
                        risk_score,
                        uncertainty,
                        escalation_state,
                        is_acknowledged,
                        is_resolved,
                        source_event_ids_json,
                        analysis_id,
                        risk_id,
                        triggering_rule_ids_json,
                        full_json,
                        now,
                        now,
                    ),
                )
                conn.commit()

    def get_incident(self, incident_id: str) -> Any | None:
        """Retrieve an incident by ID."""
        from atlas.incidents.schema import Incident
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT full_json FROM incidents WHERE incident_id = ?", (incident_id,))
                row = cursor.fetchone()
                if row:
                    return Incident.model_validate_json(row["full_json"])
                return None

    def get_active_incidents(self) -> list[Any]:
        """Query all currently unresolved / un-dismissed incidents."""
        from atlas.incidents.schema import Incident
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT full_json FROM incidents WHERE status NOT IN ('RESOLVED', 'DISMISSED') ORDER BY created_at DESC"
                )
                rows = cursor.fetchall()
                return [Incident.model_validate_json(r["full_json"]) for r in rows]

    def get_recent_incidents(self, limit: int = 50) -> list[Any]:
        """Query recent incidents chronologically descending."""
        from atlas.incidents.schema import Incident
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT full_json FROM incidents ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
                rows = cursor.fetchall()
                return [Incident.model_validate_json(r["full_json"]) for r in rows]

    # ------------------------------------------------------------------------
    # Alert Storage Methods
    # ------------------------------------------------------------------------
    def save_alert(self, alert: Any) -> None:
        """Persist or update an Alert record in SQLite."""
        if hasattr(alert, "model_dump"):
            data = alert.model_dump()
        else:
            data = dict(alert)

        now = time.time()
        alert_id = str(data["alert_id"])
        incident_id = str(data["incident_id"])
        severity = _extract_val(data.get("severity", "LOW"))
        status = _extract_val(data.get("status", "ACTIVE"))
        is_acknowledged = 1 if data.get("is_acknowledged", False) else 0
        is_escalated = 1 if data.get("is_escalated", False) else 0
        full_json = json.dumps(data)

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO alerts (
                        alert_id, incident_id, severity, status,
                        is_acknowledged, is_escalated, full_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(alert_id) DO UPDATE SET
                        severity = excluded.severity,
                        status = excluded.status,
                        is_acknowledged = excluded.is_acknowledged,
                        is_escalated = excluded.is_escalated,
                        full_json = excluded.full_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        alert_id,
                        incident_id,
                        severity,
                        status,
                        is_acknowledged,
                        is_escalated,
                        full_json,
                        now,
                        now,
                    ),
                )
                conn.commit()

    def get_alert(self, alert_id: str) -> Any | None:
        """Retrieve an alert by ID."""
        from atlas.alerts.schema import Alert
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT full_json FROM alerts WHERE alert_id = ?", (alert_id,))
                row = cursor.fetchone()
                return Alert.model_validate_json(row["full_json"]) if row else None

    def get_alert_for_incident(self, incident_id: str) -> Any | None:
        """Retrieve active or most recent alert for a specific incident."""
        from atlas.alerts.schema import Alert
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT full_json FROM alerts WHERE incident_id = ? ORDER BY created_at DESC LIMIT 1",
                    (incident_id,),
                )
                row = cursor.fetchone()
                return Alert.model_validate_json(row["full_json"]) if row else None

    def get_active_alerts(self) -> list[Any]:
        """Query all active (unresolved) alerts."""
        from atlas.alerts.schema import Alert
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT full_json FROM alerts WHERE status NOT IN ('RESOLVED', 'DISMISSED') ORDER BY created_at DESC"
                )
                rows = cursor.fetchall()
                return [Alert.model_validate_json(r["full_json"]) for r in rows]

    def get_recent_alerts(self, limit: int = 50) -> list[Any]:
        """Query recent alerts chronologically descending."""
        from atlas.alerts.schema import Alert
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT full_json FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,))
                rows = cursor.fetchall()
                return [Alert.model_validate_json(r["full_json"]) for r in rows]

    # ------------------------------------------------------------------------
    # Audit Storage Methods
    # ------------------------------------------------------------------------
    def save_audit_record(self, entry: Any) -> None:
        """Append an immutable audit log record to SQLite."""
        if hasattr(entry, "model_dump"):
            data = entry.model_dump()
        else:
            data = dict(entry)

        now = time.time()
        audit_id = str(data["audit_id"])
        timestamp = str(data.get("timestamp", ""))
        actor_id = str(data.get("actor_id", "system"))
        action = _extract_val(data.get("action", ""))
        target_type = _extract_val(data.get("target_type", ""))
        target_id = str(data.get("target_id", ""))
        prev_state = _extract_val(data.get("previous_state")) if data.get("previous_state") is not None else None
        new_state = _extract_val(data.get("new_state")) if data.get("new_state") is not None else None
        reason = data.get("reason")
        metadata_json = json.dumps(data.get("metadata", {}))

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO audit_logs (
                        audit_id, timestamp, actor_id, action,
                        target_type, target_id, previous_state, new_state,
                        reason, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_id,
                        timestamp,
                        actor_id,
                        action,
                        target_type,
                        target_id,
                        prev_state,
                        new_state,
                        reason,
                        metadata_json,
                        now,
                    ),
                )
                conn.commit()

    def get_audit_records_for_target(self, target_id: str, limit: int = 50) -> list[Any]:
        """Query audit trail for a specific target entity."""
        from atlas.audit.schema import AuditRecord
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM audit_logs WHERE target_id = ? ORDER BY created_at ASC LIMIT ?",
                    (target_id, limit),
                )
                rows = cursor.fetchall()
                results = []
                for r in rows:
                    meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
                    raw_act = r["action"]
                    clean_act = raw_act.split(".")[-1] if raw_act else "INCIDENT_UPDATED"
                    results.append(
                        AuditRecord(
                            audit_id=r["audit_id"],
                            timestamp=r["timestamp"],
                            actor_id=r["actor_id"],
                            action=clean_act,
                            target_type=r["target_type"],
                            target_id=r["target_id"],
                            previous_state=r["previous_state"],
                            new_state=r["new_state"],
                            reason=r["reason"],
                            metadata=meta,
                        )
                    )
                return results

    def get_recent_audit_records(self, limit: int = 50) -> list[Any]:
        """Query recent system audit records."""
        from atlas.audit.schema import AuditRecord
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
                rows = cursor.fetchall()
                results = []
                for r in rows:
                    meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
                    raw_act = r["action"]
                    clean_act = raw_act.split(".")[-1] if raw_act else "INCIDENT_UPDATED"
                    results.append(
                        AuditRecord(
                            audit_id=r["audit_id"],
                            timestamp=r["timestamp"],
                            actor_id=r["actor_id"],
                            action=clean_act,
                            target_type=r["target_type"],
                            target_id=r["target_id"],
                            previous_state=r["previous_state"],
                            new_state=r["new_state"],
                            reason=r["reason"],
                            metadata=meta,
                        )
                    )
                return results

    # ========================================================================
    # Step 6.4: Evidence Vault Methods
    # ========================================================================

    def save_evidence_record(self, record: Any) -> None:
        """Persist or update an EvidenceRecord in SQLite."""
        raw_capture = _extract_val(getattr(record, "capture_status", "CAPTURED"))
        raw_retention = _extract_val(getattr(record, "retention_status", "ACTIVE"))
        raw_temporal = _extract_val(getattr(record, "temporal_relation", "CAPTURED_AFTER_EVENT"))
        meta_json = json.dumps(getattr(record, "metadata", {}))
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO evidence_records (
                        evidence_id, incident_id, source_event_id,
                        source_event_timestamp, source_frame_timestamp,
                        captured_at, temporal_relation,
                        artifact_type, mime_type, file_path, file_size, sha256,
                        width, height, capture_status, retention_status,
                        failure_reason, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.evidence_id,
                    record.incident_id,
                    record.source_event_id,
                    getattr(record, "source_event_timestamp", None),
                    getattr(record, "source_frame_timestamp", None),
                    record.captured_at,
                    raw_temporal,
                    record.artifact_type,
                    record.mime_type,
                    record.file_path,
                    record.file_size,
                    record.sha256,
                    record.width,
                    record.height,
                    raw_capture,
                    raw_retention,
                    record.failure_reason,
                    meta_json,
                    record.created_at,
                ))
                conn.commit()

    def _row_to_evidence_record(self, r: sqlite3.Row) -> Any:
        from atlas.evidence.schema import EvidenceRecord, CaptureStatus, RetentionStatus, TemporalRelation
        meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
        raw_cap = r["capture_status"].split(".")[-1] if r["capture_status"] else "CAPTURED"
        raw_ret = r["retention_status"].split(".")[-1] if r["retention_status"] else "ACTIVE"

        keys = r.keys()
        raw_temp = r["temporal_relation"].split(".")[-1] if "temporal_relation" in keys and r["temporal_relation"] else "CAPTURED_AFTER_EVENT"
        source_event_ts = r["source_event_timestamp"] if "source_event_timestamp" in keys else None
        source_frame_ts = r["source_frame_timestamp"] if "source_frame_timestamp" in keys else None

        return EvidenceRecord(
            evidence_id=r["evidence_id"],
            incident_id=r["incident_id"],
            source_event_id=r["source_event_id"],
            source_event_timestamp=source_event_ts,
            source_frame_timestamp=source_frame_ts,
            captured_at=r["captured_at"],
            temporal_relation=TemporalRelation(raw_temp),
            artifact_type=r["artifact_type"],
            mime_type=r["mime_type"],
            file_path=r["file_path"],
            file_size=r["file_size"],
            sha256=r["sha256"],
            width=r["width"],
            height=r["height"],
            capture_status=CaptureStatus(raw_cap),
            retention_status=RetentionStatus(raw_ret),
            failure_reason=r["failure_reason"],
            metadata=meta,
            created_at=r["created_at"],
        )

    def get_evidence_record(self, evidence_id: str) -> Any | None:
        """Retrieve evidence record by ID."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM evidence_records WHERE evidence_id = ?", (evidence_id,))
                row = cursor.fetchone()
                if row is None:
                    return None
                return self._row_to_evidence_record(row)

    def get_evidence_for_incident(self, incident_id: str) -> list[Any]:
        """Retrieve all evidence records linked to a specific incident."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM evidence_records WHERE incident_id = ? ORDER BY created_at ASC",
                    (incident_id,)
                )
                rows = cursor.fetchall()
                return [self._row_to_evidence_record(r) for r in rows]

    def has_evidence_for_incident(self, incident_id: str) -> bool:
        """Check if an incident already has evidence records."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM evidence_records WHERE incident_id = ? AND capture_status = 'CAPTURED' LIMIT 1",
                    (incident_id,)
                )
                return cursor.fetchone() is not None

    def get_recent_evidence_records(self, limit: int = 50) -> list[Any]:
        """Retrieve most recent evidence records."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM evidence_records ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                )
                rows = cursor.fetchall()
                return [self._row_to_evidence_record(r) for r in rows]

    def save_fused_situation(self, fused: Any) -> None:
        """Persist a FusedSituation snapshot into SQLite storage."""
        from atlas.fusion.schema import FusedSituation
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                created_epoch = time.time()
                try:
                    dt = datetime.fromisoformat(fused.created_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    created_epoch = dt.timestamp()
                except Exception:
                    pass

                rel_val = (
                    fused.relationship_state.value
                    if hasattr(fused.relationship_state, "value")
                    else str(fused.relationship_state)
                )
                src_types_val = [
                    (s.value if hasattr(s, "value") else str(s)) for s in fused.source_types
                ]

                data_payload = fused.model_dump() if hasattr(fused, "model_dump") else dict(fused)

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO fused_situations (
                        fusion_id, created_at, observation_ids_json, source_types_json,
                        aggregate_confidence, uncertainty, relationship_state,
                        completeness, situation_summary, data_json, created_at_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fused.fusion_id,
                        fused.created_at,
                        json.dumps(fused.observation_ids),
                        json.dumps(src_types_val),
                        float(fused.aggregate_confidence),
                        float(fused.uncertainty),
                        rel_val,
                        fused.completeness,
                        fused.situation_summary,
                        json.dumps(data_payload),
                        created_epoch,
                    )
                )
                conn.commit()

    def get_fused_situation(self, fusion_id: str) -> Any | None:
        """Retrieve a specific FusedSituation by ID."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM fused_situations WHERE fusion_id = ?",
                    (fusion_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_fused_situation(row)

    def get_recent_fused_situations(self, limit: int = 50) -> list[Any]:
        """Retrieve most recent FusedSituation snapshots sorted by created_at_epoch descending."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM fused_situations ORDER BY created_at_epoch DESC LIMIT ?",
                    (limit,)
                )
                rows = cursor.fetchall()
                return [self._row_to_fused_situation(r) for r in rows]

    def _row_to_fused_situation(self, row: sqlite3.Row) -> Any:
        """Deserialize a SQLite row into a FusedSituation object."""
        from atlas.fusion.schema import FusedSituation
        data = json.loads(row["data_json"])
        return FusedSituation.model_validate(data)

    # ---------------------------------------------------------
    # Step 7: Notifications & Escalation Storage Methods
    # ---------------------------------------------------------
    def save_notification(self, notification: Any) -> None:
        """Insert or update a Notification record in SQLite."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO notifications (
                        notification_id, incident_id, recipient_user_id, recipient_role,
                        severity, channel, status, title, summary,
                        escalation_level, attempt_count, max_retries, failure_reason,
                        created_at, sent_at, acknowledged_at, acknowledged_by,
                        metadata_json, created_at_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    notification.notification_id,
                    notification.incident_id,
                    notification.recipient_user_id,
                    _extract_val(notification.recipient_role),
                    str(notification.severity),
                    _extract_val(notification.channel),
                    _extract_val(notification.status),
                    notification.title,
                    notification.summary,
                    notification.escalation_level,
                    notification.attempt_count,
                    notification.max_retries,
                    notification.failure_reason,
                    notification.created_at,
                    notification.sent_at,
                    notification.acknowledged_at,
                    notification.acknowledged_by,
                    json.dumps(notification.metadata),
                    time.time(),
                ))
                conn.commit()

    def get_notification(self, notification_id: str) -> Any | None:
        """Retrieve a single notification by ID."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM notifications WHERE notification_id = ?", (notification_id,))
                row = cursor.fetchone()
                return self._row_to_notification(row) if row else None

    def get_notifications(
        self,
        incident_id: str | None = None,
        recipient_user_id: str | None = None,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[Any]:
        """Retrieve filtered notifications ordered by most recent."""
        query = "SELECT * FROM notifications WHERE 1=1"
        params: list[Any] = []

        if incident_id:
            query += " AND incident_id = ?"
            params.append(incident_id)

        if recipient_user_id:
            query += " AND recipient_user_id = ?"
            params.append(recipient_user_id)

        if unread_only:
            query += " AND status NOT IN ('ACKNOWLEDGED', 'CANCELLED')"

        query += " ORDER BY created_at_epoch DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, tuple(params))
                rows = cursor.fetchall()
                return [self._row_to_notification(r) for r in rows]

    def get_notifications_for_incident(self, incident_id: str) -> list[Any]:
        """Retrieve all notifications for an incident."""
        return self.get_notifications(incident_id=incident_id, limit=200)

    def acknowledge_notification(self, notification_id: str, actor_id: str) -> Any | None:
        """Mark a notification as acknowledged."""
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE notifications
                    SET status = 'ACKNOWLEDGED', acknowledged_at = ?, acknowledged_by = ?
                    WHERE notification_id = ?
                """, (now_iso, actor_id, notification_id))
                conn.commit()

                cursor.execute("SELECT * FROM notifications WHERE notification_id = ?", (notification_id,))
                row = cursor.fetchone()
                return self._row_to_notification(row) if row else None

    def _row_to_notification(self, row: sqlite3.Row) -> Any:
        """Deserialize a SQLite row into a Notification model."""
        from atlas.authority.models import Role
        from atlas.notifications.schema import Notification, NotificationChannel, NotificationStatus
        return Notification(
            notification_id=row["notification_id"],
            incident_id=row["incident_id"],
            recipient_user_id=row["recipient_user_id"],
            recipient_role=Role(row["recipient_role"]),
            severity=row["severity"],
            channel=NotificationChannel(row["channel"]),
            status=NotificationStatus(row["status"]),
            title=row["title"],
            summary=row["summary"],
            escalation_level=row["escalation_level"],
            attempt_count=row["attempt_count"],
            max_retries=row["max_retries"],
            failure_reason=row["failure_reason"],
            created_at=row["created_at"],
            sent_at=row["sent_at"],
            acknowledged_at=row["acknowledged_at"],
            acknowledged_by=row["acknowledged_by"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    def save_escalation_record(self, record: Any) -> None:
        """Save or update an incident's escalation state record."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO escalation_records (
                        incident_id, severity, current_level, is_active,
                        started_at, last_escalated_at, next_escalation_epoch,
                        stopped_reason, notifications_sent_count,
                        acknowledged_by, acknowledged_at, updated_at_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.incident_id,
                    str(record.severity),
                    record.current_level,
                    1 if record.is_active else 0,
                    record.started_at,
                    record.last_escalated_at,
                    record.next_escalation_epoch,
                    record.stopped_reason,
                    record.notifications_sent_count,
                    record.acknowledged_by,
                    record.acknowledged_at,
                    time.time(),
                ))
                conn.commit()

    def get_escalation_record(self, incident_id: str) -> Any | None:
        """Retrieve an escalation state record by incident ID."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM escalation_records WHERE incident_id = ?", (incident_id,))
                row = cursor.fetchone()
                return self._row_to_escalation_record(row) if row else None

    def get_active_escalations(self) -> list[Any]:
        """Retrieve all currently active escalation records."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM escalation_records WHERE is_active = 1 ORDER BY updated_at_epoch DESC")
                rows = cursor.fetchall()
                return [self._row_to_escalation_record(r) for r in rows]

    def _row_to_escalation_record(self, row: sqlite3.Row) -> Any:
        """Deserialize a SQLite row into an EscalationStateRecord."""
        from atlas.notifications.schema import EscalationStateRecord
        return EscalationStateRecord(
            incident_id=row["incident_id"],
            severity=row["severity"],
            current_level=row["current_level"],
            is_active=bool(row["is_active"]),
            started_at=row["started_at"],
            last_escalated_at=row["last_escalated_at"],
            next_escalation_epoch=row["next_escalation_epoch"],
            stopped_reason=row["stopped_reason"],
            notifications_sent_count=row["notifications_sent_count"],
            acknowledged_by=row["acknowledged_by"],
            acknowledged_at=row["acknowledged_at"],
        )

    def close(self) -> None:
        """Close storage resources."""
        pass



_global_storage: EventStorage | None = None


def get_event_storage(db_path: str | Path | None = None, reload: bool = False) -> EventStorage:
    """Get or initialize singleton event storage."""
    global _global_storage
    if _global_storage is None or reload or (db_path is not None and Path(db_path) != _global_storage.db_path):
        _global_storage = EventStorage(db_path=db_path)
    return _global_storage
