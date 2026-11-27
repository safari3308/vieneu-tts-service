"""Integration tests for the gRPC transport over an in-process loopback server."""

from __future__ import annotations

from typing import Any

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from tts_service.config import Settings
from tts_service.services import TTSService
from tts_service.transport.grpc.gen.tts.v1 import tts_pb2, tts_pb2_grpc
from tts_service.transport.grpc.server import create_grpc_server, set_grpc_health_status


@pytest.fixture
async def grpc_channel(
    test_settings: Settings,
    tts_service: TTSService,
    subsync_service: Any,
):
    server, health_servicer = create_grpc_server(
        test_settings, tts_service, subsync_service
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    await set_grpc_health_status(health_servicer, serving=True)

    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield channel
    finally:
        await channel.close()
        await server.stop(grace=1.0)


async def test_grpc_health_check_is_public(grpc_channel: grpc.aio.Channel) -> None:
    health_stub = health_pb2_grpc.HealthStub(grpc_channel)
    resp = await health_stub.Check(health_pb2.HealthCheckRequest(service=""))
    assert resp.status == health_pb2.HealthCheckResponse.SERVING


async def test_grpc_get_info_is_public(
    grpc_channel: grpc.aio.Channel, test_settings: Settings
) -> None:
    stub = tts_pb2_grpc.TTSServiceStub(grpc_channel)
    resp = await stub.GetInfo(tts_pb2.GetInfoRequest())
    assert resp.engine == "vieneu-tts+kokoro"
    assert resp.model == test_settings.vieneu_mode
    assert resp.sample_rate == test_settings.sample_rate
    assert resp.native_sample_rate == 48000
    assert dict(resp.voices) == {
        "vi_a": test_settings.vi_voice_a,
        "vi_b": test_settings.vi_voice_b,
        "en_a": test_settings.en_voice_a,
        "en_b": test_settings.en_voice_b,
    }


async def test_grpc_rejects_unauthenticated_calls(grpc_channel: grpc.aio.Channel) -> None:
    stub = tts_pb2_grpc.TTSServiceStub(grpc_channel)
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await stub.Synthesize(tts_pb2.SynthesizeRequest(text="Hello"))
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_grpc_list_voices_is_public(
    grpc_channel: grpc.aio.Channel, test_settings: Settings
) -> None:
    stub = tts_pb2_grpc.TTSServiceStub(grpc_channel)
    resp = await stub.ListVoices(tts_pb2.ListVoicesRequest(lang=tts_pb2.LANGUAGE_EN))
    assert resp.engine == "vieneu-tts+kokoro"
    assert len(resp.voices) >= 25
    assert resp.default_voices["vi"] == test_settings.vi_voice_a
    assert any(v.id == "Hải Đăng" and "en" in v.languages for v in resp.voices)


async def test_grpc_synthesize_unary(
    grpc_channel: grpc.aio.Channel, test_settings: Settings
) -> None:
    stub = tts_pb2_grpc.TTSServiceStub(grpc_channel)
    md = (("authorization", f"Bearer {test_settings.api_token}"),)

    resp = await stub.Synthesize(
        tts_pb2.SynthesizeRequest(
            text="Xin chao gRPC",
            lang=tts_pb2.LANGUAGE_VI,
            voice="Ngọc Huyền",
            speed=0.0,  # defaults to 1.0
        ),
        metadata=md,
    )
    assert resp.sample_rate == 48000
    assert resp.duration_seconds == pytest.approx(0.5, abs=0.01)
    assert resp.audio_wav[:4] == b"RIFF"


async def test_grpc_synthesize_stream(
    grpc_channel: grpc.aio.Channel, test_settings: Settings
) -> None:
    stub = tts_pb2_grpc.TTSServiceStub(grpc_channel)
    md = (("authorization", f"Bearer {test_settings.api_token}"),)

    chunks = [
        chunk
        async for chunk in stub.SynthesizeStream(
            tts_pb2.SynthesizeRequest(
                text="Stream me",
                lang=tts_pb2.LANGUAGE_EN,
                role=tts_pb2.VOICE_ROLE_B,
                speed=1.0,
            ),
            metadata=md,
        )
    ]
    assert len(chunks) == 2
    assert chunks[0].is_final is False
    assert chunks[1].is_final is True
    assert all(c.sample_rate == 48000 and len(c.pcm_data) == 24000 for c in chunks)


async def test_grpc_sync_subtitle_unary_and_stream(
    grpc_channel: grpc.aio.Channel,
    test_settings: Settings,
    subsync_service: Any,
) -> None:
    stub = tts_pb2_grpc.TTSServiceStub(grpc_channel)
    md = (("authorization", f"Bearer {test_settings.api_token}"),)

    # Unary
    unary_resp = await stub.SyncSubtitle(
        tts_pb2.SyncSubtitleRequest(
            target_srt=b"1\n00:00:00,000 --> 00:00:01,000\nLine\n",
            reference_data=b"REF_SUB",
            ref_type=tts_pb2.REFERENCE_TYPE_SUB,
        ),
        metadata=md,
    )
    assert b"Synced line" in unary_resp.synced_srt

    # Client-streaming
    async def chunk_gen():
        yield tts_pb2.SyncSubtitleChunk(
            metadata=tts_pb2.SyncSubtitleMetadata(ref_type=tts_pb2.REFERENCE_TYPE_AUDIO)
        )
        yield tts_pb2.SyncSubtitleChunk(target_srt_chunk=b"1\n00:00:00,000 --> ")
        yield tts_pb2.SyncSubtitleChunk(target_srt_chunk=b"00:00:01,000\nLine\n")
        yield tts_pb2.SyncSubtitleChunk(reference_chunk=b"AAC_PART_1")
        yield tts_pb2.SyncSubtitleChunk(reference_chunk=b"AAC_PART_2")

    stream_resp = await stub.SyncSubtitleStream(chunk_gen(), metadata=md)
    assert b"Synced line" in stream_resp.synced_srt
    assert subsync_service.calls[-1] == (".aac", 37, 20)
