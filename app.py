"""Backwards-compatible ASGI entrypoint for `uvicorn app:app`.

For full dual-protocol (HTTP + gRPC) serving, run `python -m tts_service`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = str(Path(__file__).resolve().parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from tts_service.logging_config import configure_logging
from tts_service.config import Settings
from tts_service.transport.http import create_app

_settings = Settings()
configure_logging(_settings.log_level)
app = create_app(settings=_settings)
