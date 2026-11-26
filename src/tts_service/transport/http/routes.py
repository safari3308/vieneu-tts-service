"""FastAPI route definitions."""

from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from tts_service.domain import (
    EngineNotReadyError,
    ReferenceType,
    SubsyncError,
    SynthesisError,
    ValidationError,
)
from tts_service.services import SubsyncService, TTSService

from .auth import verify_bearer_token
from .schemas import HealthResponse, ReadinessResponse, TTSRequest

logger = logging.getLogger(__name__)

router = APIRouter()

_UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB


async def _write_upload_to_path(upload: UploadFile, dest: Path) -> int:
    total = 0
    with dest.open("wb") as out_file:
        while chunk := await upload.read(_UPLOAD_CHUNK_SIZE):
            out_file.write(chunk)
            total += len(chunk)
    return total


@router.post(
    "/v1/audio/speech",
    response_class=StreamingResponse,
    summary="Synthesize speech to 16-bit PCM WAV",
    responses={
        200: {"content": {"audio/wav": {}}},
        400: {"description": "Invalid request payload"},
        403: {"description": "Invalid or missing Bearer token"},
        503: {"description": "TTS engines not ready"},
    },
)
async def text_to_speech(
    req: TTSRequest,
    request: Request,
    _: str = Depends(verify_bearer_token),
) -> StreamingResponse:
    tts_service: TTSService = request.app.state.tts_service
    try:
        result = await tts_service.synthesize(
            text=req.text,
            lang=req.lang,
            role=req.role,
            speed=req.speed,
        )
        return StreamingResponse(
            io.BytesIO(result.audio_wav),
            media_type="audio/wav",
            headers={
                "X-Audio-Sample-Rate": str(result.sample_rate),
                "X-Audio-Duration-Seconds": f"{result.duration_seconds:.3f}",
            },
        )
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except EngineNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except SynthesisError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post(
    "/v1/utils/subsync",
    response_class=StreamingResponse,
    summary="Synchronize target SRT against a reference audio or subtitle track",
    responses={
        200: {"content": {"text/plain": {}}},
        400: {"description": "Invalid upload or ref_type"},
        403: {"description": "Invalid or missing Bearer token"},
    },
)
async def sync_subtitle(
    request: Request,
    target_sub: UploadFile = File(...),
    reference_file: UploadFile = File(...),
    ref_type: Literal["sub", "audio"] = Form(...),
    _: str = Depends(verify_bearer_token),
) -> StreamingResponse:
    subsync_service: SubsyncService = request.app.state.subsync_service
    parsed_ref_type = ReferenceType(ref_type)

    try:
        with tempfile.TemporaryDirectory(prefix="http_subsync_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            target_path = tmp_path / "target.srt"
            ref_path = tmp_path / f"reference.{subsync_service.ref_extension(parsed_ref_type)}"
            out_path = tmp_path / "synced.srt"

            target_bytes = await _write_upload_to_path(target_sub, target_path)
            ref_bytes = await _write_upload_to_path(reference_file, ref_path)
            if target_bytes == 0:
                raise ValidationError("target_sub file is empty")
            if ref_bytes == 0:
                raise ValidationError("reference_file is empty")

            synced_content = await subsync_service.sync_files(target_path, ref_path, out_path)
            return StreamingResponse(io.BytesIO(synced_content), media_type="text/plain")
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SubsyncError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get("/health", response_model=HealthResponse, tags=["Health"])
@router.get("/healthz", response_model=HealthResponse, tags=["Health"])
async def health() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=ReadinessResponse, tags=["Health"])
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    """Readiness probe verifying that TTS engines are loaded."""
    tts_service: TTSService = request.app.state.tts_service
    if not tts_service.is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="not_ready", engines_loaded=False)
    return ReadinessResponse(status="ready", engines_loaded=True)
