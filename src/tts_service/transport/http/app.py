"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tts_service import __version__
from tts_service.config import Settings
from tts_service.services import SubsyncService, TTSService

from .routes import router


def create_app(
    settings: Settings | None = None,
    tts_service: TTSService | None = None,
    subsync_service: SubsyncService | None = None,
    manage_lifespan: bool = True,
) -> FastAPI:
    """Construct the FastAPI application with injected or default services."""
    cfg = settings or Settings()
    tts = tts_service or TTSService(cfg)
    subsync = subsync_service or SubsyncService(cfg)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if manage_lifespan and not tts.is_ready:
            await asyncio.to_thread(tts.load)
        yield

    app = FastAPI(
        title="VieNeu-TTS Distributed Engine",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.tts_service = tts
    app.state.subsync_service = subsync
    app.include_router(router)
    return app
