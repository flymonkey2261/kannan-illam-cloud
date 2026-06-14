import os
import tempfile

os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["ADMIN_PASSWORD"] = "test-password"
os.environ["VOICE_WEBHOOK_SECRET"] = "test-voice-secret"

from fastapi.testclient import TestClient

from app.main import app


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
