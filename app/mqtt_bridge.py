import json
import ssl
import threading
from typing import Any

import paho.mqtt.client as mqtt

from .config import settings
from .database import db
from .models import PresenceTelemetry
from .realtime import hub
from .presence import ingest_presence


class MqttBridge:
    def __init__(self) -> None:
        self.connected = False
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="kannan-illam-backend",
            protocol=mqtt.MQTTv311,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.timeout_timers: dict[str, threading.Timer] = {}

    def start(self) -> None:
        if not settings.mqtt_enabled:
            return
        self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        if settings.mqtt_enabled:
            self.client.loop_stop()
            self.client.disconnect()

    def topic(self, device_id: str, suffix: str) -> str:
        return f"{settings.mqtt_topic_root}/{device_id}/{suffix}"

    def publish_command(self, command: dict[str, Any]) -> bool:
        if not self.connected:
            db.update_command(command["commandId"], "failed", "MQTT broker unavailable")
            hub.broadcast_from_thread(
                {"type": "command", "command": db.command(command["commandId"])}
            )
            return False
        topic = self.topic(command["deviceId"], f"command/{command['action']}")
        info = self.client.publish(topic, json.dumps(command), qos=1, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            db.update_command(command["commandId"], "failed", mqtt.error_string(info.rc))
            return False
        timer = threading.Timer(
            settings.command_timeout_seconds,
            self._timeout_command,
            args=(command["commandId"],),
        )
        self.timeout_timers[command["commandId"]] = timer
        timer.start()
        return True

    def _timeout_command(self, command_id: str) -> None:
        command = db.command(command_id)
        if command and command["status"] == "pending":
            db.update_command(command_id, "failed", "ESP32 acknowledgement timeout")
            hub.broadcast_from_thread(
                {"type": "command", "command": db.command(command_id)}
            )
        self.timeout_timers.pop(command_id, None)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected = reason_code == 0
        if self.connected:
            client.subscribe(
                self.topic(settings.device_id, "status/#"), qos=1
            )
            client.subscribe(
                f"{settings.mqtt_topic_root}/presence/+/telemetry", qos=1
            )

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self.connected = False

    def _on_message(self, client, userdata, message) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return

        presence_prefix = f"{settings.mqtt_topic_root}/presence/"
        if (
            message.topic.startswith(presence_prefix)
            and message.topic.endswith("/telemetry")
        ):
            node_id = message.topic[len(presence_prefix) : -len("/telemetry")]
            if payload.get("nodeId") != node_id:
                return
            try:
                telemetry = PresenceTelemetry.model_validate(payload)
            except ValueError:
                return
            if telemetry.nodeId != settings.presence_node_id:
                return
            normalized = telemetry.model_dump(mode="json")
            if telemetry.lastSeen is None:
                return
            normalized["lastSeen"] = telemetry.lastSeen.isoformat()
            ingest_presence(normalized)
            return

        suffix = message.topic.split(f"{settings.device_id}/", 1)[-1]
        if suffix in ("status/motors", "status/device", "status/display"):
            current = db.get_state(settings.device_id) or {}
            if suffix == "status/display":
                current["display"] = payload
                current["online"] = True
                current["cloudConnected"] = True
            elif suffix == "status/motors":
                current.update(payload)
                current["online"] = True
                current["cloudConnected"] = True
            else:
                current.update(payload)
            db.save_state(settings.device_id, current)
            hub.broadcast_from_thread({"type": "state", "state": current})
            return

        if suffix == "status/ack":
            command_id = payload.get("commandId")
            if not isinstance(command_id, str):
                return
            status = "acknowledged" if payload.get("ok") is True else "failed"
            db.update_command(command_id, status, payload.get("error"))
            timer = self.timeout_timers.pop(command_id, None)
            if timer:
                timer.cancel()
            hub.broadcast_from_thread(
                {"type": "command", "command": db.command(command_id)}
            )


mqtt_bridge = MqttBridge()
