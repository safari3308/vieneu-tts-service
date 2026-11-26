"""Unit tests for SubsyncService async subprocess handling."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tts_service.config import Settings
from tts_service.domain import ReferenceType, SubsyncError, ValidationError
from tts_service.services import SubsyncService


async def test_sync_bytes_rejects_empty_inputs(test_settings: Settings) -> None:
    svc = SubsyncService(test_settings)
    with pytest.raises(ValidationError):
        await svc.sync_bytes(b"", b"ref", ReferenceType.SUB)
    with pytest.raises(ValidationError):
        await svc.sync_bytes(b"srt", b"", ReferenceType.AUDIO)


async def test_sync_files_success(test_settings: Settings, tmp_path: Path) -> None:
    svc = SubsyncService(test_settings)
    target = tmp_path / "target.srt"
    ref = tmp_path / "reference.srt"
    out = tmp_path / "synced.srt"
    target.write_bytes(b"orig")
    ref.write_bytes(b"ref")

    expected = b"1\n00:00:00,000 --> 00:00:01,000\nOK\n"

    class FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            out.write_bytes(expected)
            return b"", b""

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=FakeProc())):
        result = await svc.sync_files(target, ref, out)

    assert result == expected


async def test_sync_files_nonzero_exit_raises(
    test_settings: Settings, tmp_path: Path
) -> None:
    svc = SubsyncService(test_settings)
    target = tmp_path / "target.srt"
    ref = tmp_path / "reference.aac"
    out = tmp_path / "synced.srt"
    target.write_bytes(b"orig")
    ref.write_bytes(b"audio")

    class FailingProc:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"ffsubsync failed"

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=FailingProc())):
        with pytest.raises(SubsyncError, match="ffsubsync failed"):
            await svc.sync_files(target, ref, out)


async def test_sync_files_timeout_kills_process(tmp_path: Path) -> None:
    cfg = Settings(subsync_timeout_seconds=1.1)
    svc = SubsyncService(cfg)
    target = tmp_path / "target.srt"
    ref = tmp_path / "reference.aac"
    out = tmp_path / "synced.srt"
    target.write_bytes(b"orig")
    ref.write_bytes(b"audio")

    killed = False

    class HangingProc:
        returncode = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return b"", b""

        def kill(self) -> None:
            nonlocal killed
            killed = True

        async def wait(self) -> int:
            return -9

    svc._settings = cfg.model_copy(update={"subsync_timeout_seconds": 0.05})
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=HangingProc())):
        with pytest.raises(SubsyncError, match="timed out"):
            await svc.sync_files(target, ref, out)

    assert killed is True
