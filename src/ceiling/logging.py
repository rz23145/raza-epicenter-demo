"""Logging setup: rich console handler at INFO, rotating file handler at DEBUG."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

_configured = False


def setup_logging(logs_dir: Path) -> logging.Logger:
    """Configure the ceiling logger once per process and return it."""
    global _configured
    logger = logging.getLogger("ceiling")
    if _configured:
        return logger
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.DEBUG)

    console_handler = RichHandler(show_path=False, rich_tracebacks=False)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        logs_dir / "ceiling.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(file_handler)

    logger.propagate = False
    _configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ceiling.{name}")
