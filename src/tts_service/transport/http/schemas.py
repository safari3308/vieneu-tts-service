"""Pydantic request and response schemas for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tts_service.domain import Language, VoiceRole


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text content to synthesize.")
    lang: Language = Field(default=Language.VI, description="Target language (vi or en).")
    role: VoiceRole = Field(default=VoiceRole.A, description="Legacy voice role (A or B). Ignored when voice is set.")
    voice: str | None = Field(default=None, description="Explicit voice identifier (overrides role).")
    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="Speech speed multiplier (0.25 to 4.0).",
    )


class VoiceInfoResponse(BaseModel):
    id: str
    name: str
    languages: list[str]
    gender: str = ""
    description: str = ""


class ListVoicesResponse(BaseModel):
    engine: str
    voices: list[VoiceInfoResponse]
    default_voices: dict[str, str]


class HealthResponse(BaseModel):
    status: str


class ReadinessResponse(BaseModel):
    status: str
    engines_loaded: bool


class ServiceInfoResponse(BaseModel):
    engine: str
    model: str
    sample_rate: int
    native_sample_rate: int
    voices: dict[str, str]
