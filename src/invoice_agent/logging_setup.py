"""
Shared logging configuration for the ``invoice_agent`` package (used by CLI and API).

The verbosity (``LOG_LEVEL``) and output format (``LOG_FORMAT`` = ``plain`` | ``json``) are
environment-driven. Only the ``invoice_agent`` logger is configured, so library noise stays
at its own levels.
"""

from __future__ import annotations

import json
import logging
import sys

from invoice_agent.config import Settings

_PLAIN_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _JsonFormatter(logging.Formatter):
    """Minimal structured formatter for production telemetry ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings) -> None:
    """Configure the ``invoice_agent`` logger from settings. Idempotent."""
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_PLAIN_FORMAT))

    logger = logging.getLogger("invoice_agent")
    logger.handlers = [handler]  # replace, so reconfiguration never duplicates handlers
    logger.setLevel(settings.log_level)
    logger.propagate = False
