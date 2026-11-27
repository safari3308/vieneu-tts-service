"""Transport-agnostic domain types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Language(str, Enum):
    VI = "vi"
    EN = "en"


class VoiceRole(str, Enum):
    A = "A"
    B = "B"


class ReferenceType(str, Enum):
    SUB = "sub"
    AUDIO = "audio"


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    """Encoded WAV synthesis output."""

    audio_wav: bytes
    sample_rate: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class VoiceInfo:
    """Metadata describing an available synthesis voice."""

    id: str
    name: str
    languages: tuple[str, ...]
    gender: str = ""
    description: str = ""
