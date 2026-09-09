import urllib.request
import json

def test_live_cleanup():
    # 1. Probe /api/status
    resp = urllib.request.urlopen("http://127.0.0.1:5000/api/status")
    status = json.loads(resp.read().decode())
    cam = status.get("camera", {})
    fps = cam.get("fps") or 0.0
    print(f"[*] Camera connected: {cam.get('is_connected')}, FPS: {fps:.1f}")

    # 2. Probe GET / (index.html)
    html = urllib.request.urlopen("http://127.0.0.1:5000/").read().decode()
    for term in ["2b. sources", "fusion", "drone", "glasses", "robot", "imu", "wearable"]:
        assert term not in html.lower(), f"Found {term} in live index.html!"
    print("[*] Live index.html: zero banned terms verified!")

    # 3. Probe GET /app.js
    js = urllib.request.urlopen("http://127.0.0.1:5000/app.js").read().decode()
    for term in ["fusion", "drone", "glasses", "robot"]:
        assert term not in js.lower(), f"Found {term} in live app.js!"
    assert "fs." not in js, "Found fs. in live app.js!"
    print("[*] Live app.js: zero banned terms and zero fs. verified!")

    # 4. Probe GET /api/dashboard/state
    dash = json.loads(urllib.request.urlopen("http://127.0.0.1:5000/api/dashboard/state").read().decode())
    print(f"[*] Dashboard state: active_incidents={len(dash.get('active_incidents', []))}, people={dash.get('active_people_count')}, objects={dash.get('active_object_count')}")
    print("[SUCCESS] All live cleanup verifications passed!")

if __name__ == "__main__":
    test_live_cleanup()
