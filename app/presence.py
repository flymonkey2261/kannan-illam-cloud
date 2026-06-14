from datetime import datetime, timezone
from typing import Any

from .config import settings
from .database import db
from .realtime import hub


def current_presence_state() -> dict[str, Any]:
    state = db.get_presence(settings.presence_node_id)
    if state is None:
        return {
            "nodeId": settings.presence_node_id,
            "zone": "Unassigned",
            "detected": False,
            "personCount": 0,
            "distance": None,
            "x": None,
            "y": None,
            "z": None,
            "confidence": 0.0,
            "movementState": "unknown",
            "lastSeen": None,
            "source": "ruview-edge-vitals",
            "stale": True,
            "online": False,
        }
    try:
        updated_at = datetime.fromisoformat(state["cloudUpdatedAt"])
        age = (datetime.now(timezone.utc) - updated_at).total_seconds()
    except (KeyError, TypeError, ValueError):
        age = settings.presence_stale_seconds + 1
    state["stale"] = age > settings.presence_stale_seconds
    state["online"] = not state["stale"]
    return state


def ingest_presence(payload: dict[str, Any]) -> dict[str, Any]:
    previous = db.get_presence(payload["nodeId"])
    if (
        previous
        and previous.get("lastSeen") == payload.get("lastSeen")
        and previous.get("source") == payload.get("source")
    ):
        return {
            "type": "presence",
            "presence": current_presence_state(),
            "safetyMode": db.safety_mode(),
            "alert": False,
            "duplicate": True,
        }
    rising_detection = payload["detected"] and not (
        previous and previous.get("detected") is True
    )
    db.save_presence(payload["nodeId"], payload)
    safety_mode = db.safety_mode()
    alert = safety_mode and rising_detection
    if alert:
        db.add_presence_event(payload)
    event = {
        "type": "presence",
        "presence": current_presence_state(),
        "safetyMode": safety_mode,
        "alert": alert,
    }
    hub.broadcast_from_thread(event)
    return event
