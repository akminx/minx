from __future__ import annotations

import io
import json
import logging

import pytest

from minx_mcp.logging_config import JSONFormatter, configure_logging


def test_configure_logging_sets_json_handler():
    configure_logging(level="WARNING")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler.formatter, JSONFormatter)
    assert root.level == logging.WARNING


def test_json_formatter_emits_core_fields():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    payload = json.loads(formatter.format(record))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["msg"] == "hello"
    assert "ts" in payload


def test_json_formatter_includes_structured_tool_call_fields():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="minx_mcp.contracts",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="tool call",
        args=(),
        exc_info=None,
    )
    record.tool = "example_tool"
    record.duration_ms = 5
    record.success = True
    record.domain = "finance"

    payload = json.loads(formatter.format(record))
    assert payload["tool"] == "example_tool"
    assert payload["duration_ms"] == 5
    assert payload["success"] is True
    assert payload["domain"] == "finance"


def test_json_formatter_includes_error_code_when_present():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="minx_mcp.contracts",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="tool call failed",
        args=(),
        exc_info=None,
    )
    record.tool = "example_tool"
    record.duration_ms = 7
    record.success = False
    record.error_code = "INVALID_INPUT"

    payload = json.loads(formatter.format(record))
    assert payload["success"] is False
    assert payload["error_code"] == "INVALID_INPUT"


def test_json_formatter_redacts_listed_secret_fields():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="minx_mcp",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ok",
        args=(),
        exc_info=None,
    )
    record.token = "super-secret-value"  # type: ignore[attr-defined]
    record._secret_fields = ["token"]  # type: ignore[attr-defined]

    payload = json.loads(formatter.format(record))
    assert payload["token"] == "[REDACTED]"
    assert "super-secret-value" not in json.dumps(payload)


def test_json_formatter_truncates_very_long_messages():
    formatter = JSONFormatter()
    long_msg = "x" * 5000
    record = logging.LogRecord(
        name="minx_mcp",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=long_msg,
        args=(),
        exc_info=None,
    )

    payload = json.loads(formatter.format(record))
    assert len(payload["msg"]) <= 4096
    assert payload["msg"].endswith("…[truncated]")
    assert "x" * 100 in payload["msg"]


def test_json_formatter_includes_exception_details_when_exc_info_present():
    formatter = JSONFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="minx_mcp",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="failure",
        args=(),
        exc_info=exc_info,
    )

    payload = json.loads(formatter.format(record))
    assert "exc" in payload
    assert "RuntimeError" in payload["exc"]


def test_configured_handler_writes_json_records():
    configure_logging(level="INFO")
    root = logging.getLogger()
    handler = root.handlers[0]
    buffer = io.StringIO()
    handler.stream = buffer  # type: ignore[attr-defined]

    logger = logging.getLogger("minx_mcp.contracts")
    logger.info(
        "tool call",
        extra={"tool": "example_tool", "duration_ms": 3, "success": True},
    )

    output = buffer.getvalue().strip()
    assert output
    payload = json.loads(output)
    assert payload["tool"] == "example_tool"
    assert payload["duration_ms"] == 3
    assert payload["success"] is True


@pytest.fixture(autouse=True)
def _reset_root_logger():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.setLevel(original_level)
