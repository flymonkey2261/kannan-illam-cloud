from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


MotorName = Literal["SILENT", "RAJA", "RANI"]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class StartRequest(BaseModel):
    motor: MotorName
    durationMinutes: Literal[5, 10, 20, 30]


class StopRequest(BaseModel):
    motor: MotorName


class DisplayRequest(BaseModel):
    mode: Literal["on", "off", "timer"]
    durationMinutes: Literal[5, 15, 30, 60] | None = None


class VoiceDirective(BaseModel):
    assistant: Literal["alexa", "google"]
    action: Literal["start", "stop", "stopAll", "displayOn", "displayOff"]
    motor: MotorName | None = None


class PresenceTelemetry(BaseModel):
    nodeId: str = Field(min_length=1, max_length=96)
    zone: str = Field(default="Unassigned", min_length=1, max_length=96)
    detected: bool
    personCount: int = Field(default=0, ge=0, le=32)
    distance: float | None = Field(default=None, ge=0, le=1000)
    x: float | None = None
    y: float | None = None
    z: float | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    movementState: str = Field(default="unknown", max_length=48)
    lastSeen: datetime | None = None
    source: Literal["ruview-edge-vitals", "ruview-sensing-server", "simulator"] = (
        "ruview-edge-vitals"
    )


class SafetyModeRequest(BaseModel):
    enabled: bool
