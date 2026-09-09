import json
import sys
import time
import urllib.request
import sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atlas.incidents.schema import Incident, IncidentType, IncidentStatus
from atlas.notifications.service import get_notification_service

def test_live_lifecycle():
    base_url = "http://127.0.0.1:5000"
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    # 1. Authenticate as operator_01
    login_payload = json.dumps({"username": "operator", "password": "operator123"}).encode()
    login_req = urllib.request.Request(f"{base_url}/api/auth/login", data=login_payload, headers={"Content-Type": "application/json"})
    with opener.open(login_req) as resp:
        assert resp.status == 200
        print("[*] Logged in as operator_01")

    # 2. Process HIGH incident through NotificationService
    notif_svc = get_notification_service()
    inc = Incident(
        incident_type=IncidentType.POSSIBLE_FALL,
        severity="HIGH",
        status=IncidentStatus.ACTIVE,
    )
    # Save incident to storage first so FK and lookups succeed
    notif_svc.storage.save_incident(inc)
    
    dispatched = notif_svc.process_incident(inc)
    assert len(dispatched) >= 1
    print(f"[*] Dispatched {len(dispatched)} notifications for HIGH incident {inc.incident_id}")
    for d in dispatched:
        print(f"    - ID={d.notification_id}, To={d.recipient_user_id} ({d.recipient_role}), Channel={d.channel}, Status={d.status}")

    # 3. Query notifications via live API
    req = urllib.request.Request(f"{base_url}/api/notifications?incident_id={inc.incident_id}")
    with opener.open(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode())
        notifs = data.get("notifications", [])
        print(f"[*] GET /api/notifications returned {len(notifs)} records for incident {inc.incident_id}")
        assert len(notifs) >= 1
        operator_notif = next((n for n in notifs if n["recipient_user_id"] == "operator_01"), notifs[0])
        assert operator_notif["status"] == "DELIVERED"
        assert operator_notif["channel"] == "IN_APP"

    # 4. Acknowledge notification via live API as operator_01
    target_id = operator_notif["notification_id"]
    ack_req = urllib.request.Request(
        f"{base_url}/api/notifications/{target_id}/acknowledge",
        data=b"{}",
        headers={"Content-Type": "application/json"}
    )
    with opener.open(ack_req) as resp:
        assert resp.status == 200
        ack_res = json.loads(resp.read().decode())
        print(f"[*] POST /api/notifications/{target_id}/acknowledge -> status={ack_res.get('notification', {}).get('status')}")
        assert ack_res.get("notification", {}).get("status") == "ACKNOWLEDGED"

    # 5. Verify unread count in dashboard state API
    dash_req = urllib.request.Request(f"{base_url}/api/dashboard/state")
    with opener.open(dash_req) as resp:
        dash_data = json.loads(resp.read().decode())
        summ = dash_data.get("notifications_summary", {})
        print(f"[*] GET /api/dashboard/state -> total={summ.get('total_notifications')}, unread={summ.get('unread_count')}")
        assert summ.get("total_notifications") >= 1

    # 6. Verify SQLite audit trail
    conn = sqlite3.connect("data/atlas_events.db")
    cur = conn.cursor()
    cur.execute("SELECT action, actor_id, target_id FROM audit_logs WHERE target_id = ? OR metadata_json LIKE ? ORDER BY created_at DESC", (target_id, f"%{target_id}%"))
    rows = cur.fetchall()
    print(f"[*] Found {len(rows)} audit log entry/entries for notification {target_id}:")
    for r in rows:
        print(f"    - Action: {r[0]}, Actor: {r[1]}, Target: {r[2]}")
    assert any("NOTIFICATION_ACKNOWLEDGED" in r[0] for r in rows)
    conn.close()

    print("[SUCCESS] Live notification lifecycle, API, acknowledge, and audit trail completely verified!")

if __name__ == "__main__":
    test_live_lifecycle()
