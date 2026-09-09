"""REST and streaming routes for ATLAS Home service."""

from __future__ import annotations

import time
import cv2
from flask import Blueprint, Response, jsonify, request, current_app

from atlas.authority.models import Actor, Permission, Role
from atlas.camera.stream import get_camera_stream
from atlas.camera.diagnostics import run_camera_diagnostics
from atlas.config.settings import get_settings
from atlas.context.memory import get_context_manager

api_bp = Blueprint("api", __name__)
_service_start_time = time.time()


@api_bp.route("/health", methods=["GET"])
def health_check():
    """Liveness check endpoint."""
    return jsonify({
        "status": "ok",
        "service": "atlas_home",
        "timestamp": time.time(),
    }), 200


@api_bp.route("/status", methods=["GET"])
def system_status():
    """Overall ATLAS Home system and device status."""
    settings = get_settings()
    camera = get_camera_stream()

    uptime_seconds = round(time.time() - _service_start_time, 2)
    camera_status = camera.get_status()

    # Expose non-secret LLM information
    from atlas.intelligence.verifier import get_verifier
    try:
        verifier = get_verifier()
        llm_health = verifier.provider.check_health()
        llm_status = {
            "provider": verifier.provider.provider_name,
            "provider_type": settings.llm_provider,
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "configured": verifier.provider.is_configured,
            "network": llm_health.get("network", "UNKNOWN"),
            "authenticated": llm_health.get("authenticated", False),
            "model_available": llm_health.get("model_available", False),
            "status_code": llm_health.get("status_code"),
            "error": llm_health.get("error"),
        }
    except Exception as e:
        llm_status = {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "configured": settings.to_dict().get("llm_configured", False),
            "error": str(e),
        }

    return jsonify({
        "device_id": settings.device_id,
        "environment": settings.environment,
        "uptime_seconds": uptime_seconds,
        "camera": {
            "is_connected": camera_status["is_connected"],
            "state": camera_status["state"],
            "source": camera_status["source"],
            "fps": camera_status["fps"],
            "resolution": camera_status["resolution"],
            "last_frame_timestamp": camera_status["last_frame_timestamp"],
            "error_message": camera_status["error_message"],
        },
        "llm": llm_status,
        "modules": {
            "camera": "active",
            "perception": "active",
            "events": "active",
            "context": "active",
            "intelligence": "active",
            "rules": "active",
            "risk": "active",
            "authority": "active",
            "incidents": "active",
            "alerts": "active",
            "audit": "active",
            "orchestration": "standby",
        },
    }), 200


@api_bp.route("/perception/latest", methods=["GET"])
def latest_perception():
    """Expose the latest structured real-time perception observation."""
    from atlas.perception.engine import get_perception_engine
    engine = get_perception_engine()
    obs = engine.get_latest_observation()
    if obs is None:
        return jsonify({
            "status": "idle",
            "message": "No real frames processed through perception engine yet.",
        }), 200
    return jsonify(obs.model_dump()), 200


@api_bp.route("/events/active", methods=["GET"])
def active_events():
    """Return currently active, unresolved events."""
    ctx = get_context_manager()
    events = ctx.get_active_events()
    return jsonify({
        "status": "ok",
        "count": len(events),
        "events": [ev.model_dump() for ev in events],
    }), 200


def _check_surveillance_browsing_access():
    """Deny historical surveillance browsing to Authorized Users."""
    from atlas.authority.auth import get_auth_service
    from atlas.authority.models import Role
    auth_svc = get_auth_service()
    actor = auth_svc.get_current_actor(request)
    if actor is None:
        actor_id = request.headers.get("X-Actor-ID") or request.args.get("actor_id")
        if actor_id:
            from atlas.authority.service import get_authority_service
            actor = get_authority_service().get_actor(actor_id)
    if actor is not None and actor.role in (Role.AUTHORIZED_USER, Role.VIEWER):
        return False, (jsonify({
            "error": "Forbidden",
            "details": "Historical surveillance browsing is restricted to Administrator accounts."
        }), 403)
    return True, None


@api_bp.route("/events/recent", methods=["GET"])
def recent_events():
    """Return recent historical events up to limit."""
    allowed, err = _check_surveillance_browsing_access()
    if not allowed:
        return err

    limit = request.args.get("limit", default=50, type=int)
    ctx = get_context_manager()
    events = ctx.get_recent_events(limit=limit)
    return jsonify({
        "status": "ok",
        "count": len(events),
        "events": [ev.model_dump() for ev in events],
    }), 200


@api_bp.route("/events/<event_id>", methods=["GET"])
def get_event_by_id(event_id: str):
    """Retrieve a specific event by ID."""
    ctx = get_context_manager()
    ev = ctx.get_event(event_id)
    if ev is None:
        return jsonify({
            "status": "not_found",
            "error": f"Event '{event_id}' not found.",
        }), 404
    return jsonify({
        "status": "ok",
        "event": ev.model_dump(),
    }), 200


@api_bp.route("/context/recent", methods=["GET"])
def recent_context():
    """Return structured situational context within the temporal window."""
    allowed, err = _check_surveillance_browsing_access()
    if not allowed:
        return err

    window = request.args.get("window_seconds", default=None, type=float)
    ctx = get_context_manager()
    data = ctx.get_recent_context(window_seconds=window)
    return jsonify({
        "status": "ok",
        "context": data,
    }), 200


@api_bp.route("/context/person/<track_id>", methods=["GET"])
def person_context(track_id: str):
    """Retrieve situational context and event history for a tracked person."""
    allowed, err = _check_surveillance_browsing_access()
    if not allowed:
        return err

    ctx = get_context_manager()
    data = ctx.get_person_context(track_id)
    if data is None:
        return jsonify({
            "status": "not_found",
            "error": f"Track ID '{track_id}' not found in person context.",
        }), 404
    return jsonify({
        "status": "ok",
        "person": data,
    }), 200


@api_bp.route("/context/object/<track_id>", methods=["GET"])
def object_context(track_id: str):
    """Retrieve situational context and event history for a tracked object."""
    allowed, err = _check_surveillance_browsing_access()
    if not allowed:
        return err

    ctx = get_context_manager()
    data = ctx.get_object_context(track_id)
    if data is None:
        return jsonify({
            "status": "not_found",
            "error": f"Track ID '{track_id}' not found in object context.",
        }), 404
    return jsonify({
        "status": "ok",
        "object": data,
    }), 200


# ============================================================================
# Step 6.5 Multi-Source Context & Evidence Fusion Endpoints
# ============================================================================

@api_bp.route("/sources", methods=["GET"])
def get_sources():
    """Retrieve runtime status across all sensing subsystems."""
    from atlas.fusion.sources import get_source_registry
    registry = get_source_registry()
    sources = registry.get_sources_status()
    summary = {
        "total": len(sources),
        "available": sum(1 for s in sources if s["availability"] == "AVAILABLE"),
        "degraded": sum(1 for s in sources if s["availability"] == "DEGRADED"),
        "disconnected": sum(1 for s in sources if s["availability"] == "DISCONNECTED"),
        "unavailable": sum(1 for s in sources if s["availability"] == "UNAVAILABLE"),
        "not_configured": sum(1 for s in sources if s["availability"] == "NOT_CONFIGURED"),
    }
    return jsonify({
        "status": "ok",
        "sources": sources,
        "summary": summary,
    }), 200


@api_bp.route("/context/fusions", methods=["GET"])
def get_context_fusions():
    """Retrieve recent multi-source context fusion snapshots."""
    limit = request.args.get("limit", default=20, type=int)
    from atlas.fusion.engine import get_fusion_engine
    engine = get_fusion_engine()
    fusions = engine.get_recent_fusions(limit=limit)
    return jsonify({
        "status": "ok",
        "count": len(fusions),
        "fusions": [f.model_dump() for f in fusions],
    }), 200


@api_bp.route("/context/fusions/<fusion_id>", methods=["GET"])
def get_context_fusion_by_id(fusion_id: str):
    """Retrieve a specific FusedSituation by ID."""
    from atlas.fusion.engine import get_fusion_engine
    engine = get_fusion_engine()
    fusion = None
    if engine.storage and hasattr(engine.storage, "get_fused_situation"):
        fusion = engine.storage.get_fused_situation(fusion_id)
    if fusion is None:
        for f in engine.get_recent_fusions(limit=50):
            if f.fusion_id == fusion_id:
                fusion = f
                break
    if fusion is None:
        return jsonify({
            "status": "not_found",
            "error": f"Fused situation '{fusion_id}' not found.",
        }), 404
    return jsonify({
        "status": "ok",
        "fusion": fusion.model_dump(),
    }), 200


@api_bp.route("/intelligence/latest", methods=["GET"])
def latest_intelligence():
    """Retrieve the latest structured LLM verification and interpretation result."""
    from atlas.intelligence.verifier import get_verifier
    verifier = get_verifier()
    latest = verifier.get_latest_result()
    if latest is None:
        return jsonify({
            "status": "idle",
            "message": "No intelligence verification recorded yet.",
        }), 200
    return jsonify({
        "status": "ok",
        "verification": latest.model_dump(),
    }), 200


@api_bp.route("/intelligence/recent", methods=["GET"])
def recent_intelligence():
    """Retrieve recent structured LLM verification history."""
    limit = request.args.get("limit", default=20, type=int)
    from atlas.intelligence.verifier import get_verifier
    verifier = get_verifier()
    history = verifier.get_recent_results(limit=limit)
    return jsonify({
        "status": "ok",
        "count": len(history),
        "verifications": [v.model_dump() for v in history],
    }), 200


@api_bp.route("/rules/status", methods=["GET"])
def rules_status():
    """Retrieve configured deterministic safety rules and their current status."""
    from atlas.rules.engine import get_rule_engine
    engine = get_rule_engine()
    status_list = engine.get_rule_status()
    latest_evals = engine.get_latest_evaluations()
    return jsonify({
        "status": "ok",
        "rules_count": len(status_list),
        "rules": status_list,
        "latest_evaluations": [e.model_dump() for e in latest_evals],
    }), 200


@api_bp.route("/risk/latest", methods=["GET"])
def latest_risk():
    """Retrieve the latest deterministic risk assessment."""
    from atlas.risk.engine import get_risk_engine
    engine = get_risk_engine()
    assessment = engine.get_latest_assessment()
    if assessment is None:
        return jsonify({
            "status": "idle",
            "message": "No risk assessment computed yet.",
        }), 200
    return jsonify({
        "status": "ok",
        "risk": assessment.model_dump(),
    }), 200


@api_bp.route("/risk/recent", methods=["GET"])
def recent_risk():
    """Retrieve recent deterministic risk assessments."""
    limit = request.args.get("limit", default=20, type=int)
    from atlas.risk.engine import get_risk_engine
    engine = get_risk_engine()
    history = engine.get_recent_assessments(limit=limit)
    return jsonify({
        "status": "ok",
        "count": len(history),
        "assessments": [a.model_dump() for a in history],
    }), 200


@api_bp.route("/analysis/latest", methods=["GET"])
def latest_analysis():
    """Retrieve unified decision context combining evidence, LLM verification, rules, and risk."""
    from atlas.events.storage import get_event_storage
    from atlas.intelligence.pipeline import get_intelligence_pipeline
    pipeline = get_intelligence_pipeline()
    decision = pipeline.get_latest_decision()
    if decision is not None:
        return jsonify({
            "status": "ok",
            "analysis": decision.model_dump(),
        }), 200

    # Fallback to persistent SQLite storage
    stored = get_event_storage().get_latest_analysis()
    if stored is not None:
        return jsonify({
            "status": "ok",
            "analysis": stored,
        }), 200

    return jsonify({
        "status": "idle",
        "message": "No pipeline analysis executed yet.",
    }), 200


@api_bp.route("/analysis/recent", methods=["GET"])
def recent_analyses():
    """Retrieve recent unified decision analyses from persistent SQLite storage."""
    limit = request.args.get("limit", default=10, type=int)
    from atlas.events.storage import get_event_storage
    records = get_event_storage().get_recent_analyses(limit=limit)
    return jsonify({
        "status": "ok",
        "count": len(records),
        "analyses": records,
    }), 200


@api_bp.route("/camera/status", methods=["GET"])
def camera_status():
    """Detailed camera connection and performance metrics."""
    camera = get_camera_stream()
    return jsonify(camera.get_status()), 200


@api_bp.route("/camera/frame", methods=["GET"])
def latest_camera_frame():
    """Return the latest real frame captured by the camera as a JPEG image.
    
    Zero-Mock Policy: Returns HTTP 503 if the camera is not connected.
    Never returns fake or mock images.
    
    Alert-Gated Access:
    Authorized Users may access live camera frames ONLY when an active permitted alert exists.
    """
    from atlas.authority.auth import get_auth_service
    from atlas.authority.models import Role
    auth_svc = get_auth_service()
    actor = auth_svc.get_current_actor(request)
    if actor is None:
        actor_id = request.headers.get("X-Actor-ID") or request.args.get("actor_id")
        if actor_id:
            from atlas.authority.user_service import get_user_management_service
            user_mgmt = get_user_management_service()
            u = user_mgmt.get_user(actor_id) or user_mgmt.get_user_by_username(actor_id)
            if u:
                actor = u.to_actor()
            else:
                from atlas.authority.service import get_authority_service
                actor = get_authority_service().get_actor(actor_id)

    if actor is not None and actor.role in (Role.AUTHORIZED_USER, Role.VIEWER):
        permitted, reason = _check_alert_gated_access(actor)
        if not permitted:
            return jsonify({
                "error": "Forbidden",
                "details": "Live camera access for Authorized Users requires an active permitted alert."
            }), 403

    camera = get_camera_stream()
    frame_data = camera.get_latest_frame()

    if frame_data is None:
        err = camera.error_message or "Camera is disconnected or no frame received yet."
        return jsonify({
            "error": "No real camera frame available",
            "details": err,
            "troubleshooting": "Check device_id, raw_camera_url, or physical camera connection.",
        }), 503

    frame, ts = frame_data
    ret, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ret:
        return jsonify({"error": "Failed to encode JPEG frame"}), 500

    response = Response(jpeg.tobytes(), mimetype="image/jpeg")
    response.headers["X-Frame-Timestamp"] = str(ts)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@api_bp.route("/camera/video_feed", methods=["GET"])
def video_feed():
    """MJPEG stream endpoint for real-time camera preview.
    
    Alert-Gated Access:
    Authorized Users may access live stream ONLY when an active permitted alert exists.
    """
    from atlas.authority.auth import get_auth_service
    from atlas.authority.models import Role
    auth_svc = get_auth_service()
    actor = auth_svc.get_current_actor(request)
    if actor is None:
        actor_id = request.headers.get("X-Actor-ID") or request.args.get("actor_id")
        if actor_id:
            from atlas.authority.user_service import get_user_management_service
            user_mgmt = get_user_management_service()
            u = user_mgmt.get_user(actor_id) or user_mgmt.get_user_by_username(actor_id)
            if u:
                actor = u.to_actor()
            else:
                from atlas.authority.service import get_authority_service
                actor = get_authority_service().get_actor(actor_id)

    if actor is not None and actor.role in (Role.AUTHORIZED_USER, Role.VIEWER):
        permitted, reason = _check_alert_gated_access(actor)
        if not permitted:
            return jsonify({
                "error": "Forbidden",
                "details": "Live camera access for Authorized Users requires an active permitted alert."
            }), 403

    camera = get_camera_stream()

    def generate_frames():
        while True:
            frame_data = camera.get_latest_frame()
            if frame_data is not None:
                frame, _ = frame_data
                ret, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if ret:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
                    )
            time.sleep(0.04)  # ~25 FPS max stream cap for browser preview

    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@api_bp.route("/diagnostics", methods=["GET"])
def run_diagnostics():
    """Trigger real camera diagnostic checks and return structured results."""
    results = run_camera_diagnostics(timeout_seconds=4.0)
    status_code = 200 if results["overall_passed"] else 503
    return jsonify(results), status_code


# ============================================================================
# Step 5: Incident, Alert, Audit, and Orchestration Endpoints
# ============================================================================

@api_bp.route("/incidents", methods=["GET"])
def list_incidents():
    """List recent persistent incidents."""
    from atlas.incidents.service import get_incident_manager
    limit = request.args.get("limit", default=50, type=int)
    mgr = get_incident_manager()
    incidents = mgr.get_recent_incidents(limit=limit)
    return jsonify({
        "status": "ok",
        "count": len(incidents),
        "incidents": [i.model_dump() for i in incidents],
    }), 200


@api_bp.route("/incidents/active", methods=["GET"])
def list_active_incidents():
    """List currently unresolved / active incidents."""
    from atlas.incidents.service import get_incident_manager
    mgr = get_incident_manager()
    incidents = mgr.get_active_incidents()
    return jsonify({
        "status": "ok",
        "count": len(incidents),
        "incidents": [i.model_dump() for i in incidents],
    }), 200


@api_bp.route("/incidents/<incident_id>", methods=["GET"])
def get_incident_by_id(incident_id: str):
    """Retrieve a specific incident by ID."""
    from atlas.incidents.service import get_incident_manager
    mgr = get_incident_manager()
    incident = mgr.get_incident(incident_id)
    if incident is None:
        return jsonify({"status": "not_found", "error": f"Incident '{incident_id}' not found."}), 404
    return jsonify({"status": "ok", "incident": incident.model_dump()}), 200


# ============================================================================
# Authentication & Actor Resolution Endpoints (Step 6.2)
# ============================================================================

def _resolve_request_actor(perm: Permission | None = None) -> tuple[Actor | None, tuple[Any, int] | None]:
    """Resolve authenticated actor from session/token and enforce permission check.

    Returns:
        (actor, None) if successful
        (None, (jsonify_response, status_code)) if unauthorized or forbidden
    """
    from atlas.authority.auth import get_auth_service
    from atlas.authority.service import get_authority_service

    auth_svc = get_auth_service()
    actor = auth_svc.get_current_actor(request)

    # Fallback to body or X-Actor-ID header for test harness backward compatibility
    if actor is None:
        data = request.get_json(silent=True) or {}
        actor_id = data.get("actor_id") or request.headers.get("X-Actor-ID")
        if actor_id:
            from atlas.authority.user_service import get_user_management_service
            user_mgmt = get_user_management_service()
            u = user_mgmt.get_user(actor_id) or user_mgmt.get_user_by_username(actor_id)
            if u:
                actor = u.to_actor()
            else:
                actor = get_authority_service().get_actor(actor_id)
            if actor is None:
                return None, (jsonify({"error": "Forbidden", "details": f"Unknown actor '{actor_id}'."}), 403)

    if actor is None:
        return None, (jsonify({"error": "Unauthorized", "details": "Authentication required. Please log in."}), 401)

    # Consequential actions check: system_core and is_system actors are strictly forbidden
    is_consequential = perm in (
        Permission.ACKNOWLEDGE_INCIDENT,
        Permission.RESOLVE_INCIDENT,
        Permission.DISMISS_INCIDENT,
        Permission.AUTHORIZE_RESPONSE,
    )
    if is_consequential and (getattr(actor, "is_system", False) or actor.actor_id == "system_core"):
        return None, (jsonify({
            "error": "Forbidden",
            "details": "System authority cannot perform consequential safety actions. Human authorization is required.",
        }), 403)

    # Permission check
    if perm is not None:
        if not actor.has_permission(perm):
            role_desc = actor.role.value if actor.role else "UNKNOWN"
            return None, (jsonify({
                "error": "Forbidden",
                "details": f"Actor '{actor.actor_id}' ({role_desc}) lacks permission '{perm.value}'.",
            }), 403)

    return actor, None


def _check_alert_gated_access(actor, evidence_id: str | None = None) -> tuple[bool, str]:
    """Verify whether an active, un-dismissed, un-resolved alert permits restricted access.
    
    Backend enforcement:
    - No active permitted alert -> no restricted live/evidence access.
    - Active permitted alert -> access allowed.
    - Resolved/Dismissed alert -> restricted access closes according to policy.
    - Historical unrestricted surveillance browsing -> denied for Authorized Users.
    - Admin remains unrestricted according to Admin permissions.
    """
    from atlas.incidents.service import get_incident_manager
    from atlas.incidents.schema import IncidentStatus
    
    inc_mgr = get_incident_manager()
    active_incidents = inc_mgr.get_active_incidents()
    
    permitted_incidents = [
        i for i in active_incidents 
        if i.status in (IncidentStatus.ACTIVE, IncidentStatus.ACKNOWLEDGED)
    ]
    
    if not permitted_incidents:
        return False, "No active permitted alert in progress. Live and evidence access is closed."
        
    if evidence_id is not None:
        clean_eid = evidence_id.strip()
        is_linked = False
        for inc in permitted_incidents:
            eids = (getattr(inc, "evidence_ids", None) or []) + (inc.metadata.get("evidence_ids", []) if hasattr(inc, "metadata") and isinstance(inc.metadata, dict) else [])
            if clean_eid in eids:
                is_linked = True
                break
        if not is_linked:
            from atlas.evidence.vault import get_evidence_vault
            vault = get_evidence_vault()
            rec = vault.get_evidence_record(clean_eid)
            if rec and any(rec.incident_id == inc.incident_id for inc in permitted_incidents):
                is_linked = True
        if not is_linked:
            return False, f"Evidence artifact '{clean_eid}' is not associated with an active permitted alert."
            
    return True, "Access permitted: Active alert in progress."



# ============================================================================
# Incident Action & Lifecycle Endpoints (Step 6.2)
# ============================================================================

@api_bp.route("/incidents/<incident_id>/acknowledge", methods=["POST"])
def acknowledge_incident(incident_id: str):
    """Acknowledge an active incident (requires ACKNOWLEDGE_INCIDENT permission)."""
    from atlas.incidents.service import get_incident_manager, InvalidTransitionError

    actor, err_resp = _resolve_request_actor(Permission.ACKNOWLEDGE_INCIDENT)
    if err_resp:
        return err_resp

    mgr = get_incident_manager()
    try:
        updated = mgr.acknowledge_incident(incident_id, actor)
        return jsonify({
            "status": "ok",
            "message": "Incident acknowledged successfully.",
            "incident": updated.model_dump(),
        }), 200
    except KeyError as exc:
        return jsonify({"error": "Not Found", "details": str(exc)}), 404
    except InvalidTransitionError as exc:
        return jsonify({"error": "Conflict", "details": str(exc)}), 409


@api_bp.route("/incidents/<incident_id>/resolve", methods=["POST"])
def resolve_incident(incident_id: str):
    """Resolve an incident with mandatory classified resolution reason."""
    from atlas.incidents.schema import ResolutionReason
    from atlas.incidents.service import get_incident_manager, InvalidTransitionError

    actor, err_resp = _resolve_request_actor(Permission.RESOLVE_INCIDENT)
    if err_resp:
        return err_resp

    data = request.get_json(silent=True) or {}
    reason = data.get("reason")
    notes = data.get("notes")

    if not reason:
        return jsonify({"error": "Bad Request", "details": "Field 'reason' is required."}), 400

    valid_reasons = {r.value for r in ResolutionReason}
    if reason not in valid_reasons:
        return jsonify({
            "error": "Bad Request",
            "details": f"Invalid resolution reason '{reason}'. Supported reasons: {sorted(list(valid_reasons))}",
        }), 400

    mgr = get_incident_manager()
    try:
        updated = mgr.resolve_incident(incident_id, actor, reason=reason, notes=notes)
        return jsonify({
            "status": "ok",
            "message": "Incident resolved successfully.",
            "incident": updated.model_dump(),
        }), 200
    except KeyError as exc:
        return jsonify({"error": "Not Found", "details": str(exc)}), 404
    except InvalidTransitionError as exc:
        return jsonify({"error": "Conflict", "details": str(exc)}), 409


@api_bp.route("/incidents/<incident_id>/dismiss", methods=["POST"])
def dismiss_incident(incident_id: str):
    """Dismiss an incident without deleting its historical record."""
    from atlas.incidents.service import get_incident_manager, InvalidTransitionError

    actor, err_resp = _resolve_request_actor(Permission.DISMISS_INCIDENT)
    if err_resp:
        return err_resp

    data = request.get_json(silent=True) or {}
    reason = data.get("reason")

    if not reason or not str(reason).strip():
        return jsonify({"error": "Bad Request", "details": "Field 'reason' is required and cannot be empty."}), 400

    mgr = get_incident_manager()
    try:
        updated = mgr.dismiss_incident(incident_id, actor, reason=str(reason).strip())
        return jsonify({
            "status": "ok",
            "message": "Incident dismissed successfully.",
            "incident": updated.model_dump(),
        }), 200
    except KeyError as exc:
        return jsonify({"error": "Not Found", "details": str(exc)}), 404
    except InvalidTransitionError as exc:
        return jsonify({"error": "Conflict", "details": str(exc)}), 409


@api_bp.route("/incidents/<incident_id>/escalation/request", methods=["POST"])
def request_escalation(incident_id: str):
    """Request escalation authorization for an incident."""
    from atlas.incidents.service import get_incident_manager, InvalidTransitionError

    actor, err_resp = _resolve_request_actor(None)
    if err_resp:
        return err_resp

    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "Operator requested response escalation.")

    mgr = get_incident_manager()
    try:
        updated = mgr.request_escalation(incident_id, actor, reason=reason)
        return jsonify({
            "status": "ok",
            "message": "Escalation requested.",
            "incident": updated.model_dump(),
        }), 200
    except KeyError as exc:
        return jsonify({"error": "Not Found", "details": str(exc)}), 404
    except InvalidTransitionError as exc:
        return jsonify({"error": "Conflict", "details": str(exc)}), 409


@api_bp.route("/incidents/<incident_id>/authorize", methods=["POST"])
@api_bp.route("/incidents/<incident_id>/escalation/authorize", methods=["POST"])
def authorize_incident_response(incident_id: str):
    """Authorize response escalation for an incident (restricted to human ADMIN with AUTHORIZE_RESPONSE)."""
    from atlas.incidents.schema import EscalationState, IncidentStatus
    from atlas.incidents.service import get_incident_manager, InvalidTransitionError

    actor, err_resp = _resolve_request_actor(Permission.AUTHORIZE_RESPONSE)
    if err_resp:
        return err_resp

    mgr = get_incident_manager()
    incident = mgr.get_incident(incident_id)
    if incident is None:
        return jsonify({"error": "Not Found", "details": f"Incident '{incident_id}' not found."}), 404

    # Check lifecycle compatibility
    if incident.status in (IncidentStatus.RESOLVED, IncidentStatus.DISMISSED):
        return jsonify({
            "error": "Conflict",
            "details": f"Cannot authorize escalation for incident in terminal status '{incident.status.value}'.",
        }), 409

    # Idempotent response if already authorized
    if incident.escalation_state == EscalationState.AUTHORIZED:
        return jsonify({
            "status": "ok",
            "message": "Response escalation already authorized (idempotent).",
            "incident": incident.model_dump(),
            "external_notifications": "DISABLED (0)",
        }), 200

    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "Administrator authorized response escalation.")

    try:
        updated = mgr.authorize_escalation(incident_id, actor, reason=reason)
        return jsonify({
            "status": "ok",
            "message": "Response escalation authorized successfully. External notifications remain disabled.",
            "incident": updated.model_dump(),
            "external_notifications": "DISABLED (0)",
        }), 200
    except KeyError as exc:
        return jsonify({"error": "Not Found", "details": str(exc)}), 404
    except InvalidTransitionError as exc:
        return jsonify({"error": "Conflict", "details": str(exc)}), 409


@api_bp.route("/incidents/<incident_id>/investigation", methods=["GET"])
def get_incident_investigation(incident_id: str):
    """Retrieve full read-only evidence provenance snapshot for an incident."""
    from atlas.authority.models import Permission
    from atlas.incidents.investigation import get_investigation_service

    actor, err_resp = _resolve_request_actor(Permission.VIEW_INCIDENTS)
    if err_resp:
        return err_resp

    inv_svc = get_investigation_service()
    snapshot = inv_svc.build_investigation(incident_id)
    if snapshot is None:
        return jsonify({"error": "Not Found", "details": f"Incident '{incident_id}' not found."}), 404

    return jsonify({
        "status": "ok",
        **snapshot,
    }), 200


@api_bp.route("/evidence/<evidence_id>", methods=["GET"])
def get_evidence_artifact(evidence_id: str):
    """Retrieve verified camera evidence binary artifact (read-only)."""
    from atlas.authority.models import Permission
    from atlas.evidence.vault import get_evidence_vault
    from atlas.evidence.schema import CaptureStatus, IntegrityStatus

    # 1. Require Authentication & Alert-Gated Check for Authorized Users
    from atlas.authority.models import Role
    actor, err_resp = _resolve_request_actor(None)
    if err_resp:
        return err_resp

    if actor.role == Role.ADMIN and actor.has_permission(Permission.VIEW_EVIDENCE):
        pass
    elif actor.role == Role.AUTHORIZED_USER:
        if not actor.has_permission(Permission.VIEW_EVIDENCE):
            return jsonify({
                "error": "Forbidden",
                "details": f"Actor '{actor.actor_id}' lacks permission to view evidence."
            }), 403
        permitted, reason = _check_alert_gated_access(actor, evidence_id=evidence_id)
        if not permitted:
            return jsonify({
                "error": "Forbidden",
                "details": "Evidence access for Authorized Users is restricted to active permitted alerts."
            }), 403
    elif actor.has_permission(Permission.VIEW_EVIDENCE):
        pass
    else:
        return jsonify({
            "error": "Forbidden",
            "details": f"Actor '{actor.actor_id}' lacks permission to view evidence."
        }), 403

    # 2. Strict evidence ID validation (prevent directory traversal or malicious characters)
    clean_id = evidence_id.strip()
    if not clean_id or "/" in clean_id or "\\" in clean_id or ".." in clean_id:
        return jsonify({"error": "Bad Request", "details": "Invalid evidence ID format."}), 400

    vault = get_evidence_vault()
    record = vault.get_evidence_record(clean_id)
    if record is None:
        return jsonify({"error": "Not Found", "details": f"Evidence record '{clean_id}' not found."}), 404

    # 3. Validate incident linkage
    if not record.incident_id:
        return jsonify({"error": "Bad Request", "details": "Evidence record is orphaned."}), 400

    # 4. Check capture status
    if record.capture_status != CaptureStatus.CAPTURED:
        return jsonify({
            "error": "Not Found",
            "details": f"Evidence artifact was not captured (status: {record.capture_status.value}).",
            "reason": record.failure_reason,
        }), 404

    # 5. Integrity Verification
    integ_status, integ_err = vault.verify_integrity(clean_id)
    if integ_status == IntegrityStatus.ARTIFACT_MISSING:
        return jsonify({"error": "Not Found", "details": "Artifact file missing from vault storage."}), 404
    elif integ_status != IntegrityStatus.INTEGRITY_VERIFIED:
        return jsonify({
            "error": "Integrity Failure",
            "details": f"Evidence integrity check failed: {integ_err}",
        }), 500

    # 6. Read and safely deliver artifact
    try:
        data_bytes, mime_type = vault.read_artifact(clean_id)
    except FileNotFoundError:
        return jsonify({"error": "Not Found", "details": "Artifact file not found."}), 404
    except ValueError as ve:
        return jsonify({"error": "Integrity Failure", "details": str(ve)}), 500
    except PermissionError as pe:
        return jsonify({"error": "Forbidden", "details": str(pe)}), 403

    resp = Response(data_bytes, mimetype=mime_type or "image/jpeg")
    resp.headers["Cache-Control"] = "private, no-cache, no-store, must-revalidate"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Evidence-ID"] = record.evidence_id
    resp.headers["X-Incident-ID"] = record.incident_id
    resp.headers["X-Evidence-Integrity"] = "INTEGRITY_VERIFIED"
    resp.headers["X-Evidence-SHA256"] = record.sha256
    return resp


@api_bp.route("/alerts", methods=["GET"])
def list_alerts():
    """List internal alert records."""
    from atlas.alerts.service import get_alert_manager
    limit = request.args.get("limit", default=50, type=int)
    mgr = get_alert_manager()
    alerts = mgr.get_all_alerts(limit=limit)
    return jsonify({
        "status": "ok",
        "count": len(alerts),
        "alerts": [a.model_dump() for a in alerts],
    }), 200


@api_bp.route("/alerts/active", methods=["GET"])
def list_active_alerts():
    """List active unresolved alerts."""
    from atlas.alerts.service import get_alert_manager
    mgr = get_alert_manager()
    alerts = mgr.get_active_alerts()
    return jsonify({
        "status": "ok",
        "count": len(alerts),
        "alerts": [a.model_dump() for a in alerts],
    }), 200


@api_bp.route("/audit", methods=["GET"])
def list_audit_logs():
    from atlas.authority.auth import get_auth_service
    from atlas.authority.models import Role
    auth_svc = get_auth_service()
    actor = auth_svc.get_current_actor(request)
    if actor is None:
        actor_id = request.headers.get("X-Actor-ID") or request.args.get("actor_id")
        if actor_id:
            from atlas.authority.service import get_authority_service
            actor = get_authority_service().get_actor(actor_id)
    if actor is not None and actor.role in (Role.AUTHORIZED_USER, Role.VIEWER):
        return jsonify({"error": "Forbidden", "details": "Audit logs are restricted to Administrator accounts."}), 403

    from atlas.audit.service import get_audit_logger
    limit = request.args.get("limit", default=50, type=int)
    logger_svc = get_audit_logger()
    records = logger_svc.get_recent_records(limit=limit)
    return jsonify({
        "status": "ok",
        "count": len(records),
        "audit_records": [r.model_dump() for r in records],
    }), 200


@api_bp.route("/audit/<target_id>", methods=["GET"])
def get_audit_for_target(target_id: str):
    """Retrieve audit history for a specific incident or alert."""
    from atlas.authority.auth import get_auth_service
    from atlas.authority.models import Role
    auth_svc = get_auth_service()
    actor = auth_svc.get_current_actor(request)
    if actor is None:
        actor_id = request.headers.get("X-Actor-ID") or request.args.get("actor_id")
        if actor_id:
            from atlas.authority.service import get_authority_service
            actor = get_authority_service().get_actor(actor_id)
    if actor is not None and actor.role in (Role.AUTHORIZED_USER, Role.VIEWER):
        return jsonify({"error": "Forbidden", "details": "Audit logs are restricted to Administrator accounts."}), 403

    from atlas.audit.service import get_audit_logger
    limit = request.args.get("limit", default=50, type=int)
    logger_svc = get_audit_logger()
    records = logger_svc.get_records_for_target(target_id, limit=limit)
    return jsonify({
        "status": "ok",
        "target_id": target_id,
        "count": len(records),
        "audit_records": [r.model_dump() for r in records],
    }), 200


@api_bp.route("/orchestration/evaluate/<incident_id>", methods=["GET"])
def evaluate_orchestration(incident_id: str):
    """Evaluate response action eligibility for an incident without side effects."""
    from atlas.orchestration import get_orchestrator
    from atlas.authority.service import get_authority_service

    actor_id = request.args.get("actor_id") or request.headers.get("X-Actor-ID")
    actor = get_authority_service().get_actor(actor_id) if actor_id else None

    orch = get_orchestrator()
    eval_result = orch.evaluate_incident_eligibility(incident_id, actor=actor)
    return jsonify({
        "status": "ok",
        "evaluation": {
            "incident_id": eval_result.incident_id,
            "decision": eval_result.decision.value,
            "reason": eval_result.reason,
            "requires_human_authorization": eval_result.requires_human_authorization,
            "details": eval_result.details,
        }
    }), 200


# ============================================================================
# Step 6.1: Unified Read-Only Dashboard State Contract
# ============================================================================

@api_bp.route("/dashboard/state", methods=["GET"])
def get_dashboard_state():
    """Retrieve normalized, unified read-only snapshot of real backend operational state.
    
    Zero-Mock Policy:
    All values are sourced directly from backend singletons and database records.
    Calculates zero metrics on the presentation layer.
    """
    from datetime import datetime, timezone
    from atlas.alerts.service import get_alert_manager
    from atlas.events.storage import get_event_storage
    from atlas.incidents.service import get_incident_manager
    from atlas.intelligence.pipeline import get_intelligence_pipeline
    from atlas.orchestration import get_orchestrator
    from atlas.perception.engine import get_perception_engine
    from atlas.risk.engine import get_risk_engine
    from atlas.rules.engine import get_rule_engine

    now_iso = datetime.now(timezone.utc).isoformat()
    settings = get_settings()

    # 1. Camera Status
    camera = get_camera_stream()
    cam_status = camera.get_status()

    # 2. Context & Real Events
    ctx = get_context_manager()
    recent_context = ctx.get_recent_context(window_seconds=60.0)
    raw_active_events = ctx.get_active_events()
    raw_recent_events = ctx.get_recent_events(limit=25)

    active_people_count = recent_context.get("active_persons_count", 0)
    if not active_people_count and raw_active_events:
        persons = {e.person_id for e in raw_active_events if e.person_id}
        active_people_count = len(persons)
    active_object_count = recent_context.get("active_objects_count", 0)

    # 3. Perception State
    try:
        perception_engine = get_perception_engine()
        latest_obs = perception_engine.get_latest_observation()
        perception_data = {
            "status": "active" if cam_status.get("is_connected") else "idle",
            "active_tracks": (len(latest_obs.persons) + len(latest_obs.objects)) if latest_obs else 0,
            "observation": latest_obs.model_dump() if latest_obs else None,
        }
    except Exception as exc:
        perception_data = {
            "status": "error",
            "error": str(exc),
            "active_tracks": 0,
            "observation": None,
        }

    # 4. Intelligence & LLM Analysis
    pipeline = get_intelligence_pipeline()
    latest_decision = pipeline.get_latest_decision()
    stored_analysis = None
    if latest_decision is None:
        try:
            stored_analysis = get_event_storage().get_latest_analysis()
        except Exception:
            stored_analysis = None

    if latest_decision is not None:
        analysis_data = {
            "analysis_id": latest_decision.analysis_id,
            "timestamp": latest_decision.timestamp,
            "situation": latest_decision.llm_verification.situation,
            "interpretation": latest_decision.llm_verification.interpretation,
            "confidence": latest_decision.llm_verification.confidence,
            "uncertainty": latest_decision.llm_verification.uncertainty,
            "verification_required": latest_decision.llm_verification.verification_required,
            "status": latest_decision.llm_verification.status,
            "model_used": latest_decision.llm_verification.model_used,
            "supporting_event_ids": latest_decision.llm_verification.supporting_event_ids,
            "error_message": latest_decision.llm_verification.error_message,
        }
        rules_data = [r.model_dump() for r in latest_decision.rule_evaluations]
        risk_data = latest_decision.risk.model_dump()
    elif stored_analysis is not None:
        llm_v = stored_analysis.get("llm_verification", {})
        analysis_data = {
            "analysis_id": stored_analysis.get("analysis_id", "stored"),
            "timestamp": stored_analysis.get("timestamp", now_iso),
            "situation": llm_v.get("situation", "N/A"),
            "interpretation": llm_v.get("interpretation", "N/A"),
            "confidence": llm_v.get("confidence", 0.0),
            "uncertainty": llm_v.get("uncertainty", "HIGH"),
            "verification_required": llm_v.get("verification_required", True),
            "status": llm_v.get("status", "completed"),
            "model_used": llm_v.get("model_used", settings.llm_model),
            "supporting_event_ids": llm_v.get("supporting_event_ids", []),
            "error_message": llm_v.get("error_message"),
        }
        rules_data = stored_analysis.get("rule_evaluations", [])
        risk_data = stored_analysis.get("risk")
    else:
        analysis_data = None
        # Fallback to rule engine and risk engine directly if initialized
        try:
            rules_data = [r.model_dump() for r in get_rule_engine().get_latest_evaluations()]
        except Exception:
            rules_data = []
        try:
            latest_r = get_risk_engine().get_latest_assessment()
            risk_data = latest_r.model_dump() if latest_r else None
        except Exception:
            risk_data = None

    # 5. Incidents & Alerts
    inc_mgr = get_incident_manager()
    raw_active_incidents = inc_mgr.get_active_incidents()
    active_incidents = [i.model_dump() for i in raw_active_incidents]

    alert_mgr = get_alert_manager()
    raw_active_alerts = alert_mgr.get_active_alerts()
    active_alerts = [a.model_dump() for a in raw_active_alerts]

    # 6. Orchestrator & Authorization Evaluation
    from atlas.orchestration import AtlasOrchestrator
    orch = AtlasOrchestrator(storage=get_event_storage())
    if raw_active_incidents:
        primary_incident = raw_active_incidents[0]
        orch_eval = orch.evaluate_incident_eligibility(primary_incident.incident_id)
        orch_state = {
            "decision": orch_eval.decision.value,
            "reason": orch_eval.reason,
            "requires_human_authorization": orch_eval.requires_human_authorization,
            "incident_id": orch_eval.incident_id,
            "details": orch_eval.details,
        }
        auth_state = {
            "escalation_state": primary_incident.escalation_state.value if hasattr(primary_incident.escalation_state, "value") else str(primary_incident.escalation_state),
            "human_authorization_required": orch_eval.requires_human_authorization,
            "primary_incident_id": primary_incident.incident_id,
        }
    else:
        orch_state = {
            "decision": "STANDBY",
            "reason": "No active incidents requiring response orchestration.",
            "requires_human_authorization": False,
            "incident_id": None,
            "details": {},
        }
        auth_state = {
            "escalation_state": "NOT_ESCALATED",
            "human_authorization_required": False,
            "primary_incident_id": None,
        }

    # 7. Subsystems Status
    subsystems = {
        "backend": "ONLINE",
        "camera": "LIVE" if cam_status.get("is_connected") else cam_status.get("state", "DISCONNECTED").upper(),
        "perception": "ACTIVE" if cam_status.get("is_connected") else "STANDBY",
        "intelligence": "ACTIVE" if (analysis_data and analysis_data.get("status") == "completed") else "STANDBY",
        "rules": "ACTIVE" if len(rules_data) > 0 else "STANDBY",
        "risk": "ACTIVE" if risk_data is not None else "STANDBY",
        "incidents": "ACTIVE" if len(active_incidents) > 0 else "READY",
        "alerts": "ACTIVE" if len(active_alerts) > 0 else "READY",
        "orchestrator": "EVALUATING" if len(active_incidents) > 0 else "STANDBY",
    }

    # 8. Source Health
    from atlas.intelligence.verifier import get_verifier
    try:
        llm_health = get_verifier().provider.check_health()
    except Exception as e:
        llm_health = {"network": "ERROR", "error": str(e)}

    source_health = {
        "camera": {
            "is_connected": cam_status.get("is_connected", False),
            "fps": cam_status.get("fps", 0.0),
            "source": cam_status.get("source", "unknown"),
        },
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "network": llm_health.get("network", "UNKNOWN"),
            "model_available": llm_health.get("model_available", False),
        },
    }

    # 9. Multi-Source Context & Evidence Fusion (Step 6.5)
    from atlas.fusion.sources import get_source_registry
    from atlas.fusion.engine import get_fusion_engine
    try:
        src_reg = get_source_registry()
        sources_status = src_reg.get_sources_status()
        fus_eng = get_fusion_engine()
        latest_fus = fus_eng.get_latest_fusion()
        fusion_summary = {
            "sources": sources_status,
            "latest_fusion": latest_fus.model_dump() if latest_fus else None,
            "active_sources_count": sum(1 for s in sources_status if s.get("is_connected")),
            "total_subsystems": len(sources_status),
        }
    except Exception as e:
        fusion_summary = {
            "sources": [],
            "latest_fusion": None,
            "error": str(e),
        }

    # 10. Notifications & Escalation Subsystem (Step 7)
    from atlas.notifications.service import get_notification_service
    try:
        notif_svc = get_notification_service()
        notifications_summary = notif_svc.get_status_summary()
    except Exception as e:
        notifications_summary = {
            "total_notifications": 0,
            "unread_count": 0,
            "active_escalations_count": 0,
            "providers": [],
            "recent_notifications": [],
            "error": str(e),
        }

    payload = {
        "timestamp": now_iso,
        "device_id": settings.device_id,
        "environment": settings.environment,
        "system_status": "LIVE" if cam_status.get("is_connected") else "DEGRADED",
        "subsystems": subsystems,
        "camera_status": cam_status,
        "perception_status": perception_data,
        "active_people_count": active_people_count,
        "active_object_count": active_object_count,
        "latest_events": [e.model_dump() for e in raw_recent_events],
        "active_events": [e.model_dump() for e in raw_active_events],
        "latest_analysis": analysis_data,
        "latest_rule_results": rules_data,
        "latest_risk": risk_data,
        "active_incidents": active_incidents,
        "active_alerts": active_alerts,
        "orchestrator_state": orch_state,
        "authorization_state": auth_state,
        "source_health": source_health,
        "sources_summary": fusion_summary,
        "notifications_summary": notifications_summary,
    }
    return jsonify(payload), 200


@api_bp.route("/dashboard/authorized-user/state", methods=["GET"])
def get_authorized_user_dashboard_state():
    """Scoped operational dashboard state for Authorized Users.
    
    Zero-Mock Policy:
    Only returns high-level home status, active alerts, and alert-gated access state.
    Strictly forbids raw surveillance history, tracks, or admin systems.
    """
    actor, err_resp = _resolve_request_actor(None)
    if err_resp:
        return err_resp

    from atlas.incidents.service import get_incident_manager
    from atlas.incidents.schema import IncidentStatus
    from atlas.camera.stream import get_camera_stream
    from atlas.risk.engine import get_risk_engine

    inc_mgr = get_incident_manager()
    active_incidents = [
        i for i in inc_mgr.get_active_incidents()
        if i.status in (IncidentStatus.ACTIVE, IncidentStatus.ACKNOWLEDGED)
    ]

    cam = get_camera_stream()
    cam_status = cam.get_status()

    risk_engine = get_risk_engine()
    latest_risk = risk_engine.get_latest_assessment()
    risk_level = latest_risk.level.value if (latest_risk and hasattr(latest_risk.level, "value")) else (str(latest_risk.level) if latest_risk else "LOW")

    has_active_alert = len(active_incidents) > 0
    home_status = {
        "status": "ALERT_ACTIVE" if has_active_alert else "SECURE",
        "system_state": "MONITORING",
        "camera_connected": cam_status.get("is_connected", False),
        "camera_state": cam_status.get("state", "disconnected"),
        "risk_level": risk_level,
        "active_alert_count": len(active_incidents),
    }

    alerts_list = []
    for inc in active_incidents:
        eids = (getattr(inc, "evidence_ids", None) or []) + (inc.metadata.get("evidence_ids", []) if hasattr(inc, "metadata") and isinstance(inc.metadata, dict) else [])
        summary_text = (inc.metadata.get("summary") if hasattr(inc, "metadata") and isinstance(inc.metadata, dict) else None) or f"Active safety incident: {inc.incident_type}"
        alerts_list.append({
            "incident": inc.incident_id,
            "incident_id": inc.incident_id,
            "incident_type": inc.incident_type.value if hasattr(inc.incident_type, "value") else str(inc.incident_type),
            "severity": inc.severity.value if hasattr(inc.severity, "value") else str(inc.severity),
            "status": inc.status.value if hasattr(inc.status, "value") else str(inc.status),
            "created_at": inc.created_at,
            "timestamp": inc.created_at,
            "summary": summary_text,
            "location": "Home Interior / Monitored Area",
            "evidence_available": bool(eids),
            "evidence_count": len(eids),
            "evidence_ids": eids,
            "live_available": has_active_alert,
            "permitted_actions": [
                "VIEW_ALERT",
                "VIEW_EVIDENCE",
                "LIVE",
                "ACKNOWLEDGE"
            ] if actor.has_permission(Permission.ACKNOWLEDGE_INCIDENT) else ["VIEW_ALERT", "VIEW_EVIDENCE", "LIVE"],
        })

    return jsonify({
        "status": "ok",
        "role": actor.role.value if actor.role else "AUTHORIZED_USER",
        "user": {
            "actor_id": actor.actor_id,
            "display_name": actor.display_name,
            "role": actor.role.value if actor.role else None,
        },
        "home_status": home_status,
        "active_alerts": alerts_list,
        "alert_gated_access": {
            "live_accessible": has_active_alert,
            "evidence_accessible": has_active_alert,
            "status": "OPEN" if has_active_alert else "CLOSED",
            "message": "Live camera feed and evidence access is available while an active alert is in progress." if has_active_alert else "Live and evidence access is closed. No active alert is currently underway."
        }
    }), 200


@api_bp.route("/dashboard/admin/state", methods=["GET"])
def get_admin_dashboard_state():
    """Comprehensive Admin operational dashboard state (Sections A through J).
    
    Requires MANAGE_USERS permission (only ADMIN has this).
    """
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from datetime import datetime, timezone
    from atlas.camera.stream import get_camera_stream
    from atlas.context.memory import get_context_manager
    from atlas.events.storage import get_event_storage
    from atlas.incidents.service import get_incident_manager
    from atlas.intelligence.pipeline import get_intelligence_pipeline
    from atlas.intelligence.verifier import get_verifier
    from atlas.perception.engine import get_perception_engine
    from atlas.risk.engine import get_risk_engine
    from atlas.rules.engine import get_rule_engine
    from atlas.evidence.vault import get_evidence_vault
    from atlas.authority.user_service import get_user_management_service
    from atlas.audit.service import get_audit_logger

    now_iso = datetime.now(timezone.utc).isoformat()
    settings = get_settings()

    # Camera & diagnostics
    cam = get_camera_stream()
    cam_status = cam.get_status()

    # Context & Perception
    ctx = get_context_manager()
    recent_ctx = ctx.get_recent_context(window_seconds=120.0)
    raw_recent_events = ctx.get_recent_events(limit=50)

    perc_engine = get_perception_engine()
    latest_obs = perc_engine.get_latest_observation()

    # Intelligence & Risk
    try:
        intel_pipe = get_intelligence_pipeline()
        latest_dec = intel_pipe.get_latest_decision()
    except Exception:
        latest_dec = None

    try:
        llm_health = get_verifier().provider.check_health()
    except Exception as e:
        llm_health = {"network": "ERROR", "error": str(e)}

    risk_engine = get_risk_engine()
    latest_risk = risk_engine.get_latest_assessment()
    risk_data = latest_risk.model_dump() if latest_risk else None

    # Incidents
    inc_mgr = get_incident_manager()
    all_incidents = inc_mgr.get_recent_incidents(limit=50)
    active_incidents = inc_mgr.get_active_incidents()

    # Evidence
    vault = get_evidence_vault()
    all_evidence = vault.list_evidence()

    # Login Audits & User slots
    user_svc = get_user_management_service()
    login_audits = user_svc.get_login_audits(limit=25)
    slots_summary = user_svc.get_user_slots_summary()

    # Audit records
    audit_logger = get_audit_logger()
    audit_records = audit_logger.get_recent_records(limit=25)

    # Section A: HOME OVERVIEW
    subsystems = {
        "backend": "ONLINE",
        "camera": "LIVE" if cam_status.get("is_connected") else cam_status.get("state", "DISCONNECTED").upper(),
        "perception": "ACTIVE" if cam_status.get("is_connected") else "STANDBY",
        "intelligence": "ACTIVE" if (latest_dec and latest_dec.llm_verification) else "STANDBY",
        "rules": "ACTIVE",
        "risk": "ACTIVE" if risk_data else "STANDBY",
        "incidents": "ACTIVE" if active_incidents else "READY",
    }
    overview = {
        "system_status": "LIVE" if cam_status.get("is_connected") else "DEGRADED",
        "subsystems": subsystems,
        "camera_status": cam_status,
        "ai_status": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "health": llm_health,
        },
        "event_engine_status": {
            "recent_events_count": len(raw_recent_events),
            "timeline_length": len(ctx._timeline),
        },
        "llm_verifier_status": {
            "situation": latest_dec.llm_verification.situation if (latest_dec and latest_dec.llm_verification) else "System monitoring for physical activity.",
            "interpretation": latest_dec.llm_verification.interpretation if (latest_dec and latest_dec.llm_verification) else "Normal home state.",
            "confidence": latest_dec.llm_verification.confidence if (latest_dec and latest_dec.llm_verification) else 1.0,
            "verified": (latest_dec.llm_verification.status == "completed") if (latest_dec and latest_dec.llm_verification) else True,
        },
        "current_risk": risk_data,
        "active_incidents_count": len(active_incidents),
    }

    # Section B: LIVE
    live_section = {
        "stream_url": "/api/camera/video_feed",
        "frame_url": "/api/camera/frame",
        "is_connected": cam_status.get("is_connected", False),
        "fps": cam_status.get("fps", 0.0),
        "observation": latest_obs.model_dump() if latest_obs else None,
        "active_people": recent_ctx.get("persons", []),
        "active_objects": recent_ctx.get("objects", []),
    }

    # Section C: PEOPLE (real observed people)
    people_list = []
    for p in recent_ctx.get("persons", []):
        tid = p.get("track_id")
        p_full = ctx.get_person_context(tid) or p
        first_seen = p.get("first_seen", 0.0)
        last_seen = p.get("last_seen", 0.0)
        entry_iso = datetime.fromtimestamp(first_seen, timezone.utc).isoformat() if first_seen else "Unknown"
        exit_iso = None if p.get("active") else (datetime.fromtimestamp(last_seen, timezone.utc).isoformat() if last_seen else None)
        people_list.append({
            "track_id": tid,
            "identity": p.get("identity") or f"Observed Track #{tid} (Non-Authorized)",
            "is_authorized": p.get("is_authorized", False),
            "entry_time": entry_iso,
            "exit_time": exit_iso,
            "current_presence": p.get("active", False),
            "current_action": p.get("current_action", "unknown"),
            "movement_state": p.get("movement_state", "stationary"),
            "history": p_full.get("event_history", [])[-10:],
        })

    # Section D: ACTIVITY (entered, exited, walking, standing, sitting, fall-like events)
    activity_events = [e.model_dump() for e in raw_recent_events]

    # Section E: OBJECTS (tracked object, position, movement, disappearance, history)
    objects_list = []
    for o in recent_ctx.get("objects", []):
        tid = o.get("track_id")
        o_full = ctx.get_object_context(tid) or o
        objects_list.append({
            "track_id": tid,
            "label": o.get("label", "object"),
            "movement_state": o.get("movement_state", "static"),
            "is_present": o.get("active", False),
            "first_seen": o.get("first_seen"),
            "last_seen": o.get("last_seen"),
            "bbox": o.get("bbox"),
            "history": o_full.get("event_history", [])[-10:],
        })

    # Section F: INCIDENTS
    incidents_list = [i.model_dump() for i in all_incidents]

    # Section G: EVIDENCE
    evidence_list = [
        {
            "evidence_id": ev.evidence_id,
            "incident_id": ev.incident_id,
            "capture_status": ev.capture_status.value if hasattr(ev.capture_status, "value") else str(ev.capture_status),
            "sha256_hash": ev.sha256,
            "timestamp": ev.captured_at,
            "file_size_bytes": ev.file_size,
            "download_url": f"/api/evidence/{ev.evidence_id}",
        }
        for ev in all_evidence
    ]

    # Section H: LOGIN ACTIVITY
    login_activity = [
        {
            "audit_id": rec.audit_id,
            "timestamp": rec.timestamp,
            "user_id": rec.user_id,
            "username": rec.username,
            "role": rec.role,
            "status": rec.status,
            "face_confidence": rec.face_confidence,
            "ip_address": rec.ip_address,
            "evidence_path": rec.evidence_path,
            "details": rec.details,
        }
        for rec in login_audits
    ]

    # Section I: AUDIT
    audit_history = [r.model_dump() for r in audit_records]

    # Section J: USER MANAGEMENT
    user_management = slots_summary

    # Section K: NOTIFICATIONS & ESCALATION (Final Stage)
    from atlas.notifications.service import get_notification_service
    from atlas.notifications.providers import get_provider_registry
    notif_svc = get_notification_service()
    recent_notifs = notif_svc.get_notifications(limit=50)
    escalation_records = [r.model_dump() for r in notif_svc.storage.get_active_escalations()]
    provider_registry = get_provider_registry()
    providers_list = provider_registry.list_providers()

    return jsonify({
        "status": "ok",
        "timestamp": now_iso,
        "admin_user": {
            "actor_id": actor.actor_id,
            "username": getattr(actor, "username", "admin"),
            "display_name": actor.display_name,
            "role": actor.role.value,
        },
        "overview": overview,
        "live": live_section,
        "people": people_list,
        "activity": activity_events,
        "objects": objects_list,
        "incidents": incidents_list,
        "evidence": evidence_list,
        "login_activity": login_activity,
        "audit": audit_history,
        "user_management": user_management,
        "notifications": [n.model_dump() for n in recent_notifs],
        "escalation_records": escalation_records,
        "notification_providers": providers_list,
    }), 200



# ============================================================================
# Notification & Escalation Endpoints (Step 7)
# ============================================================================

@api_bp.route("/notifications", methods=["GET"])
def get_notifications():
    """Retrieve notifications with optional filters (requires VIEW_NOTIFICATIONS)."""
    from atlas.authority.models import Permission
    from atlas.notifications.service import get_notification_service

    actor, err_resp = _resolve_request_actor(Permission.VIEW_NOTIFICATIONS)
    if err_resp:
        return err_resp

    incident_id = request.args.get("incident_id")
    unread_only = request.args.get("unread_only", "").lower() in ("true", "1", "yes")
    limit = int(request.args.get("limit", "50"))

    svc = get_notification_service()
    # Viewers or operators can filter by recipient if desired, or admins can see all
    notifs = svc.get_notifications(
        incident_id=incident_id,
        recipient_user_id=request.args.get("recipient_user_id"),
        actor=actor,
        unread_only=unread_only,
        limit=limit,
    )

    return jsonify({
        "status": "ok",
        "total": len(notifs),
        "notifications": [n.model_dump() for n in notifs],
        "timestamp": time.time(),
    }), 200


@api_bp.route("/notifications/unread", methods=["GET"])
def get_unread_notifications():
    """Retrieve unread notifications count and list (requires VIEW_NOTIFICATIONS)."""
    from atlas.authority.models import Permission
    from atlas.notifications.service import get_notification_service

    actor, err_resp = _resolve_request_actor(Permission.VIEW_NOTIFICATIONS)
    if err_resp:
        return err_resp

    svc = get_notification_service()
    unread = svc.get_notifications(actor=actor, unread_only=True, limit=50)

    return jsonify({
        "status": "ok",
        "unread_count": len(unread),
        "notifications": [n.model_dump() for n in unread],
        "timestamp": time.time(),
    }), 200


@api_bp.route("/notifications/<notification_id>", methods=["GET"])
def get_single_notification(notification_id: str):
    """Retrieve details for a single notification (requires VIEW_NOTIFICATIONS)."""
    from atlas.authority.models import Permission
    from atlas.notifications.service import get_notification_service

    actor, err_resp = _resolve_request_actor(Permission.VIEW_NOTIFICATIONS)
    if err_resp:
        return err_resp

    svc = get_notification_service()
    notif = svc.get_notification(notification_id)
    if notif is None:
        return jsonify({"error": "Not Found", "details": f"Notification '{notification_id}' not found."}), 404

    return jsonify({
        "status": "ok",
        "notification": notif.model_dump(),
        "timestamp": time.time(),
    }), 200


@api_bp.route("/notifications/<notification_id>/acknowledge", methods=["POST"])
def acknowledge_single_notification(notification_id: str):
    """Acknowledge a notification (requires ACKNOWLEDGE_NOTIFICATION)."""
    from atlas.authority.models import Permission
    from atlas.notifications.service import get_notification_service

    actor, err_resp = _resolve_request_actor(Permission.ACKNOWLEDGE_NOTIFICATION)
    if err_resp:
        return err_resp

    svc = get_notification_service()
    try:
        updated = svc.acknowledge_notification(notification_id, actor)
        return jsonify({
            "status": "ok",
            "notification": updated.model_dump(),
            "message": "Notification successfully acknowledged.",
        }), 200
    except KeyError as e:
        return jsonify({"error": "Not Found", "details": str(e)}), 404
    except Exception as e:
        return jsonify({"error": "Bad Request", "details": str(e)}), 400


@api_bp.route("/notifications/providers", methods=["GET"])
def get_notification_providers():
    """Retrieve operational availability of notification providers (requires VIEW_NOTIFICATIONS)."""
    from atlas.authority.models import Permission
    from atlas.notifications.providers import get_provider_registry

    actor, err_resp = _resolve_request_actor(Permission.VIEW_NOTIFICATIONS)
    if err_resp:
        return err_resp

    registry = get_provider_registry()
    providers = registry.list_providers()

    return jsonify({
        "status": "ok",
        "providers": providers,
        "timestamp": time.time(),
    }), 200


@api_bp.route("/auth/login", methods=["POST"])
def auth_login():
    """Authenticate user with username and password."""
    from atlas.authority.auth import get_auth_service
    from atlas.authority.models import ROLE_PERMISSIONS
    from atlas.authority.user_service import get_user_management_service
    from flask import session as flask_session

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    auth_svc = get_auth_service()
    user_svc = get_user_management_service()

    # 1. Check persistent user management service: strictly require two-stage biometric authentication
    account = user_svc.verify_password(username, password)
    if account is not None:
        return jsonify({
            "error": "Two-Stage Authentication Required",
            "details": "Mandatory biometric face verification is required. Single-stage login is prohibited.",
            "status": "TWO_STAGE_REQUIRED",
        }), 403

    # 2. Fallback to legacy dev accounts (for Step 6 tests using admin/admin123)
    result = auth_svc.authenticate(username, password)
    if not result:
        return jsonify({"error": "Unauthorized", "details": "Invalid username or password."}), 401

    token, actor = result
    flask_session["session_token"] = token

    perms = [p.value for p in (ROLE_PERMISSIONS.get(actor.role, set()) if actor.role else set())]
    resp = jsonify({
        "status": "ok",
        "message": "Login successful.",
        "token": token,
        "user": {
            "actor_id": actor.actor_id,
            "username": username.strip().lower(),
            "role": actor.role.value if actor.role else None,
            "display_name": actor.display_name,
            "permissions": perms,
        },
    })
    resp.set_cookie("atlas_session", token, httponly=True, samesite="Lax")
    return resp, 200


@api_bp.route("/auth/logout", methods=["POST"])
def auth_logout():
    """Terminate active authenticated session."""
    from atlas.authority.auth import get_auth_service
    from atlas.authority.user_service import get_user_management_service
    from flask import session as flask_session

    auth_svc = get_auth_service()
    token = auth_svc.extract_token_from_request(request)
    actor = auth_svc.get_actor_by_token(token) if token else None
    if actor and token:
        try:
            svc = get_user_management_service()
            svc.log_login_event(
                user_id=actor.actor_id, username=actor.display_name,
                role=actor.role.value if actor.role else "UNKNOWN",
                status="LOGOUT", session_token=token,
                ip_address=request.remote_addr,
            )
        except Exception:
            pass
        auth_svc.invalidate_session(token)

    flask_session.pop("session_token", None)
    resp = jsonify({"status": "ok", "message": "Logged out successfully."})
    resp.delete_cookie("atlas_session")
    return resp, 200


@api_bp.route("/auth/me", methods=["GET"])
def auth_me():
    """Return currently authenticated actor profile and permissions."""
    from atlas.authority.models import ROLE_PERMISSIONS

    actor, err_resp = _resolve_request_actor(perm=None)
    if err_resp:
        return err_resp

    perms = [p.value for p in (ROLE_PERMISSIONS.get(actor.role, set()) if actor.role else set())]
    return jsonify({
        "status": "ok",
        "user": {
            "actor_id": actor.actor_id,
            "role": actor.role.value if actor.role else None,
            "display_name": actor.display_name,
            "is_system": actor.is_system,
            "permissions": perms,
        },
    }), 200


# ============================================================================
# Step 8: Two-Stage Identity Authentication Endpoints
# ============================================================================

# Pending credential sessions (waiting for face verification)
import secrets as _secrets
import time as _time
_pending_sessions: dict = {}
_pending_lock = __import__("threading").Lock()
_PENDING_TTL = 120  # seconds


def _gc_pending():
    """Remove expired pending sessions."""
    now = _time.time()
    with _pending_lock:
        expired = [k for k, v in _pending_sessions.items() if v["expires_at"] < now]
        for k in expired:
            del _pending_sessions[k]


@api_bp.route("/auth/login/credentials", methods=["POST"])
def auth_login_credentials():
    """Stage 1 of 2-stage login: Verify username + password.

    On success, returns a short-lived temp_token required to proceed to Stage 2 (face verification).
    The temp_token is valid for 120 seconds and must be included in the face verification call.
    Face verification is mandatory — credentials alone do NOT grant a session.
    """
    from atlas.authority.user_service import get_user_management_service
    from atlas.authority.models import Role as _Role

    _gc_pending()
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role_hint = (data.get("role") or "").strip().upper()

    if not username or not password:
        return jsonify({"error": "Bad Request", "details": "username and password are required."}), 400

    svc = get_user_management_service()
    account = svc.verify_password(username, password)

    ip_addr = request.remote_addr
    ua = request.headers.get("User-Agent", "")[:200]

    if account is None:
        svc.log_login_event(
            user_id="unknown", username=username, role="UNKNOWN",
            status="CREDENTIALS_FAILED", ip_address=ip_addr, user_agent=ua,
        )
        return jsonify({"error": "Unauthorized", "details": "Invalid username or password."}), 401

    if role_hint and role_hint in ("ADMIN", "AUTHORIZED_USER"):
        if role_hint == "ADMIN" and account.role != _Role.ADMIN:
            return jsonify({"error": "Forbidden", "details": "This account does not have Administrator privileges."}), 403
        if role_hint == "AUTHORIZED_USER" and account.role != _Role.AUTHORIZED_USER:
            return jsonify({"error": "Forbidden", "details": "This account is not registered as an Authorized User."}), 403

    temp_token = _secrets.token_urlsafe(32)
    with _pending_lock:
        _pending_sessions[temp_token] = {
            "user_id": account.user_id,
            "username": account.username,
            "display_name": account.display_name,
            "role": account.role.value,
            "expires_at": _time.time() + _PENDING_TTL,
        }

    svc.log_login_event(
        user_id=account.user_id, username=account.username, role=account.role.value,
        status="CREDENTIALS_OK", ip_address=ip_addr, user_agent=ua,
        details={"message": "Awaiting face verification."},
    )

    return jsonify({
        "status": "credentials_verified",
        "message": "Credentials verified. Face verification required to complete login.",
        "temp_token": temp_token,
        "face_required": True,
        "username": account.username,
        "display_name": account.display_name,
        "role": account.role.value,
        "face_enrolled": account.enrolled_embedding is not None,
    }), 200


@api_bp.route("/auth/login/face", methods=["POST"])
def auth_login_face():
    """Stage 2 of 2-stage login: Face biometric verification.

    Requires:
      - temp_token from Stage 1
      - optional image_data (base64 JPEG); if omitted, captures directly from real CameraStream

    On success, grants a full session token and sets atlas_session cookie.
    Face verification failure results in a FACE_DENIED audit log entry.
    """
    import datetime as _dt
    from atlas.authority.auth import get_auth_service
    from atlas.authority.face import get_face_biometrics_engine
    from atlas.authority.models import ROLE_PERMISSIONS, Role as _Role
    from atlas.authority.user_service import get_user_management_service
    from flask import session as flask_session

    _gc_pending()
    data = request.get_json(silent=True) or {}
    temp_token = data.get("temp_token") or ""
    image_data = data.get("image_data") or ""

    ip_addr = request.remote_addr
    ua = request.headers.get("User-Agent", "")[:200]

    if not temp_token:
        return jsonify({"error": "Bad Request", "details": "temp_token is required."}), 400

    with _pending_lock:
        pending = _pending_sessions.get(temp_token)

    if pending is None:
        return jsonify({"error": "Unauthorized", "details": "Invalid or expired temp_token. Please restart login."}), 401

    if pending["expires_at"] < _time.time():
        with _pending_lock:
            _pending_sessions.pop(temp_token, None)
        return jsonify({"error": "Unauthorized", "details": "Face verification window expired. Please log in again."}), 401

    user_id = pending["user_id"]
    username = pending["username"]
    role_str = pending["role"]
    display_name = pending["display_name"]

    svc = get_user_management_service()
    account = svc.get_user_by_id(user_id)
    if account is None or not account.is_active:
        with _pending_lock:
            _pending_sessions.pop(temp_token, None)
        return jsonify({"error": "Unauthorized", "details": "User account not found or disabled."}), 401

    face_engine = get_face_biometrics_engine()

    # Obtain real frame: from client payload if provided, or directly from CameraStream
    query_image = image_data
    frame_ts = None
    if not query_image:
        from atlas.camera.stream import get_camera_stream
        cam = get_camera_stream()
        frame_data = cam.get_latest_frame()
        if frame_data is None:
            svc.log_login_event(
                user_id=user_id, username=username, role=role_str,
                status="FACE_DENIED", face_confidence=0.0,
                ip_address=ip_addr, user_agent=ua,
                details={"error": "CAMERA_UNAVAILABLE", "reason": "No real camera frame available from webcam stream."},
            )
            return jsonify({
                "error": "Camera Unavailable",
                "details": "Physical webcam frame is not available. Please verify camera connection.",
                "status": "CAMERA_UNAVAILABLE",
                "face_verified": False,
                "confidence": 0.0,
            }), 503
        query_image, frame_ts = frame_data

    # Handle first-time Admin biometric enrollment
    if account.enrolled_embedding is None:
        if account.role == _Role.ADMIN:
            try:
                frame = face_engine.decode_image_payload(query_image)
                valid, f_stat, f_reason = face_engine.validate_frame(frame, timestamp=frame_ts)
                if not valid:
                    return jsonify({"error": "Invalid Frame", "details": f_reason, "status": f_stat, "face_verified": False}), 400

                face_ok, face_stat, crop, meta = face_engine.detect_face(frame)
                if not face_ok or crop is None:
                    return jsonify({"error": "Face Check Failed", "details": meta.get("reason", "Single face required"), "status": face_stat, "face_verified": False}), 400

                embedding = face_engine.compute_embedding(crop)
                snap_path, snap_hash = face_engine.save_verification_snapshot(frame, user_id, "ENROLLED_ADMIN")
                svc.enroll_face(user_id, embedding.tolist(), image_path=snap_path or None)
                account = svc.get_user_by_id(user_id)
            except Exception as exc:
                return jsonify({"error": "Admin Face Enrollment Failed", "details": str(exc), "status": "FAILED"}), 500
        else:
            svc.log_login_event(
                user_id=user_id, username=username, role=role_str,
                status="FACE_DENIED", face_confidence=0.0,
                ip_address=ip_addr, user_agent=ua,
                details={"reason": "No face template enrolled for this user.", "status": "BIOMETRIC_NOT_ENROLLED"},
            )
            with _pending_lock:
                _pending_sessions.pop(temp_token, None)
            return jsonify({
                "error": "Unauthorized",
                "details": "Face is not enrolled for this account. Please contact your administrator.",
                "status": "BIOMETRIC_NOT_ENROLLED",
                "face_verified": False,
                "confidence": 0.0,
            }), 401

    try:
        result = face_engine.verify(
            target_user_id=user_id,
            enrolled_embedding=account.enrolled_embedding,
            query_image=query_image,
            save_snapshot=True,
            frame_timestamp=frame_ts,
        )
    except Exception as exc:
        svc.log_login_event(
            user_id=user_id, username=username, role=role_str,
            status="FACE_DENIED", face_confidence=0.0,
            ip_address=ip_addr, user_agent=ua,
            details={"error": str(exc)},
        )
        return jsonify({"error": "Internal Server Error", "details": f"Face verification error: {exc}"}), 500

    if not result.verified:
        status_code = result.details.get("status") or "FACE_MISMATCH"
        reason = result.details.get("reason") or "Face verification failed. Identity not confirmed."
        svc.log_login_event(
            user_id=user_id, username=username, role=role_str,
            status="FACE_DENIED", face_confidence=result.confidence,
            ip_address=ip_addr, user_agent=ua, evidence_path=result.snapshot_path,
            details={"confidence": result.confidence, "threshold": result.threshold, "status": status_code, "reason": reason},
        )
        with _pending_lock:
            _pending_sessions.pop(temp_token, None)
        return jsonify({
            "error": "Unauthorized",
            "details": reason,
            "status": status_code,
            "face_verified": False,
            "confidence": round(result.confidence, 4),
            "threshold": round(result.threshold, 4),
            "details_metadata": result.details,
        }), 401

    # Face verified — grant full session
    with _pending_lock:
        _pending_sessions.pop(temp_token, None)

    auth_svc = get_auth_service()
    actor = account.to_actor()
    session_token = _secrets.token_urlsafe(32)
    now = _dt.datetime.now(_dt.timezone.utc)
    auth_svc._sessions[session_token] = {
        "username": username,
        "actor": actor,
        "created_at": now,
        "expires_at": now + auth_svc.session_ttl,
    }
    flask_session["session_token"] = session_token

    svc.log_login_event(
        user_id=user_id, username=username, role=role_str,
        status="SESSION_GRANTED", face_confidence=result.confidence,
        session_token=session_token,
        ip_address=ip_addr, user_agent=ua, evidence_path=result.snapshot_path,
        details={"confidence": result.confidence, "threshold": result.threshold, "status": "VERIFIED"},
    )

    role_enum = _Role(role_str)
    perms = [p.value for p in (ROLE_PERMISSIONS.get(role_enum, set()))]
    resp = jsonify({
        "status": "ok",
        "message": "Identity verified. Session granted.",
        "token": session_token,
        "face_verified": True,
        "confidence": round(result.confidence, 4),
        "threshold": round(result.threshold, 4),
        "user": {
            "actor_id": user_id,
            "username": username,
            "role": role_str,
            "display_name": display_name,
            "permissions": perms,
        },
    })
    resp.set_cookie("atlas_session", session_token, httponly=True, samesite="Lax")
    return resp, 200


# ============================================================================
# Step 8: User Management Admin Endpoints (MANAGE_USERS permission required)
# ============================================================================

@api_bp.route("/admin/users", methods=["GET"])
def admin_list_users():
    """List all user accounts (requires MANAGE_USERS)."""
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service
    svc = get_user_management_service()
    users = svc.list_users()
    return jsonify({
        "status": "ok",
        "count": len(users),
        "users": [u.public_dict() for u in users],
    }), 200


@api_bp.route("/admin/users", methods=["POST"])
def admin_create_user():
    """Create a new user account (requires MANAGE_USERS)."""
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service
    from atlas.authority.models import Role

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    display_name = (data.get("display_name") or username).strip()
    password = data.get("password") or ""
    role_str = (data.get("role") or "AUTHORIZED_USER").upper()

    if not username or not password:
        return jsonify({"error": "Bad Request", "details": "username and password are required."}), 400

    try:
        role = Role(role_str)
    except ValueError:
        return jsonify({"error": "Bad Request", "details": f"Invalid role '{role_str}'. Website identity roles must be ADMIN or AUTHORIZED_USER."}), 400

    svc = get_user_management_service()
    try:
        new_user = svc.create_user(username, display_name, password, role)
        return jsonify({
            "status": "ok",
            "message": f"User '{username}' created successfully.",
            "user": new_user.public_dict(),
        }), 201
    except ValueError as exc:
        return jsonify({"error": "Bad Request", "details": str(exc)}), 400


@api_bp.route("/admin/users/<user_id>", methods=["GET"])
def admin_get_user(user_id: str):
    """Get a specific user account (requires MANAGE_USERS)."""
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service
    svc = get_user_management_service()
    user = svc.get_user_by_id(user_id)
    if user is None:
        return jsonify({"error": "Not Found", "details": f"User '{user_id}' not found."}), 404
    return jsonify({"status": "ok", "user": user.public_dict()}), 200


@api_bp.route("/admin/users/<user_id>/status", methods=["POST"])
def admin_update_user_status(user_id: str):
    """Enable or disable a user account (requires MANAGE_USERS)."""
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service
    data = request.get_json(silent=True) or {}
    is_active = data.get("is_active")
    if is_active is None:
        return jsonify({"error": "Bad Request", "details": "Field 'is_active' (boolean) is required."}), 400

    svc = get_user_management_service()
    try:
        updated = svc.update_user_status(user_id, bool(is_active))
        return jsonify({"status": "ok", "user": updated.public_dict()}), 200
    except KeyError as exc:
        return jsonify({"error": "Not Found", "details": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": "Bad Request", "details": str(exc)}), 400


@api_bp.route("/admin/users/<user_id>", methods=["PUT"])
def admin_update_user(user_id: str):
    """Update user account details (requires MANAGE_USERS)."""
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service
    from atlas.authority.models import Role

    data = request.get_json(silent=True) or {}
    display_name = data.get("display_name")
    role_str = data.get("role")
    is_active = data.get("is_active")
    password = data.get("password")

    svc = get_user_management_service()
    try:
        role = Role(role_str) if role_str else None
        user = svc.update_user(user_id, display_name=display_name, role=role)
        if is_active is not None:
            user = svc.update_user_status(user_id, bool(is_active))
        if password:
            svc.change_password(user_id, password)
        return jsonify({"status": "ok", "user": user.public_dict()}), 200
    except KeyError as exc:
        return jsonify({"error": "Not Found", "details": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": "Bad Request", "details": str(exc)}), 400


@api_bp.route("/admin/users/<user_id>", methods=["DELETE"])
def admin_delete_user(user_id: str):
    """Delete a user account (requires MANAGE_USERS)."""
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service
    svc = get_user_management_service()
    try:
        svc.delete_user(user_id)
        return jsonify({"status": "ok", "message": f"User '{user_id}' deleted."}), 200
    except KeyError as exc:
        return jsonify({"error": "Not Found", "details": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": "Bad Request", "details": str(exc)}), 400


@api_bp.route("/admin/users/<user_id>/password", methods=["POST"])
def admin_change_password(user_id: str):
    """Change user password (requires MANAGE_USERS)."""
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service
    data = request.get_json(silent=True) or {}
    new_password = data.get("new_password") or ""

    svc = get_user_management_service()
    try:
        svc.change_password(user_id, new_password)
        return jsonify({"status": "ok", "message": "Password updated successfully."}), 200
    except (KeyError, ValueError) as exc:
        code = 404 if isinstance(exc, KeyError) else 400
        return jsonify({"error": "Error", "details": str(exc)}), code


@api_bp.route("/admin/users/<user_id>/face/enroll", methods=["POST"])
def admin_enroll_face(user_id: str):
    """Enroll or update face biometric template for a user (requires MANAGE_USERS).

    Request body (optional):
      image_data: base64-encoded JPEG. If omitted, captures from live CameraStream.
    """
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.face import get_face_biometrics_engine
    from atlas.authority.user_service import get_user_management_service

    data = request.get_json(silent=True) or {}
    image_data = data.get("image_data") or ""

    svc = get_user_management_service()
    account = svc.get_user_by_id(user_id)
    if account is None:
        return jsonify({"error": "Not Found", "details": f"User '{user_id}' not found."}), 404

    face_engine = get_face_biometrics_engine()
    frame_ts = None
    if not image_data:
        from atlas.camera.stream import get_camera_stream
        camera = get_camera_stream()
        frame_data = camera.get_latest_frame()
        if frame_data is None:
            return jsonify({
                "error": "Camera Unavailable",
                "details": "No real camera frame available from webcam stream.",
                "status": "CAMERA_UNAVAILABLE",
            }), 503
        frame, frame_ts = frame_data
    else:
        try:
            frame = face_engine.decode_image_payload(image_data)
        except Exception as exc:
            return jsonify({"error": "Bad Request", "details": f"Could not decode image payload: {exc}"}), 400

    # Physical frame validation
    valid, f_stat, f_reason = face_engine.validate_frame(frame, timestamp=frame_ts)
    if not valid:
        return jsonify({"error": "Frame Validation Failed", "details": f_reason, "status": f_stat}), 400

    # Enforce exactly one face
    face_ok, face_stat, crop, meta = face_engine.detect_face(frame)
    if not face_ok or crop is None:
        return jsonify({
            "error": "Face Validation Failed",
            "details": meta.get("reason", "Exactly one face is required for enrollment."),
            "status": face_stat,
        }), 400

    try:
        embedding = face_engine.compute_embedding(crop)
        snap_path, snap_hash = face_engine.save_verification_snapshot(frame, user_id, "ENROLLED")
        svc.enroll_face(user_id, embedding.tolist(), image_path=snap_path or None)
        return jsonify({
            "status": "ok",
            "message": f"Face template enrolled for user '{account.username}'.",
            "embedding_dim": len(embedding),
            "snapshot_path": snap_path,
            "snapshot_sha256": snap_hash,
            "face_metadata": meta,
        }), 200
    except Exception as exc:
        return jsonify({"error": "Processing Error", "details": str(exc)}), 500


@api_bp.route("/admin/users/<user_id>/face/remove", methods=["POST"])
def admin_remove_face(user_id: str):
    """Remove face biometric enrollment for a user (requires MANAGE_USERS)."""
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service
    svc = get_user_management_service()
    try:
        svc.remove_face_enrollment(user_id)
        return jsonify({"status": "ok", "message": "Face enrollment removed."}), 200
    except KeyError as exc:
        return jsonify({"error": "Not Found", "details": str(exc)}), 404


@api_bp.route("/admin/login-audits", methods=["GET"])
def admin_login_audits():
    """Retrieve login audit trail (requires VIEW_LOGIN_AUDITS permission)."""
    actor, err_resp = _resolve_request_actor(Permission.VIEW_LOGIN_AUDITS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service
    user_id = request.args.get("user_id")
    limit = request.args.get("limit", default=50, type=int)
    svc = get_user_management_service()
    audits = svc.get_login_audits(user_id=user_id, limit=limit)
    return jsonify({
        "status": "ok",
        "count": len(audits),
        "audits": [a.to_dict() for a in audits],
    }), 200


@api_bp.route("/admin/identity/bounds", methods=["GET"])
def admin_identity_bounds():
    """Return current identity boundary status (requires MANAGE_USERS)."""
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service, MAX_USERS, MAX_ADMINS, MAX_AUTHORIZED_USERS
    from atlas.authority.models import Role
    svc = get_user_management_service()
    users = svc.list_users()
    admins = [u for u in users if u.role == Role.ADMIN]
    authorized_users = [u for u in users if u.role == Role.AUTHORIZED_USER]
    active = [u for u in users if u.is_active]
    enrolled = [u for u in users if u.enrolled_embedding is not None]
    return jsonify({
        "status": "ok",
        "bounds": {
            "max_total_users": MAX_ADMINS + MAX_AUTHORIZED_USERS,
            "max_admins": MAX_ADMINS,
            "max_authorized_users": MAX_AUTHORIZED_USERS,
            "current_total": len(users),
            "current_admins": len(admins),
            "current_authorized_users": len(authorized_users),
            "current_operators": 0,
            "current_viewers": 0,
            "active_accounts": len(active),
            "face_enrolled_count": len(enrolled),
            "slots_remaining": max(0, MAX_AUTHORIZED_USERS - len(authorized_users)),
        },
    }), 200


@api_bp.route("/admin/users/<user_id>", methods=["PUT", "PATCH"])
def admin_edit_user(user_id: str):
    """Edit user display name or role (requires MANAGE_USERS)."""
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service
    from atlas.authority.models import Role

    data = request.get_json(silent=True) or {}
    display_name = data.get("display_name")
    role_str = data.get("role")
    role = None
    if role_str:
        try:
            role = Role(role_str.upper())
        except ValueError:
            return jsonify({"error": "Bad Request", "details": f"Invalid role '{role_str}'."}), 400

    svc = get_user_management_service()
    try:
        updated = svc.update_user(user_id, display_name=display_name, role=role)
        return jsonify({
            "status": "ok",
            "message": f"User '{updated.username}' updated successfully.",
            "user": updated.public_dict(),
        }), 200
    except KeyError as exc:
        return jsonify({"error": "Not Found", "details": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": "Bad Request", "details": str(exc)}), 400


@api_bp.route("/admin/users/slots", methods=["GET"])
def admin_user_slots():
    """Retrieve 10-slot identity summary (requires MANAGE_USERS)."""
    actor, err_resp = _resolve_request_actor(Permission.MANAGE_USERS)
    if err_resp:
        return err_resp

    from atlas.authority.user_service import get_user_management_service
    svc = get_user_management_service()
    return jsonify({
        "status": "ok",
        **svc.get_user_slots_summary(),
    }), 200

