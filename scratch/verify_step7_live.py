import json
import sys
import time
import urllib.request
import sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from atlas.config import settings

def test_live_step7():
    time.sleep(2) # Give server time to initialize camera and routes
    
    base_url = "http://127.0.0.1:5000"
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    
    # 1. Probe /api/status (public)
    req = urllib.request.Request(f"{base_url}/api/status")
    with opener.open(req) as resp:
        assert resp.status == 200
        status_data = json.loads(resp.read().decode())
        cam = status_data.get("camera", {})
        fps_val = cam.get("fps") or 0.0
        print(f"[*] /api/status: camera connected={cam.get('is_connected')}, fps={fps_val:.1f}, state={cam.get('state')}")
        assert cam.get("is_connected") is True
        assert cam.get("state") == "connected"

    # Authenticate as operator_01
    login_payload = json.dumps({"username": "operator", "password": "operator123"}).encode()
    login_req = urllib.request.Request(f"{base_url}/api/auth/login", data=login_payload, headers={"Content-Type": "application/json"})
    with opener.open(login_req) as resp:
        assert resp.status == 200
        login_res = json.loads(resp.read().decode())
        print(f"[*] /api/auth/login: logged in as {login_res.get('user', {}).get('actor_id')} (role={login_res.get('user', {}).get('role')})")
        
    # 2. Probe /api/notifications/providers
    req = urllib.request.Request(f"{base_url}/api/notifications/providers")
    with opener.open(req) as resp:
        assert resp.status == 200
        prov_data = json.loads(resp.read().decode())
        providers = prov_data.get("providers", [])
        print(f"[*] /api/notifications/providers: {len(providers)} registered providers:")
        for p in providers:
            print(f"    - {p['channel']}: state={p['state']}, active={p['is_active']}, reason={p.get('reason')}")
        
        # Verify provider truths
        in_app = next(p for p in providers if p["channel"] == "IN_APP")
        assert in_app["state"] == "ACTIVE"
        assert in_app["is_active"] is True
        
        web_push = next(p for p in providers if p["channel"] == "WEB_PUSH")
        assert web_push["state"] == "NOT_CONFIGURED"
        assert web_push["is_active"] is False
        
        email = next(p for p in providers if p["channel"] == "EMAIL")
        assert email["state"] == "NOT_CONFIGURED"
        assert email["is_active"] is False

    # 3. Probe /api/notifications (unread & list)
    req = urllib.request.Request(f"{base_url}/api/notifications")
    with opener.open(req) as resp:
        assert resp.status == 200
        notifs_data = json.loads(resp.read().decode())
        notif_list = notifs_data.get("notifications", [])
        print(f"[*] /api/notifications: returned {len(notif_list)} records")
        assert "notifications" in notifs_data

    # 4. Probe /api/dashboard/state
    req = urllib.request.Request(f"{base_url}/api/dashboard/state")
    with opener.open(req) as resp:
        assert resp.status == 200
        dash_data = json.loads(resp.read().decode())
        notif_summ = dash_data.get("notifications_summary")
        print(f"[*] /api/dashboard/state notifications_summary: {notif_summ}")
        assert notif_summ is not None
        assert "unread_count" in notif_summ
        assert "providers" in notif_summ
        assert "recent_notifications" in notif_summ
        
        # External notifications remain disabled
        auth_state = dash_data.get("authorization_state", {})
        print(f"[*] Escalation state: {auth_state.get('escalation_state')}, human_required: {auth_state.get('human_authorization_required')}")
        assert auth_state.get("escalation_state") in ("NOT_ESCALATED", "ESCALATION_AUTHORIZED")

    # 5. Verify SQLite Schema & Tables
    conn = sqlite3.connect("data/atlas_events.db")
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('notifications', 'escalation_records')")
    tables = [r[0] for r in cur.fetchall()]
    print(f"[*] Verified SQLite tables exist: {tables}")
    assert "notifications" in tables
    assert "escalation_records" in tables
    
    # Check column counts
    cur.execute("PRAGMA table_info(notifications)")
    notif_cols = [r[1] for r in cur.fetchall()]
    assert "notification_id" in notif_cols
    assert "recipient_user_id" in notif_cols
    assert "channel" in notif_cols
    assert "escalation_level" in notif_cols
    assert "status" in notif_cols
    
    cur.execute("PRAGMA table_info(escalation_records)")
    esc_cols = [r[1] for r in cur.fetchall()]
    assert "incident_id" in esc_cols
    assert "current_level" in esc_cols
    assert "is_active" in esc_cols
    conn.close()

    print("[SUCCESS] All Live Step 7 Checks Passed!")

if __name__ == "__main__":
    test_live_step7()
