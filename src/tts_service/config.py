"""Application configuration loaded from environment variables."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed service configuration via environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Authentication
    api_token: str = Field(
        default="my_super_secret_token_123",
        description="Bearer token required by HTTP and gRPC clients.",
    )

    # Network bindings
    http_host: str = Field(default="0.0.0.0")
    http_port: int = Field(default=8000, ge=1, le=65535)
    grpc_host: str = Field(default="0.0.0.0")
    grpc_port: int = Field(default=50051, ge=1, le=65535)
    enable_http: bool = Field(default=True)
    enable_grpc: bool = Field(default=True)
    grpc_reflection: bool = Field(default=True)
    grpc_max_message_mb: int = Field(default=100, ge=4, le=1024)

    # Runtime & VieNeu-TTS engine configuration
    device: Literal["auto", "cuda", "cpu"] = Field(default="auto")
    vieneu_mode: Literal["v3turbo", "v3nano"] = Field(default="v3turbo")
    vieneu_backend: Literal["auto", "onnx", "pytorch"] = Field(default="auto")
    vieneu_precision: Literal["fp32", "int8"] = Field(default="fp32")
    vieneu_backbone_repo: str | None = Field(default=None)
    vieneu_onnx_dir: str | None = Field(default=None)
    vieneu_threads: int = Field(default=0, ge=0)
    vieneu_max_streams: int = Field(default=16, ge=1, le=64)
    vieneu_watermark: bool = Field(default=False)
    vieneu_warmup: bool = Field(default=True)

    # Audio & concurrency settings
    sample_rate: int = Field(default=48000, ge=8000, le=96000)
    peak_target: float = Field(default=0.95, gt=0.0, le=1.0)
    max_concurrent_tts: int = Field(default=2, ge=1, le=32)
    max_concurrent_subsync: int = Field(default=2, ge=1, le=16)
    subsync_timeout_seconds: float = Field(default=300.0, gt=1.0)

    # Voice mappings (VieNeu-TTS preset voice names)
    vi_voice_a: str = Field(default="Hải Đăng")
    vi_voice_b: str = Field(default="Mai Anh")
    en_voice_a: str = Field(default="Hải Đăng")
    en_voice_b: str = Field(default="Mai Anh")

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")

    @property
    def grpc_max_message_bytes(self) -> int:
        return self.grpc_max_message_mb * 1024 * 1024

    def resolve_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"
