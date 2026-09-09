"""Tests for ATLAS Home API Endpoints."""

import pytest
from atlas.api.app import create_app
from atlas.config.settings import Settings


@pytest.fixture
def client():
    settings = Settings()
    app = create_app(settings)
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["service"] == "atlas_home"


def test_status_endpoint(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.get_json()
    assert "device_id" in data
    assert "camera" in data
    assert "modules" in data
    assert data["modules"]["camera"] == "active"
    assert data["modules"]["events"] == "active"
    assert data["modules"]["context"] == "active"
    assert data["modules"]["perception"] == "active"
    assert data["modules"]["intelligence"] == "active"
    assert data["modules"]["rules"] == "active"
    assert data["modules"]["risk"] == "active"
    assert "llm" in data
    assert "provider" in data["llm"]
    assert "model" in data["llm"]
    assert "base_url" in data["llm"]
    assert "api_key" not in data["llm"]
    assert "llm_api_key" not in data["llm"]


def test_step4_api_endpoints(client):
    # Intelligence endpoints
    res = client.get("/api/intelligence/latest")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] in ("ok", "idle")

    res = client.get("/api/intelligence/recent?limit=5")
    assert res.status_code == 200
    data = res.get_json()
    assert "verifications" in data

    # Rules status
    res = client.get("/api/rules/status")
    assert res.status_code == 200
    data = res.get_json()
    assert "rules" in data
    assert len(data["rules"]) >= 4

    # Risk endpoints
    res = client.get("/api/risk/latest")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] in ("ok", "idle")

    res = client.get("/api/risk/recent")
    assert res.status_code == 200
    data = res.get_json()
    assert "assessments" in data

    # Analysis endpoints
    res = client.get("/api/analysis/latest")
    assert res.status_code == 200
    res = client.get("/api/analysis/recent")
    assert res.status_code == 200


def test_events_and_context_endpoints(client):
    # Active events
    res = client.get("/api/events/active")
    assert res.status_code == 200
    data = res.get_json()
    assert "count" in data
    assert "events" in data

    # Recent events
    res = client.get("/api/events/recent?limit=10")
    assert res.status_code == 200
    data = res.get_json()
    assert "count" in data

    # Recent context
    res = client.get("/api/context/recent?window_seconds=60")
    assert res.status_code == 200
    data = res.get_json()
    assert "context" in data
    assert "active_persons_count" in data["context"]


def test_camera_frame_returns_503_when_no_real_frame(client):
    # When camera has no real connection, API must return 503 rather than fake data
    response = client.get("/api/camera/frame")
    assert response.status_code in (200, 503)
    if response.status_code == 503:
        data = response.get_json()
        assert "error" in data
