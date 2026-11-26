"""Unit tests for TTSService backed by VieNeu-TTS."""

from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf
from conftest import FakeVieneuEngine

from tts_service.config import Settings
from tts_service.domain import EngineNotReadyError, Language, ValidationError, VoiceRole
from tts_service.services import TTSService


async def test_synthesize_vietnamese_wav(
    tts_service: TTSService, fake_engine: FakeVieneuEngine
) -> None:
    result = await tts_service.synthesize(
        text="Xin chao", lang=Language.VI, role=VoiceRole.B, speed=1.0
    )

    assert result.sample_rate == 48000
    assert result.duration_seconds == pytest.approx(0.5, abs=0.01)
    assert fake_engine.infer_calls == [("Xin chao", "Ngọc Huyền", False)]

    samples, sr = sf.read(io.BytesIO(result.audio_wav))
    assert sr == 48000
    assert len(samples) == 24000
    assert float(np.max(np.abs(samples))) == pytest.approx(0.95, abs=0.02)


async def test_synthesize_english_wav(
    tts_service: TTSService, fake_engine: FakeVieneuEngine
) -> None:
    result = await tts_service.synthesize(
        text="Hello world", lang=Language.EN, role=VoiceRole.A, speed=1.0
    )

    assert result.sample_rate == 48000
    assert fake_engine.infer_calls == [("Hello world", "Thiện Minh", False)]
    samples, sr = sf.read(io.BytesIO(result.audio_wav))
    assert sr == 48000
    assert len(samples) == 24000


async def test_synthesize_resamples_when_target_rate_differs() -> None:
    settings = Settings(
        api_token="test-secret-token",
        device="cpu",
        sample_rate=24000,
        peak_target=0.95,
    )
    engine = FakeVieneuEngine(sample_rate=48000)
    service = TTSService(settings, engine=engine)

    result = await service.synthesize(text="Resample test", lang=Language.VI, role=VoiceRole.A)
    assert result.sample_rate == 24000
    assert result.duration_seconds == pytest.approx(0.5, abs=0.01)

    samples, sr = sf.read(io.BytesIO(result.audio_wav))
    assert sr == 24000
    assert len(samples) == 12000


async def test_synthesize_stream_yields_multiple_chunks(
    tts_service: TTSService, fake_engine: FakeVieneuEngine
) -> None:
    chunks = [
        chunk
        async for chunk in tts_service.synthesize_stream(
            text="Streaming test", lang=Language.VI, role=VoiceRole.B, speed=1.0
        )
    ]
    assert len(chunks) == 2
    assert fake_engine.stream_calls == [("Streaming test", "Ngọc Huyền", False)]
    # Each chunk is 12000 int16 samples (0.25s @ 48kHz) -> 24000 bytes
    assert all(len(c) == 24000 for c in chunks)


async def test_synthesize_validation_errors(tts_service: TTSService) -> None:
    with pytest.raises(ValidationError):
        await tts_service.synthesize(text="   ")

    with pytest.raises(ValidationError):
        await tts_service.synthesize(text="hello", speed=10.0)


async def test_synthesize_when_not_ready(test_settings: Settings) -> None:
    unloaded = TTSService(test_settings)
    with pytest.raises(EngineNotReadyError):
        await unloaded.synthesize(text="hello")
