"""Structured logging configuration.

Log records are written as one JSON object per line to a rotating file in the
data directory, and as human-readable text to stderr. Extra context is passed
with ``logger.info("msg", extra={"ctx": {...}})``.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import UTC, datetime
from pathlib import Path

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON documents."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict):
            payload["ctx"] = ctx
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(log_dir: Path | None, level: int = logging.INFO) -> None:
    """Configure the root ``tidy`` logger once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger("tidy")
    logger.setLevel(level)
    logger.propagate = False

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logger.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "tidy.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child of the ``tidy`` logger."""
    if not name.startswith("tidy"):
        name = f"tidy.{name}"
    return logging.getLogger(name)
