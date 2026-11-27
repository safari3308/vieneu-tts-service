"""Integration tests for the FastAPI HTTP transport."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tts_service.config import Settings
from tts_service.services import TTSService
from tts_service.transport.http import create_app


@pytest.fixture
def http_app(
    test_settings: Settings,
    tts_service: TTSService,
    subsync_service: Any,
):
    return create_app(
        settings=test_settings,
        tts_service=tts_service,
        subsync_service=subsync_service,
        manage_lifespan=False,
    )


async def test_health_and_readiness_endpoints(
    http_app, test_settings: Settings, subsync_service: Any
) -> None:
    transport = httpx.ASGITransport(app=http_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        ready = await client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready", "engines_loaded": True}

        info = await client.get("/v1/info")
        assert info.status_code == 200
        body = info.json()
        assert body["engine"] == "vieneu-tts+kokoro"
        assert body["model"] == test_settings.vieneu_mode
        assert body["sample_rate"] == test_settings.sample_rate
        assert body["native_sample_rate"] == 48000
        assert body["voices"] == {
            "vi_a": test_settings.vi_voice_a,
            "vi_b": test_settings.vi_voice_b,
            "en_a": test_settings.en_voice_a,
            "en_b": test_settings.en_voice_b,
        }

    unloaded_app = create_app(
        settings=test_settings,
        tts_service=TTSService(test_settings),
        subsync_service=subsync_service,
        manage_lifespan=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=unloaded_app), base_url="http://test"
    ) as client:
        not_ready = await client.get("/readyz")
        assert not_ready.status_code == 503
        assert not_ready.json() == {"status": "not_ready", "engines_loaded": False}


async def test_speech_endpoint_auth_and_synthesis(http_app, test_settings: Settings) -> None:
    transport = httpx.ASGITransport(app=http_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing token -> 403
        unauth = await client.post("/v1/audio/speech", json={"text": "Hello"})
        assert unauth.status_code == 403

        # Valid token with explicit voice -> 200 WAV
        resp = await client.post(
            "/v1/audio/speech",
            headers={"Authorization": f"Bearer {test_settings.api_token}"},
            json={"text": "Hello world", "lang": "en", "voice": "Thiện Minh", "speed": 1.1},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/wav"
        assert resp.headers["x-audio-sample-rate"] == "48000"
        assert resp.content[:4] == b"RIFF"

        # Voices endpoint is public and supports optional lang filter
        voices_resp = await client.get("/v1/voices")
        assert voices_resp.status_code == 200
        voices_body = voices_resp.json()
        assert voices_body["engine"] == "vieneu-tts+kokoro"
        assert len(voices_body["voices"]) >= 50
        assert voices_body["default_voices"]["vi"] == test_settings.vi_voice_a
        assert voices_body["default_voices"]["en"] == test_settings.en_voice_a

        en_voices_resp = await client.get("/v1/voices?lang=en")
        assert en_voices_resp.status_code == 200
        en_voices_body = en_voices_resp.json()
        assert any(v["id"] == "af_heart" for v in en_voices_body["voices"])
        assert any(v["id"] == "Hải Đăng" for v in en_voices_body["voices"])


async def test_subsync_endpoint(
    http_app, test_settings: Settings, subsync_service: Any
) -> None:
    transport = httpx.ASGITransport(app=http_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/utils/subsync",
            headers={"Authorization": f"Bearer {test_settings.api_token}"},
            files={
                "target_sub": ("target.srt", b"1\n00:00:00,000 --> 00:00:01,000\nHi\n"),
                "reference_file": ("ref.aac", b"FAKE_AAC_BYTES"),
            },
            data={"ref_type": "audio"},
        )
        assert resp.status_code == 200
        assert b"Synced line" in resp.content
        assert subsync_service.calls == [(".aac", 35, 14)]
