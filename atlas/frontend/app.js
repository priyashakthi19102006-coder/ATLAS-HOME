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
  const viewAdminCompanion = document.getElementById("view-admin-companion");
  const viewAuth = document.getElementById("view-auth-dashboard");
  const viewGuest = document.getElementById("view-guest-dashboard");
  const elAdminNavBar = document.getElementById("admin-nav-bar");
  const tabAdminDashboard = document.getElementById("tab-admin-dashboard");
  const tabAdminCompanion = document.getElementById("tab-admin-companion");
  const elNavCompanionBadge = document.getElementById("nav-companion-badge");
  let currentAdminTab = "dashboard"; // "dashboard" | "companion"

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

  // Admin Section B (Live Camera)
  const elCameraFeed = document.getElementById("camera-feed");
  const elCameraSourceLabel = document.getElementById("camera-source-label");
  const elLiveCamStatusPill = document.getElementById("live-cam-status-pill");
  const elLiveCamStatusText = document.getElementById("live-cam-status-text");
  const btnCameraToggle = document.getElementById("btn-camera-toggle");
  const elVideoOverlay = document.getElementById("video-overlay");
  const elOverlayTitle = document.getElementById("overlay-title");
  const elOverlayMsg = document.getElementById("overlay-msg");
  const elOverlayIcon = document.getElementById("overlay-icon");
  const btnRetryStream = document.getElementById("btn-retry-stream");
  const elMetricFps = document.getElementById("metric-fps");
  const elMetricResolution = document.getElementById("metric-resolution");
  const elMetricPeopleCount = document.getElementById("metric-people-count");
  const elMetricObjectsCount = document.getElementById("metric-objects-count");
  const elMetricLastFrame = document.getElementById("metric-last-frame");

  // Camera Confirmation Modal
  const modalCameraConfirm = document.getElementById("modal-camera-confirm");
  const btnCloseCameraConfirm = document.getElementById("btn-close-camera-confirm");
  const btnCancelCameraOff = document.getElementById("btn-cancel-camera-off");
  const btnConfirmCameraOff = document.getElementById("btn-confirm-camera-off");

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
  const selectEditRelationship = document.getElementById("edit-relationship");
  const inputEditPhone = document.getElementById("edit-phone");
  const selectEditRole = document.getElementById("edit-role");
  const checkEditIsActive = document.getElementById("edit-is-active");
  const inputEditPassword = document.getElementById("edit-password");
  const elEditUserError = document.getElementById("edit-user-error");

  const modalRemoveUser = document.getElementById("modal-remove-user");
  const btnCloseRemoveUser = document.getElementById("btn-close-remove-user");
  const btnCancelRemoveUser = document.getElementById("btn-cancel-remove-user");
  const btnConfirmRemoveUser = document.getElementById("btn-confirm-remove-user");
  const inputRemoveUserId = document.getElementById("remove-user-id");
  const elRemoveUserName = document.getElementById("remove-user-name");
  const elRemoveUserHandle = document.getElementById("remove-user-handle");
  const elRemoveUserError = document.getElementById("remove-user-error");

  const modalManageFace = document.getElementById("modal-manage-face");
  const btnCloseManageFace = document.getElementById("btn-close-manage-face");
  const inputFaceMgmtUserId = document.getElementById("face-mgmt-user-id");
  const elFaceMgmtUserName = document.getElementById("face-mgmt-user-name");
  const elFaceMgmtUserHandle = document.getElementById("face-mgmt-user-handle");
  const elFaceMgmtStatusBadge = document.getElementById("face-mgmt-status-badge");
  const elFaceMgmtError = document.getElementById("face-mgmt-error");
  const btnActionRemoveFace = document.getElementById("btn-action-remove-face");
  const btnActionReplaceFace = document.getElementById("btn-action-replace-face");

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

  // Helper: Format ISO string to browser's local timezone date & time
  function formatLocalDateTime(isoStr) {
    if (!isoStr) return "--";
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return String(isoStr).replace("T", " ").split(".")[0];
      return d.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      }) + " " + d.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
      });
    } catch (e) {
      return String(isoStr).replace("T", " ").split(".")[0];
    }
  }

  // Helper: Format ISO string to browser's local time (HH:MM:SS AM/PM)
  function formatLocalTime(isoStr) {
    if (!isoStr) return "--";
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return String(isoStr).split("T")[1]?.split(".")[0] || "--";
      return d.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
      });
    } catch (e) {
      return String(isoStr).split("T")[1]?.split(".")[0] || "--";
    }
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
  }

  // Watchdog freshness
  function checkFreshness() {
    if (lastSuccessfulFetchTime === 0) return;
    const deltaMs = Date.now() - lastSuccessfulFetchTime;
    if (deltaMs > STALE_THRESHOLD_MS && connectionState === "LIVE") {
      setConnectionState("STALE");
    }
  }

  // Live Camera Stream State (Section B)
  let cameraEnabled = true;
  let streamConnected = false;
  let lastFrameReceivedAt = 0;
  let streamReconnectTimer = null;
  let streamWatchdogTimer = null;

  function setLiveCameraUI(state, customMsg) {
    if (!elLiveCamStatusPill || !elLiveCamStatusText) return;

    if (state === "LIVE") {
      elLiveCamStatusPill.className = "status-pill status-live";
      elLiveCamStatusText.textContent = "CAMERA LIVE";
      if (btnCameraToggle) {
        btnCameraToggle.textContent = "TURN CAMERA OFF";
        btnCameraToggle.className = "btn btn-sm btn-turn-off";
        btnCameraToggle.disabled = false;
      }
      if (elVideoOverlay) elVideoOverlay.className = "video-overlay hidden";
      if (btnRetryStream) btnRetryStream.style.display = "none";
    } else if (state === "OFF") {
      elLiveCamStatusPill.className = "status-pill status-off";
      elLiveCamStatusText.textContent = "CAMERA OFF";
      if (btnCameraToggle) {
        btnCameraToggle.textContent = "TURN CAMERA ON";
        btnCameraToggle.className = "btn btn-sm btn-turn-on";
        btnCameraToggle.disabled = false;
      }
      if (elVideoOverlay) {
        elVideoOverlay.className = "video-overlay";
        if (elOverlayIcon) elOverlayIcon.textContent = "🛑";
        if (elOverlayTitle) elOverlayTitle.textContent = "CAMERA OFF";
        if (elOverlayMsg) elOverlayMsg.textContent = customMsg || "Home surveillance is stopped. Turn on camera to resume monitoring.";
      }
      if (btnRetryStream) btnRetryStream.style.display = "none";
      if (elMetricFps) elMetricFps.textContent = "0.0";
      if (elMetricResolution) elMetricResolution.textContent = "--";
      if (elMetricPeopleCount) elMetricPeopleCount.textContent = "0";
      if (elMetricObjectsCount) elMetricObjectsCount.textContent = "0";
      if (elMetricLastFrame) elMetricLastFrame.textContent = "--";
    } else if (state === "STARTING") {
      elLiveCamStatusPill.className = "status-pill status-starting";
      elLiveCamStatusText.textContent = "STARTING CAMERA...";
      if (btnCameraToggle) {
        btnCameraToggle.textContent = "STARTING...";
        btnCameraToggle.disabled = true;
      }
      if (elVideoOverlay) {
        elVideoOverlay.className = "video-overlay";
        if (elOverlayIcon) elOverlayIcon.textContent = "⏳";
        if (elOverlayTitle) elOverlayTitle.textContent = "STARTING CAMERA...";
        if (elOverlayMsg) elOverlayMsg.textContent = customMsg || "Initializing physical camera device and perception pipeline...";
      }
      if (btnRetryStream) btnRetryStream.style.display = "none";
    } else if (state === "CONNECTING") {
      elLiveCamStatusPill.className = "status-pill status-starting";
      elLiveCamStatusText.textContent = "CONNECTING";
      if (elVideoOverlay) {
        elVideoOverlay.className = "video-overlay";
        if (elOverlayIcon) elOverlayIcon.textContent = "⏳";
        if (elOverlayTitle) elOverlayTitle.textContent = "CONNECTING";
        if (elOverlayMsg) elOverlayMsg.textContent = customMsg || "Acquiring authoritative video stream from Device 0...";
      }
      if (btnRetryStream) btnRetryStream.style.display = "none";
    } else if (state === "INTERRUPTED") {
      elLiveCamStatusPill.className = "status-pill status-error";
      elLiveCamStatusText.textContent = "STREAM INTERRUPTED";
      if (elVideoOverlay) {
        elVideoOverlay.className = "video-overlay";
        if (elOverlayIcon) elOverlayIcon.textContent = "⚠️";
        if (elOverlayTitle) elOverlayTitle.textContent = "CAMERA STREAM INTERRUPTED";
        if (elOverlayMsg) elOverlayMsg.textContent = customMsg || "Stream connection lost. Attempting controlled reconnection...";
      }
      if (btnRetryStream) btnRetryStream.style.display = "inline-block";
    } else if (state === "ERROR" || state === "UNAVAILABLE") {
      elLiveCamStatusPill.className = "status-pill status-error";
      elLiveCamStatusText.textContent = "STREAM ERROR";
      if (elVideoOverlay) {
        elVideoOverlay.className = "video-overlay";
        if (elOverlayIcon) elOverlayIcon.textContent = "⚠️";
        if (elOverlayTitle) elOverlayTitle.textContent = "CAMERA STREAM UNAVAILABLE";
        if (elOverlayMsg) elOverlayMsg.textContent = customMsg || "Hardware camera or video feed is unavailable.";
      }
      if (btnRetryStream) btnRetryStream.style.display = "inline-block";
    }
  }

  function attachAdminLiveStream() {
    if (!elCameraFeed || !currentUser || currentUser.role !== "ADMIN") return;
    if (!cameraEnabled) {
      setLiveCameraUI("OFF");
      return;
    }
    if (elCameraFeed.src && elCameraFeed.src.includes("/api/camera/video_feed") && streamConnected) {
      return;
    }
    setLiveCameraUI("CONNECTING");
    elCameraFeed.src = `/api/camera/video_feed?t=${Date.now()}`;
  }

  function detachAdminLiveStream() {
    if (streamReconnectTimer) {
      clearTimeout(streamReconnectTimer);
      streamReconnectTimer = null;
    }
    if (elCameraFeed) {
      elCameraFeed.src = "";
    }
    streamConnected = false;
  }

  function scheduleStreamReconnect(delayMs = 3000) {
    if (streamReconnectTimer) return;
    if (!cameraEnabled || !currentUser || currentUser.role !== "ADMIN") return;
    streamReconnectTimer = setTimeout(() => {
      streamReconnectTimer = null;
      if (cameraEnabled && currentUser && currentUser.role === "ADMIN") {
        setLiveCameraUI("CONNECTING", "Reconnecting to live camera stream...");
        if (elCameraFeed) {
          elCameraFeed.src = `/api/camera/video_feed?t=${Date.now()}`;
        }
      }
    }, delayMs);
  }

  function startStreamWatchdog() {
    if (streamWatchdogTimer) clearInterval(streamWatchdogTimer);
    streamWatchdogTimer = setInterval(() => {
      if (!currentUser || currentUser.role !== "ADMIN" || !cameraEnabled) return;
      if (streamConnected && lastFrameReceivedAt > 0) {
        const elapsed = Date.now() - lastFrameReceivedAt;
        if (elapsed > 6000) {
          streamConnected = false;
          setLiveCameraUI("INTERRUPTED");
          scheduleStreamReconnect(2000);
        }
      }
    }, 2000);
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
      if (elAdminNavBar) elAdminNavBar.style.display = "none";
      if (viewAdmin) viewAdmin.style.display = "none";
      if (viewAdminCompanion) viewAdminCompanion.style.display = "none";
      if (viewAuth) viewAuth.style.display = "none";
      if (viewGuest) viewGuest.style.display = "block";
      detachAdminLiveStream();
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
      const adminName = (currentUser.display_name || currentUser.username || "Administrator").trim();
      if (elHeaderRoleBadge) {
        elHeaderRoleBadge.textContent = `${adminName.toUpperCase()} / ADMIN / VERIFIED`;
        elHeaderRoleBadge.className = "role-badge";
      }
      const elSubtitle = document.getElementById("header-subtitle");
      if (elSubtitle) {
        const hour = new Date().getHours();
        const greeting = hour < 12 ? "Good morning" : (hour < 18 ? "Good afternoon" : "Good evening");
        elSubtitle.textContent = `${greeting}, ${adminName} • Continuous Physical Safety Intelligence • Perimeter Protection`;
      }
      const btnEditProfile = document.getElementById("btn-edit-admin-profile");
      if (btnEditProfile) btnEditProfile.style.display = "inline-block";

      if (elHeaderIndicators) elHeaderIndicators.style.display = "flex";
      if (elAdminNavBar) elAdminNavBar.style.display = "flex";

      if (currentAdminTab === "companion") {
        showAdminCompanionTab();
      } else {
        showAdminDashboardTab();
      }
      startStreamWatchdog();
      fetchCompanionStatus();
    } else {
      // AUTHORIZED USER DASHBOARD
      const userName = (currentUser.display_name || currentUser.username || "Authorized User").trim();
      detachAdminLiveStream();
      if (elHeaderRoleBadge) {
        elHeaderRoleBadge.textContent = `${userName.toUpperCase()} / AUTHORIZED USER`;
        elHeaderRoleBadge.className = "role-badge auth-user";
      }
      const elSubtitle = document.getElementById("header-subtitle");
      if (elSubtitle) {
        elSubtitle.textContent = `Residential Protection • Family & Household Safety Feed`;
      }
      const btnEditProfile = document.getElementById("btn-edit-admin-profile");
      if (btnEditProfile) btnEditProfile.style.display = "none";

      if (elHeaderIndicators) elHeaderIndicators.style.display = "none";
      if (elAdminNavBar) elAdminNavBar.style.display = "none";
      if (viewAdmin) viewAdmin.style.display = "none";
      if (viewAdminCompanion) viewAdminCompanion.style.display = "none";
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
    fetchCompanionStatus();
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

    if (typeof live.enabled === "boolean") {
      const wasEnabled = cameraEnabled;
      cameraEnabled = live.enabled;
      if (!cameraEnabled) {
        setLiveCameraUI("OFF");
        detachAdminLiveStream();
      } else if (!wasEnabled && cameraEnabled && currentUser?.role === "ADMIN") {
        attachAdminLiveStream();
      }
    }

    if (cameraEnabled) {
      if (streamConnected) {
        setLiveCameraUI("LIVE");
      }
      if (elMetricFps) elMetricFps.textContent = typeof live.fps === "number" ? live.fps.toFixed(1) : (cam.fps ? cam.fps.toFixed(1) : "0.0");
      if (elMetricResolution) {
        if (live.resolution?.width) {
          elMetricResolution.textContent = `${live.resolution.width}x${live.resolution.height}`;
        } else if (cam.resolution?.width) {
          elMetricResolution.textContent = `${cam.resolution.width}x${cam.resolution.height}`;
        } else {
          elMetricResolution.textContent = "640x480";
        }
      }
      if (elMetricPeopleCount) elMetricPeopleCount.textContent = live.active_people?.length || 0;
      if (elMetricObjectsCount) elMetricObjectsCount.textContent = live.active_objects?.length || 0;
      if (elMetricLastFrame) {
        if (lastFrameReceivedAt > 0) {
          const secAgo = Math.max(0, Math.round((Date.now() - lastFrameReceivedAt) / 1000));
          elMetricLastFrame.textContent = secAgo === 0 ? "Just now" : `${secAgo}s ago`;
        } else if (live.last_frame_timestamp) {
          const secAgo = Math.max(0, Math.round((Date.now() / 1000) - live.last_frame_timestamp));
          elMetricLastFrame.textContent = `${secAgo}s ago`;
        } else {
          elMetricLastFrame.textContent = "--";
        }
      }
    }

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
              <span>TRACK ID: #${escapeHtml(p.track_id !== undefined ? p.track_id : '--')}</span>
              <span>STATE: ${escapeHtml(p.movement_state || 'stationary')}</span>
              <span>PRESENCE: <strong class="${p.current_presence ? 'text-green' : 'text-dim'}">${p.current_presence ? 'IN VIEW' : 'EXITED'}</strong></span>
              <span>ENTERED: ${escapeHtml(p.entry_time ? formatLocalTime(p.entry_time) : '--')}</span>
            </div>
          </div>
        `).join("");
      }
    }

    // D. Activity Feed (6 Columns: TIME, WHAT HAPPENED, WHO / OBJECT, ACTIVITY, STATUS, TRACE)
    const events = data.activity || [];
    if (elEventsCountBadge) elEventsCountBadge.textContent = `${events.length} RECENT`;
    if (elEventsTbody) {
      if (events.length === 0) {
        elEventsTbody.innerHTML = '<tr><td colspan="6" class="empty-table">Awaiting events from perception engine...</td></tr>';
      } else {
        elEventsTbody.innerHTML = events.slice(0, 25).map(e => {
          const timeStr = formatLocalTime(e.timestamp);
          const fullTimeStr = formatLocalDateTime(e.timestamp);
          const conf = typeof e.confidence === "number" ? `${Math.round(e.confidence * 100)}%` : "--";

          // Human-readable WHAT HAPPENED
          let whatHappened = e.metadata?.summary || (e.event_type ? e.event_type.replace(/_/g, " ") : "--");

          // WHO / OBJECT
          let whoObject = "NOT ASSOCIATED";
          if (e.metadata?.person_name) {
            whoObject = e.metadata.person_name;
          } else if (e.person_id) {
            whoObject = `@${e.person_id}`;
          } else if (e.object_id) {
            whoObject = e.object_id;
          } else if (e.track_id !== undefined && e.track_id !== null && e.track_id !== "--") {
            whoObject = `TRACK ${e.track_id}`;
          }

          // ACTIVITY
          let activityStr = "Stationary";
          if (e.metadata?.action) {
            activityStr = e.metadata.action;
          } else if (e.event_type?.includes("WALK")) {
            activityStr = "Walking";
          } else if (e.event_type?.includes("STAND")) {
            activityStr = "Standing";
          } else if (e.event_type?.includes("SIT")) {
            activityStr = "Sitting";
          } else if (e.event_type?.includes("ENTER")) {
            activityStr = "Entered area";
          } else if (e.event_type?.includes("LEFT") || e.event_type?.includes("LEAVE")) {
            activityStr = "Departed area";
          } else if (e.movement_state) {
            activityStr = e.movement_state;
          }

          // STATUS (ACTIVE, RESOLVED, OBSERVED)
          const status = e.status || "OBSERVED";
          const statusClass = status === "ACTIVE" ? "badge-live" : (status === "RESOLVED" ? "badge-subtle" : "badge-stale");

          // TRACE (never '#--', display TRACK <id> or NOT ASSOCIATED)
          const trackLabel = (e.track_id !== undefined && e.track_id !== null && e.track_id !== "--") ? `TRACK ${e.track_id}` : "NOT ASSOCIATED";
          const eventIdShort = e.event_id ? e.event_id.substring(0, 8) + ".." : "--";
          const ruleStr = e.rule_id || "--";

          return `
            <tr>
              <td title="${escapeHtml(fullTimeStr)}"><strong>${escapeHtml(timeStr)}</strong></td>
              <td><strong>${escapeHtml(whatHappened)}</strong></td>
              <td><span class="badge badge-subtle">${escapeHtml(whoObject)}</span></td>
              <td>${escapeHtml(activityStr)}</td>
              <td><span class="badge ${statusClass}">${escapeHtml(status)}</span></td>
              <td>
                <details style="cursor: pointer; font-size: 10px; font-family: var(--font-mono);">
                  <summary style="outline: none; color: var(--accent-cyan); font-weight: 600;">${escapeHtml(trackLabel)}</summary>
                  <div style="margin-top: 4px; padding: 4px 6px; background: rgba(0,0,0,0.3); border-radius: 4px; font-size: 9px; line-height: 1.4; color: var(--text-dim);">
                    <div>Event: <code>${escapeHtml(eventIdShort)}</code></div>
                    <div>Conf: <code>${escapeHtml(conf)}</code></div>
                    <div>Rule: <code>${escapeHtml(ruleStr)}</code></div>
                  </div>
                </details>
              </td>
            </tr>
          `;
        }).join("");
      }
    }

    const elEventsTimelineView = document.getElementById("events-timeline-view");
    if (elEventsTimelineView) {
      if (events.length === 0) {
        elEventsTimelineView.innerHTML = '<div class="empty-state">Awaiting timeline events...</div>';
      } else {
        elEventsTimelineView.innerHTML = events.slice(0, 20).map(e => {
          const timeStr = formatLocalTime(e.timestamp);
          const fullTimeStr = formatLocalDateTime(e.timestamp);
          const trackLabel = (e.track_id !== undefined && e.track_id !== null && e.track_id !== "--") ? `Track ${e.track_id}` : 'Perimeter Observation';
          const pName = e.metadata?.person_name || trackLabel;
          const attrs = e.metadata?.visual_attributes || {};
          let attrNotes = [];
          if (attrs.upper_color) attrNotes.push(`Wearing ${attrs.upper_color} top`);
          if (attrs.lower_color) attrNotes.push(`${attrs.lower_color} pants`);
          if (attrs.movement_direction) attrNotes.push(`Moving ${attrs.movement_direction}`);
          if (e.metadata?.carried_objects && e.metadata.carried_objects.length > 0) {
            attrNotes.push(`Carrying: ${e.metadata.carried_objects.map(c => c.label).join(", ")}`);
          }
          const attrStr = attrNotes.join(" • ");
          const isAlert = e.event_type?.includes("BREACH") || e.event_type?.includes("ALERT") || e.event_type?.includes("THREAT");

          return `
            <div class="timeline-card ${isAlert ? 'alert' : ''}">
              <div class="timeline-time" title="${escapeHtml(fullTimeStr)}">${escapeHtml(timeStr)}</div>
              <div class="timeline-content">
                <div class="timeline-title-row">
                  <span class="timeline-event-name">${escapeHtml(e.event_type ? e.event_type.replace(/_/g, " ") : "--")}</span>
                  <span class="badge ${e.status === 'ACTIVE' ? 'badge-live' : (e.status === 'RESOLVED' ? 'badge-subtle' : 'badge-stale')}" style="font-size: 9px;">${escapeHtml(e.status || 'OBSERVED')}</span>
                </div>
                <div class="timeline-meta">
                  <strong>${escapeHtml(pName)}</strong> ${attrStr ? `<span style="color: var(--text-dim);">— ${escapeHtml(attrStr)}</span>` : ''}
                </div>
              </div>
            </div>
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
          const createdShort = formatLocalDateTime(inc.created_at);
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
              <span>CAPTURED: ${escapeHtml(formatLocalDateTime(ev.timestamp))}</span>
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
          const tsShort = formatLocalDateTime(l.timestamp);
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
          const tsShort = formatLocalDateTime(a.timestamp);
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
          const tsShort = formatLocalDateTime(n.created_at);
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
        const lastLoginShort = u.last_login_at ? formatLocalDateTime(u.last_login_at) : "Never";
        const relStr = u.relationship || "Household Member";
        const phoneStr = u.phone_number || "--";

        html += `
          <div class="user-slot-card occupied">
            <div>
              <div class="slot-header">
                <span class="slot-number-tag">SLOT ${i}</span>
                <div style="display: flex; gap: 4px;">
                  <span class="badge ${faceEnrolled ? 'badge-live' : 'badge-stale'}" style="font-size: 9px;">${faceEnrolled ? 'FACE ENROLLED' : 'NO FACE'}</span>
                  <span class="badge ${isActive ? 'badge-live' : 'badge-subtle'}" style="font-size: 9px;">${isActive ? 'ACTIVE' : 'DEACTIVATED'}</span>
                </div>
              </div>
              <div class="slot-user-name">${escapeHtml(u.display_name || u.username)}</div>
              <div class="slot-username">@${escapeHtml(u.username)}</div>
              <div style="display: flex; gap: 6px; align-items: center; margin: 4px 0 6px;">
                <span class="role-badge ${isAdmin ? '' : 'auth-user'}" style="font-size: 9px;">${escapeHtml(u.role)}</span>
                <span class="badge badge-subtle" style="font-size: 9px;">${escapeHtml(relStr)}</span>
              </div>
              <div style="font-size: 10px; color: var(--text-dim); margin-top: 6px; font-family: var(--font-mono); line-height: 1.4;">
                <div>PHONE: ${escapeHtml(phoneStr)}</div>
                <div>LAST LOGIN: ${escapeHtml(lastLoginShort)}</div>
              </div>
            </div>
            <div class="slot-actions">
              <button class="btn btn-xs btn-outline" data-action="edit-user" data-uid="${escapeHtml(u.user_id)}" data-username="${escapeHtml(u.username)}" data-name="${escapeHtml(u.display_name || '')}" data-role="${escapeHtml(u.role)}" data-relationship="${escapeHtml(u.relationship || 'Family')}" data-phone="${escapeHtml(u.phone_number || '')}" data-active="${isActive}">EDIT</button>
              <button class="btn btn-xs btn-outline-cyan" data-action="manage-face" data-uid="${escapeHtml(u.user_id)}" data-name="${escapeHtml(u.display_name || u.username)}" data-username="${escapeHtml(u.username)}" data-enrolled="${faceEnrolled}">MANAGE FACE</button>
              <button class="btn btn-xs ${isActive ? 'btn-outline-danger' : 'btn-outline'}" data-action="toggle-active" data-uid="${escapeHtml(u.user_id)}" data-active="${isActive}">${isActive ? 'DISABLE' : 'ENABLE'}</button>
              ${!isAdmin ? `<button class="btn btn-xs btn-outline-danger" data-action="remove-user" data-uid="${escapeHtml(u.user_id)}" data-name="${escapeHtml(u.display_name || u.username)}" data-username="${escapeHtml(u.username)}">REMOVE</button>` : ''}
            </div>
          </div>
        `;
      } else {
        html += `
          <div class="user-slot-card empty">
            <span class="slot-number-tag">SLOT ${i}</span>
            <div style="font-size: 11px; color: var(--text-dim); font-family: var(--font-mono);">AVAILABLE IDENTITY SLOT</div>
            <button class="btn btn-xs btn-primary" data-action="open-add-user" data-slot="${i}">+ ADD AUTHORIZED USER</button>
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

        // Credentials verified -> Advance to Stage 2: Biometric Face Verification
        tempAuthToken = data.temp_token;
        if (loginStageCredentials) loginStageCredentials.style.display = "none";
        if (loginStageFace) loginStageFace.style.display = "block";
        if (stageStep1) stageStep1.className = "stage-step completed";
        if (stageConnectorBar) stageConnectorBar.className = "stage-connector active";
        if (stageStep2) stageStep2.className = "stage-step active";
        if (elLoginError) elLoginError.style.display = "none";
        if (faceError) faceError.style.display = "none";
        if (faceUserHint) faceUserHint.textContent = `Authenticating ${data.display_name || data.username} (${data.role || selectedRoleHint || 'ADMIN'})...`;
        if (valVerifyStatus) valVerifyStatus.textContent = "AWAITING_CAPTURE";
        if (faceStatusMsg) faceStatusMsg.textContent = "Position your face in the reticle and click CAPTURE & VERIFY FACE.";

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

        if (!resp.ok || !data.face_verified) {
          btnCaptureFace.disabled = false;
          if (valVerifyStatus) valVerifyStatus.textContent = "DENIED";
          if (faceError) {
            faceError.textContent = data.details || data.error || "Face verification failed. Face does not match enrolled template.";
            faceError.style.display = "block";
          }
          if (faceStatusMsg) {
            faceStatusMsg.textContent = "Biometric check failed. You can re-try or click RE-ENROLL FACE to register your current face.";
          }
          return;
        }

        // Biometric verification success -> Grant dashboard access
        currentUser = data.user;
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

  // Stage 2: Re-Enroll Face Button
  const btnReEnrollFace = document.getElementById("btn-re-enroll-face");
  if (btnReEnrollFace) {
    btnReEnrollFace.addEventListener("click", async () => {
      if (!tempAuthToken) {
        if (faceError) {
          faceError.textContent = "Authentication session expired. Please return to step 1.";
          faceError.style.display = "block";
        }
        return;
      }
      if (faceError) faceError.style.display = "none";
      if (faceStatusMsg) faceStatusMsg.textContent = "Capturing face from camera & computing 1856-D biometric embedding...";
      if (valVerifyStatus) valVerifyStatus.textContent = "ENROLLING...";
      btnReEnrollFace.disabled = true;

      try {
        const resp = await fetch("/api/auth/login/re-enroll", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ temp_token: tempAuthToken }),
          credentials: "same-origin",
        });

        const data = await resp.json();
        if (resp.ok) {
          if (valVerifyStatus) valVerifyStatus.textContent = "ENROLLED ✓";
          if (faceStatusMsg) faceStatusMsg.textContent = "New face enrolled successfully! Click CAPTURE & VERIFY FACE to log in.";
          if (faceError) faceError.style.display = "none";
        } else {
          if (valVerifyStatus) valVerifyStatus.textContent = "ERROR";
          if (faceError) {
            faceError.textContent = data.details || data.error || "Enrollment failed.";
            faceError.style.display = "block";
          }
        }
      } catch (err) {
        if (valVerifyStatus) valVerifyStatus.textContent = "ERROR";
        if (faceError) {
          faceError.textContent = `Network error: ${err.message}`;
          faceError.style.display = "block";
        }
      } finally {
        btnReEnrollFace.disabled = false;
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

  // Live Camera Feed Event Handlers
  if (elCameraFeed) {
    elCameraFeed.onload = () => {
      lastFrameReceivedAt = Date.now();
      streamConnected = true;
      if (cameraEnabled) {
        setLiveCameraUI("LIVE");
      }
    };
    elCameraFeed.onerror = () => {
      streamConnected = false;
      if (!cameraEnabled) {
        setLiveCameraUI("OFF");
        return;
      }
      setLiveCameraUI("ERROR", "Failed to connect to camera stream.");
      scheduleStreamReconnect(3000);
    };
  }

  // Camera ON / OFF Control & Modal Listeners
  if (btnCameraToggle) {
    btnCameraToggle.addEventListener("click", () => {
      if (cameraEnabled) {
        if (modalCameraConfirm) modalCameraConfirm.style.display = "flex";
      } else {
        handleCameraControl(true);
      }
    });
  }

  if (btnCloseCameraConfirm) {
    btnCloseCameraConfirm.addEventListener("click", () => {
      if (modalCameraConfirm) modalCameraConfirm.style.display = "none";
    });
  }
  if (btnCancelCameraOff) {
    btnCancelCameraOff.addEventListener("click", () => {
      if (modalCameraConfirm) modalCameraConfirm.style.display = "none";
    });
  }
  if (btnConfirmCameraOff) {
    btnConfirmCameraOff.addEventListener("click", async () => {
      if (modalCameraConfirm) modalCameraConfirm.style.display = "none";
      await handleCameraControl(false);
    });
  }

  if (btnRetryStream) {
    btnRetryStream.addEventListener("click", () => {
      if (cameraEnabled) {
        setLiveCameraUI("CONNECTING", "Retrying stream connection...");
        if (elCameraFeed) elCameraFeed.src = `/api/camera/video_feed?t=${Date.now()}`;
      }
    });
  }

  async function handleCameraControl(enable) {
    if (btnCameraToggle) btnCameraToggle.disabled = true;
    if (enable) {
      setLiveCameraUI("STARTING");
    }

    try {
      const resp = await fetch("/api/camera/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: enable }),
        credentials: "same-origin",
      });

      const data = await resp.json();
      if (!resp.ok) {
        alert(data.details || data.error || "Failed to update camera state.");
        if (btnCameraToggle) btnCameraToggle.disabled = false;
        return;
      }

      cameraEnabled = data.camera_enabled;
      if (cameraEnabled) {
        setTimeout(() => {
          attachAdminLiveStream();
        }, 600);
      } else {
        detachAdminLiveStream();
        setLiveCameraUI("OFF");
      }

      pollDashboard();
    } catch (err) {
      alert(`Network error updating camera: ${err.message}`);
      if (btnCameraToggle) btnCameraToggle.disabled = false;
    }
  }

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

  // =========================================================================
  // GUIDED 4-STEP BIOMETRIC ENROLLMENT WIZARD STATE & HANDLERS
  // =========================================================================
  let guidedEnrollUserId = null;
  let guidedEnrollUserName = null;
  let guidedEnrollSamplesCount = 0;

  async function startBiometricEnrollmentSession(userId, userName) {
    if (!userId) {
      alert("No user selected for biometric enrollment.");
      return;
    }

    guidedEnrollUserId = userId;
    guidedEnrollUserName = userName || "User";
    sessionStorage.setItem("atlas_active_enroll_uid", userId);
    sessionStorage.setItem("atlas_active_enroll_name", guidedEnrollUserName);

    // Reset visual sample cards
    guidedEnrollSamplesCount = 0;
    for (let i = 1; i <= 5; i++) {
      const slot = document.getElementById(`sample-slot-${i}`);
      if (slot) {
        slot.className = "sample-slot-card";
        slot.innerHTML = `<span class="slot-num">${i}</span> <span class="slot-stat">EMPTY</span>`;
      }
    }

    const btnCap = document.getElementById("btn-capture-sample");
    const btnFuse = document.getElementById("btn-fuse-and-activate");
    const feedText = document.getElementById("enroll-sample-feedback");
    const errText = document.getElementById("enroll-sample-error");
    const countNum = document.getElementById("sample-count-num");

    if (btnCap) {
      btnCap.style.display = "inline-block";
      btnCap.disabled = false;
    }
    if (btnFuse) btnFuse.style.display = "none";
    if (feedText) feedText.textContent = `Connecting to camera stream for ${guidedEnrollUserName}...`;
    if (errText) errText.style.display = "none";
    if (countNum) countNum.textContent = "1";

    // Switch panes to Step 3
    const step1 = document.getElementById("add-step-1-pane");
    const step2 = document.getElementById("add-step-2-pane");
    const step3 = document.getElementById("add-step-3-pane");
    const step4 = document.getElementById("add-step-4-pane");
    if (step1) step1.style.display = "none";
    if (step2) step2.style.display = "none";
    if (step3) step3.style.display = "block";
    if (step4) step4.style.display = "none";
    _updateStepIndicator(3);

    const enrollPreview = document.getElementById("enroll-video-preview");
    if (enrollPreview) {
      enrollPreview.src = "/api/camera/video_feed?t=" + Date.now();
    }
    if (modalAddUser) modalAddUser.style.display = "flex";

    // Call backend to initialize / reset the enrollment session
    try {
      const resp = await fetch(`/api/admin/users/${encodeURIComponent(userId)}/face/session/start`, {
        method: "POST",
        credentials: "same-origin",
      });
      if (resp.ok) {
        const data = await resp.json();
        if (feedText) feedText.textContent = `Camera READY. Capture 5 distinct face angles/samples for ${data.username || guidedEnrollUserName}.`;
      }
    } catch (err) {
      console.warn("Backend enrollment session start returned error:", err);
      if (feedText) feedText.textContent = `Camera READY. Capture 5 distinct face samples for ${guidedEnrollUserName}.`;
    }
  }

  function resetGuidedEnrollmentModal() {
    guidedEnrollUserId = null;
    guidedEnrollUserName = null;
    guidedEnrollSamplesCount = 0;
    sessionStorage.removeItem("atlas_active_enroll_uid");
    sessionStorage.removeItem("atlas_active_enroll_name");

    const step1 = document.getElementById("add-step-1-pane");
    const step2 = document.getElementById("add-step-2-pane");
    const step3 = document.getElementById("add-step-3-pane");
    const step4 = document.getElementById("add-step-4-pane");
    if (step1) step1.style.display = "block";
    if (step2) step2.style.display = "none";
    if (step3) step3.style.display = "none";
    if (step4) step4.style.display = "none";

    _updateStepIndicator(1);

    for (let i = 1; i <= 5; i++) {
      const slot = document.getElementById(`sample-slot-${i}`);
      if (slot) {
        slot.className = "sample-slot-card";
        slot.innerHTML = `<span class="slot-num">${i}</span> <span class="slot-stat">EMPTY</span>`;
      }
    }

    const btnCap = document.getElementById("btn-capture-sample");
    const btnFuse = document.getElementById("btn-fuse-and-activate");
    const feedText = document.getElementById("enroll-sample-feedback");
    const errText = document.getElementById("enroll-sample-error");
    const countNum = document.getElementById("sample-count-num");

    if (btnCap) {
      btnCap.style.display = "inline-block";
      btnCap.disabled = false;
    }
    if (btnFuse) btnFuse.style.display = "none";
    if (feedText) feedText.textContent = "Sample progress: 0 of 5 captured. Face camera directly.";
    if (errText) errText.style.display = "none";
    if (countNum) countNum.textContent = "1";
    if (elAddUserError) elAddUserError.style.display = "none";
  }

  function _updateStepIndicator(currentStep) {
    for (let i = 1; i <= 4; i++) {
      const pill = document.getElementById(`add-step-pill-${i}`);
      if (pill) {
        if (i < currentStep) {
          pill.className = "stage-step done";
        } else if (i === currentStep) {
          pill.className = "stage-step active";
        } else {
          pill.className = "stage-step";
        }
      }
      if (i < 4) {
        const conn = document.getElementById(`add-step-conn-${i}`);
        if (conn) {
          conn.className = i < currentStep ? "stage-connector active" : "stage-connector";
        }
      }
    }
  }

  // Admin User Slot Management Click Handling
  if (elAdminUserSlotsGrid) {
    elAdminUserSlotsGrid.addEventListener("click", async (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      const action = btn.dataset.action;

      if (action === "open-add-user") {
        resetGuidedEnrollmentModal();
        if (formAddUser) formAddUser.reset();
        modalAddUser.style.display = "flex";
      } else if (action === "edit-user") {
        inputEditUserId.value = btn.dataset.uid;
        inputEditUsername.value = btn.dataset.username;
        inputEditDisplayName.value = btn.dataset.name;
        if (selectEditRelationship) selectEditRelationship.value = btn.dataset.relationship || "Family";
        if (inputEditPhone) inputEditPhone.value = btn.dataset.phone || "";
        checkEditIsActive.checked = btn.dataset.active === "true";
        if (inputEditPassword) inputEditPassword.value = "";
        elEditUserError.style.display = "none";
        modalEditUser.style.display = "flex";
      } else if (action === "manage-face") {
        inputFaceMgmtUserId.value = btn.dataset.uid;
        elFaceMgmtUserName.textContent = btn.dataset.name;
        elFaceMgmtUserHandle.textContent = `@${btn.dataset.username}`;
        const enrolled = btn.dataset.enrolled === "true";
        elFaceMgmtStatusBadge.textContent = enrolled ? "1856-D BIOMETRIC ENROLLED" : "NO FACE ENROLLED";
        elFaceMgmtStatusBadge.className = `badge ${enrolled ? 'badge-live' : 'badge-stale'}`;
        elFaceMgmtError.style.display = "none";
        btnActionRemoveFace.style.display = enrolled ? "inline-block" : "none";
        btnActionReplaceFace.textContent = enrolled ? "REPLACE FACE →" : "ENROLL FACE →";
        modalManageFace.style.display = "flex";
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
      } else if (action === "remove-user") {
        inputRemoveUserId.value = btn.dataset.uid;
        elRemoveUserName.textContent = btn.dataset.name;
        elRemoveUserHandle.textContent = `@${btn.dataset.username}`;
        elRemoveUserError.style.display = "none";
        modalRemoveUser.style.display = "flex";
      }
    });
  }

  // Edit User Form Submission
  if (formEditUser) {
    formEditUser.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (elEditUserError) elEditUserError.style.display = "none";

      const uid = inputEditUserId.value;
      const username = inputEditUsername.value.trim();
      const displayName = inputEditDisplayName.value.trim();
      const relationship = selectEditRelationship ? selectEditRelationship.value : "Family";
      const phone = inputEditPhone ? inputEditPhone.value.trim() : null;
      const isActive = checkEditIsActive.checked;
      const password = inputEditPassword.value;

      const payload = {
        username: username,
        display_name: displayName,
        relationship: relationship,
        phone_number: phone,
        is_active: isActive,
      };
      if (password) {
        payload.password = password;
      }

      try {
        const resp = await fetch(`/api/admin/users/${encodeURIComponent(uid)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
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
        if (elEditUserError) {
          elEditUserError.textContent = `Network error: ${err.message}`;
          elEditUserError.style.display = "block";
        }
      }
    });
  }

  if (btnCloseEditUser) btnCloseEditUser.addEventListener("click", () => { modalEditUser.style.display = "none"; });
  if (btnCancelEditUser) btnCancelEditUser.addEventListener("click", () => { modalEditUser.style.display = "none"; });

  // Remove User Confirmation
  if (btnCloseRemoveUser) btnCloseRemoveUser.addEventListener("click", () => { modalRemoveUser.style.display = "none"; });
  if (btnCancelRemoveUser) btnCancelRemoveUser.addEventListener("click", () => { modalRemoveUser.style.display = "none"; });

  if (btnConfirmRemoveUser) {
    btnConfirmRemoveUser.addEventListener("click", async () => {
      const uid = inputRemoveUserId.value;
      if (!uid) return;
      btnConfirmRemoveUser.disabled = true;
      if (elRemoveUserError) elRemoveUserError.style.display = "none";

      try {
        const resp = await fetch(`/api/admin/users/${encodeURIComponent(uid)}`, {
          method: "DELETE",
          credentials: "same-origin",
        });

        const data = await resp.json();
        btnConfirmRemoveUser.disabled = false;

        if (!resp.ok) {
          if (elRemoveUserError) {
            elRemoveUserError.textContent = data.details || data.error || "Failed to remove user.";
            elRemoveUserError.style.display = "block";
          }
          return;
        }

        modalRemoveUser.style.display = "none";
        pollDashboard();
      } catch (err) {
        btnConfirmRemoveUser.disabled = false;
        if (elRemoveUserError) {
          elRemoveUserError.textContent = `Network error: ${err.message}`;
          elRemoveUserError.style.display = "block";
        }
      }
    });
  }

  // Manage Face Actions
  if (btnCloseManageFace) btnCloseManageFace.addEventListener("click", () => { modalManageFace.style.display = "none"; });

  if (btnActionReplaceFace) {
    btnActionReplaceFace.addEventListener("click", async () => {
      const uid = inputFaceMgmtUserId.value;
      const name = elFaceMgmtUserName.textContent;
      modalManageFace.style.display = "none";
      await startBiometricEnrollmentSession(uid, name);
    });
  }

  if (btnActionRemoveFace) {
    btnActionRemoveFace.addEventListener("click", async () => {
      const uid = inputFaceMgmtUserId.value;
      if (!uid) return;
      btnActionRemoveFace.disabled = true;
      if (elFaceMgmtError) elFaceMgmtError.style.display = "none";

      try {
        const resp = await fetch(`/api/admin/users/${encodeURIComponent(uid)}/face/remove`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
        });

        const data = await resp.json();
        btnActionRemoveFace.disabled = false;

        if (!resp.ok) {
          if (elFaceMgmtError) {
            elFaceMgmtError.textContent = data.details || data.error || "Failed to remove face template.";
            elFaceMgmtError.style.display = "block";
          }
          return;
        }

        modalManageFace.style.display = "none";
        pollDashboard();
      } catch (err) {
        btnActionRemoveFace.disabled = false;
        if (elFaceMgmtError) {
          elFaceMgmtError.textContent = `Network error: ${err.message}`;
          elFaceMgmtError.style.display = "block";
        }
      }
    });
  }

  // Step 1: Submit Form to Create Draft User
  if (formAddUser) {
    formAddUser.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (elAddUserError) elAddUserError.style.display = "none";

      const username = inputNewUsername.value.trim();
      const displayName = inputNewDisplayName.value.trim();
      const password = inputNewPassword.value;
      const role = selectNewRole ? selectNewRole.value : "AUTHORIZED_USER";
      const phoneInput = document.getElementById("new-phone");
      const phoneNumber = phoneInput ? phoneInput.value.trim() : null;
      const relInput = document.getElementById("new-relationship");
      const relationship = relInput ? relInput.value : "Family";

      try {
        const resp = await fetch("/api/admin/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username,
            display_name: displayName,
            password,
            role,
            phone_number: phoneNumber,
            relationship: relationship,
          }),
          credentials: "same-origin",
        });

        const data = await resp.json();
        if (!resp.ok) {
          elAddUserError.textContent = data.details || data.error || "Failed to create user.";
          elAddUserError.style.display = "block";
          return;
        }

        // Advance to Step 2 (Draft Account Confirmation)
        guidedEnrollUserId = data.user_id;
        guidedEnrollUserName = displayName || username;

        const draftDisplay = document.getElementById("draft-user-display");
        const draftId = document.getElementById("draft-user-id");
        if (draftDisplay) draftDisplay.textContent = `${guidedEnrollUserName} (@${username})`;
        if (draftId) draftId.textContent = guidedEnrollUserId;

        document.getElementById("add-step-1-pane").style.display = "none";
        document.getElementById("add-step-2-pane").style.display = "block";
        _updateStepIndicator(2);

      } catch (err) {
        if (elAddUserError) {
          elAddUserError.textContent = `Network error: ${err.message}`;
          elAddUserError.style.display = "block";
        }
      }
    });
  }

  // Step 2 Buttons
  const btnStartBio = document.getElementById("btn-start-biometrics");
  if (btnStartBio) {
    btnStartBio.addEventListener("click", async () => {
      await startBiometricEnrollmentSession(guidedEnrollUserId, guidedEnrollUserName);
    });
  }

  const btnCancelDraft = document.getElementById("btn-cancel-draft");
  if (btnCancelDraft) {
    btnCancelDraft.addEventListener("click", () => {
      modalAddUser.style.display = "none";
      pollDashboard();
    });
  }

  // Step 3: Capture Biometric Sample
  const btnCaptureSample = document.getElementById("btn-capture-sample");
  if (btnCaptureSample) {
    btnCaptureSample.addEventListener("click", async () => {
      if (!guidedEnrollUserId) {
        guidedEnrollUserId = sessionStorage.getItem("atlas_active_enroll_uid");
        guidedEnrollUserName = sessionStorage.getItem("atlas_active_enroll_name");
      }
      if (!guidedEnrollUserId) {
        const errEl = document.getElementById("enroll-sample-error");
        if (errEl) {
          errEl.textContent = "No active user enrollment session. Please select a user from User Management to begin.";
          errEl.style.display = "block";
        }
        alert("No active user enrollment session. Please select a user from User Management.");
        return;
      }
      const feedback = document.getElementById("enroll-sample-feedback");
      const errEl = document.getElementById("enroll-sample-error");
      if (errEl) errEl.style.display = "none";
      if (feedback) feedback.textContent = "Acquiring live hardware frame & verifying biometric quality...";
      btnCaptureSample.disabled = true;

      // Optional: grab frame from preview element if available
      let imagePayload = "";
      try {
        const previewImg = document.getElementById("enroll-video-preview");
        if (previewImg && previewImg.naturalWidth > 0 && previewImg.naturalHeight > 0) {
          const canvas = document.createElement("canvas");
          canvas.width = previewImg.naturalWidth;
          canvas.height = previewImg.naturalHeight;
          const ctx = canvas.getContext("2d");
          ctx.drawImage(previewImg, 0, 0);
          imagePayload = canvas.toDataURL("image/jpeg", 0.92);
        }
      } catch (_) {}

      try {
        const resp = await fetch(`/api/admin/users/${encodeURIComponent(guidedEnrollUserId)}/face/sample`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(imagePayload ? { image_data: imagePayload } : {}),
          credentials: "same-origin",
        });

        const data = await resp.json();
        btnCaptureSample.disabled = false;

        if (!resp.ok) {
          if (errEl) {
            errEl.textContent = data.details || data.error || "Sample quality check failed.";
            errEl.style.display = "block";
          }
          if (feedback) feedback.textContent = "Sample rejected. Ensure face is centered, well-lit, and in focus.";
          return;
        }

        guidedEnrollSamplesCount = data.sample_count || (guidedEnrollSamplesCount + 1);

        // Update slot card
        const slotEl = document.getElementById(`sample-slot-${guidedEnrollSamplesCount}`);
        if (slotEl) {
          slotEl.className = "sample-slot-card captured";
          slotEl.innerHTML = `<span class="slot-num">${guidedEnrollSamplesCount}</span> <span class="slot-stat">VALID ✓</span>`;
        }

        const countNum = document.getElementById("sample-count-num");
        if (countNum) countNum.textContent = Math.min(guidedEnrollSamplesCount + 1, 5);

        if (guidedEnrollSamplesCount >= 5) {
          btnCaptureSample.style.display = "none";
          const btnFuse = document.getElementById("btn-fuse-and-activate");
          if (btnFuse) btnFuse.style.display = "inline-block";
          if (feedback) {
            feedback.textContent = "✓ 5 valid biometric samples acquired! Click below to fuse 1856-D template and activate account.";
          }
        } else {
          if (feedback) {
            feedback.textContent = `Sample ${guidedEnrollSamplesCount} of 5 accepted (Blur score: ${Math.round(data.quality?.blur_score || 0)}, Contrast: ${Math.round(data.quality?.contrast || 0)}). Shift angle slightly for next sample.`;
          }
        }

      } catch (err) {
        btnCaptureSample.disabled = false;
        if (errEl) {
          errEl.textContent = `Network error: ${err.message}`;
          errEl.style.display = "block";
        }
      }
    });
  }

  // Step 3: Fuse and Activate
  const btnFuseActivate = document.getElementById("btn-fuse-and-activate");
  if (btnFuseActivate) {
    btnFuseActivate.addEventListener("click", async () => {
      if (!guidedEnrollUserId) return;
      btnFuseActivate.disabled = true;
      btnFuseActivate.textContent = "FUSING TEMPLATE...";

      try {
        const resp = await fetch(`/api/admin/users/${encodeURIComponent(guidedEnrollUserId)}/face/enroll-multi`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
          credentials: "same-origin",
        });

        const data = await resp.json();
        btnFuseActivate.disabled = false;

        if (!resp.ok) {
          alert(`Fusion failed: ${data.details || data.error || 'Biometric template fusion error'}`);
          btnFuseActivate.textContent = "FUSE TEMPLATE & ACTIVATE →";
          return;
        }

        // Advance to Step 4
        sessionStorage.removeItem("atlas_active_enroll_uid");
        sessionStorage.removeItem("atlas_active_enroll_name");
        const finalDisplay = document.getElementById("final-user-display");
        if (finalDisplay) finalDisplay.textContent = guidedEnrollUserName || "User";

        document.getElementById("add-step-3-pane").style.display = "none";
        document.getElementById("add-step-4-pane").style.display = "block";
        _updateStepIndicator(4);

      } catch (err) {
        btnFuseActivate.disabled = false;
        btnFuseActivate.textContent = "FUSE TEMPLATE & ACTIVATE →";
        alert(`Network error: ${err.message}`);
      }
    });
  }

  // Step 4: Finish Enrollment
  const btnFinishEnrollment = document.getElementById("btn-finish-enrollment");
  if (btnFinishEnrollment) {
    btnFinishEnrollment.addEventListener("click", () => {
      modalAddUser.style.display = "none";
      pollDashboard();
    });
  }

  // Reset samples button
  const btnResetSamples = document.getElementById("btn-reset-samples");
  if (btnResetSamples) {
    btnResetSamples.addEventListener("click", async () => {
      guidedEnrollSamplesCount = 0;
      for (let i = 1; i <= 5; i++) {
        const slot = document.getElementById(`sample-slot-${i}`);
        if (slot) {
          slot.className = "sample-slot-card";
          slot.innerHTML = `<span class="slot-num">${i}</span> <span class="slot-stat">EMPTY</span>`;
        }
      }
      const btnCap = document.getElementById("btn-capture-sample");
      const btnFuse = document.getElementById("btn-fuse-and-activate");
      const countNum = document.getElementById("sample-count-num");
      const feedback = document.getElementById("enroll-sample-feedback");
      if (btnCap) {
        btnCap.style.display = "inline-block";
        btnCap.disabled = false;
      }
      if (btnFuse) btnFuse.style.display = "none";
      if (countNum) countNum.textContent = "1";
      if (feedback) feedback.textContent = "Samples reset. Face camera directly.";

      if (guidedEnrollUserId) {
        try {
          await fetch(`/api/admin/users/${encodeURIComponent(guidedEnrollUserId)}/face/session/clear`, {
            method: "POST",
            credentials: "same-origin",
          });
        } catch (_) {}
      }
    });
  }

  if (btnCloseAddUser) btnCloseAddUser.addEventListener("click", () => { modalAddUser.style.display = "none"; });
  if (btnCancelAddUser) btnCancelAddUser.addEventListener("click", () => { modalAddUser.style.display = "none"; });

  // =========================================================================
  // ADMIN RE-AUTHENTICATION MODAL FOR SENSITIVE ACTIONS
  // =========================================================================
  let pendingReauthAction = null;
  const modalAdminReauth = document.getElementById("modal-admin-reauth");
  const formAdminReauth = document.getElementById("form-admin-reauth");
  const inputReauthPass = document.getElementById("reauth-password");
  const elReauthError = document.getElementById("reauth-error");
  const btnCloseReauth = document.getElementById("btn-close-reauth");
  const btnCancelReauth = document.getElementById("btn-cancel-reauth");

  function triggerAdminReauth(onSuccessCallback, actionDescription) {
    pendingReauthAction = onSuccessCallback;
    if (inputReauthPass) inputReauthPass.value = "";
    if (elReauthError) elReauthError.style.display = "none";
    if (modalAdminReauth) modalAdminReauth.style.display = "flex";
  }

  if (formAdminReauth) {
    formAdminReauth.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (elReauthError) elReauthError.style.display = "none";

      const password = inputReauthPass ? inputReauthPass.value : "";
      try {
        const resp = await fetch("/api/auth/reauthenticate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password }),
          credentials: "same-origin",
        });

        const data = await resp.json();
        if (!resp.ok) {
          elReauthError.textContent = data.details || data.error || "Re-authentication failed.";
          elReauthError.style.display = "block";
          return;
        }

        if (modalAdminReauth) modalAdminReauth.style.display = "none";
        if (typeof pendingReauthAction === "function") {
          const action = pendingReauthAction;
          pendingReauthAction = null;
          await action();
        }

      } catch (err) {
        if (elReauthError) {
          elReauthError.textContent = `Network error: ${err.message}`;
          elReauthError.style.display = "block";
        }
      }
    });
  }

  if (btnCloseReauth) btnCloseReauth.addEventListener("click", () => {
    if (modalAdminReauth) modalAdminReauth.style.display = "none";
    pendingReauthAction = null;
  });
  if (btnCancelReauth) btnCancelReauth.addEventListener("click", () => {
    if (modalAdminReauth) modalAdminReauth.style.display = "none";
    pendingReauthAction = null;
  });

  // =========================================================================
  // ADMIN PROFILE UPDATE MODAL
  // =========================================================================
  const modalAdminProfile = document.getElementById("modal-admin-profile");
  const formAdminProfile = document.getElementById("form-admin-profile");
  const inputAdminNewName = document.getElementById("admin-new-display-name");
  const elAdminProfileError = document.getElementById("admin-profile-error");
  const btnCloseAdminProfile = document.getElementById("btn-close-admin-profile");
  const btnCancelAdminProfile = document.getElementById("btn-cancel-admin-profile");
  const btnEditAdminProfile = document.getElementById("btn-edit-admin-profile");

  if (btnEditAdminProfile) {
    btnEditAdminProfile.addEventListener("click", () => {
      if (inputAdminNewName) {
        inputAdminNewName.value = currentUser?.display_name || currentUser?.username || "";
      }
      if (elAdminProfileError) elAdminProfileError.style.display = "none";
      if (modalAdminProfile) modalAdminProfile.style.display = "flex";
    });
  }

  if (formAdminProfile) {
    formAdminProfile.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (elAdminProfileError) elAdminProfileError.style.display = "none";

      const newName = inputAdminNewName.value.trim();
      if (!newName) return;

      try {
        const resp = await fetch("/api/admin/profile", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: newName }),
          credentials: "same-origin",
        });

        const data = await resp.json();
        if (!resp.ok) {
          elAdminProfileError.textContent = data.details || data.error || "Failed to update profile name.";
          elAdminProfileError.style.display = "block";
          return;
        }

        if (currentUser) {
          currentUser.display_name = newName;
          applyRoleView();
        }
        if (modalAdminProfile) modalAdminProfile.style.display = "none";
        pollDashboard();

      } catch (err) {
        if (elAdminProfileError) {
          elAdminProfileError.textContent = `Network error: ${err.message}`;
          elAdminProfileError.style.display = "block";
        }
      }
    });
  }

  if (btnCloseAdminProfile) btnCloseAdminProfile.addEventListener("click", () => {
    if (modalAdminProfile) modalAdminProfile.style.display = "none";
  });
  if (btnCancelAdminProfile) btnCancelAdminProfile.addEventListener("click", () => {
    if (modalAdminProfile) modalAdminProfile.style.display = "none";
  });

  // =========================================================================
  // ACTIVITY FEED TABLE VS TIMELINE TOGGLE
  // =========================================================================
  const btnViewTable = document.getElementById("btn-view-table");
  const btnViewTimeline = document.getElementById("btn-view-timeline");
  const elEventsTableView = document.getElementById("events-table-view");
  const elEventsTimelineView = document.getElementById("events-timeline-view");

  if (btnViewTable && btnViewTimeline) {
    btnViewTable.addEventListener("click", () => {
      btnViewTable.className = "btn btn-xs btn-toggle active";
      btnViewTimeline.className = "btn btn-xs btn-toggle";
      if (elEventsTableView) elEventsTableView.style.display = "table";
      if (elEventsTimelineView) elEventsTimelineView.style.display = "none";
    });

    btnViewTimeline.addEventListener("click", () => {
      btnViewTimeline.className = "btn btn-xs btn-toggle active";
      btnViewTable.className = "btn btn-xs btn-toggle";
      if (elEventsTableView) elEventsTableView.style.display = "none";
      if (elEventsTimelineView) elEventsTimelineView.style.display = "flex";
    });
  }

  // =========================================================================
  // CONVERSATIONAL ATLAS INTELLIGENCE CHAT CLIENT
  // =========================================================================
  async function handleChatSubmit(promptText, containerId, inputEl, sendBtn) {
    const text = (promptText || (inputEl ? inputEl.value : "")).trim();
    if (!text) return;

    if (inputEl) inputEl.value = "";
    if (sendBtn) sendBtn.disabled = true;

    const messagesContainer = document.getElementById(containerId);
    if (!messagesContainer) return;

    // 1. Append User Message
    const userMsgEl = document.createElement("div");
    userMsgEl.className = "chat-message chat-message-user";
    userMsgEl.innerHTML = `
      <div class="chat-msg-header">
        <span class="chat-sender">YOU</span>
        <span style="color: var(--text-dim);">${new Date().toLocaleTimeString()}</span>
      </div>
      <div class="chat-msg-body">${escapeHtml(text)}</div>
    `;
    messagesContainer.appendChild(userMsgEl);

    // 2. Append Loading Placeholder
    const loadingEl = document.createElement("div");
    loadingEl.className = "chat-message chat-message-assistant";
    loadingEl.id = `chat-loading-${Date.now()}`;
    loadingEl.innerHTML = `
      <div class="chat-msg-header">
        <span class="chat-sender">ATLAS INTELLIGENCE</span>
        <span class="badge badge-loading" style="font-size: 9px;">REASONING...</span>
      </div>
      <div class="chat-msg-body" style="color: var(--text-dim); font-style: italic;">
        Querying real-time perception state &amp; authoritative database records...
      </div>
    `;
    messagesContainer.appendChild(loadingEl);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    try {
      const resp = await fetch("/api/assistant/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
        credentials: "same-origin",
      });

      const data = await resp.json();
      loadingEl.remove();

      if (!resp.ok) {
        const errEl = document.createElement("div");
        errEl.className = "chat-message chat-message-assistant";
        errEl.innerHTML = `
          <div class="chat-msg-header">
            <span class="chat-sender">ATLAS INTELLIGENCE</span>
            <span class="badge badge-disconnected" style="font-size: 9px;">ERROR</span>
          </div>
          <div class="chat-msg-body text-red">${escapeHtml(data.details || data.error || 'Failed to process grounded query.')}</div>
        `;
        messagesContainer.appendChild(errEl);
        return;
      }

      // 3. Render Grounded Assistant Response
      const isGrounded = data.confidence_status === "GROUNDED_IN_EVIDENCE";
      const isUnavail = data.confidence_status === "VISUAL_VERIFICATION_UNAVAILABLE";
      const statusBadgeClass = isGrounded ? "badge-live" : (isUnavail ? "badge-stale" : "badge-subtle");

      const formatCiteItem = (c) => {
        if (!c) return "";
        if (typeof c === "string") return escapeHtml(c);
        if (typeof c === "object") {
          const label = c.title || c.label || c.event || c.source || c.type;
          if (label) return escapeHtml(label);
          const entries = Object.entries(c).filter(([k, v]) => typeof v !== 'object' && v !== null && v !== undefined);
          if (entries.length > 0) return escapeHtml(entries.map(([k, v]) => `${k}: ${v}`).join(" | "));
          return escapeHtml(JSON.stringify(c));
        }
        return escapeHtml(String(c));
      };

      const citationsHtml = (Array.isArray(data.citations) && data.citations.length > 0)
        ? `<div class="chat-citations-wrap">${data.citations.map(c => `<span class="chat-cite-pill">${formatCiteItem(c)}</span>`).join("")}</div>`
        : '';

      const formatTraceText = (t) => {
        if (!t) return "";
        if (typeof t === "string") return escapeHtml(t);
        if (typeof t === "object") {
          return `<pre style="margin:0; font-family:var(--font-mono); font-size:11px; white-space:pre-wrap;">${escapeHtml(JSON.stringify(t, null, 2))}</pre>`;
        }
        return escapeHtml(String(t));
      };

      const traceHtml = data.why_atlas_said_this
        ? `<details class="chat-traceability">
            <summary>Why ATLAS said this (Technical Traceability)</summary>
            <div class="chat-trace-content">${formatTraceText(data.why_atlas_said_this)}</div>
           </details>`
        : '';

      const answerText = typeof data.answer === 'string' ? data.answer : (data.answer?.text || data.text || JSON.stringify(data.answer) || 'Response received.');

      const assistantMsgEl = document.createElement("div");
      assistantMsgEl.className = "chat-message chat-message-assistant";
      assistantMsgEl.innerHTML = `
        <div class="chat-msg-header">
          <span class="chat-sender">ATLAS INTELLIGENCE</span>
          <div style="display: flex; gap: 6px; align-items: center;">
            <span class="badge ${statusBadgeClass}" style="font-size: 9px;">${escapeHtml(data.confidence_status || "GROUNDED")}</span>
            <span style="color: var(--text-dim);">${new Date().toLocaleTimeString()}</span>
          </div>
        </div>
        <div class="chat-msg-body">${escapeHtml(answerText)}</div>
        ${citationsHtml}
        ${traceHtml}
      `;
      messagesContainer.appendChild(assistantMsgEl);
      messagesContainer.scrollTop = messagesContainer.scrollHeight;

    } catch (err) {
      loadingEl.remove();
      const netErrEl = document.createElement("div");
      netErrEl.className = "chat-message chat-message-assistant";
      netErrEl.innerHTML = `
        <div class="chat-msg-header">
          <span class="chat-sender">ATLAS INTELLIGENCE</span>
          <span class="badge badge-disconnected" style="font-size: 9px;">NETWORK ERROR</span>
        </div>
        <div class="chat-msg-body text-red">Communication with ATLAS Chat Service failed: ${escapeHtml(err.message)}</div>
      `;
      messagesContainer.appendChild(netErrEl);
    } finally {
      if (sendBtn) sendBtn.disabled = false;
      if (inputEl) inputEl.focus();
    }
  }

  // Admin Chat Form Submit
  const adminChatForm = document.getElementById("admin-chat-form");
  const adminChatInput = document.getElementById("admin-chat-input");
  const btnAdminChatSend = document.getElementById("btn-admin-chat-send");

  if (adminChatForm) {
    adminChatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      handleChatSubmit(null, "admin-chat-messages", adminChatInput, btnAdminChatSend);
    });
  }

  // Authorized User Chat Form Submit
  const authChatForm = document.getElementById("auth-chat-form");
  const authChatInput = document.getElementById("auth-chat-input");
  const btnAuthChatSend = document.getElementById("btn-auth-chat-send");

  if (authChatForm) {
    authChatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      handleChatSubmit(null, "auth-chat-messages", authChatInput, btnAuthChatSend);
    });
  }

  // Quick Query Chips (Admin & Authorized User)
  document.addEventListener("click", (e) => {
    const chip = e.target.closest("button[data-chat-prompt]");
    if (!chip) return;
    const prompt = chip.dataset.chatPrompt;
    if (!prompt) return;

    if (currentUser?.role === "ADMIN") {
      handleChatSubmit(prompt, "admin-chat-messages", adminChatInput, btnAdminChatSend);
    } else {
      handleChatSubmit(prompt, "auth-chat-messages", authChatInput, btnAuthChatSend);
    }
  });

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
          authModalAlertTime.textContent = alertObj.created_at ? formatLocalDateTime(alertObj.created_at) : "--";
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
            <span class="inv-item-time">${escapeHtml(item.timestamp ? formatLocalTime(item.timestamp) : '--')}</span>
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
            <div style="font-size: 10px; color: var(--text-dim);">${escapeHtml(o.timestamp ? formatLocalTime(o.timestamp) : '')}</div>
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
        if (invCamCaptured) invCamCaptured.textContent = formatLocalDateTime(evArtifact.captured_at);
        if (invCamSourceEvent) invCamSourceEvent.textContent = evArtifact.source_event_id || "--";
        if (invCamEventTime) invCamEventTime.textContent = formatLocalDateTime(evArtifact.source_event_timestamp);
        if (invCamFrameTime) invCamFrameTime.textContent = formatLocalDateTime(evArtifact.source_frame_timestamp);
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
  // ATLAS COMPANION CLIENT (ESP32 USB Serial COM5 Integration)
  // =========================================================================

  let isFetchingCompanion = false;

  function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  }

  const COMPANION_STATE_META = {
    Idle: { icon: "🤖", label: "IDLE", desc: "Companion is resting in standby, monitoring home safety events and waiting for speech.", badgeClass: "badge-live" },
    Listening: { icon: "🎧", label: "LISTENING", desc: "Companion microphone is actively processing acoustic input and awaiting voice commands.", badgeClass: "badge-loading" },
    Thinking: { icon: "🧠", label: "THINKING", desc: "ATLAS Core LLM and deterministic perception pipeline are analyzing residential context.", badgeClass: "badge-loading" },
    Speaking: { icon: "🗣️", label: "SPEAKING", desc: "Forwarding grounded verbal synthesis over serial bus to physical speaker.", badgeClass: "badge-live" },
    Happy: { icon: "😊", label: "HAPPY", desc: "Positive affective state. All perimeter bounds and home occupants are verified safe.", badgeClass: "badge-live" },
    Sad: { icon: "😢", label: "SAD", desc: "Sympathetic affective expression.", badgeClass: "badge-subtle" },
    Surprised: { icon: "😲", label: "SURPRISED", desc: "Alert posture. High-risk security event or unexpected occupant transition detected.", badgeClass: "badge-error" },
    Confused: { icon: "🤔", label: "CONFUSED", desc: "Perception ambiguity or uncertainty threshold exceeded.", badgeClass: "badge-warning" },
  };

  let companionPollInterval = null;

  function startCompanionPolling() {
    if (companionPollInterval) clearInterval(companionPollInterval);
    fetchCompanionStatus();
    companionPollInterval = setInterval(() => {
      if (currentAdminTab === "companion" && currentUser?.role === "ADMIN") {
        fetchCompanionStatus();
      }
    }, 1000);
  }

  function stopCompanionPolling() {
    if (companionPollInterval) {
      clearInterval(companionPollInterval);
      companionPollInterval = null;
    }
  }

  function showAdminDashboardTab() {
    currentAdminTab = "dashboard";
    if (tabAdminDashboard) tabAdminDashboard.classList.add("active");
    if (tabAdminCompanion) tabAdminCompanion.classList.remove("active");
    if (viewAdmin) viewAdmin.style.display = "block";
    if (viewAdminCompanion) viewAdminCompanion.style.display = "none";
    stopCompanionPolling();
    attachAdminLiveStream();
  }

  function showAdminCompanionTab() {
    currentAdminTab = "companion";
    if (tabAdminDashboard) tabAdminDashboard.classList.remove("active");
    if (tabAdminCompanion) tabAdminCompanion.classList.add("active");
    if (viewAdmin) viewAdmin.style.display = "none";
    if (viewAdminCompanion) viewAdminCompanion.style.display = "block";
    startCompanionPolling();
  }

  if (tabAdminDashboard) {
    tabAdminDashboard.addEventListener("click", () => {
      showAdminDashboardTab();
    });
  }

  if (tabAdminCompanion) {
    tabAdminCompanion.addEventListener("click", () => {
      showAdminCompanionTab();
    });
  }

  function renderCompanionStatus(comp) {
    if (!comp) return;

    // 1. Navigation Badge
    if (elNavCompanionBadge) {
      if (comp.connected) {
        elNavCompanionBadge.textContent = "CONNECTED (COM5)";
        elNavCompanionBadge.className = "tab-badge companion-conn-badge badge-live";
      } else {
        elNavCompanionBadge.textContent = "OFFLINE (COM5)";
        elNavCompanionBadge.className = "tab-badge companion-conn-badge badge-subtle";
      }
    }

    if (!comp) return;

    // 0. Update ATLAS COMPANION Command Interface Status Strip
    const elCmdRobotDot = document.getElementById("comp-robot-dot");
    const elCmdRobotVal = document.getElementById("comp-robot-val");
    if (elCmdRobotVal) {
      elCmdRobotVal.textContent = comp.connected ? "Connected" : "Disconnected";
      elCmdRobotVal.style.color = comp.connected ? "#22c55e" : "#ef4444";
    }
    if (elCmdRobotDot) {
      elCmdRobotDot.style.color = comp.connected ? "#22c55e" : "#ef4444";
    }

    const elCmdOllamaDot = document.getElementById("comp-ollama-dot");
    const elCmdOllamaVal = document.getElementById("comp-ollama-val");
    if (elCmdOllamaVal) {
      const isOllamaOnline = Boolean(comp.ollama_online);
      elCmdOllamaVal.textContent = isOllamaOnline ? "Online" : "Offline";
      elCmdOllamaVal.style.color = isOllamaOnline ? "#22c55e" : "#ef4444";
    }
    if (elCmdOllamaDot) {
      elCmdOllamaDot.style.color = comp.ollama_online ? "#22c55e" : "#ef4444";
    }

    const elCmdStateDot = document.getElementById("comp-state-dot");
    const elCmdStateVal = document.getElementById("comp-state-val");
    if (elCmdStateVal) {
      let dispState = comp.is_speaking ? "Speaking" : (comp.state || "Idle");
      if (comp.active_task && (comp.active_task.includes("Processing") || comp.active_task.includes("Thinking"))) {
        dispState = "Thinking";
      }
      elCmdStateVal.textContent = dispState;
      const stateColors = {
        Idle: "#38bdf8",
        Listening: "#fbbf24",
        Thinking: "#c084fc",
        Speaking: "#22c55e",
        Happy: "#22c55e",
        Sad: "#94a3b8",
        Surprised: "#f59e0b",
        Confused: "#f87171",
        Concerned: "#f97316",
      };
      const col = stateColors[dispState] || "#38bdf8";
      elCmdStateVal.style.color = col;
      if (elCmdStateDot) elCmdStateDot.style.color = col;
    }

    // 1. Hardware Hero Connection Pill
    const pill = document.getElementById("companion-conn-pill");
    const pillText = document.getElementById("companion-conn-text");
    if (pill && pillText) {
      if (comp.connected) {
        pill.className = "badge badge-live";
        pillText.textContent = "ONLINE (COM5)";
      } else {
        pill.className = "badge badge-error";
        pillText.textContent = "OFFLINE / DISCONNECTED";
      }
    }

    // 3. Telemetry Strips
    const elPort = document.getElementById("comp-metric-port");
    const elBaud = document.getElementById("comp-metric-baud");
    const elTx = document.getElementById("comp-metric-tx");
    const elRx = document.getElementById("comp-metric-rx");
    if (elPort) elPort.textContent = comp.port || "COM5";
    if (elBaud) elBaud.textContent = Number(comp.baudrate || 921600).toLocaleString();
    if (elTx) elTx.textContent = formatBytes(comp.bytes_sent);
    if (elRx) elRx.textContent = formatBytes(comp.bytes_received);

    const elPkts = document.getElementById("companion-packet-stats");
    if (elPkts) {
      const total = (comp.packets_sent || 0) + (comp.packets_received || 0);
      elPkts.textContent = `${total} PKTS (${comp.packets_sent || 0} TX / ${comp.packets_received || 0} RX)`;
    }

    const elAuthBadge = document.getElementById("comp-auth-status-badge");
    if (elAuthBadge) {
      if (currentUser && (currentUser.role === "ADMIN" || currentUser.is_admin)) {
        elAuthBadge.className = "badge badge-live";
        elAuthBadge.textContent = `ADMIN: ${currentUser.display_name || currentUser.username} (AUTHENTICATED)`;
      } else if (currentUser) {
        elAuthBadge.className = "badge badge-subtle";
        elAuthBadge.textContent = `USER: ${currentUser.display_name || currentUser.username}`;
      } else {
        elAuthBadge.className = "badge badge-error";
        elAuthBadge.textContent = "AUTH: NOT LOGGED IN";
      }
    }

    const elLastSeen = document.getElementById("companion-last-seen");
    if (elLastSeen) {
      elLastSeen.textContent = comp.last_seen_iso
        ? `Last sync: ${formatLocalTime(comp.last_seen_iso)}`
        : "Last sync: Awaiting link";
    }

    // 4. Affective State Card & Avatar
    const stateName = comp.affective_state || comp.state || "Idle";
    const robotState = comp.robot_state || (comp.is_speaking ? "Speaking" : (comp.connected ? "Idle" : "Offline"));
    const stateMeta = COMPANION_STATE_META[stateName] || COMPANION_STATE_META.Idle;
    const stateCard = document.getElementById("companion-state-container");
    const avatarIcon = document.getElementById("companion-avatar-icon");
    const stateLabel = document.getElementById("companion-state-label");
    const robotLabel = document.getElementById("companion-robot-label");
    const stateDesc = document.getElementById("companion-state-description");

    // Update synchronized STATE and ROBOT indicators
    if (stateLabel) {
      stateLabel.textContent = stateName.toUpperCase();
      stateLabel.className = `badge ${stateMeta.badgeClass || 'badge-live'}`;
    }

    if (robotLabel) {
      robotLabel.textContent = robotState.toUpperCase();
      if (comp.is_speaking) {
        robotLabel.className = "badge badge-live";
      } else if (!comp.connected) {
        robotLabel.className = "badge badge-error";
      } else {
        robotLabel.className = "badge badge-subtle";
      }
    }

    if (comp.is_speaking) {
      if (stateCard) stateCard.className = `companion-state-card state-speaking state-${stateName.toLowerCase()}`;
      if (avatarIcon) avatarIcon.textContent = "🗣️";
      if (stateDesc) stateDesc.textContent = "Physical robot is actively speaking over GPIO25 DAC.";
    } else {
      if (stateCard) {
        stateCard.className = `companion-state-card state-${stateName.toLowerCase()}`;
      }
      if (avatarIcon) avatarIcon.textContent = stateMeta.icon;
      if (stateDesc) stateDesc.textContent = stateMeta.desc;
    }

    // Synchronize chat message badges when robot finishes speaking
    if (!comp.is_speaking) {
      document.querySelectorAll(".comp-active-speaking-badge").forEach((el) => {
        el.textContent = "ROBOT: Idle";
        el.className = "badge badge-subtle";
      });
    }

    // Active Task Banner
    const taskWrap = document.getElementById("companion-active-task-wrap");
    const taskText = document.getElementById("companion-active-task-text");
    if (taskWrap && taskText) {
      if (comp.active_task) {
        taskWrap.style.display = "block";
        taskText.textContent = comp.active_task;
      } else {
        taskWrap.style.display = "none";
      }
    }

    // 5. Hardware Serial Trace Terminal
    const term = document.getElementById("companion-terminal-log");
    if (term && Array.isArray(comp.recent_activities) && comp.recent_activities.length > 0) {
      term.innerHTML = comp.recent_activities.map((act) => {
        const dirClass = `dir-${(act.direction || "sys").toLowerCase()}`;
        return `
          <div class="term-entry">
            <span class="term-ts">${formatLocalTime(act.timestamp)}</span>
            <span class="term-dir ${dirClass}">[${escapeHtml(act.direction)}]</span>
            <span class="term-type">${escapeHtml(act.event_type)}</span>
            <span class="term-payload">${escapeHtml(act.payload)}</span>
          </div>
        `;
      }).join("");
      term.scrollTop = term.scrollHeight;
    }
  }

  async function fetchCompanionStatus() {
    if (!currentUser || currentUser.role !== "ADMIN") return;
    if (isFetchingCompanion) return;
    isFetchingCompanion = true;

    try {
      const resp = await fetch("/api/admin/companion/status", {
        headers: { "Accept": "application/json" },
        credentials: "same-origin",
      });
      if (resp.ok) {
        const data = await resp.json();
        renderCompanionStatus(data.companion);
      }
    } catch (err) {
      console.warn("Companion status sync error:", err);
    } finally {
      isFetchingCompanion = false;
    }
  }

  // Diagnostics Refresh button
  const btnCompRefresh = document.getElementById("btn-companion-refresh");
  if (btnCompRefresh) {
    btnCompRefresh.addEventListener("click", () => {
      fetchCompanionStatus();
    });
  }

  // Diagnostics Test Companion button
  const btnTestComp = document.getElementById("btn-test-companion");
  if (btnTestComp) {
    btnTestComp.addEventListener("click", async () => {
      btnTestComp.disabled = true;
      const origContent = btnTestComp.innerHTML;
      btnTestComp.innerHTML = `<span>⏳</span> RUNNING TEST...`;

      try {
        const resp = await fetch("/api/admin/companion/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
        });
        const data = await resp.json();
        if (resp.ok) {
          fetchCompanionStatus();
        } else {
          alert(`Diagnostic test error: ${data.details || data.error}`);
        }
      } catch (err) {
        alert(`Failed to trigger diagnostic test: ${err.message}`);
      } finally {
        btnTestComp.disabled = false;
        btnTestComp.innerHTML = origContent;
      }
    });
  }

  // Diagnostics Servo Test button (90 -> 60 -> 120 -> 90)
  const btnServoTest = document.getElementById("btn-servo-test");
  if (btnServoTest) {
    btnServoTest.addEventListener("click", async () => {
      btnServoTest.disabled = true;
      const origContent = btnServoTest.innerHTML;
      btnServoTest.innerHTML = `<span>⏳</span> SERVO MOVING...`;

      try {
        const resp = await fetch("/api/admin/companion/servo_test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
        });
        const data = await resp.json();
        if (resp.ok) {
          fetchCompanionStatus();
        } else {
          alert(`Servo test error: ${data.details || data.error}`);
        }
      } catch (err) {
        alert(`Failed to trigger servo test: ${err.message}`);
      } finally {
        setTimeout(() => {
          btnServoTest.disabled = false;
          btnServoTest.innerHTML = origContent;
        }, 3000);
      }
    });
  }

  // Direct Voice Synthesis & Streaming Test button
  const btnDirectSpeak = document.getElementById("btn-companion-direct-speak");
  const inputDirectSpeak = document.getElementById("companion-speak-input");
  if (btnDirectSpeak && inputDirectSpeak) {
    btnDirectSpeak.addEventListener("click", async () => {
      const text = inputDirectSpeak.value.trim();
      if (!text) {
        inputDirectSpeak.focus();
        return;
      }
      btnDirectSpeak.disabled = true;
      const origText = btnDirectSpeak.innerHTML;
      btnDirectSpeak.innerHTML = `<span>⏳</span> STREAMING AUDIO...`;

      try {
        const resp = await fetch("/api/admin/companion/speak", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
          credentials: "same-origin",
        });
        const data = await resp.json();
        if (resp.ok) {
          fetchCompanionStatus();
        } else {
          alert(`Direct voice error: ${data.details || data.error}`);
        }
      } catch (err) {
        alert(`Failed to stream voice audio: ${err.message}`);
      } finally {
        btnDirectSpeak.disabled = false;
        btnDirectSpeak.innerHTML = origText;
      }
    });

    inputDirectSpeak.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        btnDirectSpeak.click();
      }
    });
  }

  // Manual Affective State buttons
  document.querySelectorAll("button[data-companion-state]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const targetState = btn.dataset.companionState;
      if (!targetState) return;

      try {
        const resp = await fetch("/api/admin/companion/state", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state: targetState }),
          credentials: "same-origin",
        });
        if (resp.ok) {
          fetchCompanionStatus();
        }
      } catch (err) {
        console.error("Failed to set companion state:", err);
      }
    });
  });

  // Companion Chat Prompt Chips
  document.querySelectorAll("button[data-companion-prompt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const prompt = btn.dataset.companionPrompt;
      const input = document.getElementById("companion-chat-input");
      const form = document.getElementById("companion-chat-form");
      if (input && form && prompt) {
        input.value = prompt;
        form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
      }
    });
  });

  // Format Grounding Context: renders structured, human-readable bullet points (never [object Object])
  function formatGroundingHtml(citations) {
    if (!Array.isArray(citations) || citations.length === 0) return "";

    const items = citations.map((c) => {
      if (typeof c === "string") {
        return `<li>${escapeHtml(c)}</li>`;
      }
      if (c && typeof c === "object") {
        const parts = [];
        if (c.source || c.type) {
          parts.push(`<strong>Source:</strong> ${escapeHtml(c.source || c.type)}`);
        }
        if (c.event || c.label) {
          parts.push(`<strong>Event:</strong> ${escapeHtml(c.event || c.label)}`);
        }
        if (c.time) {
          parts.push(`<strong>Time:</strong> ${escapeHtml(c.time)}`);
        }
        if (c.confidence) {
          parts.push(`<strong>Confidence:</strong> ${escapeHtml(String(c.confidence))}`);
        }
        if (parts.length === 0) {
          const fallbackParts = Object.entries(c)
            .filter(([k, v]) => v !== undefined && v !== null && typeof v !== "object")
            .map(([k, v]) => `<strong>${escapeHtml(k)}:</strong> ${escapeHtml(String(v))}`);
          if (fallbackParts.length > 0) {
            return `<li>${fallbackParts.join(" &bull; ")}</li>`;
          }
          return `<li><strong>Context:</strong> Verified ATLAS observation</li>`;
        }
        return `<li>${parts.join(" &bull; ")}</li>`;
      }
      return `<li>${escapeHtml(String(c))}</li>`;
    }).join("");

    return `
      <div class="companion-grounding-block">
        <div class="companion-grounding-title">
          <span>🔍</span> GROUNDING CONTEXT
        </div>
        <ul class="companion-grounding-list">
          ${items}
        </ul>
      </div>
    `;
  }

  // Companion Chat Form Submit
  const companionChatForm = document.getElementById("companion-chat-form");
  const companionChatInput = document.getElementById("companion-chat-input");
  const companionChatMessages = document.getElementById("companion-chat-messages");
  const btnCompanionChatSend = document.getElementById("btn-companion-chat-send");
  const btnCompanionMic = document.getElementById("btn-companion-mic");
  const micStatusLabel = document.getElementById("companion-mic-status");
  const micIcon = document.getElementById("companion-mic-icon");
  const voiceFeedback = document.getElementById("companion-voice-feedback");

  // Voice Feedback banner helper
  function setVoiceFeedback(msg, isError = false) {
    if (!voiceFeedback) return;
    if (!msg) {
      voiceFeedback.style.display = "none";
      return;
    }
    voiceFeedback.style.display = "block";
    voiceFeedback.className = isError ? "badge badge-error" : "badge badge-live";
    voiceFeedback.style.padding = "6px 10px";
    voiceFeedback.style.fontSize = "11px";
    voiceFeedback.style.fontFamily = "var(--font-mono)";
    voiceFeedback.textContent = msg;
    if (!isError) {
      setTimeout(() => {
        if (voiceFeedback) voiceFeedback.style.display = "none";
      }, 4500);
    }
  }

  // Update Microphone State: IDLE, LISTENING, TRANSCRIBING, SENDING
  function updateMicState(state) {
    if (!btnCompanionMic || !micStatusLabel) return;
    btnCompanionMic.classList.remove("mic-listening", "mic-transcribing", "mic-sending");

    if (state === "LISTENING") {
      btnCompanionMic.classList.add("mic-listening");
      micStatusLabel.textContent = "LISTENING";
      if (micIcon) micIcon.textContent = "🔴";
    } else if (state === "TRANSCRIBING") {
      btnCompanionMic.classList.add("mic-transcribing");
      micStatusLabel.textContent = "TRANSCRIBING";
      if (micIcon) micIcon.textContent = "⏳";
    } else if (state === "SENDING") {
      btnCompanionMic.classList.add("mic-sending");
      micStatusLabel.textContent = "SENDING";
      if (micIcon) micIcon.textContent = "🚀";
    } else {
      micStatusLabel.textContent = "IDLE";
      if (micIcon) micIcon.textContent = "🎙️";
    }
  }

  // Real Browser Speech Recognition (Web Speech API)
  const BrowserSpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let activeRecognition = null;
  let isMicActive = false;

  if (btnCompanionMic) {
    if (!BrowserSpeechRecognition) {
      btnCompanionMic.title = "Speech recognition is not natively supported in this browser. Please use Chrome, Edge, or Chromium.";
      btnCompanionMic.addEventListener("click", () => {
        setVoiceFeedback("Speech recognition is not natively supported in this browser. Please type your message or switch to Chrome/Edge.", true);
      });
    } else {
      btnCompanionMic.addEventListener("click", () => {
        if (isMicActive) {
          if (activeRecognition) {
            try { activeRecognition.stop(); } catch (err) {}
          }
          isMicActive = false;
          updateMicState("IDLE");
          setVoiceFeedback("Microphone deactivated.", false);
          return;
        }

        try {
          activeRecognition = new BrowserSpeechRecognition();
          activeRecognition.continuous = false;
          activeRecognition.interimResults = true;
          activeRecognition.lang = "en-US";

          activeRecognition.onstart = () => {
            isMicActive = true;
            updateMicState("LISTENING");
            setVoiceFeedback("Listening... Speak clearly to ATLAS.", false);
            // Put physical robot into Listening mode immediately
            fetch("/api/admin/companion/state", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ state: "Listening" }),
              credentials: "same-origin",
            }).catch(() => {});
            const elCmdState = document.getElementById("comp-state-val");
            if (elCmdState) {
              elCmdState.textContent = "Listening";
              elCmdState.style.color = "#fbbf24";
            }
          };

          activeRecognition.onspeechstart = () => {
            updateMicState("LISTENING");
          };

          activeRecognition.onspeechend = () => {
            updateMicState("TRANSCRIBING");
          };

          activeRecognition.onresult = (event) => {
            let interimTranscript = "";
            let finalTranscript = "";

            for (let i = event.resultIndex; i < event.results.length; ++i) {
              const res = event.results[i];
              if (res.isFinal) {
                finalTranscript += res[0].transcript;
              } else {
                interimTranscript += res[0].transcript;
              }
            }

            if (companionChatInput) {
              companionChatInput.value = finalTranscript || interimTranscript;
            }

            if (finalTranscript) {
              const cleanFinal = finalTranscript.trim();
              if (cleanFinal && companionChatInput) {
                companionChatInput.value = cleanFinal;
                updateMicState("SENDING");
                setVoiceFeedback(`Sending voice query: "${cleanFinal}"`, false);
                // Dispatch form submission automatically
                setTimeout(() => {
                  updateMicState("IDLE");
                  if (companionChatForm) {
                    companionChatForm.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
                  }
                }, 350);
              }
            }
          };

          activeRecognition.onerror = (event) => {
            isMicActive = false;
            updateMicState("IDLE");
            console.warn("Browser SpeechRecognition error:", event.error);
            if (event.error === "not-allowed" || event.error === "service-not-allowed") {
              setVoiceFeedback("Microphone access was denied. Please allow microphone permissions in browser settings.", true);
            } else if (event.error === "no-speech") {
              setVoiceFeedback("No speech detected. Please click the mic and speak again.", false);
            } else {
              setVoiceFeedback(`Speech recognition error (${event.error}). Please type your message.`, true);
            }
          };

          activeRecognition.onend = () => {
            isMicActive = false;
            updateMicState("IDLE");
          };

          activeRecognition.start();
        } catch (err) {
          isMicActive = false;
          updateMicState("IDLE");
          setVoiceFeedback(`Failed to start microphone: ${err.message}`, true);
        }
      });
    }
  }

  if (companionChatForm && companionChatInput && companionChatMessages) {
    companionChatForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const message = companionChatInput.value.trim();
      if (!message) return;

      // 1. Append User Message with sender YOU
      const userMsgHtml = `
        <div class="chat-message chat-message-user">
          <div class="chat-msg-header">
            <span class="chat-sender">YOU</span>
            <span class="chat-msg-time">${new Date().toLocaleTimeString()}</span>
          </div>
          <div class="chat-msg-body">${escapeHtml(message)}</div>
        </div>
      `;
      companionChatMessages.insertAdjacentHTML("beforeend", userMsgHtml);
      companionChatInput.value = "";

      // 2. Append Assistant Thinking Placeholder
      const placeholderId = `comp-msg-${Date.now()}`;
      const placeholderHtml = `
        <div class="chat-message chat-message-assistant" id="${placeholderId}">
          <div class="chat-msg-header">
            <span class="chat-sender">ATLAS</span>
            <div class="chat-companion-meta" style="display: flex; gap: 8px; align-items: center;">
              <span class="badge badge-loading" style="font-size: 9px;">State: <strong>Thinking</strong></span>
              <span class="badge badge-loading" style="font-size: 9px;">Robot: <strong>Thinking</strong></span>
            </div>
            <span class="chat-msg-time">${new Date().toLocaleTimeString()}</span>
          </div>
          <div class="chat-msg-body" style="color: var(--text-dim); font-style: italic;">
            ATLAS is thinking...
          </div>
        </div>
      `;
      companionChatMessages.insertAdjacentHTML("beforeend", placeholderHtml);
      companionChatMessages.scrollTop = companionChatMessages.scrollHeight;

      // Update Command Interface state strip to Thinking
      const elCmdState = document.getElementById("comp-state-val");
      if (elCmdState) {
        elCmdState.textContent = "Thinking";
        elCmdState.style.color = "#c084fc";
      }

      if (btnCompanionChatSend) btnCompanionChatSend.disabled = true;

      try {
        const resp = await fetch("/api/admin/companion/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message }),
          credentials: "same-origin",
        });

        const data = await resp.json();
        const placeholder = document.getElementById(placeholderId);

        if (resp.ok && data.status === "ok") {
          const rawEmotion = (data.emotion || data.state || "neutral").toLowerCase();
          const isSpeaking = Boolean(data.is_speaking || data.robot_state === "Speaking");
          const robotVal = isSpeaking ? "Speaking" : (data.robot_state || "Idle");
          const robotBadgeClass = isSpeaking ? "badge-live comp-active-speaking-badge" : "badge-subtle";
          const robotColor = isSpeaking ? "#22c55e" : "#94a3b8";

          // Format clean Grounding Context HTML
          const citationsHtml = formatGroundingHtml(data.citations);

          let errorWarningHtml = "";
          if (data.hardware_error) {
            errorWarningHtml += `<div class="badge badge-error" style="display: block; width: fit-content; margin-top: 8px; font-size: 10px;">⚠️ ESP32 DISCONNECTED: ${escapeHtml(data.hardware_error)}</div>`;
          }
          if (data.audio_error) {
            errorWarningHtml += `<div class="badge badge-error" style="display: block; width: fit-content; margin-top: 8px; font-size: 10px;">⚠️ TTS / AUDIO ERROR: ${escapeHtml(data.audio_error)}</div>`;
          }

          const answerText = data.text || data.answer || "I am currently monitoring your home.";

          if (placeholder) {
            placeholder.innerHTML = `
              <div class="chat-msg-header">
                <span class="chat-sender">ATLAS</span>
                <div class="chat-companion-meta" style="display: flex; gap: 8px; align-items: center;">
                  <span class="badge badge-subtle" style="font-size: 9px;">Emotion: <strong style="color: #38bdf8;">${escapeHtml(rawEmotion.toUpperCase())}</strong></span>
                  <span class="badge ${robotBadgeClass}" style="font-size: 9px;">Robot: <strong style="color: ${robotColor};">${escapeHtml(robotVal)}</strong></span>
                  ${data.dispatched_to_hardware ? '<span class="badge badge-live" style="font-size: 9px;">COM5 DISPATCHED</span>' : '<span class="badge badge-error" style="font-size: 9px;">HARDWARE OFFLINE</span>'}
                  ${data.audio_duration_seconds ? `<span class="badge badge-subtle" style="font-size: 9px;">VOICE: ${data.audio_duration_seconds}s</span>` : ''}
                </div>
                <span class="chat-msg-time">${new Date().toLocaleTimeString()}</span>
              </div>
              <div class="chat-msg-body">
                ${escapeHtml(answerText)}
              </div>
              ${citationsHtml}
              ${errorWarningHtml}
            `;
          }

          // Dynamically reflect LLM emotion on companion card widget
          const stateVal = rawEmotion;
          const stateCard = document.getElementById("companion-state-container");
          const stateLabel = document.getElementById("companion-state-label");
          const avatarIcon = document.getElementById("companion-avatar-icon");
          const emotionIconMap = {
            idle: "🤖",
            neutral: "🤖",
            happy: "😊",
            sad: "😢",
            surprised: "😲",
            confused: "🤔",
            thinking: "🧠",
            speaking: "🗣️",
            listening: "🎧",
            concerned: "⚠️",
          };
          if (stateCard) {
            stateCard.className = `companion-state-card state-${stateVal.toLowerCase()}`;
          }
          if (stateLabel) {
            stateLabel.textContent = stateVal.toUpperCase();
          }
          if (avatarIcon) {
            avatarIcon.textContent = emotionIconMap[stateVal.toLowerCase()] || "🤖";
          }
        } else {
          // Failure handling: ATLAS Core failure or API error
          if (placeholder) {
            const errTitle = data.error === "ATLAS_CORE_FAILURE" ? "ATLAS CORE FAILURE" : "COMPANION ERROR";
            placeholder.innerHTML = `
              <div class="chat-msg-header">
                <span class="chat-sender">ATLAS</span>
                <span class="badge badge-error" style="font-size: 9px; margin-left: 6px;">STATE: Confused</span>
                <span class="badge badge-subtle" style="font-size: 9px; margin-left: 6px;">ROBOT: Idle</span>
                <span class="badge badge-error" style="font-size: 9px; margin-left: 6px;">${escapeHtml(errTitle)}</span>
              </div>
              <div class="chat-msg-body" style="color: #f87171;">
                <strong>${escapeHtml(errTitle)}:</strong> ${escapeHtml(data.details || data.error || "Request failed.")}
              </div>
            `;
          }
        }
      } catch (err) {
        const placeholder = document.getElementById(placeholderId);
        if (placeholder) {
          placeholder.innerHTML = `
            <div class="chat-msg-header">
              <span class="chat-sender">ATLAS</span>
              <span class="badge badge-error" style="font-size: 9px; margin-left: 6px;">COMMUNICATION ERROR</span>
              <span class="badge badge-subtle" style="font-size: 9px; margin-left: 6px;">ROBOT: Idle</span>
            </div>
            <div class="chat-msg-body" style="color: #f87171;">
              <strong>NETWORK / API ERROR:</strong> Communication with ATLAS Companion Bridge failed: ${escapeHtml(err.message)}
            </div>
          `;
        }
      } finally {
        if (btnCompanionChatSend) btnCompanionChatSend.disabled = false;
        companionChatMessages.scrollTop = companionChatMessages.scrollHeight;
        fetchCompanionStatus();
      }
    });
  }


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
