"""VieNeu-TTS neural speech synthesis service."""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import soundfile as sf

from tts_service.config import Settings
from tts_service.domain import (
    EngineNotReadyError,
    Language,
    SynthesisError,
    SynthesisResult,
    ValidationError,
    VoiceRole,
)

logger = logging.getLogger(__name__)

MIN_SPEED = 0.25
MAX_SPEED = 4.0
DEFAULT_VIENEU_SAMPLE_RATE = 48000


def normalize_audio(speech: np.ndarray, peak_target: float = 0.95) -> np.ndarray:
    """Peak-normalize a float waveform to [-peak_target, peak_target]."""
    arr = np.asarray(speech, dtype=np.float32)
    peak = float(np.max(np.abs(arr))) if arr.size > 0 else 0.0
    if peak > 0.0:
        arr = arr * (peak_target / peak)
    return arr


def resample_audio(speech: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample a 1-D float32 waveform from orig_sr to target_sr."""
    arr = np.asarray(speech, dtype=np.float32)
    if orig_sr == target_sr or arr.size == 0:
        return arr
    try:
        import soxr

        return np.asarray(soxr.resample(arr, orig_sr, target_sr), dtype=np.float32)
    except ImportError:
        new_len = max(1, int(round(arr.size * target_sr / orig_sr)))
        x_old = np.linspace(0.0, 1.0, arr.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, new_len, endpoint=False)
        return np.interp(x_new, x_old, arr).astype(np.float32)


def adjust_speed(speech: np.ndarray, speed: float) -> np.ndarray:
    """Time-stretch a float32 waveform without changing pitch when speed != 1.0."""
    arr = np.asarray(speech, dtype=np.float32)
    if abs(speed - 1.0) <= 1e-3 or arr.size == 0:
        return arr
    try:
        import librosa

        stretched = librosa.effects.time_stretch(y=arr, rate=float(speed))
        return np.asarray(stretched, dtype=np.float32)
    except ImportError:
        logger.warning(
            "librosa is not installed; ignoring speed=%.2f and returning native pacing",
            speed,
        )
        return arr


def encode_wav(speech: np.ndarray, sample_rate: int) -> bytes:
    """Encode a float waveform into a 16-bit PCM WAV byte payload."""
    buf = io.BytesIO()
    sf.write(buf, speech, sample_rate, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def float_to_pcm16(speech: np.ndarray) -> bytes:
    """Convert float32 samples in [-1, 1] to little-endian int16 PCM bytes."""
    clipped = np.clip(np.asarray(speech, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    return pcm.tobytes()


class TTSService:
    """Manages the VieNeu-TTS engine and executes bounded concurrent synthesis."""

    def __init__(
        self,
        settings: Settings,
        engine: Any | None = None,
    ) -> None:
        self._settings = settings
        self._engine: Any | None = engine
        self._voices: dict[tuple[Language, VoiceRole], str] = {
            (Language.VI, VoiceRole.A): settings.vi_voice_a,
            (Language.VI, VoiceRole.B): settings.vi_voice_b,
            (Language.EN, VoiceRole.A): settings.en_voice_a,
            (Language.EN, VoiceRole.B): settings.en_voice_b,
        }
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_tts)
        self._ready = engine is not None

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def sample_rate(self) -> int:
        return self._settings.sample_rate

    @property
    def _engine_sample_rate(self) -> int:
        return int(getattr(self._engine, "sample_rate", DEFAULT_VIENEU_SAMPLE_RATE))

    def _resolve_voice(self, lang: Language, role: VoiceRole) -> str:
        return self._voices[(lang, role)]

    def load(self) -> None:
        """Load the VieNeu-TTS model into memory."""
        if self._ready:
            return

        device = self._settings.resolve_device()
        mode = self._settings.vieneu_mode
        logger.info(
            "Loading VieNeu-TTS engine mode=%s backend=%s device=%s precision=%s",
            mode,
            self._settings.vieneu_backend,
            device,
            self._settings.vieneu_precision,
        )

        from vieneu import Vieneu

        kwargs: dict[str, Any] = {}
        if mode == "v3turbo":
            kwargs.update(
                device=device,
                backend=self._settings.vieneu_backend,
                precision=self._settings.vieneu_precision,
                threads=self._settings.vieneu_threads,
                max_streams=self._settings.vieneu_max_streams,
            )
            if self._settings.vieneu_onnx_dir:
                kwargs["onnx_dir"] = self._settings.vieneu_onnx_dir
        else:
            if self._settings.vieneu_threads:
                kwargs["threads"] = self._settings.vieneu_threads

        if self._settings.vieneu_backbone_repo:
            kwargs["backbone_repo"] = self._settings.vieneu_backbone_repo

        self._engine = Vieneu(mode=mode, **kwargs)

        if self._settings.vieneu_warmup:
            logger.info("Warming up VieNeu-TTS engine...")
            for _ in self._engine.infer_stream(
                "Xin chào.",
                voice=self._settings.vi_voice_a,
                apply_watermark=False,
            ):
                pass

        self._ready = True
        logger.info(
            "VieNeu-TTS engine loaded successfully (native_sr=%d, output_sr=%d)",
            self._engine_sample_rate,
            self._settings.sample_rate,
        )

    def _validate_inputs(self, text: str, speed: float) -> str:
        cleaned = text.strip()
        if not cleaned:
            raise ValidationError("Empty text prompt")
        if not (MIN_SPEED <= speed <= MAX_SPEED):
            raise ValidationError(
                f"Speed must be between {MIN_SPEED} and {MAX_SPEED}, got {speed}"
            )
        if not self._ready or self._engine is None:
            raise EngineNotReadyError("TTS engine is not loaded yet")
        return cleaned

    def _render_sync(
        self,
        text: str,
        lang: Language,
        role: VoiceRole,
        speed: float,
    ) -> np.ndarray:
        assert self._engine is not None
        voice = self._resolve_voice(lang, role)
        speech = self._engine.infer(
            text,
            voice=voice,
            apply_watermark=self._settings.vieneu_watermark,
        )
        if speech is None or len(speech) == 0:
            raise SynthesisError("VieNeu-TTS rendering produced no audio")

        arr = np.asarray(speech, dtype=np.float32)
        arr = adjust_speed(arr, speed)
        arr = resample_audio(arr, self._engine_sample_rate, self._settings.sample_rate)
        return arr

    async def synthesize(
        self,
        text: str,
        lang: Language = Language.VI,
        role: VoiceRole = VoiceRole.A,
        speed: float = 1.0,
    ) -> SynthesisResult:
        """Synthesize text into a complete 16-bit PCM WAV payload."""
        cleaned = self._validate_inputs(text, speed)

        async with self._semaphore:
            try:
                speech = await asyncio.to_thread(
                    self._render_sync, cleaned, lang, role, speed
                )
                normalized = normalize_audio(speech, self._settings.peak_target)
                wav_bytes = await asyncio.to_thread(
                    encode_wav, normalized, self._settings.sample_rate
                )
                duration = float(len(normalized)) / float(self._settings.sample_rate)
                return SynthesisResult(
                    audio_wav=wav_bytes,
                    sample_rate=self._settings.sample_rate,
                    duration_seconds=duration,
                )
            except (ValidationError, EngineNotReadyError, SynthesisError):
                raise
            except Exception as exc:
                logger.exception("TTS synthesis failed for text=%r", cleaned[:40])
                raise SynthesisError(str(exc)) from exc

    async def synthesize_stream(
        self,
        text: str,
        lang: Language = Language.VI,
        role: VoiceRole = VoiceRole.A,
        speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        """Yield raw signed 16-bit little-endian mono PCM chunks via VieNeu-TTS streaming."""
        cleaned = self._validate_inputs(text, speed)
        if abs(speed - 1.0) > 1e-3:
            logger.debug(
                "speed=%.2f requested on streaming synthesis; VieNeu-TTS streams at native pacing",
                speed,
            )

        async with self._semaphore:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()
            voice = self._resolve_voice(lang, role)
            orig_sr = self._engine_sample_rate
            target_sr = self._settings.sample_rate

            def _produce() -> None:
                assert self._engine is not None
                try:
                    emitted = False
                    for chunk in self._engine.infer_stream(
                        cleaned,
                        voice=voice,
                        apply_watermark=self._settings.vieneu_watermark,
                    ):
                        if chunk is None or len(chunk) == 0:
                            continue
                        emitted = True
                        arr = resample_audio(chunk, orig_sr, target_sr)
                        chunk_pcm = float_to_pcm16(arr)
                        loop.call_soon_threadsafe(queue.put_nowait, chunk_pcm)
                    if not emitted:
                        raise SynthesisError("VieNeu-TTS streaming produced no audio")
                    loop.call_soon_threadsafe(queue.put_nowait, None)
                except Exception as exc:
                    loop.call_soon_threadsafe(queue.put_nowait, exc)

            producer_task = asyncio.create_task(asyncio.to_thread(_produce))
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    if isinstance(item, Exception):
                        if isinstance(item, SynthesisError):
                            raise item
                        logger.exception("Streaming TTS failed for text=%r", cleaned[:40])
                        raise SynthesisError(str(item)) from item
                    yield item
            finally:
                await producer_task
