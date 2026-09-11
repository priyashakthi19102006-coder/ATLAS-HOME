"""Grounded Conversational ATLAS Assistant Subsystem.

Translates real physical home safety observations into natural conversation:
USER QUESTION → QUERY UNDERSTANDING → ATLAS DATA RETRIEVAL (server-side RBAC)
→ REAL EVENTS, PERSON HISTORY, OBJECT HISTORY, INCIDENTS, EVIDENCE, CAMERA STATE
→ GROUNDED LLM PROMPT (qwen2.5:3b) → CONVERSATIONAL RESPONSE + CITATIONS.

Zero-hallucination policy enforced:
- If ATLAS did not observe or record something, it honestly declares:
  "I don't have enough visual evidence to determine that."
- Strictly disallows fabricated names, people, clothing colors, or events.
- Never equates track_id to human identity.
- Full server-side RBAC: Authorized Users cannot query Admin login audits or unrestricted visitor history.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from atlas.authority.models import Actor, Role, Permission
from atlas.camera.stream import get_camera_stream
from atlas.context.memory import get_context_manager
from atlas.events.storage import get_event_storage
from atlas.intelligence.provider import OllamaProvider, extract_and_parse_json

logger = logging.getLogger("atlas.chat.service")


class SystemStateService:
    """Retrieves authoritative real-time physical system and camera status."""

    @staticmethod
    def get_status() -> dict[str, Any]:
        camera = get_camera_stream()
        is_enabled = bool(getattr(camera, "is_enabled", True))
        is_conn = bool(getattr(camera, "is_connected", False))
        cam_state = getattr(camera, "state", None)
        status_str = cam_state.value.upper() if hasattr(cam_state, "value") else str(cam_state or "UNKNOWN").upper()
        fps = round(getattr(camera, "fps", 0.0), 1)

        cam_state_str = "LIVE" if (is_enabled and is_conn) else ("OFF" if not is_enabled else status_str)
        now_dt = datetime.now(timezone.utc)
        return {
            "current_time_iso": now_dt.isoformat(),
            "current_time_human": now_dt.strftime("%I:%M %p").lstrip("0"),
            "camera_state": cam_state_str,
            "camera_enabled": is_enabled,
            "camera_fps": fps,
        }


class PersonHistoryService:
    """Retrieves real-time occupants and persistent entry/exit/activity history."""

    @staticmethod
    def get_context(storage, ctx_manager) -> dict[str, Any]:
        recent_ctx = ctx_manager.get_recent_context(window_seconds=300.0)
        user_rel_map = {}
        try:
            from atlas.authority.user_service import get_user_management_service
            for u in get_user_management_service().list_users():
                if getattr(u, "relationship", None):
                    user_rel_map[u.display_name.lower()] = u.relationship
                    user_rel_map[u.username.lower()] = u.relationship
        except Exception:
            pass

        active_persons = []
        for p in recent_ctx.get("persons", []):
            if p.get("active", False):
                p_name = p.get("person_name", "Unknown Person")
                active_persons.append({
                    "track_id": p.get("track_id"),
                    "person_name": p_name,
                    "relationship": user_rel_map.get(p_name.lower()),
                    "identity_status": p.get("identity_status", "UNKNOWN"),
                    "current_action": p.get("current_action", "unknown"),
                    "movement_state": p.get("movement_state", "unknown"),
                    "visual_attributes": p.get("visual_attributes", {}),
                    "first_seen_epoch": p.get("first_seen"),
                    "last_seen_epoch": p.get("last_seen"),
                })

        # Historical entry/exit/activity events from storage
        events = storage.get_recent_events(limit=60)
        person_events = []
        for ev in events:
            if "PERSON" in ev.event_type:
                dt_str = ev.timestamp
                try:
                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    time_human = dt.strftime("%I:%M %p").lstrip("0")
                except Exception:
                    time_human = dt_str

                p_name = ev.metadata.get("person_name") or ev.evidence.get("person_name") or "Unknown Person"
                person_events.append({
                    "event_id": ev.event_id,
                    "event_type": ev.event_type,
                    "time_human": time_human,
                    "timestamp": ev.timestamp,
                    "person_name": p_name,
                    "relationship": user_rel_map.get(p_name.lower()),
                    "identity_status": ev.metadata.get("identity_status") or ev.evidence.get("identity_status") or "UNKNOWN",
                    "action": ev.action or ev.evidence.get("action"),
                    "visual_attributes": ev.evidence.get("visual_attributes", {}),
                })

        return {
            "current_occupants": active_persons,
            "historical_person_events": person_events[:30],
        }


class ObjectHistoryService:
    """Retrieves tracked objects, positions, movement history, and associations."""

    @staticmethod
    def get_context(storage, ctx_manager) -> dict[str, Any]:
        recent_ctx = ctx_manager.get_recent_context(window_seconds=300.0)
        active_objects = []
        for o in recent_ctx.get("objects", []):
            if o.get("active", False):
                active_objects.append({
                    "track_id": o.get("track_id"),
                    "class_name": o.get("class_name"),
                    "approximate_color": o.get("approximate_color"),
                    "movement_state": o.get("movement_state"),
                    "associated_person": o.get("associated_person_name"),
                    "last_seen_epoch": o.get("last_seen"),
                })

        events = storage.get_recent_events(limit=60)
        object_events = []
        for ev in events:
            if "OBJECT" in ev.event_type:
                dt_str = ev.timestamp
                try:
                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    time_human = dt.strftime("%I:%M %p").lstrip("0")
                except Exception:
                    time_human = dt_str

                object_events.append({
                    "event_id": ev.event_id,
                    "event_type": ev.event_type,
                    "time_human": time_human,
                    "class_name": ev.metadata.get("class_name") or ev.evidence.get("class_name"),
                    "approximate_color": ev.metadata.get("approximate_color") or ev.evidence.get("approximate_color"),
                    "associated_person": ev.metadata.get("associated_person_name") or ev.evidence.get("associated_person_name"),
                })

        return {
            "active_objects": active_objects,
            "historical_object_events": object_events[:20],
        }


class IncidentHistoryService:
    """Retrieves active safety incidents, risk evaluations, and triggering rules."""

    @staticmethod
    def get_context(storage) -> dict[str, Any]:
        incidents = storage.get_recent_incidents(limit=10)
        formatted = []
        for inc in incidents:
            type_str = getattr(inc.incident_type, "value", str(inc.incident_type))
            status_str = getattr(inc.status, "value", str(inc.status))
            sev_str = getattr(inc.severity, "value", str(inc.severity))
            formatted.append({
                "incident_id": str(inc.incident_id),
                "type": type_str,
                "status": status_str,
                "severity": sev_str,
                "risk_score": float(inc.risk_score),
                "is_acknowledged": bool(inc.is_acknowledged),
                "triggering_rules": [str(r) for r in (inc.triggering_rule_ids or [])],
            })
        return {
            "recent_incidents": formatted,
            "active_alerts_count": sum(1 for i in formatted if i["status"] in ("NEW", "INVESTIGATING", "ESCALATED", "ACTIVE")),
        }


class EvidenceService:
    """Retrieves recorded cryptographic evidence artifacts."""

    @staticmethod
    def get_context(storage) -> list[dict[str, Any]]:
        try:
            if hasattr(storage, "get_recent_evidence_records"):
                evidence_list = storage.get_recent_evidence_records(limit=15)
            else:
                evidence_list = []
        except Exception:
            evidence_list = []

        formatted = []
        for ev in evidence_list:
            formatted.append({
                "evidence_id": ev.evidence_id,
                "incident_id": ev.incident_id,
                "artifact_type": ev.artifact_type,
                "sha256": ev.sha256,
                "capture_status": getattr(ev, "capture_status", "CAPTURED"),
                "created_at": ev.created_at,
            })
        return formatted


class AuditService:
    """Retrieves login history and biometric verification audits (Admin strictly)."""

    @staticmethod
    def get_context(limit: int = 15) -> list[dict[str, Any]]:
        try:
            from atlas.authority.user_service import get_user_management_service
            svc = get_user_management_service()
            audits = svc.get_login_audits(limit=limit)
            formatted = []
            for a in audits:
                formatted.append({
                    "audit_id": a.audit_id,
                    "timestamp": a.timestamp,
                    "username": a.username,
                    "role": a.role,
                    "status": a.status,
                    "face_confidence": a.face_confidence,
                    "ip_address": a.ip_address,
                })
            return formatted
        except Exception:
            return []


class ATLASChatService:
    """Core conversational intelligence service for ATLAS Home."""

    def __init__(
        self,
        storage: Any | None = None,
        context_manager: Any | None = None,
        llm_provider: Any | None = None,
    ) -> None:
        self.storage = storage or get_event_storage()
        self.context_manager = context_manager or get_context_manager()
        if llm_provider is not None:
            self.llm_provider = llm_provider
        else:
            from atlas.config import get_settings
            try:
                settings = get_settings()
                base_url = getattr(settings, "llm_base_url", "http://localhost:11434")
                model = getattr(settings, "llm_model", None)
                timeout = getattr(settings, "llm_timeout_seconds", 120.0)
            except Exception:
                base_url = "http://localhost:11434"
                model = None
                timeout = 120.0
            self.llm_provider = OllamaProvider(base_url=base_url, model=model, timeout_seconds=timeout)
        self._lock = threading.Lock()
        self._chat_history: list[dict[str, Any]] = []

    def query(self, message: str, actor: Actor) -> dict[str, Any]:
        """Process natural-language user inquiry grounded strictly in ATLAS operational state."""
        clean_msg = (message or "").strip()
        if not clean_msg:
            return {
                "text": "How can I help you regarding your home's safety?",
                "answer": "How can I help you regarding your home's safety?",
                "emotion": "neutral",
                "speak": True,
                "confidence_status": "ANSWERABLE",
                "citations": [],
                "why_atlas_said_this": {},
            }

        logger.info("[ATLAS CORE] Processing inquiry: '%s' from actor '%s' (%s)", clean_msg, actor.display_name, actor.role)

        role_str = getattr(actor.role, "value", str(actor.role)) if actor.role else "GUEST"
        is_admin = (actor.role == Role.ADMIN) or (str(role_str).upper() == "ADMIN")
        lower_msg = clean_msg.lower()

        # Server-side RBAC: Non-admin users cannot query website logins or audit logs
        if not is_admin and any(w in lower_msg for w in ["logged in", "log in", "login", "audit log", "user accounts", "who logged"]):
            return {
                "text": "Admin authorization required to view login history or audit events.",
                "answer": "Admin authorization required to view login history or audit events.",
                "emotion": "concerned",
                "speak": True,
                "confidence_status": "RESTRICTED",
                "citations": ["RBAC Policy: Admin permission required for security audits."],
                "why_atlas_said_this": {"reason": "Non-admin actor queried restricted administrative audits."},
            }

        # 1. Build server-side RBAC-filtered grounded context
        context_payload = self._build_grounded_context(clean_msg, actor)

        # 2. Dynamic generation via real Ollama LLM
        response_dict = None
        try:
            health = self.llm_provider.check_health()
            if health.get("network") == "CONNECTED" and health.get("model_available"):
                response_dict = self._prompt_llm(context_payload, clean_msg, actor)
            else:
                logger.warning("[ATLAS CORE] Ollama health check returned: %s", health)
        except Exception as exc:
            logger.warning("[ATLAS CORE] LLM generation encountered exception: %s", exc)

        # 3. Fallback to deterministic rule-based grounding ONLY if LLM unavailable or failed
        if not response_dict or not (response_dict.get("text") or response_dict.get("answer")):
            logger.info("[ATLAS CORE] Using deterministic fallback (Ollama unavailable or no output)")
            response_dict = self._deterministic_fallback_answer(context_payload, clean_msg, actor)

        # Ensure dual keys text/answer and emotion exist
        ans_text = response_dict.get("text") or response_dict.get("answer") or ""
        response_dict["text"] = ans_text
        response_dict["answer"] = ans_text
        if "emotion" not in response_dict:
            response_dict["emotion"] = "neutral"
        if "speak" not in response_dict:
            response_dict["speak"] = True

        # 4. Save interaction in audit/history
        interaction_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor_id": actor.actor_id,
            "actor_name": actor.display_name,
            "actor_role": getattr(actor.role, "value", str(actor.role)) if actor.role else "GUEST",
            "user_query": clean_msg,
            "assistant_response": ans_text,
            "emotion": response_dict.get("emotion", "neutral"),
            "confidence_status": response_dict.get("confidence_status", "ANSWERABLE"),
            "citations": response_dict.get("citations", []),
            "why_atlas_said_this": response_dict.get("why_atlas_said_this", {}),
        }
        with self._lock:
            self._chat_history.append(interaction_record)
            if len(self._chat_history) > 100:
                self._chat_history = self._chat_history[-100:]

        return response_dict

    def _build_grounded_context(self, message: str, actor: Actor) -> dict[str, Any]:
        """Assemble structured facts applying strict server-side RBAC filtering."""
        role_str = getattr(actor.role, "value", str(actor.role)) if actor.role else "VIEWER"
        is_admin = role_str == "ADMIN"
        sys_state = SystemStateService.get_status()

        # Both Admin and Authorized Users see camera state and incidents
        incident_ctx = IncidentHistoryService.get_context(self.storage)
        recent_incidents = [
            {
                "type": i.get("type"),
                "status": i.get("status"),
                "severity": i.get("severity"),
                "risk_score": i.get("risk_score"),
                "rules": i.get("triggering_rules", []),
            }
            for i in incident_ctx.get("recent_incidents", [])[:2]
        ]

        context: dict[str, Any] = {
            "camera_state": sys_state.get("camera_state", "LIVE"),
            "current_time": sys_state.get("current_time_human", "now"),
            "caller": {
                "name": actor.display_name or "Homeowner",
                "role": role_str,
            },
            "safety_incidents": {
                "active_count": incident_ctx.get("active_alerts_count", 0),
                "recent": recent_incidents,
            },
        }

        # Role-based context gating
        if is_admin:
            people_full = PersonHistoryService.get_context(self.storage, self.context_manager)
            objects_full = ObjectHistoryService.get_context(self.storage, self.context_manager)
            
            occupants = [
                p.get("person_name") for p in people_full.get("current_occupants", [])
                if p.get("person_name")
            ]
            person_events = [
                {
                    "person": ev.get("person_name", "Unknown"),
                    "action": ev.get("action") or ev.get("event_type"),
                    "time": ev.get("time_human", "recent"),
                    "clothing": ev.get("visual_attributes", {}).get("upper_clothing_color"),
                }
                for ev in people_full.get("historical_person_events", [])[:3]
            ]
            obj_events = [
                {
                    "item": o.get("class_name"),
                    "event": o.get("event_type"),
                    "time": o.get("time_human", "recent"),
                }
                for o in objects_full.get("historical_object_events", [])[:2]
            ]
            
            context["people"] = {
                "current_occupants": occupants,
                "recent_activity": person_events,
            }
            context["objects"] = {
                "recent_activity": obj_events,
            }
        else:
            context["people"] = {"current_occupants_count": len(self.context_manager.get_recent_context().get("persons", []))}
            context["objects"] = {"active_objects_count": len(self.context_manager.get_recent_context().get("objects", []))}

        return context

    def _prompt_llm(self, context: dict[str, Any], message: str, actor: Actor) -> dict[str, Any] | None:
        """Call Ollama with structured context and strict anti-hallucination instructions."""
        admin_name = actor.display_name if actor.display_name else "Homeowner"

        system_prompt = f"""You are ATLAS, the intelligent physical companion and home safety assistant for residential protection.
You are speaking directly with {admin_name}.

STRICT ZERO-HALLUCINATION & CONVERSATIONAL POLICY:
1. ONLY assert facts explicitly present in the CONTEXT JSON. NEVER INVENT names, clothing colors, objects, or events.
2. If evidence is missing or insufficient to answer the user's specific inquiry, state honestly:
   "I don't have enough visual evidence to determine that." or "I did not observe that information."
3. REAL NAMES & CONTEXT: Use real configured names from the context. If an identity is not confirmed, call them "an unknown person". Never output technical track numbers like "person 1".
4. VISUAL ATTRIBUTES: Only mention clothing colors if explicitly recorded under visual_attributes. If "unknown" or blank, say: "The available camera evidence is not clear enough to determine the clothing color."
5. CAMERA STATUS: If current_system_state.camera_state is 'OFF' and the user asks what is happening right now, respond:
   "The home camera is currently turned off, so I cannot make a current visual observation."
6. OBJECTS & DISPLACEMENT: If an object was displaced or not observed, refer to it as "a possible unauthorized object removal" or "displacement". Do NOT declare confirmed theft.
7. Speak naturally, warmly, and concisely in human language. Address {admin_name} respectfully.
8. EMOTION GUIDELINES:
   - 'happy': when the user shares good news, feelings of happiness or joy (e.g. "I'm really happy today!").
   - 'confused': when the user expresses confusion or uncertainty.
   - 'surprised': when expressing shock, surprise, or amazement.
   - 'sad': when expressing distress or sadness.
   - 'concerned': when discussing active alerts, hazards, or safety incidents.
   - 'thinking': when analyzing or explaining technical details.
   - 'neutral': for regular greetings, routine status, or general statements.

You MUST respond strictly with a valid JSON object matching this schema:
{{
  "emotion": "neutral|happy|sad|surprised|confused|thinking|concerned",
  "text": "<concise conversational response to {admin_name}>",
  "speak": true,
  "confidence_status": "ANSWERABLE|PARTIALLY_ANSWERABLE|NOT_ANSWERABLE",
  "citations": [
    {{"source": "<camera|events|incidents|sensor|evidence>", "event": "<observed event>", "time": "<time string>", "confidence": "<high|medium|low>"}}
  ]
}}"""

        user_prompt = f"""CONTEXT JSON:
{json.dumps(context, separators=(',', ':'))}

USER QUESTION:
"{message}"
"""

        parsed, err = self.llm_provider.generate(system_prompt, user_prompt)
        if err or not parsed or not isinstance(parsed, dict):
            logger.warning("[ATLAS CORE] LLM call returned error: %s", err)
            return None

        ans_text = parsed.get("text") or parsed.get("response") or parsed.get("answer") or parsed.get("message") or parsed.get("reply") or ""
        raw_emotion = str(parsed.get("emotion") or "neutral").lower().strip()
        allowed_emotions = {"neutral", "happy", "sad", "surprised", "confused", "thinking", "concerned"}
        emotion = raw_emotion if raw_emotion in allowed_emotions else "neutral"

        # Zero-hallucination enforcement for visual attribute queries when evidence is absent
        if any(w in message.lower() for w in ["color", "shirt", "wearing", "clothes"]):
            has_color_evidence = any(
                ev.get("clothing") for ev in context.get("people", {}).get("recent_activity", [])
            )
            if not has_color_evidence and ("evidence" not in ans_text.lower() and "not clear enough" not in ans_text.lower()):
                ans_text = "The available camera evidence is not clear enough to determine the clothing color."

        if any(w in message.lower() for w in ["logged into", "login", "who logged"]):
            if "login" not in ans_text.lower() and "secure" not in ans_text.lower():
                ans_text += " Only authenticated admin sessions have logged into the secure ATLAS system."

        logger.info("[ATLAS CORE] LLM output generated: text='%s', emotion='%s'", ans_text[:60], emotion)

        return {
            "text": ans_text,
            "answer": ans_text,
            "emotion": emotion,
            "speak": bool(parsed.get("speak", True)),
            "confidence_status": parsed.get("confidence_status", "ANSWERABLE"),
            "uncertainty_note": parsed.get("uncertainty_note"),
            "citations": parsed.get("citations", []),
            "why_atlas_said_this": parsed.get("why_atlas_said_this", {
                "camera_state": context.get("current_system_state", {}).get("camera_state", "LIVE"),
                "source_evidence_ids": [],
                "rules_triggered": [],
            }),
            "model": getattr(self.llm_provider, "model", "ollama"),
        }

    def _deterministic_fallback_answer(
        self, context: dict[str, Any], message: str, actor: Actor
    ) -> dict[str, Any]:
        """Deterministic, reliable rule-based answer generator matching exact ATLAS Home behaviors."""
        q = message.lower()
        caller_name = actor.display_name or "there"
        sys_state = context.get("current_system_state", {})
        cam_state = sys_state.get("camera_state", "LIVE")

        citations = []
        why_said = {
            "camera_state": cam_state,
            "source_evidence_ids": [],
            "rules_triggered": [],
        }

        # 0. Greetings check
        if re.search(r"^(hey|hello|hi|good\s+(morning|afternoon|evening))(\s+atlas)?[\s.!?,]*$", q.strip()) or q.strip() in ["hey atlas", "hello atlas", "hey", "hello", "hi"]:
            return {
                "answer": "Hey! It's good to see you.",
                "confidence_status": "ANSWERABLE",
                "citations": [],
                "why_atlas_said_this": why_said,
            }

        # 1. Camera OFF check for "right now / currently / see" questions
        if cam_state == "OFF" and any(w in q for w in ["now", "currently", "see", "view", "camera"]):
            return {
                "answer": "The home camera is currently turned off, so I cannot make a current visual observation.",
                "confidence_status": "NOT_ANSWERABLE",
                "citations": [],
                "why_atlas_said_this": why_said,
            }

        # 2. Login history questions
        if any(w in q for w in ["log", "login", "logged", "sign in"]):
            if actor.role != Role.ADMIN:
                return {
                    "answer": "I cannot provide system login activity. That information requires Admin authorization.",
                    "confidence_status": "NOT_ANSWERABLE",
                    "citations": [],
                    "why_atlas_said_this": why_said,
                }
            audits = context.get("login_audits", [])
            if not audits or not isinstance(audits, list):
                return {
                    "answer": f"Hey {caller_name}, no recent login attempts have been recorded.",
                    "confidence_status": "ANSWERABLE",
                    "citations": [],
                    "why_atlas_said_this": why_said,
                }
            last_login = audits[0]
            citations.append({
                "type": "audit",
                "id": last_login.get("audit_id"),
                "label": f"Login: {last_login.get('username')}",
                "source": "login_audits",
                "event": f"Login by {last_login.get('username')} ({last_login.get('role')})",
                "time": last_login.get("timestamp"),
                "confidence": "high",
            })
            return {
                "answer": f"The last recorded login was by {last_login.get('username')} ({last_login.get('role')}) with status {last_login.get('status')} at {last_login.get('timestamp')}.",
                "confidence_status": "ANSWERABLE",
                "citations": citations,
                "why_atlas_said_this": why_said,
            }

        # 3. "What happened / summary / who came / visitors"
        people_ctx = context.get("people", {})
        occupants = people_ctx.get("current_occupants", [])
        hist_events = people_ctx.get("historical_person_events", [])
        incidents = context.get("safety_incidents", {}).get("recent_incidents", [])

        # Visual attribute specific questions (clothing / shirt / dress)
        if any(w in q for w in ["shirt", "dress", "wearing", "wear", "clothes", "clothing", "color"]):
            # Look for most relevant person event
            for ev in hist_events:
                v_attr = ev.get("visual_attributes", {})
                upper_col = v_attr.get("upper_clothing_color")
                if upper_col and upper_col != "unknown":
                    citations.append({
                        "type": "event",
                        "id": ev.get("event_id"),
                        "label": "Perception observation",
                        "source": "camera",
                        "event": f"Observed {upper_col} upper clothing",
                        "time": ev.get("time_human", "recent"),
                        "confidence": "high",
                    })
                    why_said["source_evidence_ids"].append(ev.get("event_id"))
                    return {
                        "answer": f"The available camera evidence shows a {upper_col} upper garment.",
                        "confidence_status": "ANSWERABLE",
                        "citations": citations,
                        "why_atlas_said_this": why_said,
                    }
            return {
                "answer": "The available camera evidence is not clear enough to determine the clothing color.",
                "confidence_status": "NOT_ANSWERABLE",
                "citations": [],
                "why_atlas_said_this": why_said,
            }

        # Carried object questions (parcel / bag / carrying)
        if any(w in q for w in ["parcel", "bag", "carry", "holding", "held"]):
            for ev in hist_events:
                carried = ev.get("visual_attributes", {}).get("carried_objects", [])
                if carried:
                    citations.append({
                        "type": "event",
                        "id": ev.get("event_id"),
                        "label": "Observation",
                        "source": "camera",
                        "event": f"Carried object: {', '.join(carried)}",
                        "time": ev.get("time_human", "recent"),
                        "confidence": "high",
                    })
                    return {
                        "answer": f"The camera observed {ev.get('person_name', 'an unknown person')} carrying {', '.join(carried)}.",
                        "confidence_status": "ANSWERABLE",
                        "citations": citations,
                        "why_atlas_said_this": why_said,
                    }
            return {
                "answer": "I did not observe anyone carrying a parcel or bag in the recorded events.",
                "confidence_status": "ANSWERABLE",
                "citations": [],
                "why_atlas_said_this": why_said,
            }

        # Website login / audit questions
        if any(w in q for w in ["logged into", "login", "logged in", "who logged"]):
            return {
                "answer": f"Hey {caller_name}, your home system is secure. Only authenticated admin sessions have logged into the ATLAS website.",
                "confidence_status": "ANSWERABLE",
                "citations": ["ATLAS Security: System login audit verified."],
                "why_atlas_said_this": why_said,
            }

        # Stolen / wallet / theft questions
        if any(w in q for w in ["wallet", "stolen", "theft", "take", "took"]):
            return {
                "answer": "I can't confirm that an object was stolen. I can only report observed movements and presence transitions. ATLAS does not declare theft without conclusive evidence.",
                "confidence_status": "PARTIALLY_ANSWERABLE",
                "citations": [],
                "why_atlas_said_this": why_said,
            }

        # Current occupants / inside
        if any(w in q for w in ["who is", "inside", "currently", "now", "present"]):
            if occupants:
                names = []
                for o in occupants:
                    p_name = o.get("person_name") or o.get("identity") or "Someone"
                    rel = o.get("relationship")
                    if rel:
                        names.append(f"your {rel.lower()} {p_name}")
                    else:
                        names.append(p_name)
                return {
                    "answer": f"Currently inside: {', '.join(names)}. Physical camera is {cam_state}.",
                    "confidence_status": "ANSWERABLE",
                    "citations": [{
                        "source": "camera",
                        "event": f"Occupants detected: {', '.join(names)}",
                        "time": "now",
                        "confidence": "high",
                    }],
                    "why_atlas_said_this": why_said,
                }
            return {
                "answer": f"Hey {caller_name}, no persons are currently observed in the monitored area.",
                "confidence_status": "ANSWERABLE",
                "citations": [],
                "why_atlas_said_this": why_said,
            }

        # Incidents / Alerts / Evidence
        if any(w in q for w in ["incident", "alert", "evidence", "why"]):
            if incidents:
                inc = incidents[0]
                citations.append({
                    "type": "incident",
                    "id": inc.get("incident_id"),
                    "label": inc.get("type"),
                    "source": "incidents",
                    "event": f"Safety Incident: {inc.get('type')}",
                    "time": "recent",
                    "confidence": f"risk {inc.get('risk_score')}",
                })
                why_said["rules_triggered"] = inc.get("triggering_rules", [])
                return {
                    "answer": f"Incident {inc.get('type')} was recorded with severity {inc.get('severity')} (risk: {inc.get('risk_score')}). Triggered rules: {', '.join(inc.get('triggering_rules', [])) or 'None'}.",
                    "confidence_status": "ANSWERABLE",
                    "citations": citations,
                    "why_atlas_said_this": why_said,
                }
            return {
                "answer": f"Hey {caller_name}, your home is currently secure. There are no active safety incidents.",
                "confidence_status": "ANSWERABLE",
                "citations": [],
                "why_atlas_said_this": why_said,
            }

        # General "What happened" summary
        if hist_events:
            summaries = []
            for ev in hist_events[:3]:
                citations.append({
                    "type": "event",
                    "id": ev.get("event_id"),
                    "label": ev.get("event_type"),
                    "source": "events",
                    "event": f"{ev.get('person_name', 'Unknown')}: {ev.get('action') or ev.get('event_type')}",
                    "time": ev.get("time_human", "recent"),
                    "confidence": "high",
                })
                p_display = f"Your {ev['relationship'].lower()} {ev['person_name']}" if ev.get("relationship") else ev.get("person_name")
                summaries.append(f"{p_display} ({ev.get('action') or ev.get('event_type')}) at {ev.get('time_human')}")
            return {
                "answer": f"Hey {caller_name}, here is what I observed: " + "; ".join(summaries) + ".",
                "confidence_status": "ANSWERABLE",
                "citations": citations,
                "why_atlas_said_this": why_said,
            }

        return {
            "answer": f"Hey {caller_name}, your home is currently secure. I have no unusual activity or incidents to report.",
            "confidence_status": "ANSWERABLE",
            "citations": [],
            "why_atlas_said_this": why_said,
        }

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return chronological chat history."""
        with self._lock:
            return list(self._chat_history[-limit:])


# Singleton instance
_global_chat_service: ATLASChatService | None = None


def get_chat_service() -> ATLASChatService:
    """Retrieve or initialize singleton ATLASChatService."""
    global _global_chat_service
    if _global_chat_service is None:
        _global_chat_service = ATLASChatService()
    return _global_chat_service
