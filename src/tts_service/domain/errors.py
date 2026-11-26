"""Transport-agnostic domain exceptions."""

from __future__ import annotations


class TTSServiceError(Exception):
    """Base error for all TTS service failures."""


class ValidationError(TTSServiceError):
    """Raised when caller input is invalid."""


class AuthenticationError(TTSServiceError):
    """Raised when an API token is missing or invalid."""


class EngineNotReadyError(TTSServiceError):
    """Raised when ML engines have not finished loading."""


class SynthesisError(TTSServiceError):
    """Raised when audio synthesis fails."""


class SubsyncError(TTSServiceError):
    """Raised when subtitle synchronization fails."""
