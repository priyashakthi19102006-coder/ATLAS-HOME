"""Camera Diagnostic and Health Verification for ATLAS Home.

Performs:
1. Configuration inspection (primary and fallback candidates)
2. Network probe & HTTP handshake on DroidCam port (4747)
3. Endpoint discovery (/video, /mjpegfeed, /video/force)
4. Real camera connection attempt with timeout bounds
5. Real frame acquisition & validation (width > 0, height > 0)
6. Real frame timestamp progression
7. Clear, actionable troubleshooting instructions
"""

from __future__ import annotations

import socket
import sys
import time
from typing import Any
from urllib.parse import urlparse
import requests

from atlas.camera.base import CameraState
from atlas.camera.stream import CameraStream, parse_and_expand_sources
from atlas.config.settings import get_settings


def probe_network_port(host: str, port: int = 4747, timeout: float = 2.0) -> bool:
    """Test TCP socket reachability on target host and port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        res = sock.connect_ex((host, port))
        return res == 0
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def probe_http_handshake(url: str, timeout: float = 2.5) -> dict[str, Any]:
    """Perform HTTP GET handshake and inspect headers and body."""
    try:
        resp = requests.get(url, timeout=timeout)
        content_type = resp.headers.get("Content-Type", "")
        text = resp.text
        is_busy = "droidcam_busy" in text or "DroidCam is Busy" in text
        is_inactive = "video_inactive" in text

        return {
            "reachable": True,
            "status_code": resp.status_code,
            "content_type": content_type,
            "is_busy": is_busy,
            "is_inactive": is_inactive,
            "preview": text[:120].strip().replace("\n", " "),
        }
    except requests.exceptions.ConnectTimeout:
        return {"reachable": False, "error": "Connection timed out (no response)"}
    except requests.exceptions.ConnectionError as e:
        return {"reachable": False, "error": f"Connection refused or unreachable ({e.__class__.__name__})"}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}


def run_camera_diagnostics(timeout_seconds: float = 6.0) -> dict[str, Any]:
    """Execute diagnostic checks on configured camera sources and network endpoints."""
    settings = get_settings()
    candidates = parse_and_expand_sources(settings.raw_camera_url)

    report: dict[str, Any] = {
        "timestamp": time.time(),
        "configured_source": settings.raw_camera_url,
        "candidates": [str(c) for c in candidates],
        "checks": {
            "1_configuration_exists": {
                "name": "Configuration Exists",
                "passed": False,
                "details": "",
            },
            "2_network_probe": {
                "name": "Network & HTTP Handshake",
                "passed": False,
                "details": "",
                "probes": {},
            },
            "3_camera_connection": {
                "name": "Camera Stream Connection",
                "passed": False,
                "details": "",
            },
            "4_real_frames_received": {
                "name": "Real Frames Received",
                "passed": False,
                "details": "",
            },
            "5_valid_frame_dimensions": {
                "name": "Frame Dimensions Valid",
                "passed": False,
                "details": "",
            },
            "6_frame_timestamps_update": {
                "name": "Frame Timestamps Update",
                "passed": False,
                "details": "",
            },
        },
        "troubleshooting": [],
        "overall_passed": False,
        "metrics": {},
    }

    # Check 1: Configuration
    if bool(settings.raw_camera_url):
        report["checks"]["1_configuration_exists"]["passed"] = True
        report["checks"]["1_configuration_exists"]["details"] = (
            f"Configured source: {settings.raw_camera_url} (Resolved {len(candidates)} candidates)"
        )
    else:
        report["checks"]["1_configuration_exists"]["passed"] = False
        report["checks"]["1_configuration_exists"]["details"] = "ATLAS_CAMERA_URL is empty or undefined."
        report["troubleshooting"].append("Define ATLAS_CAMERA_URL in your .env file.")
        return report

    # Check 2: Network / HTTP Probe for network URLs
    http_candidates = [c for c in candidates if isinstance(c, str) and c.startswith("http")]
    network_ok = False
    busy_detected = False

    if http_candidates:
        hosts_checked = set()
        for cand in http_candidates:
            parsed = urlparse(cand)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 4747

            if host not in hosts_checked:
                hosts_checked.add(host)
                port_open = probe_network_port(host, port)
                report["checks"]["2_network_probe"]["probes"][f"tcp_{host}:{port}"] = {
                    "open": port_open
                }

            handshake = probe_http_handshake(cand)
            report["checks"]["2_network_probe"]["probes"][cand] = handshake

            if handshake.get("reachable"):
                network_ok = True
                if handshake.get("is_busy"):
                    busy_detected = True

        if network_ok:
            if busy_detected:
                report["checks"]["2_network_probe"]["passed"] = False
                report["checks"]["2_network_probe"]["details"] = (
                    "Port 4747 is open, but DroidCam reported 'DroidCam is Busy'."
                )
                report["troubleshooting"].append(
                    "DroidCam stream is BUSY: Another client (or DroidCam Windows client 'droidcam.exe') "
                    "is currently connected to the phone and holding the stream. "
                    "Action: Close or Stop the DroidCam Windows Client if you wish to use direct Wi-Fi HTTP, "
                    "OR set ATLAS_CAMERA_URL=1 or 2 to capture from the DroidCam Virtual Webcam."
                )
            else:
                report["checks"]["2_network_probe"]["passed"] = True
                report["checks"]["2_network_probe"]["details"] = "HTTP handshake succeeded on DroidCam port."
        else:
            report["checks"]["2_network_probe"]["passed"] = False
            report["checks"]["2_network_probe"]["details"] = "Could not reach DroidCam port 4747 on configured IP(s)."
            report["troubleshooting"].append(
                "Verify that your phone and computer are on the same Wi-Fi network and DroidCam is running."
            )
            report["troubleshooting"].append(
                "Check Windows Firewall: Ensure inbound traffic on TCP port 4747 is allowed."
            )
            report["troubleshooting"].append(
                "Router AP Isolation: Some Wi-Fi routers block direct communication between Wi-Fi clients."
            )
    else:
        # Local camera index (e.g. 0, 1)
        report["checks"]["2_network_probe"]["passed"] = True
        report["checks"]["2_network_probe"]["details"] = "Using local hardware camera index."

    # Check 3: Camera connection via OpenCV CameraStream
    stream = CameraStream(source=settings.camera_source, auto_start=True)

    try:
        start_time = time.time()
        connected = False

        while time.time() - start_time < timeout_seconds:
            if stream.connection_state == CameraState.CONNECTED and stream.get_latest_frame() is not None:
                connected = True
                break
            time.sleep(0.3)

        if connected:
            report["checks"]["3_camera_connection"]["passed"] = True
            report["checks"]["3_camera_connection"]["details"] = (
                f"Successfully connected to active stream: {stream.active_source}"
            )
        else:
            err = stream.error_message or "Connection timed out without receiving frames."
            report["checks"]["3_camera_connection"]["passed"] = False
            report["checks"]["3_camera_connection"]["details"] = (
                f"Failed to connect to camera stream. Error: {err}"
            )
            if not report["troubleshooting"]:
                report["troubleshooting"].append(f"Connection failure reason: {err}")
            if "10.178.179.140" in report["configured_source"]:
                report["troubleshooting"].append(
                    "Cellular IP 10.178.179.140: Carrier/mobile cellular IPs cannot be reached over the local Wi-Fi LAN. "
                    "Use the Wi-Fi IP (192.168.1.100) instead."
                )
            return report

        # Check 4 & 5: Real frames & dimensions
        frame_data_1 = stream.get_latest_frame()
        if frame_data_1 is not None:
            frame_1, ts_1 = frame_data_1
            h, w = frame_1.shape[:2]

            report["checks"]["4_real_frames_received"]["passed"] = True
            report["checks"]["4_real_frames_received"]["details"] = (
                f"Successfully acquired real frame ({w}x{h} px, channels={frame_1.shape[-1] if len(frame_1.shape) > 2 else 1})"
            )

            if w > 0 and h > 0:
                report["checks"]["5_valid_frame_dimensions"]["passed"] = True
                report["checks"]["5_valid_frame_dimensions"]["details"] = f"Valid resolution: {w}x{h} px"
            else:
                report["checks"]["5_valid_frame_dimensions"]["passed"] = False
                report["checks"]["5_valid_frame_dimensions"]["details"] = f"Invalid dimensions: {w}x{h}"
        else:
            report["checks"]["4_real_frames_received"]["passed"] = False
            report["checks"]["4_real_frames_received"]["details"] = "No frame returned from get_latest_frame()."
            return report

        # Check 6: Frame timestamp advancement
        ts_updated = False
        wait_start = time.time()
        while time.time() - wait_start < 2.5:
            frame_data_2 = stream.get_latest_frame()
            if frame_data_2 is not None:
                _, ts_2 = frame_data_2
                if ts_2 > ts_1:
                    ts_updated = True
                    report["checks"]["6_frame_timestamps_update"]["passed"] = True
                    report["checks"]["6_frame_timestamps_update"]["details"] = (
                        f"Timestamps advancing: initial={ts_1:.4f}, next={ts_2:.4f} (Δ={ts_2 - ts_1:.4f}s)"
                    )
                    break
            time.sleep(0.1)

        if not ts_updated:
            report["checks"]["6_frame_timestamps_update"]["passed"] = False
            report["checks"]["6_frame_timestamps_update"]["details"] = "Frame timestamps did not advance; feed stalled."

        report["metrics"] = stream.get_status()
        report["overall_passed"] = all(check["passed"] for check in report["checks"].values())

    finally:
        stream.stop()

    return report


def main() -> int:
    print("=" * 68)
    print("ATLAS HOME — CAMERA DIAGNOSTIC & NETWORK PROBE SUITE")
    print("=" * 68)

    report = run_camera_diagnostics(timeout_seconds=5.0)

    print(f"\n[CONFIGURED SOURCE]: {report['configured_source']}")
    print(f"[CANDIDATES EXPANDED]: {', '.join(report['candidates'])}")
    print("-" * 68)

    # Print Network Probes
    probes = report["checks"]["2_network_probe"].get("probes", {})
    if probes:
        print("NETWORK PROBES:")
        for target, data in probes.items():
            if target.startswith("tcp_"):
                status_str = "OPEN (Reachable)" if data.get("open") else "CLOSED / TIMEOUT (Unreachable)"
                print(f"  * TCP Handshake {target[4:]}: {status_str}")
            else:
                if data.get("reachable"):
                    busy_str = " [BUSY DETECTED]" if data.get("is_busy") else ""
                    print(f"  * HTTP GET {target} -> Status {data.get('status_code')} ({data.get('content_type')}){busy_str}")
                else:
                    print(f"  * HTTP GET {target} -> FAILED: {data.get('error')}")
        print("-" * 68)

    # Print Checks
    print("VERIFICATION CHECKS:")
    for key, check in report["checks"].items():
        status_icon = "[PASS]" if check["passed"] else "[FAIL]"
        print(f"  {status_icon} {check['name']}: {check['details']}")

    print("-" * 68)
    if report["overall_passed"]:
        print("RESULT: ALL CAMERA DIAGNOSTICS PASSED (Real stream active & receiving frames).")
        return 0
    else:
        print("RESULT: DIAGNOSTIC FAILED OR BLOCKED (Zero-Mock Enforced)")
        if report["troubleshooting"]:
            print("\nACTIONABLE TROUBLESHOOTING:")
            for step in report["troubleshooting"]:
                print(f"  -> {step}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
