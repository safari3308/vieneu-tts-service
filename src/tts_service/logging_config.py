"""Structured logging setup for the service."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Configure root logger with consistent structured output."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
        force=True,
    )
