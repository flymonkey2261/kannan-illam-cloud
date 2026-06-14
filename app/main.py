import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .config import settings
from .database import db
from .models import DisplayRequest, LoginRequest, StartRequest, StopRequest, VoiceDirective
from .mqtt_bridge import mqtt_bridge
from .realtime import hub
from .security import create_access_token, decode_access_token, verify_password


MOTOR_NAMES = {"SILENT", "RAJA", "RANI"}


def current_device_state() -> dict[str, Any] | None:
    state = db.get_state(settings.device_id)
    if state is None:
        return None
    try:
        updated_at = datetime.fromisoformat(state["cloudUpdatedAt"])
        age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
    except (KeyError, TypeError, ValueError):
        age_seconds = settings.device_stale_seconds + 1
    if age_seconds > settings.device_stale_seconds:
        state["online"] = False
        state["cloudConnected"] = False
        state["reason"] = "heartbeat_stale"
    return state


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.initialize()
    hub.bind_loop(asyncio.get_running_loop())
    mqtt_bridge.start()
    yield
    mqtt_bridge.stop()


app = FastAPI(title="KANNAN ILLAM Cloud", version="1.0.0", lifespan=lifespan)


def current_user(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    try:
        return decode_access_token(authorization[7:])
    except jwt.InvalidTokenError as error:
        raise HTTPException(401, "Invalid token") from error


def trusted_voice_service(
    x_voice_secret: str | None = Header(default=None),
) -> None:
    if not x_voice_secret or x_voice_secret != settings.voice_webhook_secret:
        raise HTTPException(401, "Invalid voice integration secret")


def create_command(
    action: str,
    origin: str,
    motor: str | None = None,
    duration_seconds: int | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    if settings.mqtt_enabled:
        if not mqtt_bridge.connected:
            raise HTTPException(503, "Cloud MQTT broker is unavailable")
        device_state = current_device_state()
        if device_state is None or device_state.get("online") is not True:
            raise HTTPException(503, "ESP32 is offline or its heartbeat is stale")
    command = {
        "commandId": str(uuid.uuid4()),
        "deviceId": settings.device_id,
        "action": action,
        "origin": origin,
    }
    if motor is not None:
        command["motor"] = motor
    if duration_seconds is not None:
        command["durationSeconds"] = duration_seconds
    if mode is not None:
        command["mode"] = mode
    db.create_command(command)
    mqtt_bridge.publish_command(command)
    return db.command(command["commandId"]) or command


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "mqttConnected": mqtt_bridge.connected,
        "mqttConfigured": settings.mqtt_enabled,
        "deviceId": settings.device_id,
    }


@app.post("/api/auth/login")
def login(request: LoginRequest) -> dict[str, str]:
    user = db.user_by_email(request.email)
    if user is None or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    return {"accessToken": create_access_token(user["email"]), "tokenType": "bearer"}


@app.get("/api/device/state")
def device_state(user: str = Depends(current_user)) -> dict[str, Any]:
    return {
        "deviceId": settings.device_id,
        "mqttConnected": mqtt_bridge.connected,
        "state": current_device_state(),
    }


@app.post("/api/commands/start", status_code=202)
def start_motor(request: StartRequest, user: str = Depends(current_user)) -> dict[str, Any]:
    return create_command(
        "start", "app", request.motor, request.durationMinutes * 60
    )


@app.post("/api/commands/stop", status_code=202)
def stop_motor(request: StopRequest, user: str = Depends(current_user)) -> dict[str, Any]:
    return create_command("stop", "app", request.motor)


@app.post("/api/commands/stop-all", status_code=202)
def stop_all(user: str = Depends(current_user)) -> dict[str, Any]:
    return create_command("stopAll", "app")


@app.post("/api/commands/display", status_code=202)
def display_command(
    request: DisplayRequest, user: str = Depends(current_user)
) -> dict[str, Any]:
    if request.mode == "timer":
        if request.durationMinutes is None:
            raise HTTPException(400, "Timed display mode requires a duration")
        return create_command(
            "display", "app", duration_seconds=request.durationMinutes * 60, mode="timer"
        )
    if request.durationMinutes is not None:
        raise HTTPException(400, "ON and OFF display modes do not accept a duration")
    return create_command("display", "app", mode=request.mode)


@app.get("/api/commands/{command_id}")
def command_status(command_id: str, user: str = Depends(current_user)) -> dict[str, Any]:
    command = db.command(command_id)
    if command is None:
        raise HTTPException(404, "Command not found")
    return command


@app.post("/integrations/voice/directive", status_code=202)
def voice_directive(
    request: VoiceDirective, _: None = Depends(trusted_voice_service)
) -> dict[str, Any]:
    if request.action in ("start", "stop") and request.motor not in MOTOR_NAMES:
        raise HTTPException(400, "A valid motor is required")
    if request.action == "start":
        return create_command("start", request.assistant, request.motor, 15 * 60)
    if request.action == "stop":
        return create_command("stop", request.assistant, request.motor)
    if request.action == "displayOn":
        return create_command("display", request.assistant, mode="on")
    if request.action == "displayOff":
        return create_command("display", request.assistant, mode="off")
    return create_command("stopAll", request.assistant)


@app.get("/app/latest")
def latest_app(request: Request) -> dict[str, Any]:
    manifest_path = "releases/latest.json"
    try:
        with open(manifest_path, "r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except FileNotFoundError as error:
        raise HTTPException(404, "No app release has been published") from error
    manifest["apkUrl"] = (
        f"{settings.public_base_url.rstrip('/')}/releases/{manifest['fileName']}"
    )
    return manifest


@app.get("/releases/{file_name}")
def download_release(file_name: str) -> FileResponse:
    if "/" in file_name or "\\" in file_name or not file_name.endswith(".apk"):
        raise HTTPException(400, "Invalid release filename")
    return FileResponse(f"releases/{file_name}", media_type="application/vnd.android.package-archive")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str):
    try:
        decode_access_token(token)
    except jwt.InvalidTokenError:
        await websocket.close(code=4401)
        return
    await hub.connect(websocket)
    await websocket.send_json(
        {"type": "state", "state": db.get_state(settings.device_id)}
    )
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(websocket)
