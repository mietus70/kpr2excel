"""Structured local logging.

Logs carry codes, ids, counters and durations. They must never carry cell text,
contractor names, evidence numbers or full user paths.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
from pathlib import Path
from typing import Any

from .config import Settings

__all__ = ["configure_logging", "JsonLogFormatter"]

_SAFE_EXTRA_KEYS = {
    "correlation_id",
    "job_id",
    "job_type",
    "document_id",
    "batch_id",
    "export_id",
    "record_id",
    "page",
    "phase",
    "stage",
    "duration_ms",
    "count",
    "records",
    "issues",
    "pages",
    "status",
    "error_code",
    "error_type",
    "size_bytes",
    "owner",
    "confidence",
}

_PATH_RE = re.compile(r"(/[\w.\-]+){2,}|([A-Za-z]:\\[\\\w.\- ]+)")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "component": record.name,
            "message": _redact(record.getMessage()),
        }
        for key in _SAFE_EXTRA_KEYS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Error"
        return json.dumps(payload, ensure_ascii=False)


def _redact(message: str) -> str:
    return _PATH_RE.sub("<path>", message)


def configure_logging(settings: Settings) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(JsonLogFormatter())
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        Path(settings.logs_dir) / "application.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonLogFormatter())
    root.addHandler(file_handler)

    # Uvicorn access logs would contain full URLs with identifiers; keep them quiet.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
