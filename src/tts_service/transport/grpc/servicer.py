"""gRPC servicer delegating to transport-agnostic TTS and Subsync services."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import grpc

from tts_service.domain import (
    EngineNotReadyError,
    Language,
    ReferenceType,
    SubsyncError,
    SynthesisError,
    ValidationError,
    VoiceRole,
)
from tts_service.services import SubsyncService, TTSService
from tts_service.transport.grpc.gen.tts.v1 import tts_pb2, tts_pb2_grpc

logger = logging.getLogger(__name__)


def _map_language(proto_lang: int) -> Language:
    if proto_lang == tts_pb2.LANGUAGE_EN:
        return Language.EN
    if proto_lang in (tts_pb2.LANGUAGE_VI, tts_pb2.LANGUAGE_UNSPECIFIED):
        return Language.VI
    raise ValidationError(f"Unsupported language enum value: {proto_lang}")


def _map_role(proto_role: int) -> VoiceRole:
    if proto_role == tts_pb2.VOICE_ROLE_B:
        return VoiceRole.B
    if proto_role in (tts_pb2.VOICE_ROLE_A, tts_pb2.VOICE_ROLE_UNSPECIFIED):
        return VoiceRole.A
    raise ValidationError(f"Unsupported voice role enum value: {proto_role}")


def _map_ref_type(proto_ref_type: int) -> ReferenceType:
    if proto_ref_type == tts_pb2.REFERENCE_TYPE_SUB:
        return ReferenceType.SUB
    if proto_ref_type == tts_pb2.REFERENCE_TYPE_AUDIO:
        return ReferenceType.AUDIO
    raise ValidationError("ref_type must be REFERENCE_TYPE_SUB or REFERENCE_TYPE_AUDIO")


class TTSGrpcServicer(tts_pb2_grpc.TTSServiceServicer):
    """Implements tts.v1.TTSService RPC methods."""

    def __init__(
        self,
        tts_service: TTSService,
        subsync_service: SubsyncService,
    ) -> None:
        self._tts = tts_service
        self._subsync = subsync_service

    async def GetInfo(
        self,
        request: tts_pb2.GetInfoRequest,
        context: grpc.aio.ServicerContext,
    ) -> tts_pb2.GetInfoResponse:
        info = self._tts.service_info()
        return tts_pb2.GetInfoResponse(
            engine=info["engine"],
            model=info["model"],
            sample_rate=info["sample_rate"],
            native_sample_rate=info["native_sample_rate"],
            voices=info["voices"],
        )

    async def ListVoices(
        self,
        request: tts_pb2.ListVoicesRequest,
        context: grpc.aio.ServicerContext,
    ) -> tts_pb2.ListVoicesResponse:
        try:
            lang: Language | None = None
            if request.lang != tts_pb2.LANGUAGE_UNSPECIFIED:
                lang = _map_language(request.lang)
            data = self._tts.list_voices(lang=lang)
            return tts_pb2.ListVoicesResponse(
                engine=data["engine"],
                voices=[
                    tts_pb2.VoiceInfo(
                        id=v.id,
                        name=v.name,
                        languages=list(v.languages),
                        gender=v.gender,
                        description=v.description,
                    )
                    for v in data["voices"]
                ],
                default_voices=data["default_voices"],
            )
        except ValidationError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        raise AssertionError("unreachable")  # pragma: no cover

    async def Synthesize(
        self,
        request: tts_pb2.SynthesizeRequest,
        context: grpc.aio.ServicerContext,
    ) -> tts_pb2.SynthesizeResponse:
        try:
            lang = _map_language(request.lang)
            role = _map_role(request.role)
            speed = request.speed if request.speed != 0.0 else 1.0
            voice = request.voice.strip() if request.voice else None

            result = await self._tts.synthesize(
                text=request.text,
                lang=lang,
                role=role,
                speed=speed,
                voice=voice,
            )
            return tts_pb2.SynthesizeResponse(
                audio_wav=result.audio_wav,
                sample_rate=result.sample_rate,
                duration_seconds=result.duration_seconds,
            )
        except ValidationError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except EngineNotReadyError as exc:
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
        except SynthesisError as exc:
            await context.abort(grpc.StatusCode.INTERNAL, str(exc))
        raise AssertionError("unreachable")  # pragma: no cover

    async def SynthesizeStream(
        self,
        request: tts_pb2.SynthesizeRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[tts_pb2.AudioChunk]:
        try:
            lang = _map_language(request.lang)
            role = _map_role(request.role)
            speed = request.speed if request.speed != 0.0 else 1.0
            voice = request.voice.strip() if request.voice else None

            pending: bytes | None = None
            async for chunk in self._tts.synthesize_stream(
                text=request.text,
                lang=lang,
                role=role,
                speed=speed,
                voice=voice,
            ):
                if pending is not None:
                    yield tts_pb2.AudioChunk(
                        pcm_data=pending,
                        sample_rate=self._tts.sample_rate,
                        is_final=False,
                    )
                pending = chunk

            if pending is not None:
                yield tts_pb2.AudioChunk(
                    pcm_data=pending,
                    sample_rate=self._tts.sample_rate,
                    is_final=True,
                )
        except ValidationError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except EngineNotReadyError as exc:
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
        except SynthesisError as exc:
            await context.abort(grpc.StatusCode.INTERNAL, str(exc))

    async def SyncSubtitle(
        self,
        request: tts_pb2.SyncSubtitleRequest,
        context: grpc.aio.ServicerContext,
    ) -> tts_pb2.SyncSubtitleResponse:
        try:
            ref_type = _map_ref_type(request.ref_type)
            synced = await self._subsync.sync_bytes(
                target_srt=request.target_srt,
                reference_data=request.reference_data,
                ref_type=ref_type,
            )
            return tts_pb2.SyncSubtitleResponse(synced_srt=synced)
        except ValidationError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except SubsyncError as exc:
            await context.abort(grpc.StatusCode.INTERNAL, str(exc))
        raise AssertionError("unreachable")  # pragma: no cover

    async def SyncSubtitleStream(
        self,
        request_iterator: AsyncIterator[tts_pb2.SyncSubtitleChunk],
        context: grpc.aio.ServicerContext,
    ) -> tts_pb2.SyncSubtitleResponse:
        try:
            with tempfile.TemporaryDirectory(prefix="grpc_subsync_") as tmp_dir:
                tmp_path = Path(tmp_dir)
                target_path = tmp_path / "target.srt"
                out_path = tmp_path / "synced.srt"
                ref_type: ReferenceType | None = None
                ref_path: Path | None = None
                target_written = 0
                ref_written = 0

                with target_path.open("wb") as target_file:
                    ref_file = None
                    try:
                        async for chunk in request_iterator:
                            payload_type = chunk.WhichOneof("payload")
                            if payload_type == "metadata":
                                ref_type = _map_ref_type(chunk.metadata.ref_type)
                                if ref_file is None:
                                    ref_path = (
                                        tmp_path
                                        / f"reference.{self._subsync.ref_extension(ref_type)}"
                                    )
                                    ref_file = ref_path.open("wb")
                            elif payload_type == "target_srt_chunk":
                                target_file.write(chunk.target_srt_chunk)
                                target_written += len(chunk.target_srt_chunk)
                            elif payload_type == "reference_chunk":
                                if ref_file is None:
                                    raise ValidationError(
                                        "metadata chunk with ref_type must be sent before reference_chunk"
                                    )
                                ref_file.write(chunk.reference_chunk)
                                ref_written += len(chunk.reference_chunk)
                    finally:
                        if ref_file is not None:
                            ref_file.close()

                if ref_type is None or ref_path is None:
                    raise ValidationError("Missing metadata chunk with ref_type")
                if target_written == 0:
                    raise ValidationError("target_srt stream payload is empty")
                if ref_written == 0:
                    raise ValidationError("reference stream payload is empty")

                synced = await self._subsync.sync_files(target_path, ref_path, out_path)
                return tts_pb2.SyncSubtitleResponse(synced_srt=synced)
        except ValidationError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except SubsyncError as exc:
            await context.abort(grpc.StatusCode.INTERNAL, str(exc))
        raise AssertionError("unreachable")  # pragma: no cover
