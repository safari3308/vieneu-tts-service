"""Shared pytest fixtures and lightweight fake VieNeu-TTS engine."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from tts_service.config import Settings
from tts_service.services import SubsyncService, TTSService


class FakeVieneuEngine:
    """Lightweight test double matching the VieNeu-TTS SDK surface."""

    def __init__(self, sample_rate: int = 48000) -> None:
        self.sample_rate = sample_rate
        self.infer_calls: list[tuple[str, str | None, bool]] = []
        self.stream_calls: list[tuple[str, str | None, bool]] = []

    def infer(
        self,
        text: str,
        voice: str | None = None,
        apply_watermark: bool = True,
    ) -> np.ndarray:
        self.infer_calls.append((text, voice, apply_watermark))
        # 0.5 seconds of sine wave at native sample rate
        n_samples = self.sample_rate // 2
        t = np.linspace(0, 0.5, n_samples, endpoint=False, dtype=np.float32)
        return 0.5 * np.sin(2.0 * np.pi * 440.0 * t)

    def infer_stream(
        self,
        text: str,
        voice: str | None = None,
        apply_watermark: bool = True,
    ) -> Iterator[np.ndarray]:
        self.stream_calls.append((text, voice, apply_watermark))
        chunk_len = self.sample_rate // 4  # 0.25s per chunk
        yield np.full(chunk_len, 0.25, dtype=np.float32)
        yield np.full(chunk_len, -0.25, dtype=np.float32)


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        api_token="test-secret-token",
        device="cpu",
        sample_rate=48000,
        peak_target=0.95,
        grpc_reflection=True,
        vieneu_watermark=False,
    )


@pytest.fixture
def fake_engine() -> FakeVieneuEngine:
    return FakeVieneuEngine(sample_rate=48000)


class FakeKokoroPipeline:
    """Lightweight test double matching Kokoro KPipeline(text, voice, speed)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []

    def __call__(
        self, text: str, voice: str = "af_heart", speed: float = 1.0
    ) -> Iterator[tuple[str, str, np.ndarray]]:
        self.calls.append((text, voice, speed))
        # 2 chunks of 0.25s at 24kHz (6000 samples each -> 12000 samples @ 48kHz after resample)
        yield ("gs", "ps", np.full(6000, 0.25, dtype=np.float32))
        yield ("gs", "ps", np.full(6000, -0.25, dtype=np.float32))


@pytest.fixture
def fake_kokoro() -> FakeKokoroPipeline:
    return FakeKokoroPipeline()


@pytest.fixture
def tts_service(
    test_settings: Settings,
    fake_engine: FakeVieneuEngine,
    fake_kokoro: FakeKokoroPipeline,
) -> TTSService:
    return TTSService(
        test_settings,
        engine=fake_engine,
        kokoro_engines={"en_a": fake_kokoro, "en_b": fake_kokoro},
    )


class FakeSubsyncService(SubsyncService):
    """SubsyncService double that returns predictable synced SRT bytes."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[tuple[str, int, int]] = []

    async def sync_files(
        self, target_path: Path, ref_path: Path, out_path: Path
    ) -> bytes:
        target_data = target_path.read_bytes()
        ref_data = ref_path.read_bytes()
        self.calls.append((ref_path.suffix, len(target_data), len(ref_data)))
        synced = b"1\n00:00:01,000 --> 00:00:02,000\nSynced line\n"
        out_path.write_bytes(synced)
        return synced


@pytest.fixture
def subsync_service(test_settings: Settings) -> FakeSubsyncService:
    return FakeSubsyncService(test_settings)
