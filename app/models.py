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
