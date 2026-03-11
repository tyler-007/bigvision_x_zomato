"""
AutoLunch — Structured Logging Setup
Uses loguru with JSON-serializable structured logs.
One call to `setup_logging()` at app startup is all that's needed.
"""
import sys
from pathlib import Path
from loguru import logger

from autolunch.config.settings import settings


def setup_logging() -> None:
    """
    Configure loguru for the entire application.
    - Console: Human-readable colored output
    - File: JSON-structured rotation for persistence / debugging
    """
    logger.remove()  # Remove default handler

    # ── Console handler (human-readable) ─────────────────────────────────────
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # ── File handler (JSON structured, daily rotation) ────────────────────────
    log_dir = Path("./logs")
    log_dir.mkdir(exist_ok=True)

    logger.add(
        log_dir / "autolunch_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        format="{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level} | {name}:{function}:{line} | {message}",
        rotation="00:00",       # Rotate at midnight
        retention="30 days",    # Keep 30 days of logs
        compression="gz",       # Compress old logs
        serialize=True,         # Write as JSON (loguru built-in)
        enqueue=True,           # Async-safe logging
    )

    logger.info("AutoLunch logging initialized", level=settings.log_level)
