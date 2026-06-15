import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["ADMIN_PASSWORD"] = "test-password"
os.environ["VOICE_WEBHOOK_SECRET"] = "test-voice-secret"
os.environ["PRESENCE_INGEST_SECRET"] = "test-presence-secret"

from fastapi.testclient import TestClient

from app.database import db
from app.main import app, current_device_state


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@kannan-illam.local", "password": "test-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


def test_rejects_non_whitelisted_app_duration() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/commands/start",
            headers=auth_headers(client),
            json={"motor": "SILENT", "durationMinutes": 15},
        )
        assert response.status_code == 422


def test_voice_start_is_fixed_to_fifteen_minutes() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/integrations/voice/directive",
            headers={"X-Voice-Secret": "test-voice-secret"},
            json={"assistant": "alexa", "action": "start", "motor": "RAJA"},
        )
        assert response.status_code == 202
        assert response.json()["duration_seconds"] == 900


def test_display_permanent_on_is_separate_from_motor_start() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/commands/display",
            headers=auth_headers(client),
            json={"mode": "on"},
        )
        assert response.status_code == 202
        assert response.json()["action"] == "display"
        assert response.json()["mode"] == "on"


def test_stale_heartbeat_is_reported_offline() -> None:
    db.save_state(
        "kannan-illam-esp32-01",
        {"online": True, "cloudConnected": True, "motors": []},
    )
    stale_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with db.lock:
        db.connection.execute(
            "UPDATE device_state SET updated_at=? WHERE device_id=?",
            (stale_at, "kannan-illam-esp32-01"),
        )
        db.connection.commit()

    state = current_device_state()
    assert state is not None
    assert state["online"] is False
    assert state["cloudConnected"] is False
    assert state["reason"] == "heartbeat_stale"


def test_presence_map_works_with_safety_off_without_event_logging() -> None:
    with TestClient(app) as client:
        headers = auth_headers(client)
        telemetry = {
            "nodeId": "kannan-illam-presence-01",
            "zone": "EB Panel",
            "detected": True,
            "personCount": 1,
            "distance": 2.4,
            "x": 1.0,
            "y": 0.0,
            "z": 2.1,
            "confidence": 0.91,
            "movementState": "moving",
            "source": "simulator",
        }
        response = client.post(
            "/api/presence/telemetry",
            headers={"X-Presence-Secret": "test-presence-secret"},
            json=telemetry,
        )
        assert response.status_code == 202
        state = client.get("/api/presence/state", headers=headers).json()
        assert state["safetyMode"] is False
        assert state["presence"]["detected"] is True
        assert state["presence"]["zone"] == "EB Panel"
        assert client.get("/api/presence/events", headers=headers).json()["events"] == []


def test_safety_mode_logs_only_detection_transition() -> None:
    with TestClient(app) as client:
        headers = auth_headers(client)
        assert client.put(
            "/api/presence/safety-mode",
            headers=headers,
            json={"enabled": True},
        ).status_code == 200
        clear = {
            "nodeId": "kannan-illam-presence-01",
            "zone": "Front Door",
            "detected": False,
            "personCount": 0,
            "confidence": 0.1,
            "movementState": "clear",
            "source": "simulator",
        }
        detected = {
            **clear,
            "detected": True,
            "personCount": 1,
            "confidence": 0.88,
            "movementState": "moving",
        }
        ingest_headers = {"X-Presence-Secret": "test-presence-secret"}
        client.post("/api/presence/telemetry", headers=ingest_headers, json=clear)
        first = client.post(
            "/api/presence/telemetry", headers=ingest_headers, json=detected
        )
        second = client.post(
            "/api/presence/telemetry", headers=ingest_headers, json=detected
        )
        assert first.json()["alert"] is True
        assert second.json()["alert"] is False
        events = client.get("/api/presence/events", headers=headers).json()["events"]
        assert len(events) == 1


def test_accepts_limited_single_esp_experiment_telemetry() -> None:
    with TestClient(app) as client:
        headers = auth_headers(client)
        response = client.post(
            "/api/presence/telemetry",
            headers={"X-Presence-Secret": "test-presence-secret"},
            json={
                "nodeId": "kannan-illam-presence-01",
                "zone": "Motor Controller",
                "detected": False,
                "personCount": 0,
                "confidence": 0.18,
                "motionScore": 0.4,
                "rssi": -58,
                "movementState": "clear",
                "source": "single-esp-experiment",
            },
        )
        assert response.status_code == 202
        state = client.get("/api/presence/state", headers=headers).json()["presence"]
        assert state["source"] == "single-esp-experiment"
        assert state["motionScore"] == 0.4
        assert state["rssi"] == -58
        assert state["x"] is None
