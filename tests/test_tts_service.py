"""Unit tests for TTSService backed by VieNeu-TTS."""

from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf
from conftest import FakeKokoroPipeline, FakeVieneuEngine

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
    tts_service: TTSService,
    fake_engine: FakeVieneuEngine,
    fake_kokoro: FakeKokoroPipeline,
    test_settings: Settings,
) -> None:
    result = await tts_service.synthesize(
        text="Hello world", lang=Language.EN, role=VoiceRole.A, speed=1.1
    )

    assert result.sample_rate == 48000
    assert fake_kokoro.calls == [("Hello world", test_settings.en_voice_a, 1.1)]
    assert fake_engine.infer_calls == []
    samples, sr = sf.read(io.BytesIO(result.audio_wav))
    assert sr == 48000
    # 12000 samples @ 24kHz resampled to 48kHz -> 24000 samples (0.5s)
    assert len(samples) == 24000


async def test_synthesize_explicit_voice_overrides_role(
    tts_service: TTSService,
    fake_engine: FakeVieneuEngine,
    fake_kokoro: FakeKokoroPipeline,
) -> None:
    # Bilingual VieNeu voice speaking English routes to VieNeu
    result = await tts_service.synthesize(
        text="Custom voice", lang=Language.EN, role=VoiceRole.A, speed=1.0, voice="Thiện Minh"
    )
    assert result.sample_rate == 48000
    assert fake_engine.infer_calls == [("Custom voice", "Thiện Minh", False)]
    assert fake_kokoro.calls == []

    # Explicit Kokoro British voice routes to Kokoro and strips VieNeu emotion tags
    result_uk = await tts_service.synthesize(
        text="Cheerio [chuckle], nice day [sigh]!", lang=Language.EN, voice="bf_emma", speed=1.0
    )
    assert result_uk.sample_rate == 48000
    assert fake_kokoro.calls == [("Cheerio, nice day!", "bf_emma", 1.0)]

    # Tag-only cue sent to Kokoro returns short silence without failing
    result_only_tag = await tts_service.synthesize(
        text="[sigh]", lang=Language.EN, voice="af_heart", speed=1.0
    )
    assert result_only_tag.sample_rate == 48000
    assert result_only_tag.duration_seconds == pytest.approx(0.15, abs=0.01)


def test_list_voices_includes_kokoro_en_and_bilingual_vietnamese_voices(
    tts_service: TTSService,
) -> None:
    all_voices = tts_service.list_voices()
    assert all_voices["engine"] == "vieneu-tts+kokoro"
    assert all_voices["default_voices"]["vi"] == "Hải Đăng"
    assert all_voices["default_voices"]["en"] == "af_heart"

    en_voices = tts_service.list_voices(lang=Language.EN)["voices"]
    vi_voices = tts_service.list_voices(lang=Language.VI)["voices"]
    # English includes both Kokoro EN voices AND bilingual VieNeu voices
    assert any(v.id == "af_heart" and v.languages == ("en",) for v in en_voices)
    assert any(v.id == "Hải Đăng" and "en" in v.languages and "vi" in v.languages for v in en_voices)
    assert len(en_voices) > len(vi_voices)
    # Vietnamese only includes VieNeu voices (not Kokoro EN)
    assert not any(v.id == "af_heart" for v in vi_voices)


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


async def test_enroll_reference_voices_uses_bundled_wavs(test_settings: Settings) -> None:
    enrolled: list[tuple[str, str, bool, str]] = []

    class FakeVoiceEngine:
        sample_rate = 48000

        def add_voice(self, name, ref_audio, *, denoise=True, gender="", save=False):
            enrolled.append((name, str(ref_audio), denoise, gender))
            return name

    ref_settings = test_settings.model_copy(
        update={
            "vi_voice_a_ref_audio": "reference_audio/vn_1.wav",
            "vi_voice_b_ref_audio": "reference_audio/vn_2.wav",
        }
    )
    svc = TTSService(ref_settings, engine=FakeVoiceEngine())
    svc._enroll_reference_voices()

    assert len(enrolled) == 2
    assert enrolled[0][0] == "vn_1"
    assert enrolled[0][1].endswith("reference_audio/vn_1.wav")
    assert enrolled[0][2] is True
    assert enrolled[0][3] == "female"
    assert enrolled[1][0] == "vn_2"
    assert enrolled[1][1].endswith("reference_audio/vn_2.wav")
    assert enrolled[1][3] == "male"

    info = svc.service_info()
    assert info["voices"]["vi_a"] == "vn_1"
    assert info["voices"]["vi_b"] == "vn_2"
