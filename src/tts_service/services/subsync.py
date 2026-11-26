"""Asynchronous ffsubsync subtitle synchronization service."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from tts_service.config import Settings
from tts_service.domain import ReferenceType, SubsyncError, ValidationError

logger = logging.getLogger(__name__)


class SubsyncService:
    """Runs ffsubsync asynchronously with bounded concurrency and timeout."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_subsync)

    @staticmethod
    def ref_extension(ref_type: ReferenceType) -> str:
        return "srt" if ref_type == ReferenceType.SUB else "aac"

    async def sync_bytes(
        self,
        target_srt: bytes,
        reference_data: bytes,
        ref_type: ReferenceType,
    ) -> bytes:
        """Synchronize in-memory SRT and reference bytes."""
        if not target_srt:
            raise ValidationError("target_srt payload is empty")
        if not reference_data:
            raise ValidationError("reference payload is empty")

        with tempfile.TemporaryDirectory(prefix="subsync_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            target_path = tmp_path / "target.srt"
            ref_path = tmp_path / f"reference.{self.ref_extension(ref_type)}"
            out_path = tmp_path / "synced.srt"

            await asyncio.to_thread(target_path.write_bytes, target_srt)
            await asyncio.to_thread(ref_path.write_bytes, reference_data)

            return await self.sync_files(target_path, ref_path, out_path)

    async def sync_files(
        self,
        target_path: Path,
        ref_path: Path,
        out_path: Path,
    ) -> bytes:
        """Invoke ffsubsync on already-staged files without blocking the event loop."""
        async with self._semaphore:
            cmd = ["ffsubsync", str(ref_path), "-i", str(target_path), "-o", str(out_path)]
            logger.info("Executing ffsubsync: ref=%s target=%s", ref_path.name, target_path.name)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise SubsyncError("ffsubsync executable not found in PATH") from exc

            try:
                _, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._settings.subsync_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.wait()
                raise SubsyncError(
                    f"ffsubsync timed out after {self._settings.subsync_timeout_seconds}s"
                ) from exc

            if proc.returncode != 0:
                stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                logger.error("ffsubsync failed (rc=%s): %s", proc.returncode, stderr_text)
                raise SubsyncError(f"ffsubsync error: {stderr_text}")

            if not out_path.exists():
                raise SubsyncError("ffsubsync completed without writing output SRT")

            return await asyncio.to_thread(out_path.read_bytes)
