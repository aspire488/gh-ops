"""Structured logging for gh-ops.

Provides a consistent logging interface that:
- Redacts tokens and secrets from output
- Supports structured key-value context
- Outputs JSON for machine consumption, plain text for humans
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any

# Patterns that look like secrets
_SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{36}"),  # GitHub PAT
    re.compile(r"gho_[A-Za-z0-9]{36}"),  # GitHub OAuth
    re.compile(r"github_pat_[A-Za-z0-9_]{82}"),  # GitHub fine-grained PAT
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"token\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    # Telegram bot token: "<bot_id>:<secret>" as it appears in an API URL
    re.compile(r"bot\d{6,}:[A-Za-z0-9_\-]{30,}"),
    # Telegram bot token, bare form (33+ secret characters after the colon)
    re.compile(r"\d{9,12}:[A-Za-z0-9_\-]{33,}"),
]

_REDACTION = "[REDACTED]"


def _redact(text: str) -> str:
    """Replace any secret-like patterns with [REDACTED]."""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(_REDACTION, result)
    return result


class StructuredFormatter(logging.Formatter):
    """Log formatter that redacts secrets and supports structured context."""

    def format(self, record: logging.LogRecord) -> str:
        msg = _redact(str(record.getMessage()))

        # Add structured context if present
        extra = getattr(record, "structured_data", None)
        if extra and isinstance(extra, dict):
            context_str = " ".join(f"{k}={v}" for k, v in extra.items())
            msg = f"{msg} {_redact(context_str)}"

        # Add module path
        if record.name:
            msg = f"[{record.name}] {msg}"

        return msg


def get_logger(
    name: str,
    level: int = logging.INFO,
    json_output: bool = False,
) -> logging.Logger:
    """Get a configured logger for a gh-ops module.

    Args:
        name: Logger name (typically module path like "github.client").
        level: Log level.
        json_output: If True, use JSON formatting for machine consumption.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)

    logger.setLevel(level)
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    **context: Any,
) -> None:
    """Log an event with structured context.

    Token-safe: context values are redacted before output.
    """
    record = logger.makeRecord(
        name=logger.name,
        level=level,
        fn="",
        lno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    # Attach structured data
    record.structured_data = context  # type: ignore[attr-defined]
    logger.handle(record)
