"""Verify real physical camera live stream, ON/OFF control, and HTTP responses on running server."""

import io
import time
import requests
import cv2
import numpy as np

BASE_URL = "http://127.0.0.1:5000"

def test_live_camera():
    print("=" * 60)
    print("STEP 1: Verify Initial Physical Camera Stream (Admin Auth)")
    print("=" * 60)
    headers = {"X-Actor-ID": "admin"}
    
    # 1. Camera Status
    resp = requests.get(f"{BASE_URL}/api/camera/status", headers=headers, timeout=5)
    assert resp.status_code == 200, f"Status failed: {resp.status_code}"
    status = resp.json()
    print("Camera Status:", status)
    assert status["enabled"] is True
    assert status["is_connected"] is True
    print("-> Camera is enabled and connected.")

    # 2. Camera Frame
    resp_frame = requests.get(f"{BASE_URL}/api/camera/frame", headers=headers, timeout=5)
    assert resp_frame.status_code == 200, f"Frame failed: {resp_frame.status_code}"
    assert resp_frame.headers["Content-Type"] == "image/jpeg"
    frame_bytes = resp_frame.content
    print(f"Frame received: {len(frame_bytes)} bytes")
    
    # Decode JPEG to verify real non-uniform pixels
    arr = np.frombuffer(frame_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None, "Failed to decode JPEG"
    h, w, c = img.shape
    mean_val = float(img.mean())
    std_val = float(img.std())
    print(f"Decoded Frame: shape=({h}, {w}, {c}), mean={mean_val:.2f}, std={std_val:.2f}")
    assert w > 0 and h > 0 and std_val > 5.0, "Frame must contain real camera imagery"

    # 3. Test Video Feed MJPEG Stream Chunks
    print("\n" + "=" * 60)
    print("STEP 2: Verify /api/camera/video_feed MJPEG Stream")
    print("=" * 60)
    stream_resp = requests.get(f"{BASE_URL}/api/camera/video_feed", headers=headers, stream=True, timeout=10)
    assert stream_resp.status_code == 200
    assert "multipart/x-mixed-replace; boundary=frame" in stream_resp.headers["Content-Type"]
    print("Response Content-Type:", stream_resp.headers["Content-Type"])
    print("Response Cache-Control:", stream_resp.headers.get("Cache-Control"))
    
    # Read first 3 frames from multipart stream
    chunks_read = 0
    buffer = b""
    for chunk in stream_resp.iter_content(chunk_size=4096):
        buffer += chunk
        if b"--frame" in buffer:
            parts = buffer.split(b"--frame")
            for part in parts[:-1]:
                if b"Content-Type: image/jpeg" in part:
                    header_end = part.find(b"\r\n\r\n")
                    if header_end != -1:
                        headers_part = part[:header_end].decode("ascii", errors="ignore")
                        body_part = part[header_end + 4:].rstrip(b"\r\n")
                        if len(body_part) > 1000:
                            chunks_read += 1
                            print(f"  Received MJPEG Frame Chunk #{chunks_read}: {len(body_part)} bytes | Headers: {headers_part.strip()}")
                            # Verify JPEG header
                            assert body_part[:2] == b"\xff\xd8", "Frame must start with JPEG SOI marker"
            buffer = parts[-1]
        if chunks_read >= 3:
            break
    stream_resp.close()
    assert chunks_read >= 3, "Must receive real MJPEG frame chunks"
    print("-> MJPEG stream delivering real frame chunks with Content-Length.")

    # 4. Turn Camera OFF via Admin Control API
    print("\n" + "=" * 60)
    print("STEP 3: Turn Camera OFF (POST /api/camera/control {enabled: false})")
    print("=" * 60)
    resp_ctrl_off = requests.post(f"{BASE_URL}/api/camera/control", headers=headers, json={"enabled": False}, timeout=5)
    assert resp_ctrl_off.status_code == 200
    ctrl_data = resp_ctrl_off.json()
    print("Control OFF response:", ctrl_data)
    assert ctrl_data["camera_enabled"] is False
    assert ctrl_data["camera_state"] == "off"

    # Verify camera endpoints return 503 CAMERA_OFF
    resp_feed_off = requests.get(f"{BASE_URL}/api/camera/video_feed", headers=headers, timeout=5)
    assert resp_feed_off.status_code == 503
    print("Video feed when OFF -> 503:", resp_feed_off.json())

    resp_frame_off = requests.get(f"{BASE_URL}/api/camera/frame", headers=headers, timeout=5)
    assert resp_frame_off.status_code == 503
    print("Camera frame when OFF -> 503:", resp_frame_off.json())

    # Test Biometric verification fails closed with CAMERA_UNAVAILABLE
    resp_l = requests.post(f"{BASE_URL}/api/auth/login/credentials", json={"username": "admin", "password": "atlas_admin_2024!"}, timeout=5)
    assert resp_l.status_code == 200
    temp_tok = resp_l.json()["temp_token"]
    resp_face_off = requests.post(f"{BASE_URL}/api/auth/login/face", json={"temp_token": temp_tok}, timeout=5)
    assert resp_face_off.status_code == 503
    face_data = resp_face_off.json()
    print("Biometric login when OFF -> 503:", face_data)
    assert face_data["status"] == "CAMERA_UNAVAILABLE"
    assert "Camera is currently OFF" in face_data["details"]

    # Verify Dashboard State reflects truthful subsystems
    resp_state = requests.get(f"{BASE_URL}/api/dashboard/admin/state", headers=headers, timeout=5)
    assert resp_state.status_code == 200
    dash_data = resp_state.json()
    print("Dashboard Subsystems when OFF:", dash_data["overview"]["subsystems"])
    assert dash_data["overview"]["subsystems"]["camera"] == "OFF"
    assert dash_data["overview"]["subsystems"]["perception"] == "OFFLINE"
    assert dash_data["live"]["enabled"] is False
    assert dash_data["live"]["fps"] == 0.0

    # 5. Turn Camera ON via Admin Control API
    print("\n" + "=" * 60)
    print("STEP 4: Turn Camera ON (POST /api/camera/control {enabled: true})")
    print("=" * 60)
    resp_ctrl_on = requests.post(f"{BASE_URL}/api/camera/control", headers=headers, json={"enabled": True}, timeout=5)
    assert resp_ctrl_on.status_code == 200
    print("Control ON response:", resp_ctrl_on.json())

    # Wait 2 seconds for hardware re-acquisition
    time.sleep(2.0)

    resp_feed_on = requests.get(f"{BASE_URL}/api/camera/video_feed", headers=headers, stream=True, timeout=10)
    assert resp_feed_on.status_code == 200
    print("Video feed when ON -> 200 Stream resumed successfully!")
    resp_feed_on.close()

    resp_state_on = requests.get(f"{BASE_URL}/api/dashboard/admin/state", headers=headers, timeout=5)
    assert resp_state_on.status_code == 200
    dash_on = resp_state_on.json()
    print("Dashboard Subsystems when ON:", dash_on["overview"]["subsystems"])
    assert dash_on["overview"]["subsystems"]["camera"] in ("LIVE", "CONNECTING")
    assert dash_on["live"]["enabled"] is True

    print("\n" + "=" * 60)
    print("ALL HARDWARE & BACKEND CAMERA STREAM VERIFICATIONS PASSED!")
    print("=" * 60)

if __name__ == "__main__":
    test_live_camera()
