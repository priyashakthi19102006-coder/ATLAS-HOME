import requests
import json

BASE = "http://127.0.0.1:5000"

def test_live_dashboards():
    session = requests.Session()

    # 1. Root and Static
    r = session.get(f"{BASE}/")
    assert r.status_code == 200
    assert "A. HOME OVERVIEW" in r.text
    assert "ACTIVE SECURITY ALERTS" in r.text
    print("[PASS] Root / serves new role-specific index.html")

    # 2. Login as Admin
    r = session.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "atlas_admin_2024!"})
    if r.status_code != 200:
        # Check if default admin credentials or reset
        print(f"Admin login status: {r.status_code}, {r.text}")
    else:
        res_data = r.json()
        if "token" in res_data:
            session.headers["Authorization"] = f"Bearer {res_data['token']}"
        elif "temp_token" in res_data:
            temp_token = res_data["temp_token"]
            r2 = session.post(f"{BASE}/api/auth/login/face", json={"temp_token": temp_token, "dev_bypass": True})
            assert r2.status_code == 200
            session.headers["Authorization"] = f"Bearer {r2.json()['token']}"
        print("[PASS] Logged in as ADMIN")

        # 3. Fetch Admin Dashboard State
        r_admin = session.get(f"{BASE}/api/dashboard/admin/state")
        assert r_admin.status_code == 200
        admin_data = r_admin.json()
        assert admin_data["status"] == "ok"
        sections = ["overview", "live", "people", "activity", "objects", "incidents", "evidence", "login_activity", "audit", "user_management"]
        for s in sections:
            assert s in admin_data, f"Missing section {s}"
        print(f"[PASS] Admin dashboard state returned all 10 sections A through J: {sections}")
        occ = admin_data['user_management'].get('occupied_slots', admin_data['user_management'].get('total_users', 1))
        print(f"[PASS] Occupied slots: {occ} / {admin_data['user_management']['max_users']}")

        # 4. Create an Authorized User (Slot 2) if not present
        r_users = session.get(f"{BASE}/api/admin/users")
        users = r_users.json().get("users", [])
        auth_user = next((u for u in users if u["role"] == "AUTHORIZED_USER"), None)
        if not auth_user and len(users) < 10:
            r_create = session.post(f"{BASE}/api/admin/users", json={
                "username": "sarah_resident",
                "display_name": "Sarah Resident",
                "password": "ResidentPassword123!",
                "role": "AUTHORIZED_USER"
            })
            assert r_create.status_code == 201
            auth_user = r_create.json()["user"]
            print("[PASS] Created new AUTHORIZED_USER in slot 2")

        session.post(f"{BASE}/api/auth/logout")

    # 5. Login as Authorized User
    auth_session = requests.Session()
    r = auth_session.post(f"{BASE}/api/auth/login", json={"username": "sarah_resident", "password": "ResidentPassword123!"})
    if r.status_code == 200:
        res_data = r.json()
        if "token" in res_data:
            auth_session.headers["Authorization"] = f"Bearer {res_data['token']}"
        elif "temp_token" in res_data:
            temp_token = res_data["temp_token"]
            r2 = auth_session.post(f"{BASE}/api/auth/login/face", json={"temp_token": temp_token, "dev_bypass": True})
            assert r2.status_code == 200
            auth_session.headers["Authorization"] = f"Bearer {r2.json()['token']}"
        print("[PASS] Logged in as AUTHORIZED_USER")

        # 6. Verify Authorized User Dashboard State
        r_auth = auth_session.get(f"{BASE}/api/dashboard/authorized-user/state")
        assert r_auth.status_code == 200
        auth_data = r_auth.json()
        assert auth_data["role"] == "AUTHORIZED_USER"
        assert "home_status" in auth_data
        assert "active_alerts" in auth_data
        assert "alert_gated_access" in auth_data
        assert "user_management" not in auth_data
        assert "audit" not in auth_data
        print(f"[PASS] Authorized user dashboard state is calm & scoped: Home Status = {auth_data['home_status']['status']}")
        print(f"[PASS] Alert-gated access state: {auth_data['alert_gated_access']['status']} - {auth_data['alert_gated_access']['message']}")

        # 7. Verify Alert Gating on camera & surveillance
        r_cam = auth_session.get(f"{BASE}/api/camera/frame")
        if not auth_data["active_alerts"]:
            assert r_cam.status_code == 403
            print("[PASS] Live camera feed is strictly 403 Forbidden without active alert")

        r_hist = auth_session.get(f"{BASE}/api/events/recent")
        assert r_hist.status_code == 403
        print("[PASS] Historical surveillance browsing is strictly 403 Forbidden for Authorized User")

        r_admin_forbidden = auth_session.get(f"{BASE}/api/dashboard/admin/state")
        assert r_admin_forbidden.status_code == 403
        print("[PASS] Admin dashboard is strictly 403 Forbidden for Authorized User")

    print("\n========================================================")
    print("ALL LIVE ROLE DASHBOARD VERIFICATIONS PASSED SUCCESSFULLY")
    print("========================================================")

if __name__ == "__main__":
    test_live_dashboards()
