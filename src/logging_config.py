"""Structured logging configuration with request-ID tracking and sensitive-data redaction.

Usage:
    from logging_config import setup_logging, get_logger, set_request_context

    setup_logging()
    logger = get_logger(__name__)

    with set_request_context(request_id="abc-123", user_id="u-456"):
        logger.info("Processing", extra={"assessment_id": "asst-789"})
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Context variables — attached to every log record via logging filter
# ---------------------------------------------------------------------------
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "user_id", default=None
)
_assessment_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "assessment_id", default=None
)

# ---------------------------------------------------------------------------
# Sensitive field patterns — values are redacted in logs
# ---------------------------------------------------------------------------
_SENSITIVE_FIELDS = re.compile(
    r"(date_of_birth|dob|passport|email|password|token|secret|api_key|credit_card)",
    re.IGNORECASE,
)

_REDACTED = "***REDACTED***"


# ---------------------------------------------------------------------------
# JSON formatter — structured logging output
# ---------------------------------------------------------------------------
class StructuredJSONFormatter(logging.Formatter):
    """JSON log formatter with context-variable injection and PII redaction."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Inject context vars
        req_id = _request_id.get()
        uid = _user_id.get()
        aid = _assessment_id.get()
        if req_id:
            log_entry["request_id"] = req_id
        if uid:
            log_entry["user_id"] = uid
        if aid:
            log_entry["assessment_id"] = aid

        # Extract extras (assessment_id, rule_id, program_id, etc.)
        standard_attrs = logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
        for key, val in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                log_entry[key] = self._redact(key, val)

        # Exception info
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)

    def _redact(self, key: str, value: Any) -> Any:
        if isinstance(value, str) and _SENSITIVE_FIELDS.search(key):
            return _REDACTED
        if isinstance(value, dict):
            return {k: self._redact(k, v) for k, v in value.items()}
        return value


# ---------------------------------------------------------------------------
# Human-readable formatter (dev environment)
# ---------------------------------------------------------------------------
class DevFormatter(logging.Formatter):
    """Simple coloured formatter for local development."""

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[1;31m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        parts = [
            f"{color}{record.levelname:<8}{self.RESET}",
            f"[{record.name}]",
        ]

        req_id = _request_id.get()
        if req_id:
            parts.append(f"req={req_id[:8]}")

        aid = _assessment_id.get()
        if aid:
            parts.append(f"asst={aid[:8]}")

        parts.append(record.getMessage())

        if record.exc_info and record.exc_info[1]:
            parts.append(f"\n{self.formatException(record.exc_info)}")

        return " ".join(parts)


# ---------------------------------------------------------------------------
# Context manager for request-scoped context
# ---------------------------------------------------------------------------
class set_request_context:
    """Context manager that sets request_id / user_id / assessment_id for logging."""

    def __init__(
        self,
        request_id: str | None = None,
        user_id: str | None = None,
        assessment_id: str | None = None,
    ):
        self._tokens = []
        if request_id is not None:
            self._tokens.append((_request_id, _request_id.set(request_id)))
        if user_id is not None:
            self._tokens.append((_user_id, _user_id.set(user_id)))
        if assessment_id is not None:
            self._tokens.append((_assessment_id, _assessment_id.set(assessment_id)))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for var, token in reversed(self._tokens):
            var.reset(token)
        return False


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup_logging(
    level: str = "INFO",
    json_output: bool = True,
    log_file: str | None = None,
) -> None:
    """Configure root logger.

    Args:
        level: Minimum log level (DEBUG, INFO, WARNING, ERROR).
        json_output: True = structured JSON, False = dev readable.
        log_file: Optional file path to also write logs to.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    root.handlers.clear()

    formatter = StructuredJSONFormatter() if json_output else DevFormatter()

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler (optional)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(StructuredJSONFormatter())
        root.addHandler(fh)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
