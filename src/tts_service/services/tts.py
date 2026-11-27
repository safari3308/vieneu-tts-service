"""VieNeu-TTS neural speech synthesis service."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import logging
import re
import subprocess
import threading
from collections.abc import AsyncIterator
from pathlib import Path
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
    VoiceInfo,
    VoiceRole,
)

logger = logging.getLogger(__name__)

MIN_SPEED = 0.25
MAX_SPEED = 4.0
DEFAULT_VIENEU_SAMPLE_RATE = 48000
KOKORO_SAMPLE_RATE = 24000

# Curated native English voices from hexgrad/Kokoro-82M (American & British English).
KOKORO_EN_VOICES: tuple[VoiceInfo, ...] = (
    VoiceInfo(id="af_heart", name="Heart", languages=("en",), gender="female", description="American English · Female (Kokoro)"),
    VoiceInfo(id="af_bella", name="Bella", languages=("en",), gender="female", description="American English · Female (Kokoro)"),
    VoiceInfo(id="af_nicole", name="Nicole", languages=("en",), gender="female", description="American English · Female (Kokoro)"),
    VoiceInfo(id="af_aoede", name="Aoede", languages=("en",), gender="female", description="American English · Female (Kokoro)"),
    VoiceInfo(id="af_kore", name="Kore", languages=("en",), gender="female", description="American English · Female (Kokoro)"),
    VoiceInfo(id="af_sarah", name="Sarah", languages=("en",), gender="female", description="American English · Female (Kokoro)"),
    VoiceInfo(id="af_nova", name="Nova", languages=("en",), gender="female", description="American English · Female (Kokoro)"),
    VoiceInfo(id="af_sky", name="Sky", languages=("en",), gender="female", description="American English · Female (Kokoro)"),
    VoiceInfo(id="af_alloy", name="Alloy", languages=("en",), gender="female", description="American English · Female (Kokoro)"),
    VoiceInfo(id="af_jessica", name="Jessica", languages=("en",), gender="female", description="American English · Female (Kokoro)"),
    VoiceInfo(id="af_river", name="River", languages=("en",), gender="female", description="American English · Female (Kokoro)"),
    VoiceInfo(id="am_michael", name="Michael", languages=("en",), gender="male", description="American English · Male (Kokoro)"),
    VoiceInfo(id="am_fenrir", name="Fenrir", languages=("en",), gender="male", description="American English · Male (Kokoro)"),
    VoiceInfo(id="am_puck", name="Puck", languages=("en",), gender="male", description="American English · Male (Kokoro)"),
    VoiceInfo(id="am_echo", name="Echo", languages=("en",), gender="male", description="American English · Male (Kokoro)"),
    VoiceInfo(id="am_eric", name="Eric", languages=("en",), gender="male", description="American English · Male (Kokoro)"),
    VoiceInfo(id="am_liam", name="Liam", languages=("en",), gender="male", description="American English · Male (Kokoro)"),
    VoiceInfo(id="am_onyx", name="Onyx", languages=("en",), gender="male", description="American English · Male (Kokoro)"),
    VoiceInfo(id="am_adam", name="Adam", languages=("en",), gender="male", description="American English · Male (Kokoro)"),
    VoiceInfo(id="am_santa", name="Santa", languages=("en",), gender="male", description="American English · Male (Kokoro)"),
    VoiceInfo(id="bf_emma", name="Emma", languages=("en",), gender="female", description="British English · Female (Kokoro)"),
    VoiceInfo(id="bf_isabella", name="Isabella", languages=("en",), gender="female", description="British English · Female (Kokoro)"),
    VoiceInfo(id="bf_alice", name="Alice", languages=("en",), gender="female", description="British English · Female (Kokoro)"),
    VoiceInfo(id="bf_lily", name="Lily", languages=("en",), gender="female", description="British English · Female (Kokoro)"),
    VoiceInfo(id="bm_george", name="George", languages=("en",), gender="male", description="British English · Male (Kokoro)"),
    VoiceInfo(id="bm_fable", name="Fable", languages=("en",), gender="male", description="British English · Male (Kokoro)"),
    VoiceInfo(id="bm_lewis", name="Lewis", languages=("en",), gender="male", description="British English · Male (Kokoro)"),
    VoiceInfo(id="bm_daniel", name="Daniel", languages=("en",), gender="male", description="British English · Male (Kokoro)"),
)
_KOKORO_EN_VOICE_IDS: frozenset[str] = frozenset(v.id for v in KOKORO_EN_VOICES)

_VIENEU_EMOTION_TAG_RE = re.compile(
    r"\[\s*(?:cười|cuoi|chuckle|thở dài|tho dai|sigh|hắng giọng|hang giong|clear throat)\s*\]",
    re.IGNORECASE,
)


def strip_emotion_tags(text: str) -> str:
    """Remove VieNeu inline emotion tags before sending text to Kokoro-82M."""
    cleaned = _VIENEU_EMOTION_TAG_RE.sub(" ", text)
    cleaned = re.sub(r"\s+([.,!?;:…])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


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


def _build_atempo_filter(speed: float) -> str:
    """Chain ffmpeg atempo filters (each stage in [0.5, 2.0]) for any speed in [0.25, 4.0]."""
    stages: list[str] = []
    remaining = float(speed)
    while remaining > 2.0:
        stages.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        stages.append("atempo=0.5")
        remaining /= 0.5
    stages.append(f"atempo={remaining:.6f}")
    return ",".join(stages)


def adjust_speed(speech: np.ndarray, speed: float, sample_rate: int = DEFAULT_VIENEU_SAMPLE_RATE) -> np.ndarray:
    """Time-stretch a float32 waveform cleanly using ffmpeg's WSOLA atempo filter."""
    arr = np.asarray(speech, dtype=np.float32)
    if abs(speed - 1.0) <= 1e-3 or arr.size == 0:
        return arr

    atempo = _build_atempo_filter(speed)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "f32le",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-filter:a",
        atempo,
        "-f",
        "f32le",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=arr.astype("<f4", copy=False).tobytes(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        if not proc.stdout:
            return arr
        out = np.frombuffer(proc.stdout, dtype="<f4").copy()
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        logger.warning(
            "ffmpeg atempo failed (%s); returning native pacing for speed=%.2f",
            exc,
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
        kokoro_engines: dict[str, Any] | None = None,
    ) -> None:
        self._settings = settings
        self._engine: Any | None = engine
        self._kokoro_engines: dict[str, Any] = dict(kokoro_engines) if kokoro_engines else {}
        self._kokoro_lock = threading.Lock()
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

    def _kokoro_enabled(self) -> bool:
        if not self._settings.enable_kokoro_en:
            return False
        if self._kokoro_engines:
            return True
        return importlib.util.find_spec("kokoro") is not None

    @staticmethod
    def _is_kokoro_voice(voice: str) -> bool:
        v = voice.strip()
        return v in _KOKORO_EN_VOICE_IDS or v.startswith(("af_", "am_", "bf_", "bm_"))

    def _get_kokoro_pipeline(self, voice: str) -> Any:
        """Lazily load Kokoro KPipeline (reusing a single KModel across US/UK English)."""
        lang_code = "b" if voice.startswith(("bf_", "bm_")) else "a"
        key = f"en_{lang_code}"
        if key in self._kokoro_engines:
            return self._kokoro_engines[key]
        # Compatibility fallback if tests injected a single "en" fake pipeline
        if "en" in self._kokoro_engines:
            return self._kokoro_engines["en"]

        with self._kokoro_lock:
            if key in self._kokoro_engines:
                return self._kokoro_engines[key]
            try:
                from kokoro import KPipeline
            except ImportError as exc:
                raise ValidationError(
                    f"Kokoro English voice {voice!r} requested, but 'kokoro' package is not installed"
                ) from exc

            device = self._settings.resolve_device()
            # Reuse existing KModel across US/UK pipelines so weights are never duplicated in VRAM.
            existing_model: Any = True
            for existing in self._kokoro_engines.values():
                mdl = getattr(existing, "model", None)
                if mdl is not None:
                    existing_model = mdl
                    break

            logger.info(
                "Lazy-loading Kokoro-82M English pipeline (lang_code=%s, device=%s, shared_model=%s)",
                lang_code,
                device,
                existing_model is not True,
            )
            pipeline = KPipeline(lang_code=lang_code, model=existing_model, device=device)
            self._kokoro_engines[key] = pipeline
            return pipeline

    def service_info(self) -> dict[str, Any]:
        """Return active TTS engine/model metadata and sample-rate configuration."""
        engine_name = "vieneu-tts+kokoro" if self._kokoro_enabled() else "vieneu-tts"
        en_a = self._voices[(Language.EN, VoiceRole.A)]
        en_b = self._voices[(Language.EN, VoiceRole.B)]
        if not self._kokoro_enabled():
            if self._is_kokoro_voice(en_a):
                en_a = self._voices[(Language.VI, VoiceRole.A)]
            if self._is_kokoro_voice(en_b):
                en_b = self._voices[(Language.VI, VoiceRole.B)]
        return {
            "engine": engine_name,
            "model": self._settings.vieneu_mode,
            "sample_rate": self._settings.sample_rate,
            "native_sample_rate": self._engine_sample_rate,
            "voices": {
                "vi_a": self._voices[(Language.VI, VoiceRole.A)],
                "vi_b": self._voices[(Language.VI, VoiceRole.B)],
                "en_a": en_a,
                "en_b": en_b,
            },
        }

    def _resolve_voice(
        self,
        lang: Language,
        role: VoiceRole,
        voice: str | None = None,
    ) -> str:
        if voice and voice.strip():
            return voice.strip()
        resolved = self._voices[(lang, role)]
        if lang == Language.EN and not self._kokoro_enabled() and self._is_kokoro_voice(resolved):
            return self._voices[(Language.VI, role)]
        return resolved

    @staticmethod
    def _infer_gender(meta: dict[str, Any]) -> str:
        raw_gender = str(meta.get("gender", "")).strip().lower()
        if raw_gender in ("male", "female"):
            return raw_gender
        desc = str(meta.get("description", "")).strip().lower()
        if desc.startswith("nam"):
            return "male"
        if desc.startswith("nữ") or desc.startswith("nu "):
            return "female"
        return ""

    def _load_preset_voice_catalog(self) -> dict[str, dict[str, Any]]:
        """Return preset voice metadata dict from the live engine or bundled vieneu assets."""
        if self._engine is not None:
            preset_map = getattr(self._engine, "_preset_voices", None)
            if isinstance(preset_map, dict) and preset_map:
                return preset_map
        try:
            import vieneu

            asset_name = (
                "voices_v3_nano.json"
                if self._settings.vieneu_mode == "v3nano"
                else "voices_v3_turbo.json"
            )
            asset_path = Path(vieneu.__file__).parent / "assets" / asset_name
            if asset_path.is_file():
                data = json.loads(asset_path.read_text(encoding="utf-8"))
                presets = data.get("presets", data)
                if isinstance(presets, dict):
                    return presets
        except Exception as exc:
            logger.debug("Could not load bundled vieneu voice catalog: %s", exc)
        return {}

    def list_voices(self, lang: Language | None = None) -> dict[str, Any]:
        """Return available voices (Kokoro EN + bilingual VieNeu VI/EN) and per-language defaults."""
        catalog = self._load_preset_voice_catalog()
        voices: list[VoiceInfo] = []
        seen: set[str] = set()
        kokoro_on = self._kokoro_enabled()

        if kokoro_on:
            for kv in KOKORO_EN_VOICES:
                voices.append(kv)
                seen.add(kv.id)

        for voice_id, meta in catalog.items():
            if not isinstance(meta, dict):
                meta = {}
            voices.append(
                VoiceInfo(
                    id=voice_id,
                    name=voice_id,
                    languages=("vi", "en"),
                    gender=self._infer_gender(meta),
                    description=str(meta.get("description", "")).strip(),
                )
            )
            seen.add(voice_id)

        # Ensure configured / enrolled voices appear even if not in the preset JSON
        for configured_voice in self._voices.values():
            if configured_voice and configured_voice not in seen:
                if self._is_kokoro_voice(configured_voice) and not kokoro_on:
                    continue
                langs = ("en",) if self._is_kokoro_voice(configured_voice) else ("vi", "en")
                voices.append(
                    VoiceInfo(
                        id=configured_voice,
                        name=configured_voice,
                        languages=langs,
                        gender="",
                        description="Configured voice",
                    )
                )
                seen.add(configured_voice)

        if lang is not None:
            voices = [v for v in voices if lang.value in v.languages]

        default_en = self._voices[(Language.EN, VoiceRole.A)]
        if not kokoro_on and self._is_kokoro_voice(default_en):
            default_en = self._voices[(Language.VI, VoiceRole.A)]

        return {
            "engine": "vieneu-tts+kokoro" if kokoro_on else "vieneu-tts",
            "voices": voices,
            "default_voices": {
                "vi": self._voices[(Language.VI, VoiceRole.A)],
                "en": default_en,
            },
        }

    @staticmethod
    def _resolve_ref_audio_path(raw: str | None) -> Path | None:
        if not raw or not raw.strip():
            return None
        candidate = Path(raw.strip()).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        if not candidate.is_absolute():
            repo_root = Path(__file__).resolve().parents[3]
            fallback = repo_root / candidate
            if fallback.is_file():
                return fallback.resolve()
        return None

    def _enroll_reference_voices(self) -> None:
        """Enroll configured reference WAV clips via engine.add_voice() so embeddings are cached."""
        if self._engine is None or not hasattr(self._engine, "add_voice"):
            return

        ref_specs = [
            ((Language.VI, VoiceRole.A), self._settings.vi_voice_a_ref_audio, "female"),
            ((Language.VI, VoiceRole.B), self._settings.vi_voice_b_ref_audio, "male"),
            ((Language.EN, VoiceRole.A), self._settings.en_voice_a_ref_audio, ""),
            ((Language.EN, VoiceRole.B), self._settings.en_voice_b_ref_audio, ""),
        ]
        enrolled_paths: dict[Path, str] = {}

        for key, raw_path, gender in ref_specs:
            if not raw_path or not raw_path.strip():
                continue
            resolved = self._resolve_ref_audio_path(raw_path)
            if resolved is None:
                logger.warning(
                    "Reference audio %r for %s/%s not found; keeping preset voice %r",
                    raw_path,
                    key[0].value,
                    key[1].value,
                    self._voices[key],
                )
                continue

            if resolved in enrolled_paths:
                self._voices[key] = enrolled_paths[resolved]
                continue

            voice_name = resolved.stem
            try:
                registered_name = self._engine.add_voice(
                    voice_name,
                    resolved,
                    denoise=self._settings.ref_audio_denoise,
                    gender=gender,
                    save=False,
                )
                final_name = registered_name or voice_name
                enrolled_paths[resolved] = final_name
                self._voices[key] = final_name
                logger.info(
                    "Enrolled reference voice %r from %s for %s/%s (denoise=%s)",
                    final_name,
                    resolved,
                    key[0].value,
                    key[1].value,
                    self._settings.ref_audio_denoise,
                )
            except Exception:
                logger.exception(
                    "Failed to enroll reference audio %s for %s/%s; keeping preset voice %r",
                    resolved,
                    key[0].value,
                    key[1].value,
                    self._voices[key],
                )

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
        self._enroll_reference_voices()

        if self._settings.vieneu_warmup:
            logger.info("Warming up VieNeu-TTS engine...")
            for _ in self._engine.infer_stream(
                "Xin chào.",
                voice=self._resolve_voice(Language.VI, VoiceRole.A),
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
        voice: str | None = None,
    ) -> np.ndarray:
        resolved_voice = self._resolve_voice(lang, role, voice)
        if self._is_kokoro_voice(resolved_voice):
            kokoro_text = strip_emotion_tags(text)
            if not kokoro_text:
                # Cue consisted solely of an emotion tag; return short silence instead of failing
                return np.zeros(int(self._settings.sample_rate * 0.15), dtype=np.float32)
            pipeline = self._get_kokoro_pipeline(resolved_voice)
            generator = pipeline(kokoro_text, voice=resolved_voice, speed=speed)
            chunks = [
                np.asarray(audio, dtype=np.float32)
                for _, _, audio in generator
                if audio is not None and len(audio) > 0
            ]
            if not chunks:
                raise SynthesisError("Kokoro English TTS rendering produced no audio")
            arr = np.concatenate(chunks)
            return resample_audio(arr, KOKORO_SAMPLE_RATE, self._settings.sample_rate)

        assert self._engine is not None
        speech = self._engine.infer(
            text,
            voice=resolved_voice,
            apply_watermark=self._settings.vieneu_watermark,
        )
        if speech is None or len(speech) == 0:
            raise SynthesisError("VieNeu-TTS rendering produced no audio")

        arr = np.asarray(speech, dtype=np.float32)
        arr = adjust_speed(arr, speed, sample_rate=self._engine_sample_rate)
        arr = resample_audio(arr, self._engine_sample_rate, self._settings.sample_rate)
        return arr

    async def synthesize(
        self,
        text: str,
        lang: Language = Language.VI,
        role: VoiceRole = VoiceRole.A,
        speed: float = 1.0,
        voice: str | None = None,
    ) -> SynthesisResult:
        """Synthesize text into a complete 16-bit PCM WAV payload."""
        cleaned = self._validate_inputs(text, speed)

        async with self._semaphore:
            try:
                speech = await asyncio.to_thread(
                    self._render_sync, cleaned, lang, role, speed, voice
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
        voice: str | None = None,
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
            resolved_voice = self._resolve_voice(lang, role, voice)
            use_kokoro = self._is_kokoro_voice(resolved_voice)
            orig_sr = KOKORO_SAMPLE_RATE if use_kokoro else self._engine_sample_rate
            target_sr = self._settings.sample_rate

            def _produce() -> None:
                try:
                    emitted = False
                    if use_kokoro:
                        kokoro_text = strip_emotion_tags(cleaned)
                        if not kokoro_text:
                            silence = np.zeros(int(target_sr * 0.15), dtype=np.float32)
                            loop.call_soon_threadsafe(queue.put_nowait, float_to_pcm16(silence))
                            emitted = True
                        else:
                            pipeline = self._get_kokoro_pipeline(resolved_voice)
                            for _, _, chunk in pipeline(kokoro_text, voice=resolved_voice, speed=speed):
                                if chunk is None or len(chunk) == 0:
                                    continue
                                emitted = True
                                arr = resample_audio(chunk, orig_sr, target_sr)
                                chunk_pcm = float_to_pcm16(
                                    normalize_audio(arr, self._settings.peak_target)
                                )
                                loop.call_soon_threadsafe(queue.put_nowait, chunk_pcm)
                        if not emitted:
                            raise SynthesisError("Kokoro English streaming produced no audio")
                    else:
                        assert self._engine is not None
                        for chunk in self._engine.infer_stream(
                            cleaned,
                            voice=resolved_voice,
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
