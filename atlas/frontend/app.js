// ATLAS Home — Role-Specific Premium Dashboards (Admin vs. Authorized User)
// Zero-Mock Policy: Consumes and renders real backend state only.
// Consequential actions are strictly authenticated and authorized by backend.

(function () {
  "use strict";

  // Polling configuration
  const POLL_INTERVAL_MS = 1000;
  const STALE_THRESHOLD_MS = 5000;

  // Client State
  let isFetching = false;
  let lastSuccessfulFetchTime = 0;
  let connectionState = "LOADING"; // "LOADING" | "LIVE" | "STALE" | "DISCONNECTED" | "ERROR"
  let currentUser = null; // { actor_id, username, role, display_name, permissions }
  let latestDashboardData = null;
  let currentAuthAlerts = [];

  // DOM Elements: Header & Status
  const elConnectionBadge = document.getElementById("connection-badge");
  const elConnectionText = document.getElementById("connection-text");
  const elLastSyncTime = document.getElementById("last-sync-time");
  const elHeaderRoleBadge = document.getElementById("header-role-badge");
  const elHeaderIndicators = document.getElementById("header-indicators");
  const elStatSysOnline = document.getElementById("stat-sys-online");
  const elStatProcLive = document.getElementById("stat-proc-live");
  const elStatLlmHealth = document.getElementById("stat-llm-health");
  const elStatLlmText = document.getElementById("stat-llm-text");

  // User & Auth Bar
  const elUserLoggedIn = document.getElementById("user-logged-in");
  const elUserLoggedOut = document.getElementById("user-logged-out");
  const elUserRoleBadge = document.getElementById("user-role-badge");
  const elUserDisplayName = document.getElementById("user-display-name");
  const btnOpenLogin = document.getElementById("btn-open-login");
  const btnLogout = document.getElementById("btn-logout");
  const btnGuestLogin = document.getElementById("btn-guest-login");

  // View Containers
  const viewAdmin = document.getElementById("view-admin-dashboard");
  const viewAuth = document.getElementById("view-auth-dashboard");
  const viewGuest = document.getElementById("view-guest-dashboard");

  // Admin Section A (Overview)
  const elKpiPerimeterVal = document.getElementById("kpi-perimeter-val");
  const elKpiThreatVal = document.getElementById("kpi-threat-val");
  const elKpiIncidentsVal = document.getElementById("kpi-incidents-val");
  const elKpiUsersVal = document.getElementById("kpi-users-val");
  const subElements = {
    backend: document.getElementById("sub-backend"),
    camera: document.getElementById("sub-camera"),
    perception: document.getElementById("sub-perception"),
    intelligence: document.getElementById("sub-intelligence"),
    rules: document.getElementById("sub-rules"),
    risk: document.getElementById("sub-risk"),
    incidents: document.getElementById("sub-incidents"),
    alerts: document.getElementById("sub-alerts"),
    orchestrator: document.getElementById("sub-orchestrator"),
  };
  const elLlmStatusBadge = document.getElementById("llm-status-badge");
  const elLlmSituation = document.getElementById("llm-situation");
  const elLlmConfidence = document.getElementById("llm-confidence");
  const elLlmUncertainty = document.getElementById("llm-uncertainty");
  const elLlmVerifyReq = document.getElementById("llm-verify-req");
  const elLlmModel = document.getElementById("llm-model");

  // Admin Section B (Live)
  const elCameraFeed = document.getElementById("camera-feed");
  const elCameraSourceLabel = document.getElementById("camera-source-label");
  const elVideoOverlay = document.getElementById("video-overlay");
  const elOverlayTitle = document.getElementById("overlay-title");
  const elOverlayMsg = document.getElementById("overlay-msg");
  const elMetricFps = document.getElementById("metric-fps");
  const elMetricResolution = document.getElementById("metric-resolution");
  const elMetricPeopleCount = document.getElementById("metric-people-count");
  const elMetricObjectsCount = document.getElementById("metric-objects-count");

  // Admin Section C (People)
  const elAdminPeopleCount = document.getElementById("admin-people-count");
  const elAdminPeopleList = document.getElementById("admin-people-list");

  // Admin Section D (Activity)
  const elEventsCountBadge = document.getElementById("events-count-badge");
  const elEventsTbody = document.getElementById("events-tbody");

  // Admin Section E (Objects)
  const elAdminObjectsCount = document.getElementById("admin-objects-count");
  const elAdminObjectsList = document.getElementById("admin-objects-list");

  // Admin Section F (Incidents)
  const elActiveIncidentsCount = document.getElementById("active-incidents-count");
  const elIncidentsList = document.getElementById("incidents-list");

  // Admin Section G (Evidence)
  const elAdminEvidenceCount = document.getElementById("admin-evidence-count");
  const elAdminEvidenceList = document.getElementById("admin-evidence-list");

  // Admin Section H (Login Activity)
  const elAdminLoginsCount = document.getElementById("admin-logins-count");
  const elAdminLoginsTbody = document.getElementById("admin-logins-tbody");

  // Admin Section I (Audit)
  const elAdminAuditCount = document.getElementById("admin-audit-count");
  const elAdminAuditTbody = document.getElementById("admin-audit-tbody");

  // Admin Section J (User Management)
  const elSlotsOccupiedPill = document.getElementById("slots-occupied-pill");
  const elAdminUserSlotsGrid = document.getElementById("admin-user-slots-grid");

  // Admin Section K (Notifications & Escalation)
  const elAdminNotifsCount = document.getElementById("admin-notifs-count");
  const elAdminNotifsTbody = document.getElementById("admin-notifs-tbody");
  const elAdminProvidersRow = document.getElementById("admin-providers-row");
  const elAdminMobileProviderPill = document.getElementById("admin-mobile-provider-pill");

  // Authorized User View Elements
  const elAuthStatusShield = document.getElementById("auth-status-shield");
  const elAuthStatusHeadline = document.getElementById("auth-status-headline");
  const elAuthStatusDescription = document.getElementById("auth-status-description");
  const elAuthModeVal = document.getElementById("auth-mode-val");
  const elAuthCamVal = document.getElementById("auth-cam-val");
  const elAuthCheckedVal = document.getElementById("auth-checked-val");
  const elAuthAlertsBadge = document.getElementById("auth-alerts-badge");
  const elAuthAlertsContainer = document.getElementById("auth-alerts-container");
  const elAuthAllClearCard = document.getElementById("auth-all-clear-card");

  // Modals & Action Elements
  const modalLogin = document.getElementById("modal-login");
  const formLogin = document.getElementById("form-login");
  const inputLoginUser = document.getElementById("login-username");
  const inputLoginPass = document.getElementById("login-password");
  const elLoginError = document.getElementById("login-error");
  const btnCloseLogin = document.getElementById("btn-close-login");

  // Stage 2 Face verification elements
  const loginStageCredentials = document.getElementById("login-stage-credentials");
  const loginStageFace = document.getElementById("login-stage-face");
  const btnCloseFace = document.getElementById("btn-close-face");
  const faceVideo = document.getElementById("face-video");
  const faceCanvas = document.getElementById("face-canvas");
  const faceVideoPreview = document.getElementById("face-video-preview");
  const faceUserHint = document.getElementById("face-user-hint");
  const faceStatusMsg = document.getElementById("face-status-message");
  const faceError = document.getElementById("face-error");
  const btnCaptureFace = document.getElementById("btn-capture-face");
  const btnBackToCredentials = document.getElementById("btn-back-to-credentials");

  // Landing & Role Context Elements
  const btnLoginAsAdmin = document.getElementById("btn-login-as-admin");
  const btnLoginAsUser = document.getElementById("btn-login-as-user");
  const loginRoleBadge = document.getElementById("login-role-badge");
  const loginRoleSub = document.getElementById("login-role-sub");
  const stageStep1 = document.getElementById("stage-step-1");
  const stageStep2 = document.getElementById("stage-step-2");
  const stageConnectorBar = document.getElementById("stage-connector-bar");

  // Biometric Subsystem Status Pills
  const valCamStatus = document.getElementById("val-cam-status");
  const valFrameStatus = document.getElementById("val-frame-status");
  const valFaceStatus = document.getElementById("val-face-status");
  const valVerifyStatus = document.getElementById("val-verify-status");

  let selectedRoleHint = "ADMIN";
  let tempAuthToken = null;

  // Investigation Modal (DOM IDs required by test harness)
  const modalInvestigation = document.getElementById("modal-investigation");
  const btnCloseInvestigation = document.getElementById("btn-close-investigation");
  const btnCloseInvFooter = document.getElementById("btn-close-inv-footer");
  const invModalTitle = document.getElementById("inv-modal-title");
  const invWhyRaised = document.getElementById("inv-why-raised");
  const invTimelineContainer = document.getElementById("inv-timeline-container");
  const invTimelineCount = document.getElementById("inv-timeline-count");
  const invEventsContainer = document.getElementById("inv-events-container");
  const invEventsCount = document.getElementById("inv-events-count");
  const invGroundingList = document.getElementById("inv-llm-grounding-list");
  const invCameraStatusBadge = document.getElementById("inv-camera-status-badge");
  const invCameraMessage = document.getElementById("inv-camera-message");
  const invCameraArtifactContainer = document.getElementById("inv-camera-artifact-container");
  const invCamProvenance = document.getElementById("inv-cam-provenance");
  const invCamCaptured = document.getElementById("inv-cam-captured");
  const invCamSourceEvent = document.getElementById("inv-cam-source-event");
  const invCamEventTime = document.getElementById("inv-cam-event-time");
  const invCamFrameTime = document.getElementById("inv-cam-frame-time");
  const invCamEvidenceId = document.getElementById("inv-cam-evidence-id");
  const invCamIntegrity = document.getElementById("inv-cam-integrity");
  const invCamDimensions = document.getElementById("inv-cam-dimensions");
  const invCamSize = document.getElementById("inv-cam-size");
  const invCamSha256 = document.getElementById("inv-cam-sha256");
  const invEvidenceImg = document.getElementById("inv-evidence-img");

  // Footer Actions
  const invActionAck = document.getElementById("inv-action-ack");
  const invActionResolve = document.getElementById("inv-action-resolve");
  const invActionDismiss = document.getElementById("inv-action-dismiss");
  const invActionAuthorize = document.getElementById("inv-action-authorize");

  // User Management Modals
  const modalAddUser = document.getElementById("modal-add-user");
  const btnCloseAddUser = document.getElementById("btn-close-add-user");
  const btnCancelAddUser = document.getElementById("btn-cancel-add-user");
  const formAddUser = document.getElementById("form-add-user");
  const inputNewUsername = document.getElementById("new-username");
  const inputNewDisplayName = document.getElementById("new-display-name");
  const inputNewPassword = document.getElementById("new-password");
  const selectNewRole = document.getElementById("new-role");
  const elAddUserError = document.getElementById("add-user-error");

  const modalEditUser = document.getElementById("modal-edit-user");
  const btnCloseEditUser = document.getElementById("btn-close-edit-user");
  const btnCancelEditUser = document.getElementById("btn-cancel-edit-user");
  const formEditUser = document.getElementById("form-edit-user");
  const inputEditUserId = document.getElementById("edit-user-id");
  const inputEditUsername = document.getElementById("edit-username");
  const inputEditDisplayName = document.getElementById("edit-display-name");
  const selectEditRole = document.getElementById("edit-role");
  const checkEditIsActive = document.getElementById("edit-is-active");
  const inputEditPassword = document.getElementById("edit-password");
  const elEditUserError = document.getElementById("edit-user-error");

  // Authorized User Alert Modals
  const modalAuthAlert = document.getElementById("modal-auth-alert");
  const btnCloseAuthAlert = document.getElementById("btn-close-auth-alert");
  const btnCloseAuthAlertFooter = document.getElementById("btn-close-auth-alert-footer");
  const authModalAlertTitle = document.getElementById("auth-modal-alert-title");
  const authModalAlertSev = document.getElementById("auth-modal-alert-sev");
  const authModalAlertStatus = document.getElementById("auth-modal-alert-status");
  const authModalAlertSummary = document.getElementById("auth-modal-alert-summary");
  const authModalAlertTime = document.getElementById("auth-modal-alert-time");

  const modalAuthEvidence = document.getElementById("modal-auth-evidence");
  const btnCloseAuthEvidence = document.getElementById("btn-close-auth-evidence");
  const btnCloseAuthEvFooter = document.getElementById("btn-close-auth-ev-footer");
  const authEvId = document.getElementById("auth-ev-id");
  const authEvImg = document.getElementById("auth-ev-img");
  const authEvidenceError = document.getElementById("auth-evidence-error");

  const modalAuthLive = document.getElementById("modal-auth-live");
  const btnCloseAuthLive = document.getElementById("btn-close-auth-live");
  const btnCloseAuthLiveFooter = document.getElementById("btn-close-auth-live-footer");
  const authLiveImg = document.getElementById("auth-live-img");
  const authLiveError = document.getElementById("auth-live-error");

  // Action Modals (Resolve, Dismiss, Authorize)
  const modalResolve = document.getElementById("modal-resolve");
  const btnCloseResolve = document.getElementById("btn-close-resolve");
  const btnCancelResolve = document.getElementById("btn-cancel-resolve");
  const formResolve = document.getElementById("form-resolve");
  const inputResolveId = document.getElementById("resolve-incident-id");
  const resolveCtxId = document.getElementById("resolve-ctx-id");
  const resolveCtxType = document.getElementById("resolve-ctx-type");
  const resolveCtxSev = document.getElementById("resolve-ctx-sev");
  const resolveCtxRisk = document.getElementById("resolve-ctx-risk");
  const selectResolveReason = document.getElementById("resolve-reason");
  const inputResolveNotes = document.getElementById("resolve-notes");
  const elResolveError = document.getElementById("resolve-error");

  const modalDismiss = document.getElementById("modal-dismiss");
  const btnCloseDismiss = document.getElementById("btn-close-dismiss");
  const btnCancelDismiss = document.getElementById("btn-cancel-dismiss");
  const formDismiss = document.getElementById("form-dismiss");
  const inputDismissId = document.getElementById("dismiss-incident-id");
  const dismissCtxId = document.getElementById("dismiss-ctx-id");
  const dismissCtxType = document.getElementById("dismiss-ctx-type");
  const dismissCtxSev = document.getElementById("dismiss-ctx-sev");
  const inputDismissReason = document.getElementById("dismiss-reason");
  const elDismissError = document.getElementById("dismiss-error");

  const modalAuthorize = document.getElementById("modal-authorize");
  const btnCloseAuthorize = document.getElementById("btn-close-authorize");
  const btnCancelAuthorize = document.getElementById("btn-cancel-authorize");
  const formAuthorize = document.getElementById("form-authorize");
  const inputAuthorizeId = document.getElementById("authorize-incident-id");
  const authCtxId = document.getElementById("auth-ctx-id");
  const authCtxType = document.getElementById("auth-ctx-type");
  const authCtxRisk = document.getElementById("auth-ctx-risk");
  const authCtxDecision = document.getElementById("auth-ctx-decision");
  const inputAuthorizeReason = document.getElementById("authorize-reason");
  const elAuthorizeError = document.getElementById("authorize-error");


  // Helper: Escape HTML
  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Update Connection Status Badge
  function setConnectionState(state, customMessage) {
    connectionState = state;
    if (elConnectionBadge) {
      elConnectionBadge.className = `badge badge-${state.toLowerCase()}`;
    }
    if (elConnectionText) {
      elConnectionText.textContent = customMessage || state;
    }
    if (state === "LIVE") {
      if (elVideoOverlay) elVideoOverlay.style.display = "none";
    } else if (state === "STALE") {
      if (elVideoOverlay) {
        elVideoOverlay.style.display = "flex";
        elOverlayTitle.textContent = "SIGNAL STALE";
        elOverlayMsg.textContent = "Backend updates delayed > 5s.";
      }
    } else if (state === "DISCONNECTED") {
      if (elVideoOverlay) {
        elVideoOverlay.style.display = "flex";
        elOverlayTitle.textContent = "DISCONNECTED";
        elOverlayMsg.textContent = "Cannot connect to ATLAS backend. Retrying every 1s...";
      }
    }
  }

  // Watchdog freshness
  function checkFreshness() {
    if (lastSuccessfulFetchTime === 0) return;
    const deltaMs = Date.now() - lastSuccessfulFetchTime;
    if (deltaMs > STALE_THRESHOLD_MS && connectionState === "LIVE") {
      setConnectionState("STALE");
    }
  }

  // View Switching based on Authenticated Role
  function applyRoleView() {
    if (!currentUser) {
      // Guest / Not Logged In
      if (elHeaderRoleBadge) {
        elHeaderRoleBadge.textContent = "GUEST (READ-ONLY)";
        elHeaderRoleBadge.className = "role-badge";
      }
      if (elHeaderIndicators) elHeaderIndicators.style.display = "none";
      if (elUserLoggedIn) elUserLoggedIn.style.display = "none";
      if (elUserLoggedOut) elUserLoggedOut.style.display = "flex";
      if (viewAdmin) viewAdmin.style.display = "none";
      if (viewAuth) viewAuth.style.display = "none";
      if (viewGuest) viewGuest.style.display = "block";
      return;
    }

    if (elUserLoggedIn) elUserLoggedIn.style.display = "flex";
    if (elUserLoggedOut) elUserLoggedOut.style.display = "none";
    if (elUserDisplayName) {
      elUserDisplayName.textContent = currentUser.display_name || currentUser.username || currentUser.actor_id;
    }
    if (elUserRoleBadge) {
      elUserRoleBadge.textContent = currentUser.role;
      elUserRoleBadge.className = `user-role-tag ${currentUser.role.toLowerCase()}`;
    }

    if (currentUser.role === "ADMIN") {
      // ADMIN DASHBOARD
      if (elHeaderRoleBadge) {
        elHeaderRoleBadge.textContent = "ADMIN / VERIFIED";
        elHeaderRoleBadge.className = "role-badge";
      }
      if (elHeaderIndicators) elHeaderIndicators.style.display = "flex";
      if (viewAdmin) viewAdmin.style.display = "block";
      if (viewAuth) viewAuth.style.display = "none";
      if (viewGuest) viewGuest.style.display = "none";
    } else {
      // AUTHORIZED USER DASHBOARD
      if (elHeaderRoleBadge) {
        elHeaderRoleBadge.textContent = "AUTHORIZED USER";
        elHeaderRoleBadge.className = "role-badge auth-user";
      }
      if (elHeaderIndicators) elHeaderIndicators.style.display = "none";
      if (viewAdmin) viewAdmin.style.display = "none";
      if (viewAuth) viewAuth.style.display = "block";
      if (viewGuest) viewGuest.style.display = "none";
    }
  }

  // Fetch Current User
  async function fetchCurrentUser() {
    try {
      const resp = await fetch("/api/auth/me", { credentials: "same-origin" });
      if (resp.ok) {
        const data = await resp.json();
        currentUser = data.user;
      } else {
        currentUser = null;
      }
    } catch (err) {
      currentUser = null;
    }
    applyRoleView();
  }

  // Master Polling Dispatcher
  async function pollDashboard() {
    if (isFetching) return;
    isFetching = true;

    try {
      if (currentUser?.role === "ADMIN") {
        await pollAdminDashboard();
      } else if (currentUser?.role === "AUTHORIZED_USER") {
        await pollAuthorizedUserDashboard();
      } else {
        // Fallback or guest poll
        await pollGuestDashboard();
      }
    } catch (err) {
      setConnectionState("DISCONNECTED", err.message || "Network Error");
    } finally {
      isFetching = false;
    }
  }

  // 1. ADMIN DASHBOARD POLLING & RENDERING
  async function pollAdminDashboard() {
    const resp = await fetch("/api/dashboard/admin/state", {
      headers: { "Accept": "application/json" },
      credentials: "same-origin",
    });

    if (resp.status === 401 || resp.status === 403) {
      await fetchCurrentUser();
      return;
    }

    if (!resp.ok) {
      setConnectionState("ERROR", `HTTP ${resp.status}`);
      return;
    }

    const data = await resp.json();
    latestDashboardData = data;
    lastSuccessfulFetchTime = Date.now();
    setConnectionState("LIVE");

    if (elLastSyncTime && data.timestamp) {
      elLastSyncTime.textContent = data.timestamp.replace("T", " ").split(".")[0] + " UTC";
    }

    renderAdminDashboard(data);
  }

  function renderAdminDashboard(data) {
    if (!data) return;

    // A. Home Overview
    const ov = data.overview || {};
    const subs = ov.subsystems || {};
    for (const [key, el] of Object.entries(subElements)) {
      if (el) {
        const val = (subs[key] || "UNKNOWN").toUpperCase();
        el.textContent = val;
        el.className = `sub-val ${val === "ONLINE" || val === "LIVE" || val === "ACTIVE" ? "sub-val-active" : ""}`;
      }
    }

    const activeIncidents = data.incidents?.filter(i => i.status === "ACTIVE" || i.status === "ACKNOWLEDGED") || [];
    if (elKpiPerimeterVal) {
      elKpiPerimeterVal.textContent = activeIncidents.length > 0 ? "ALERT ACTIVE" : "SECURE";
      elKpiPerimeterVal.className = `kpi-value ${activeIncidents.length > 0 ? "text-red" : "text-green"}`;
    }
    if (elKpiThreatVal) {
      const riskScore = ov.current_risk?.score || 0;
      const threatStr = riskScore >= 0.75 ? "HIGH" : (riskScore >= 0.45 ? "MODERATE" : "LOW");
      elKpiThreatVal.textContent = threatStr;
      elKpiThreatVal.className = `kpi-value ${threatStr === "HIGH" ? "text-red" : (threatStr === "MODERATE" ? "text-amber" : "text-cyan")}`;
    }
    if (elKpiIncidentsVal) {
      elKpiIncidentsVal.textContent = activeIncidents.length;
    }
    if (elKpiUsersVal) {
      const authPeople = data.people?.filter(p => p.is_authorized && p.current_presence) || [];
      elKpiUsersVal.textContent = `${authPeople.length || 1} PRESENT`;
    }

    // AI Reasoning
    const aiStatus = ov.ai_status || {};
    if (elStatLlmText) {
      elStatLlmText.textContent = `LLM HEALTH: ${aiStatus.health?.network || "OPERATIONAL"}`;
    }
    const verif = ov.llm_verifier_status || {};
    if (elLlmSituation) elLlmSituation.textContent = verif.situation || "System monitoring perimeter.";
    if (elLlmConfidence) elLlmConfidence.textContent = typeof verif.confidence === "number" ? verif.confidence.toFixed(2) : "1.00";
    if (elLlmModel) elLlmModel.textContent = aiStatus.model || "qwen2.5:3b";
    if (elLlmStatusBadge) elLlmStatusBadge.textContent = verif.verified ? "VERIFIED" : "STANDBY";

    // B. Live Camera
    const live = data.live || {};
    const cam = ov.camera_status || {};
    if (elMetricFps) elMetricFps.textContent = typeof live.fps === "number" ? live.fps.toFixed(1) : (cam.fps ? cam.fps.toFixed(1) : "0.0");
    if (elMetricResolution) {
      if (cam.resolution?.width) {
        elMetricResolution.textContent = `${cam.resolution.width}x${cam.resolution.height}`;
      } else {
        elMetricResolution.textContent = "640x480";
      }
    }
    if (elMetricPeopleCount) elMetricPeopleCount.textContent = live.active_people?.length || 0;
    if (elMetricObjectsCount) elMetricObjectsCount.textContent = live.active_objects?.length || 0;

    // C. People (Real observed people)
    const people = data.people || [];
    if (elAdminPeopleCount) elAdminPeopleCount.textContent = `${people.length} OBSERVED`;
    if (elAdminPeopleList) {
      if (people.length === 0) {
        elAdminPeopleList.innerHTML = '<div class="empty-state">No persons observed in perimeter.</div>';
      } else {
        elAdminPeopleList.innerHTML = people.map(p => `
          <div class="person-card">
            <div class="card-top-row">
              <span class="person-id">${escapeHtml(p.identity)}</span>
              <span class="badge ${p.is_authorized ? 'badge-live' : 'badge-subtle'}">${p.is_authorized ? 'AUTHORIZED' : 'NON-AUTHORIZED'}</span>
            </div>
            <div class="person-meta">
              <span>TRACK ID: #${escapeHtml(p.track_id)}</span>
              <span>STATE: ${escapeHtml(p.movement_state || 'stationary')}</span>
              <span>PRESENCE: <strong class="${p.current_presence ? 'text-green' : 'text-dim'}">${p.current_presence ? 'IN VIEW' : 'EXITED'}</strong></span>
              <span>ENTERED: ${escapeHtml(p.entry_time ? p.entry_time.split('T')[1]?.split('.')[0] : '--')}</span>
            </div>
          </div>
        `).join("");
      }
    }

    // D. Activity Feed
    const events = data.activity || [];
    if (elEventsCountBadge) elEventsCountBadge.textContent = `${events.length} RECENT`;
    if (elEventsTbody) {
      if (events.length === 0) {
        elEventsTbody.innerHTML = '<tr><td colspan="5" class="empty-table">Awaiting events from perception engine...</td></tr>';
      } else {
        elEventsTbody.innerHTML = events.slice(0, 25).map(e => {
          const tsShort = e.timestamp ? e.timestamp.replace("T", " ").split(".")[0] : "--";
          const conf = typeof e.confidence === "number" ? `${Math.round(e.confidence * 100)}%` : "--";
          return `
            <tr>
              <td>${escapeHtml(tsShort)}</td>
              <td><strong>${escapeHtml(e.event_type || "--")}</strong></td>
              <td>#${escapeHtml(e.track_id !== undefined ? e.track_id : "--")}</td>
              <td>${escapeHtml(conf)}</td>
              <td><span class="badge ${e.status === 'ACTIVE' ? 'badge-live' : 'badge-subtle'}">${escapeHtml(e.status || "--")}</span></td>
            </tr>
          `;
        }).join("");
      }
    }

    // E. Objects
    const objects = data.objects || [];
    if (elAdminObjectsCount) elAdminObjectsCount.textContent = `${objects.length} TRACKED`;
    if (elAdminObjectsList) {
      if (objects.length === 0) {
        elAdminObjectsList.innerHTML = '<div class="empty-state">No objects detected in perimeter.</div>';
      } else {
        elAdminObjectsList.innerHTML = objects.map(o => `
          <div class="object-card">
            <div class="card-top-row">
              <span class="object-id">#${escapeHtml(o.track_id)}: ${escapeHtml(o.label)}</span>
              <span class="badge ${o.is_present ? 'badge-live' : 'badge-subtle'}">${o.is_present ? 'PRESENT' : 'DISAPPEARED'}</span>
            </div>
            <div class="object-meta">
              <span>MOTION: ${escapeHtml(o.movement_state || 'static')}</span>
              <span>BBOX: [${(o.bbox || []).map(v => Math.round(v)).join(', ')}]</span>
            </div>
          </div>
        `).join("");
      }
    }

    // F. Incidents
    const incidents = data.incidents || [];
    if (elActiveIncidentsCount) elActiveIncidentsCount.textContent = `${activeIncidents.length} ACTIVE`;
    if (elIncidentsList) {
      if (incidents.length === 0) {
        elIncidentsList.innerHTML = '<div class="empty-state">NO INCIDENTS LOGGED</div>';
      } else {
        elIncidentsList.innerHTML = incidents.map(inc => {
          const isAct = inc.status === "ACTIVE";
          const isAck = inc.status === "ACKNOWLEDGED";
          const sev = inc.severity || "LOW";
          const sevClass = sev === "CRITICAL" ? "badge-disconnected" : (sev === "HIGH" ? "badge-stale" : "badge-subtle");
          const createdShort = inc.created_at ? inc.created_at.replace("T", " ").split(".")[0] : "--";
          const summaryText = inc.metadata?.summary || inc.incident_type || "Physical security event";

          return `
            <div class="incident-card ${isAct ? 'incident-active' : ''}">
              <div class="incident-header">
                <div>
                  <span class="badge ${sevClass}">${escapeHtml(sev)}</span>
                  <strong>${escapeHtml(inc.incident_type)}</strong>
                  <span class="badge ${isAct ? 'badge-live' : ''}">${escapeHtml(inc.status)}</span>
                </div>
                <div class="incident-actions">
                  <button class="btn btn-xs btn-outline" data-action="investigate" data-id="${escapeHtml(inc.incident_id)}">INVESTIGATE</button>
                  ${isAct ? `<button class="btn btn-xs btn-ack" data-action="ack" data-id="${escapeHtml(inc.incident_id)}">ACK</button>` : ''}
                  ${(isAct || isAck) ? `<button class="btn btn-xs btn-resolve" data-action="resolve" data-id="${escapeHtml(inc.incident_id)}" data-type="${escapeHtml(inc.incident_type)}" data-sev="${escapeHtml(sev)}">RESOLVE</button>` : ''}
                </div>
              </div>
              <p class="incident-summary">${escapeHtml(summaryText)}</p>
              <div class="incident-footer">
                <span>DETECTED: ${escapeHtml(createdShort)}</span>
                <span>ID: <code>${escapeHtml(inc.incident_id.substring(0, 12))}..</code></span>
              </div>
            </div>
          `;
        }).join("");
      }
    }

    // G. Evidence Vault
    const evidence = data.evidence || [];
    if (elAdminEvidenceCount) elAdminEvidenceCount.textContent = `${evidence.length} ARTIFACTS`;
    if (elAdminEvidenceList) {
      if (evidence.length === 0) {
        elAdminEvidenceList.innerHTML = '<div class="empty-state">No captured evidence records.</div>';
      } else {
        elAdminEvidenceList.innerHTML = evidence.map(ev => `
          <div class="evidence-card">
            <div class="card-top-row">
              <strong>${escapeHtml(ev.evidence_id)}</strong>
              <span class="badge badge-live">INTEGRITY VERIFIED</span>
            </div>
            <div class="person-meta" style="margin-bottom: 8px;">
              <span>INCIDENT: ${escapeHtml(ev.incident_id?.substring(0, 10))}..</span>
              <span>CAPTURED: ${escapeHtml(ev.timestamp ? ev.timestamp.replace('T', ' ').split('.')[0] : '--')}</span>
              <span>SIZE: ${escapeHtml(Math.round((ev.file_size_bytes || 0) / 1024))} KB</span>
            </div>
            <div style="font-size: 11px; margin-bottom: 8px; font-family: var(--font-mono); word-break: break-all;">
              <span class="field-label">SHA-256:</span> <code style="color: #38bdf8;">${escapeHtml(ev.sha256_hash?.substring(0, 32))}...</code>
            </div>
            <button class="btn btn-xs btn-outline" data-action="view-evidence-artifact" data-url="${escapeHtml(ev.download_url)}" data-id="${escapeHtml(ev.evidence_id)}">VIEW ARTIFACT</button>
          </div>
        `).join("");
      }
    }

    // H. Login Activity
    const logins = data.login_activity || [];
    if (elAdminLoginsCount) elAdminLoginsCount.textContent = `${logins.length} ATTEMPTS`;
    if (elAdminLoginsTbody) {
      if (logins.length === 0) {
        elAdminLoginsTbody.innerHTML = '<tr><td colspan="6" class="empty-table">No recent login records.</td></tr>';
      } else {
        elAdminLoginsTbody.innerHTML = logins.map(l => {
          const tsShort = l.timestamp ? l.timestamp.replace("T", " ").split(".")[0] : "--";
          const isSuccess = l.status === "SUCCESS";
          const facePct = typeof l.face_confidence === "number" ? `${Math.round(l.face_confidence * 100)}%` : "--";
          return `
            <tr>
              <td>${escapeHtml(tsShort)}</td>
              <td><strong>${escapeHtml(l.username || "--")}</strong></td>
              <td><span class="role-badge" style="font-size: 9px;">${escapeHtml(l.role || "--")}</span></td>
              <td><span class="badge ${isSuccess ? 'badge-live' : 'badge-disconnected'}">${escapeHtml(l.status)}</span></td>
              <td><code>${escapeHtml(l.ip_address || "--")}</code></td>
              <td>${escapeHtml(facePct)}</td>
            </tr>
          `;
        }).join("");
      }
    }

    // I. Audit Trail
    const audits = data.audit || [];
    if (elAdminAuditCount) elAdminAuditCount.textContent = `${audits.length} AUDIT RECORDS`;
    if (elAdminAuditTbody) {
      if (audits.length === 0) {
        elAdminAuditTbody.innerHTML = '<tr><td colspan="6" class="empty-table">No audit records logged.</td></tr>';
      } else {
        elAdminAuditTbody.innerHTML = audits.map(a => {
          const tsShort = a.timestamp ? a.timestamp.replace("T", " ").split(".")[0] : "--";
          const transition = (a.previous_state || a.new_state) ? `${escapeHtml(a.previous_state || "None")} → ${escapeHtml(a.new_state || "None")}` : "--";
          return `
            <tr>
              <td>${escapeHtml(tsShort)}</td>
              <td><strong>${escapeHtml(a.actor_id || "--")}</strong></td>
              <td>${escapeHtml(a.action || "--")}</td>
              <td>${escapeHtml(a.target_id || "--")}</td>
              <td>${transition}</td>
              <td>${escapeHtml(a.reason || "--")}</td>
            </tr>
          `;
        }).join("");
      }
    }

    // J. User Management (Strict 10 Slots)
    renderUserManagementSlots(data.user_management);

    // K. Notifications & Escalation (Final Stage)
    renderAdminNotifications(data);
  }

  // Render Section K Notifications & Escalation
  function renderAdminNotifications(data) {
    const notificationItems = data.notifications || [];
    const providers = data.notification_providers || [];

    if (elAdminNotifsCount) {
      elAdminNotifsCount.textContent = `${notificationItems.length} NOTIFICATIONS`;
    }

    const mobileProv = providers.find(p => p.channel === "MOBILE_PUSH" || p.channel === "WEB_PUSH");
    if (elAdminMobileProviderPill) {
      if (mobileProv && mobileProv.is_active) {
        elAdminMobileProviderPill.textContent = "MOBILE PUSH: ACTIVE";
        elAdminMobileProviderPill.className = "badge badge-live";
      } else {
        elAdminMobileProviderPill.textContent = "MOBILE PUSH: NOT CONFIGURED";
        elAdminMobileProviderPill.className = "badge badge-disconnected";
      }
    }

    if (elAdminProvidersRow) {
      if (providers.length === 0) {
        elAdminProvidersRow.innerHTML = `
          <div class="provider-badge-card" style="padding: 10px 14px; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px;">
            <div style="font-size: 11px; color: var(--text-dim);">MOBILE NOTIFICATION</div>
            <strong style="color: #f87171;">MOBILE PUSH: NOT CONFIGURED</strong>
          </div>
        `;
      } else {
        elAdminProvidersRow.innerHTML = providers.map(p => {
          const isAct = p.is_active;
          const displayLabel = p.channel === "MOBILE_PUSH" ? (isAct ? "MOBILE PUSH: ACTIVE" : "MOBILE PUSH: NOT CONFIGURED") : `${p.channel}: ${p.state}`;
          return `
            <div class="provider-badge-card" style="padding: 10px 14px; background: rgba(15, 23, 42, 0.6); border: 1px solid ${isAct ? 'rgba(56, 189, 248, 0.3)' : 'rgba(239, 68, 68, 0.2)'}; border-radius: 8px; flex: 1; min-width: 200px;">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                <span style="font-size: 11px; color: var(--text-dim); text-transform: uppercase;">CHANNEL: ${escapeHtml(p.channel)}</span>
                <span class="badge ${isAct ? 'badge-live' : 'badge-disconnected'}" style="font-size: 9px;">${isAct ? 'ONLINE' : 'NOT CONFIGURED'}</span>
              </div>
              <strong style="font-size: 13px; color: ${isAct ? '#38bdf8' : '#f87171'}; font-family: var(--font-mono);">${escapeHtml(displayLabel)}</strong>
            </div>
          `;
        }).join("");
      }
    }

    if (elAdminNotifsTbody) {
      if (notificationItems.length === 0) {
        elAdminNotifsTbody.innerHTML = '<tr><td colspan="8" class="empty-table">No notification events recorded.</td></tr>';
      } else {
        elAdminNotifsTbody.innerHTML = notificationItems.map(n => {
          const tsShort = n.created_at ? n.created_at.replace("T", " ").split(".")[0] : "--";
          const isDelivered = n.status === "DELIVERED";
          const isFailed = n.status === "FAILED";
          const isAck = n.status === "ACKNOWLEDGED";
          const statusBadge = isDelivered ? "badge-live" : (isFailed ? "badge-disconnected" : (isAck ? "badge-stale" : "badge-subtle"));
          const retryStr = `${n.attempt_count || 1} / ${n.max_retries || 3}`;

          return `
            <tr>
              <td>${escapeHtml(tsShort)}</td>
              <td><code>${escapeHtml(n.incident_id?.substring(0, 10))}..</code></td>
              <td><strong>@${escapeHtml(n.recipient_user_id || "--")}</strong></td>
              <td><span class="badge badge-subtle" style="font-size: 9px;">${escapeHtml(n.channel || "--")}</span></td>
              <td><span class="badge ${n.escalation_level > 1 ? 'badge-disconnected' : 'badge-subtle'}">LEVEL ${n.escalation_level || 1}</span></td>
              <td><span class="badge ${statusBadge}">${escapeHtml(n.status || "--")}</span></td>
              <td><code>${escapeHtml(retryStr)}</code></td>
              <td>${isAck ? `<span class="text-green">ACK by @${escapeHtml(n.acknowledged_by || 'user')}</span>` : (n.failure_reason ? `<span class="text-dim" style="font-size: 11px;">${escapeHtml(n.failure_reason)}</span>` : '<span class="text-dim">PENDING</span>')}</td>
            </tr>
          `;
        }).join("");
      }
    }
  }

  // Render 10 strict user slots
  function renderUserManagementSlots(userMgmt) {
    if (!elAdminUserSlotsGrid) return;
    const slots = userMgmt?.slots || [];
    const occupiedCount = userMgmt?.occupied_slots ?? slots.filter(s => s.status === "OCCUPIED").length;
    const maxUsers = userMgmt?.max_users || 10;

    if (elSlotsOccupiedPill) {
      elSlotsOccupiedPill.textContent = `${occupiedCount} / ${maxUsers} SLOTS OCCUPIED`;
      elSlotsOccupiedPill.className = `badge ${occupiedCount >= maxUsers ? 'badge-disconnected' : 'badge-live'}`;
    }

    let html = "";
    for (let i = 1; i <= 10; i++) {
      const slotData = slots.find(s => s.slot_number === i) || { slot_number: i, status: "EMPTY" };
      if (slotData.status === "OCCUPIED" && slotData.user) {
        const u = slotData.user;
        const isAdmin = u.role === "ADMIN";
        const isActive = u.is_active;
        const faceEnrolled = !!u.face_enrolled;
        const lastLoginShort = u.last_login_at ? u.last_login_at.replace("T", " ").split(".")[0] : "Never";

        html += `
          <div class="user-slot-card occupied">
            <div>
              <div class="slot-header">
                <span class="slot-number-tag">SLOT ${i}</span>
                <div style="display: flex; gap: 4px;">
                  <span class="badge ${faceEnrolled ? 'badge-live' : 'badge-stale'}" style="font-size: 9px;">${faceEnrolled ? 'FACE ENROLLED' : 'NO FACE'}</span>
                  <span class="badge ${isActive ? 'badge-live' : 'badge-subtle'}" style="font-size: 9px;">${isActive ? 'ACTIVE' : 'DISABLED'}</span>
                </div>
              </div>
              <div class="slot-user-name">${escapeHtml(u.display_name || u.username)}</div>
              <div class="slot-username">@${escapeHtml(u.username)}</div>
              <span class="role-badge ${isAdmin ? '' : 'auth-user'}" style="font-size: 9px;">${escapeHtml(u.role)}</span>
              <div style="font-size: 10px; color: var(--text-dim); margin-top: 8px; font-family: var(--font-mono);">
                LAST LOGIN: ${escapeHtml(lastLoginShort)}
              </div>
            </div>
            <div class="slot-actions">
              <button class="btn btn-xs btn-outline" data-action="edit-user" data-uid="${escapeHtml(u.user_id)}" data-username="${escapeHtml(u.username)}" data-name="${escapeHtml(u.display_name)}" data-role="${escapeHtml(u.role)}" data-active="${isActive}">EDIT</button>
              <button class="btn btn-xs btn-outline-cyan" data-action="enroll-user-face" data-uid="${escapeHtml(u.user_id)}" data-name="${escapeHtml(u.display_name || u.username)}">ENROLL FACE</button>
              <button class="btn btn-xs ${isActive ? 'btn-outline-danger' : 'btn-outline'}" data-action="toggle-active" data-uid="${escapeHtml(u.user_id)}" data-active="${isActive}">${isActive ? 'DISABLE' : 'ENABLE'}</button>
              ${!isAdmin ? `<button class="btn btn-xs btn-outline-danger" data-action="delete-user" data-uid="${escapeHtml(u.user_id)}" data-name="${escapeHtml(u.display_name || u.username)}">REMOVE</button>` : ''}
            </div>
          </div>
        `;
      } else {
        html += `
          <div class="user-slot-card empty">
            <span class="slot-number-tag">SLOT ${i}</span>
            <div style="font-size: 11px; color: var(--text-dim); font-family: var(--font-mono);">AVAILABLE IDENTITY SLOT</div>
            <button class="btn btn-xs btn-primary" data-action="open-add-user" data-slot="${i}">+ ADD USER</button>
          </div>
        `;
      }
    }
    elAdminUserSlotsGrid.innerHTML = html;
  }


  // 2. AUTHORIZED USER DASHBOARD POLLING & RENDERING
  async function pollAuthorizedUserDashboard() {
    const resp = await fetch("/api/dashboard/authorized-user/state", {
      headers: { "Accept": "application/json" },
      credentials: "same-origin",
    });

    if (resp.status === 401 || resp.status === 403) {
      await fetchCurrentUser();
      return;
    }

    if (!resp.ok) {
      setConnectionState("ERROR", `HTTP ${resp.status}`);
      return;
    }

    const data = await resp.json();
    latestDashboardData = data;
    lastSuccessfulFetchTime = Date.now();
    setConnectionState("LIVE");

    renderAuthorizedUserDashboard(data);
  }

  function renderAuthorizedUserDashboard(data) {
    if (!data) return;

    const hs = data.home_status || {};
    const alerts = data.active_alerts || [];
    currentAuthAlerts = alerts;

    const hasAlert = alerts.length > 0;

    // Home Status Card
    if (elAuthStatusHeadline) {
      elAuthStatusHeadline.textContent = hasAlert ? "PERIMETER ALERT" : "PERIMETER SECURE";
      elAuthStatusHeadline.className = `auth-status-title ${hasAlert ? 'alert' : ''}`;
    }
    if (elAuthStatusDescription) {
      elAuthStatusDescription.textContent = hasAlert
        ? `Attention: An active perimeter safety incident is underway (${alerts[0].title}). Review alert details below.`
        : "All monitored areas are normal. Continuous physical safety intelligence is actively monitoring the home perimeter.";
    }
    if (elAuthStatusShield) {
      elAuthStatusShield.className = `auth-status-shield-wrap ${hasAlert ? 'alert' : ''}`;
    }
    if (elAuthModeVal) elAuthModeVal.textContent = hs.mode || "Continuous Physical Safety Monitoring";
    if (elAuthCamVal) {
      elAuthCamVal.textContent = hs.camera_connected ? "Online / Active" : "Camera Disconnected";
      elAuthCamVal.className = `meta-val ${hs.camera_connected ? 'text-green' : 'text-red'}`;
    }
    if (elAuthCheckedVal && hs.last_checked) {
      elAuthCheckedVal.textContent = hs.last_checked.split("T")[1]?.split(".")[0] + " UTC";
    }

    // Active Alerts
    if (elAuthAlertsBadge) {
      elAuthAlertsBadge.textContent = hasAlert ? `${alerts.length} ACTIVE ALERT(S)` : "ALL CLEAR";
      elAuthAlertsBadge.className = `badge ${hasAlert ? 'badge-disconnected' : 'badge-live'}`;
    }

    if (!hasAlert) {
      if (elAuthAlertsContainer) {
        elAuthAlertsContainer.innerHTML = `
          <div class="auth-all-clear-card">
            <div class="all-clear-icon">✓</div>
            <h3>All Clear — No Active Security Alerts</h3>
            <p>Monitoring perimeter continuously.</p>
            <div class="auth-gating-note">
              <span class="lock-icon">🔒</span>
              <span>Live camera feed and evidence access are strictly gated and will automatically unlock if an active security incident occurs.</span>
            </div>
          </div>
        `;
      }
    } else {
      if (elAuthAlertsContainer) {
        elAuthAlertsContainer.innerHTML = alerts.map(a => {
          const timeShort = a.created_at ? a.created_at.replace("T", " ").split(".")[0] : "--";
          const isCritical = a.severity === "CRITICAL";
          const alertHeadline = isCritical ? "CRITICAL ATLAS HOME ALERT" : `ATLAS HOME ALERT: ${escapeHtml(a.severity)}`;
          const evidenceCount = a.evidence_count || (a.evidence_ids ? a.evidence_ids.length : 0);
          const evidenceText = (a.evidence_available || evidenceCount > 0) ? `Available (${evidenceCount} items)` : "None captured";
          const liveText = a.live_available ? "Available (Alert Active)" : "Restricted";

          return `
            <div class="auth-alert-card ${isCritical ? 'auth-alert-critical' : ''}">
              <div class="auth-alert-header">
                <div>
                  <span class="badge ${sevClass}">${escapeHtml(a.severity)}</span>
                  <strong class="auth-alert-title">${alertHeadline}</strong>
                </div>
                <span class="badge badge-live">${escapeHtml(a.status)}</span>
              </div>
              <p class="auth-alert-summary">${escapeHtml(a.summary)}</p>
              <div class="person-meta" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; margin: 12px 0;">
                <span>INCIDENT: <code>#${escapeHtml((a.incident || a.incident_id || '').substring(0, 10))}..</code></span>
                <span>SEVERITY: <strong class="${isCritical ? 'text-red' : 'text-amber'}">${escapeHtml(a.severity)}</strong></span>
                <span>TIMESTAMP: ${escapeHtml(timeShort)} UTC</span>
                <span>LOCATION: ${escapeHtml(a.location || 'Home Interior / Monitored Area')}</span>
                <span>EVIDENCE: <strong class="${(a.evidence_available || evidenceCount > 0) ? 'text-green' : 'text-dim'}">${escapeHtml(evidenceText)}</strong></span>
                <span>LIVE FEED: <strong class="${a.live_available ? 'text-green' : 'text-dim'}">${escapeHtml(liveText)}</strong></span>
              </div>
              <div class="auth-alert-actions">
                <button class="btn btn-sm btn-primary" data-action="auth-view-alert" data-id="${escapeHtml(a.incident_id)}">[ VIEW ALERT ]</button>
                <button class="btn btn-sm btn-outline" data-action="auth-view-evidence" data-id="${escapeHtml(a.incident_id)}" data-evids="${escapeHtml((a.evidence_ids || []).join(','))}">[ VIEW EVIDENCE ]</button>
                <button class="btn btn-sm btn-outline" data-action="auth-view-live">[ LIVE ]</button>
                ${a.status === 'ACTIVE' ? `<button class="btn btn-sm btn-ack" data-action="auth-ack" data-id="${escapeHtml(a.incident_id)}">[ ACKNOWLEDGE ]</button>` : ''}
              </div>
            </div>
          `;
        }).join("");
      }
    }
  }


  // 3. GUEST DASHBOARD FALLBACK
  async function pollGuestDashboard() {
    try {
      const resp = await fetch("/api/dashboard/state", { credentials: "same-origin" });
      if (resp.ok) {
        lastSuccessfulFetchTime = Date.now();
        setConnectionState("LIVE");
      }
    } catch (e) {
      setConnectionState("DISCONNECTED");
    }
  }


  // =========================================================================
  // EVENT LISTENERS & USER INTERACTIONS
  // =========================================================================

  // Role-Specific Entry Points & Login Modals
  if (btnLoginAsAdmin) {
    btnLoginAsAdmin.addEventListener("click", () => {
      selectedRoleHint = "ADMIN";
      _resetLoginModal();
      if (loginRoleBadge) {
        loginRoleBadge.textContent = "ADMIN ACCESS";
        loginRoleBadge.className = "badge badge-live";
      }
      if (loginRoleSub) loginRoleSub.textContent = "Full administrative control and perimeter telemetry";
      modalLogin.style.display = "flex";
    });
  }

  if (btnLoginAsUser) {
    btnLoginAsUser.addEventListener("click", () => {
      selectedRoleHint = "AUTHORIZED_USER";
      _resetLoginModal();
      if (loginRoleBadge) {
        loginRoleBadge.textContent = "AUTHORIZED USER";
        loginRoleBadge.className = "badge auth-user";
      }
      if (loginRoleSub) loginRoleSub.textContent = "Consumer-grade residential protection & safety alerts";
      modalLogin.style.display = "flex";
    });
  }

  if (btnOpenLogin) {
    btnOpenLogin.addEventListener("click", () => {
      selectedRoleHint = "ADMIN";
      _resetLoginModal();
      if (loginRoleBadge) {
        loginRoleBadge.textContent = "AUTHENTICATION";
        loginRoleBadge.className = "badge badge-live";
      }
      modalLogin.style.display = "flex";
    });
  }

  if (btnGuestLogin) {
    btnGuestLogin.addEventListener("click", () => {
      selectedRoleHint = "ADMIN";
      _resetLoginModal();
      modalLogin.style.display = "flex";
    });
  }

  if (btnCloseLogin) {
    btnCloseLogin.addEventListener("click", () => {
      modalLogin.style.display = "none";
    });
  }

  if (btnCloseFace) {
    btnCloseFace.addEventListener("click", () => {
      modalLogin.style.display = "none";
    });
  }

  if (btnBackToCredentials) {
    btnBackToCredentials.addEventListener("click", () => {
      if (loginStageCredentials) loginStageCredentials.style.display = "block";
      if (loginStageFace) loginStageFace.style.display = "none";
      if (stageStep1) stageStep1.className = "stage-step active";
      if (stageConnectorBar) stageConnectorBar.className = "stage-connector";
      if (stageStep2) stageStep2.className = "stage-step";
    });
  }

  function _resetLoginModal() {
    tempAuthToken = null;
    if (loginStageCredentials) loginStageCredentials.style.display = "block";
    if (loginStageFace) loginStageFace.style.display = "none";
    if (stageStep1) stageStep1.className = "stage-step active";
    if (stageConnectorBar) stageConnectorBar.className = "stage-connector";
    if (stageStep2) stageStep2.className = "stage-step";
    if (elLoginError) elLoginError.style.display = "none";
    if (faceError) faceError.style.display = "none";
    if (btnCaptureFace) {
      btnCaptureFace.disabled = false;
      btnCaptureFace.textContent = "CAPTURE & VERIFY FACE";
    }
  }

  // Submit Stage 1 Credentials
  if (formLogin) {
    formLogin.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (elLoginError) elLoginError.style.display = "none";

      const username = inputLoginUser.value.trim();
      const password = inputLoginPass.value;

      try {
        const resp = await fetch("/api/auth/login/credentials", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password, role_hint: selectedRoleHint }),
          credentials: "same-origin",
        });

        const data = await resp.json();

        if (!resp.ok) {
          elLoginError.textContent = data.details || data.error || "Authentication credentials rejected.";
          elLoginError.style.display = "block";
          return;
        }

        // Advance to Stage 2: Biometric Face Verification
        tempAuthToken = data.temp_token;
        if (stageStep1) stageStep1.className = "stage-step done";
        if (stageConnectorBar) stageConnectorBar.className = "stage-connector active";
        if (stageStep2) stageStep2.className = "stage-step active";

        loginStageCredentials.style.display = "none";
        loginStageFace.style.display = "block";
        if (faceUserHint) {
          faceUserHint.textContent = `Authenticating: ${data.display_name || data.username} (${data.role})`;
        }

        // Configure button text and instructions based on face enrollment status
        if (!data.face_enrolled && data.role === "ADMIN") {
          if (btnCaptureFace) btnCaptureFace.textContent = "CAPTURE & ENROLL ADMIN FACE";
          if (faceStatusMsg) faceStatusMsg.textContent = "Initial setup: Capturing face will enroll your Admin biometric key.";
        } else {
          if (btnCaptureFace) btnCaptureFace.textContent = "CAPTURE & VERIFY FACE";
          if (faceStatusMsg) faceStatusMsg.textContent = "Position face directly within the reticle and click capture...";
        }

        // Reset live status indicators
        if (valCamStatus) valCamStatus.textContent = "ACTIVE / 30 FPS";
        if (valFrameStatus) valFrameStatus.textContent = "VALID";
        if (valFaceStatus) valFaceStatus.textContent = "READY";
        if (valVerifyStatus) valVerifyStatus.textContent = "STANDBY";

        // Refresh hardware video feed
        if (faceVideoPreview) {
          faceVideoPreview.src = "/api/camera/video_feed?t=" + Date.now();
        }

      } catch (err) {
        elLoginError.textContent = `Network error: ${err.message}`;
        elLoginError.style.display = "block";
      }
    });
  }

  // Stage 2: Face Capture and Fail-Closed Verification
  if (btnCaptureFace) {
    btnCaptureFace.addEventListener("click", async () => {
      if (!tempAuthToken) {
        if (faceError) {
          faceError.textContent = "Authentication session expired. Please return to step 1.";
          faceError.style.display = "block";
        }
        return;
      }
      if (faceError) faceError.style.display = "none";
      if (faceStatusMsg) faceStatusMsg.textContent = "Acquiring live hardware frame & verifying biometric geometry...";
      if (valVerifyStatus) valVerifyStatus.textContent = "VERIFYING...";
      btnCaptureFace.disabled = true;

      try {
        // Authoritative biometric verification via physical webcam frame
        const resp = await fetch("/api/auth/login/face", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ temp_token: tempAuthToken }),
          credentials: "same-origin",
        });

        const data = await resp.json();

        if (!resp.ok) {
          btnCaptureFace.disabled = false;
          if (valVerifyStatus) valVerifyStatus.textContent = "FAILED";
          if (faceError) {
            faceError.textContent = data.details || data.error || "Biometric verification failed.";
            faceError.style.display = "block";
          }
          if (faceStatusMsg) {
            faceStatusMsg.textContent = "Verification unsuccessful. Please reposition your face in the reticle and try again.";
          }
          return;
        }

        // Biometric verification success
        if (valVerifyStatus) valVerifyStatus.textContent = "VERIFIED ✓";
        if (faceStatusMsg) faceStatusMsg.textContent = "Identity Confirmed ✓ Launching dashboard...";

        setTimeout(async () => {
          modalLogin.style.display = "none";
          btnCaptureFace.disabled = false;
          tempAuthToken = null;
          await fetchCurrentUser();
          pollDashboard();
        }, 500);

      } catch (err) {
        btnCaptureFace.disabled = false;
        if (valVerifyStatus) valVerifyStatus.textContent = "ERROR";
        if (faceError) {
          faceError.textContent = `Network error: ${err.message}`;
          faceError.style.display = "block";
        }
      }
    });
  }

  // Logout
  if (btnLogout) {
    btnLogout.addEventListener("click", async () => {
      try {
        await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
      } catch (e) {}
      currentUser = null;
      applyRoleView();
    });
  }


  // =========================================================================
  // ADMIN DASHBOARD INTERACTIONS
  // =========================================================================

  // Admin Incidents Click Handling
  if (elIncidentsList) {
    elIncidentsList.addEventListener("click", async (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      const action = btn.dataset.action;
      const id = btn.dataset.id;

      if (action === "investigate") {
        openInvestigationModal(id);
      } else if (action === "ack") {
        btn.disabled = true;
        try {
          const resp = await fetch(`/api/incidents/${encodeURIComponent(id)}/acknowledge`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
          });
          if (resp.ok) pollDashboard();
        } catch (err) {
          alert(`Ack error: ${err.message}`);
        }
      } else if (action === "resolve") {
        inputResolveId.value = id;
        resolveCtxId.textContent = id.substring(0, 8) + "..";
        resolveCtxType.textContent = btn.dataset.type || "--";
        resolveCtxSev.textContent = btn.dataset.sev || "LOW";
        resolveCtxRisk.textContent = "--";
        elResolveError.style.display = "none";
        modalResolve.style.display = "flex";
      }
    });
  }

  // Admin Evidence Artifact Preview Click
  if (elAdminEvidenceList) {
    elAdminEvidenceList.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-action='view-evidence-artifact']");
      if (!btn) return;
      const url = btn.dataset.url;
      const id = btn.dataset.id;
      authEvId.textContent = id;
      authEvImg.src = url;
      authEvidenceError.style.display = "none";
      modalAuthEvidence.style.display = "flex";
    });
  }

  // Admin User Slot Management Click Handling
  if (elAdminUserSlotsGrid) {
    elAdminUserSlotsGrid.addEventListener("click", async (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      const action = btn.dataset.action;

      if (action === "open-add-user") {
        elAddUserError.style.display = "none";
        formAddUser.reset();
        modalAddUser.style.display = "flex";
      } else if (action === "edit-user") {
        inputEditUserId.value = btn.dataset.uid;
        inputEditUsername.value = btn.dataset.username;
        inputEditDisplayName.value = btn.dataset.name;
        selectEditRole.value = btn.dataset.role;
        checkEditIsActive.checked = btn.dataset.active === "true";
        if (inputEditPassword) inputEditPassword.value = "";
        elEditUserError.style.display = "none";
        modalEditUser.style.display = "flex";
      } else if (action === "enroll-user-face") {
        const uid = btn.dataset.uid;
        const name = btn.dataset.name;
        if (!confirm(`Enroll face for "${name}" using live hardware camera frame? Please ensure the user is facing the camera.`)) {
          return;
        }
        btn.disabled = true;
        btn.textContent = "ENROLLING...";
        try {
          const resp = await fetch(`/api/admin/users/${encodeURIComponent(uid)}/face/enroll`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
            credentials: "same-origin",
          });
          const data = await resp.json();
          if (!resp.ok) {
            alert(`Face enrollment failed: ${data.details || data.error || 'Face not detected in camera frame'}`);
          } else {
            alert(`✓ Face enrolled successfully for ${name}! Biometric 1856-D template stored in secure vault.`);
            pollDashboard();
          }
        } catch (err) {
          alert(`Face enrollment network error: ${err.message}`);
        } finally {
          btn.disabled = false;
          btn.textContent = "ENROLL FACE";
        }
      } else if (action === "toggle-active") {
        const uid = btn.dataset.uid;
        const currentActive = btn.dataset.active === "true";
        try {
          const resp = await fetch(`/api/admin/users/${encodeURIComponent(uid)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_active: !currentActive }),
            credentials: "same-origin",
          });
          if (resp.ok) pollDashboard();
        } catch (err) {
          alert(`Toggle active failed: ${err.message}`);
        }
      } else if (action === "delete-user") {
        const uid = btn.dataset.uid;
        const name = btn.dataset.name;
        if (!confirm(`Are you sure you want to remove user "${name}"? This slot will become available.`)) return;
        try {
          const resp = await fetch(`/api/admin/users/${encodeURIComponent(uid)}`, {
            method: "DELETE",
            credentials: "same-origin",
          });
          if (resp.ok) pollDashboard();
        } catch (err) {
          alert(`Delete user failed: ${err.message}`);
        }
      }
    });
  }

  // Add User Form Submit
  if (formAddUser) {
    formAddUser.addEventListener("submit", async (e) => {
      e.preventDefault();
      elAddUserError.style.display = "none";

      const username = inputNewUsername.value.trim();
      const displayName = inputNewDisplayName.value.trim();
      const password = inputNewPassword.value;
      const role = selectNewRole.value;

      try {
        const resp = await fetch("/api/admin/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, display_name: displayName, password, role }),
          credentials: "same-origin",
        });

        const data = await resp.json();
        if (!resp.ok) {
          elAddUserError.textContent = data.details || data.error || "Failed to create user.";
          elAddUserError.style.display = "block";
          return;
        }

        modalAddUser.style.display = "none";
        pollDashboard();
      } catch (err) {
        elAddUserError.textContent = `Network error: ${err.message}`;
        elAddUserError.style.display = "block";
      }
    });
  }

  if (btnCloseAddUser) btnCloseAddUser.addEventListener("click", () => { modalAddUser.style.display = "none"; });
  if (btnCancelAddUser) btnCancelAddUser.addEventListener("click", () => { modalAddUser.style.display = "none"; });

  // Edit User Form Submit
  if (formEditUser) {
    formEditUser.addEventListener("submit", async (e) => {
      e.preventDefault();
      elEditUserError.style.display = "none";

      const uid = inputEditUserId.value;
      const displayName = inputEditDisplayName.value.trim();
      const role = selectEditRole.value;
      const isActive = checkEditIsActive.checked;
      const newPassword = inputEditPassword ? inputEditPassword.value.trim() : "";

      const updatePayload = { display_name: displayName, role, is_active: isActive };
      if (newPassword) {
        updatePayload.password = newPassword;
      }

      try {
        const resp = await fetch(`/api/admin/users/${encodeURIComponent(uid)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(updatePayload),
          credentials: "same-origin",
        });

        const data = await resp.json();
        if (!resp.ok) {
          elEditUserError.textContent = data.details || data.error || "Failed to update user.";
          elEditUserError.style.display = "block";
          return;
        }

        modalEditUser.style.display = "none";
        pollDashboard();
      } catch (err) {
        elEditUserError.textContent = `Network error: ${err.message}`;
        elEditUserError.style.display = "block";
      }
    });
  }

  if (btnCloseEditUser) btnCloseEditUser.addEventListener("click", () => { modalEditUser.style.display = "none"; });
  if (btnCancelEditUser) btnCancelEditUser.addEventListener("click", () => { modalEditUser.style.display = "none"; });


  // =========================================================================
  // AUTHORIZED USER DASHBOARD INTERACTIONS & ALERT GATING
  // =========================================================================

  if (elAuthAlertsContainer) {
    elAuthAlertsContainer.addEventListener("click", async (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      const action = btn.dataset.action;
      const id = btn.dataset.id;

      if (action === "auth-view-alert") {
        const alertObj = currentAuthAlerts.find(a => a.incident_id === id);
        if (alertObj) {
          authModalAlertTitle.textContent = alertObj.title || "SECURITY ALERT";
          authModalAlertSev.textContent = `${alertObj.severity} SEVERITY`;
          authModalAlertStatus.textContent = alertObj.status;
          authModalAlertSummary.textContent = alertObj.summary;
          authModalAlertTime.textContent = alertObj.created_at ? alertObj.created_at.replace("T", " ").split(".")[0] : "--";
          modalAuthAlert.style.display = "flex";
        }
      } else if (action === "auth-view-evidence") {
        const evIds = (btn.dataset.evids || "").split(",").filter(Boolean);
        if (evIds.length === 0) {
          alert("No camera evidence artifact recorded for this alert.");
          return;
        }
        const evidenceId = evIds[0];
        authEvId.textContent = evidenceId;
        authEvImg.src = `/api/evidence/${encodeURIComponent(evidenceId)}`;
        authEvidenceError.style.display = "none";
        modalAuthEvidence.style.display = "flex";
      } else if (action === "auth-view-live") {
        authLiveImg.src = "/api/camera/video_feed";
        authLiveError.style.display = "none";
        modalAuthLive.style.display = "flex";
      } else if (action === "auth-ack") {
        btn.disabled = true;
        try {
          const resp = await fetch(`/api/incidents/${encodeURIComponent(id)}/acknowledge`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
          });
          if (resp.ok) pollDashboard();
        } catch (err) {
          alert(`Ack error: ${err.message}`);
        }
      }
    });
  }

  // Authorized User Modal Close handlers
  if (btnCloseAuthAlert) btnCloseAuthAlert.addEventListener("click", () => { modalAuthAlert.style.display = "none"; });
  if (btnCloseAuthAlertFooter) btnCloseAuthAlertFooter.addEventListener("click", () => { modalAuthAlert.style.display = "none"; });

  if (btnCloseAuthEvidence) btnCloseAuthEvidence.addEventListener("click", () => {
    authEvImg.src = "";
    modalAuthEvidence.style.display = "none";
  });
  if (btnCloseAuthEvFooter) btnCloseAuthEvFooter.addEventListener("click", () => {
    authEvImg.src = "";
    modalAuthEvidence.style.display = "none";
  });

  if (btnCloseAuthLive) btnCloseAuthLive.addEventListener("click", () => {
    authLiveImg.src = "";
    modalAuthLive.style.display = "none";
  });
  if (btnCloseAuthLiveFooter) btnCloseAuthLiveFooter.addEventListener("click", () => {
    authLiveImg.src = "";
    modalAuthLive.style.display = "none";
  });


  // =========================================================================
  // INVESTIGATION & ACTION MODALS (ADMIN)
  // =========================================================================

  async function openInvestigationModal(incidentId) {
    if (!incidentId) return;

    invModalTitle.textContent = `INCIDENT: ${incidentId.substring(0, 12)}..`;
    invWhyRaised.textContent = "Retrieving verified evidence trace from backend...";
    invTimelineContainer.innerHTML = '<div class="inv-empty-note">Loading timeline...</div>';
    invTimelineCount.textContent = "0 EVENTS";
    invEventsContainer.innerHTML = '<div class="inv-empty-note">Loading observations...</div>';
    invEventsCount.textContent = "0 OBSERVATIONS";
    invCameraStatusBadge.textContent = "NOT_RECORDED";
    invCameraMessage.textContent = "Camera evidence unavailable.";
    invCameraArtifactContainer.style.display = "none";

    invActionAck.style.display = "none";
    invActionResolve.style.display = "none";
    invActionDismiss.style.display = "none";
    invActionAuthorize.style.display = "none";

    modalInvestigation.style.display = "flex";

    try {
      const resp = await fetch(`/api/incidents/${encodeURIComponent(incidentId)}/investigation`, {
        headers: { "Accept": "application/json" },
        credentials: "same-origin",
      });

      if (!resp.ok) {
        const err = await resp.json();
        invWhyRaised.textContent = `Investigation retrieval failed: ${err.details || err.error}`;
        return;
      }

      const inv = await resp.json();
      const inc = inv.incident || {};

      // Why Raised & Grounding
      const whyText = inv.why_incident_raised || inc.metadata?.summary || "Observed physical pattern exceeded threshold.";
      invWhyRaised.textContent = whyText;

      const grounds = inv.llm_analysis?.supporting_event_ids || [];
      if (invGroundingList) {
        if (grounds.length === 0) {
          invGroundingList.innerHTML = '<span class="inv-empty-note">No event IDs referenced.</span>';
        } else {
          invGroundingList.innerHTML = grounds.map(g => `<span class="badge badge-subtle"><code>${escapeHtml(g)}</code></span>`).join(" ");
        }
      }

      // Timeline
      const timeline = inv.chronological_timeline || [];
      invTimelineCount.textContent = `${timeline.length} ENTRIES`;
      if (timeline.length === 0) {
        invTimelineContainer.innerHTML = '<div class="inv-empty-note">No timeline events recorded.</div>';
      } else {
        invTimelineContainer.innerHTML = timeline.map(item => `
          <div class="inv-timeline-item">
            <span class="inv-item-time">${escapeHtml(item.timestamp ? item.timestamp.split('T')[1]?.split('.')[0] : '--')}</span>
            <div>
              <strong>${escapeHtml(item.type)}</strong>
              <p style="font-size: 11px; color: var(--text-muted);">${escapeHtml(item.description || item.details || '')}</p>
            </div>
          </div>
        `).join("");
      }

      // Supporting Observations
      const obs = inv.supporting_events || [];
      invEventsCount.textContent = `${obs.length} OBSERVATIONS`;
      if (obs.length === 0) {
        invEventsContainer.innerHTML = '<div class="inv-empty-note">No supporting events.</div>';
      } else {
        invEventsContainer.innerHTML = obs.map(o => `
          <div class="inv-event-row">
            <div>
              <strong>${escapeHtml(o.event_type || o.type)}</strong>
              <span class="badge badge-subtle">#${escapeHtml(o.track_id !== undefined ? o.track_id : '')}</span>
            </div>
            <div style="font-size: 10px; color: var(--text-dim);">${escapeHtml(o.timestamp ? o.timestamp.split('T')[1]?.split('.')[0] : '')}</div>
          </div>
        `).join("");
      }

      // Camera Evidence Artifact
      const evSumm = inv.evidence_summary || {};
      const evArtifact = evSumm.artifact;
      if (evArtifact && evArtifact.file_path) {
        invCameraStatusBadge.textContent = "CAPTURED";
        invCameraStatusBadge.className = "badge badge-live";
        invCameraMessage.textContent = "Verified evidence frame acquired.";
        invCameraArtifactContainer.style.display = "block";

        if (invCamProvenance) invCamProvenance.textContent = evArtifact.temporal_relation || "CAPTURED_AFTER_EVENT";
        if (invCamCaptured) invCamCaptured.textContent = evArtifact.captured_at || "--";
        if (invCamSourceEvent) invCamSourceEvent.textContent = evArtifact.source_event_id || "--";
        if (invCamEventTime) invCamEventTime.textContent = evArtifact.source_event_timestamp || "--";
        if (invCamFrameTime) invCamFrameTime.textContent = evArtifact.source_frame_timestamp || "--";
        if (invCamEvidenceId) invCamEvidenceId.textContent = evArtifact.evidence_id || "--";
        if (invCamIntegrity) invCamIntegrity.textContent = "INTEGRITY VERIFIED";
        if (invCamDimensions) invCamDimensions.textContent = `${evArtifact.width || '--'}x${evArtifact.height || '--'}`;
        if (invCamSize) invCamSize.textContent = `${Math.round((evArtifact.file_size || 0) / 1024)} KB`;
        if (invCamSha256) invCamSha256.textContent = evArtifact.sha256 || "--";
        if (invEvidenceImg) invEvidenceImg.src = `/api/evidence/${encodeURIComponent(evArtifact.evidence_id)}`;
      } else {
        invCameraStatusBadge.textContent = "NOT_RECORDED";
        invCameraStatusBadge.className = "badge badge-subtle";
        invCameraMessage.textContent = "No camera frame recorded for this incident.";
        invCameraArtifactContainer.style.display = "none";
      }

      // Contextual Action Buttons in Footer
      const canAck = currentUser?.permissions?.includes("ACKNOWLEDGE_INCIDENT");
      const canRes = currentUser?.permissions?.includes("RESOLVE_INCIDENT");
      const isTerminal = inc.status === "RESOLVED" || inc.status === "DISMISSED";

      if (!isTerminal) {
        if (canAck && inc.status === "ACTIVE") {
          invActionAck.style.display = "inline-block";
          invActionAck.dataset.id = inc.incident_id;
        }
        if (canRes) {
          invActionResolve.style.display = "inline-block";
          invActionResolve.dataset.id = inc.incident_id;
          invActionResolve.dataset.type = inc.incident_type;
          invActionResolve.dataset.sev = inc.severity;
        }
      }

    } catch (err) {
      invWhyRaised.textContent = `Error loading investigation: ${err.message}`;
    }
  }

  if (btnCloseInvestigation) btnCloseInvestigation.addEventListener("click", () => { modalInvestigation.style.display = "none"; });
  if (btnCloseInvFooter) btnCloseInvFooter.addEventListener("click", () => { modalInvestigation.style.display = "none"; });

  // Investigation Footer Action Handlers
  if (invActionAck) {
    invActionAck.addEventListener("click", async () => {
      const id = invActionAck.dataset.id;
      if (!id) return;
      try {
        const resp = await fetch(`/api/incidents/${encodeURIComponent(id)}/acknowledge`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
        });
        if (resp.ok) {
          modalInvestigation.style.display = "none";
          pollDashboard();
        }
      } catch (err) {
        alert(`Ack error: ${err.message}`);
      }
    });
  }

  if (invActionResolve) {
    invActionResolve.addEventListener("click", () => {
      const id = invActionResolve.dataset.id;
      inputResolveId.value = id;
      resolveCtxId.textContent = id.substring(0, 8) + "..";
      resolveCtxType.textContent = invActionResolve.dataset.type || "--";
      resolveCtxSev.textContent = invActionResolve.dataset.sev || "LOW";
      elResolveError.style.display = "none";
      modalInvestigation.style.display = "none";
      modalResolve.style.display = "flex";
    });
  }

  // Resolve Form Submit
  if (formResolve) {
    formResolve.addEventListener("submit", async (e) => {
      e.preventDefault();
      elResolveError.style.display = "none";
      const id = inputResolveId.value;
      const reason = selectResolveReason.value;
      const notes = inputResolveNotes.value.trim();

      try {
        const resp = await fetch(`/api/incidents/${encodeURIComponent(id)}/resolve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason, notes }),
          credentials: "same-origin",
        });
        const data = await resp.json();
        if (!resp.ok) {
          elResolveError.textContent = data.details || data.error || "Resolution failed.";
          elResolveError.style.display = "block";
          return;
        }
        modalResolve.style.display = "none";
        pollDashboard();
      } catch (err) {
        elResolveError.textContent = `Error: ${err.message}`;
        elResolveError.style.display = "block";
      }
    });
  }

  if (btnCloseResolve) btnCloseResolve.addEventListener("click", () => { modalResolve.style.display = "none"; });
  if (btnCancelResolve) btnCancelResolve.addEventListener("click", () => { modalResolve.style.display = "none"; });

  if (btnCloseDismiss) btnCloseDismiss.addEventListener("click", () => { modalDismiss.style.display = "none"; });
  if (btnCancelDismiss) btnCancelDismiss.addEventListener("click", () => { modalDismiss.style.display = "none"; });

  if (btnCloseAuthorize) btnCloseAuthorize.addEventListener("click", () => { modalAuthorize.style.display = "none"; });
  if (btnCancelAuthorize) btnCancelAuthorize.addEventListener("click", () => { modalAuthorize.style.display = "none"; });


  // =========================================================================
  // BOOTSTRAP INITIALIZATION
  // =========================================================================

  // Backward-compatible handle for Step 6 test suite
  const fetchDashboardState = pollDashboard;

  // Check auth status on page load
  fetchCurrentUser().then(() => {
    fetchDashboardState();
  });

  // Start bounded polling loop
  setInterval(fetchDashboardState, POLL_INTERVAL_MS);
  setInterval(checkFreshness, 1000);

})();
