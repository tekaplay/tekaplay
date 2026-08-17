"""Structured logging via structlog.

Every log line is JSON in production and pretty-printed in development, and is
automatically enriched with the request_id / user_id / correlation_id bound by
middleware. Never log secrets; the processor chain is the single choke point
where redaction rules will live.
"""
import logging
import sys
from typing import Any

import structlog

from app.core.config import get_settings

_REDACTED_KEYS = {"password", "secret", "token", "authorization", "api_key"}


def _redact(_, __, event_dict: dict) -> dict:
    for key in list(event_dict):
        if key.lower() in _REDACTED_KEYS:
            event_dict[key] = "[redacted]"
    return event_dict


def configure_logging() -> None:
    settings = get_settings()
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
    ]
    # ConsoleRenderer formats exc_info itself and must not be preceded by
    # format_exc_info; JSONRenderer cannot serialise an exception tuple, so
    # there the traceback has to be rendered to a string first.
    renderer: list[Any] = (
        [structlog.processors.format_exc_info, structlog.processors.JSONRenderer()]
        if settings.is_production
        else [structlog.dev.ConsoleRenderer()]
    )
    structlog.configure(
        processors=[*shared, *renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
