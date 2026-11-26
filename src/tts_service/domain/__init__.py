"""Domain models and exceptions shared across transports."""

from .errors import (
    AuthenticationError,
    EngineNotReadyError,
    SubsyncError,
    SynthesisError,
    TTSServiceError,
    ValidationError,
)
from .models import Language, ReferenceType, SynthesisResult, VoiceRole

__all__ = [
    "AuthenticationError",
    "EngineNotReadyError",
    "Language",
    "ReferenceType",
    "SubsyncError",
    "SynthesisError",
    "SynthesisResult",
    "TTSServiceError",
    "ValidationError",
    "VoiceRole",
]
