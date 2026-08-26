"""Tests for structured logging (Sprint P2)."""
from __future__ import annotations

import json
import logging
import sys

import pytest

from logging_config import (
    DevFormatter,
    StructuredJSONFormatter,
    get_logger,
    set_request_context,
    setup_logging,
    _assessment_id,
    _request_id,
    _user_id,
)


@pytest.fixture(autouse=True)
def _clean_logging():
    """Reset root logger after each test."""
    yield
    logging.getLogger().handlers.clear()


class TestStructuredJSONFormatter:
    def test_basic_output(self):
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="hello world", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test"
        assert parsed["msg"] == "hello world"
        assert "ts" in parsed

    def test_request_id_injected(self):
        formatter = StructuredJSONFormatter()
        with set_request_context(request_id="req-abc-123"):
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=1,
                msg="test message", args=(), exc_info=None,
            )
            output = formatter.format(record)
            parsed = json.loads(output)
            assert parsed["request_id"] == "req-abc-123"

    def test_assessment_id_injected(self):
        formatter = StructuredJSONFormatter()
        with set_request_context(assessment_id="asst-xyz-789"):
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=1,
                msg="test", args=(), exc_info=None,
            )
            output = formatter.format(record)
            parsed = json.loads(output)
            assert parsed["assessment_id"] == "asst-xyz-789"

    def test_user_id_injected(self):
        formatter = StructuredJSONFormatter()
        with set_request_context(user_id="u-456"):
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=1,
                msg="test", args=(), exc_info=None,
            )
            output = formatter.format(record)
            parsed = json.loads(output)
            assert parsed["user_id"] == "u-456"

    def test_extras_preserved(self):
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="test", args=(), exc_info=None,
        )
        record.rule_id = "R-001"
        record.program_id = "SKILLED_189"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["rule_id"] == "R-001"
        assert parsed["program_id"] == "SKILLED_189"

    def test_sensitive_email_redacted(self):
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="test", args=(), exc_info=None,
        )
        record.email = "user@example.com"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["email"] == "***REDACTED***"

    def test_sensitive_passport_redacted(self):
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="test", args=(), exc_info=None,
        )
        record.passport_number = "E12345678"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["passport_number"] == "***REDACTED***"

    def test_non_sensitive_not_redacted(self):
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="test", args=(), exc_info=None,
        )
        record.age = 30
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["age"] == 30

    def test_exception_formatting(self):
        formatter = StructuredJSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=1,
            msg="error occurred", args=(), exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "boom" in parsed["exception"]


class TestDevFormatter:
    def test_basic_output(self):
        formatter = DevFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="hello", args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "INFO" in output
        assert "[test]" in output
        assert "hello" in output


class TestSetRequestContext:
    def test_sets_and_restores(self):
        assert _request_id.get() is None
        with set_request_context(request_id="req-1"):
            assert _request_id.get() == "req-1"
        assert _request_id.get() is None

    def test_multiple_vars(self):
        with set_request_context(request_id="r", user_id="u", assessment_id="a"):
            assert _request_id.get() == "r"
            assert _user_id.get() == "u"
            assert _assessment_id.get() == "a"
        assert _request_id.get() is None
        assert _user_id.get() is None
        assert _assessment_id.get() is None

    def test_nested_contexts(self):
        with set_request_context(request_id="outer"):
            assert _request_id.get() == "outer"
            with set_request_context(request_id="inner"):
                assert _request_id.get() == "inner"
            assert _request_id.get() == "outer"
        assert _request_id.get() is None


class TestSetupLogging:
    def test_setup_creates_handlers(self):
        setup_logging(level="DEBUG", json_output=True)
        root = logging.getLogger()
        assert len(root.handlers) >= 1

    def test_get_logger_returns_logger(self):
        logger = get_logger("my.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "my.module"

    def test_setup_json_mode(self):
        setup_logging(json_output=True)
        root = logging.getLogger()
        assert isinstance(root.handlers[0].formatter, StructuredJSONFormatter)

    def test_setup_dev_mode(self):
        setup_logging(json_output=False)
        root = logging.getLogger()
        assert isinstance(root.handlers[0].formatter, DevFormatter)
