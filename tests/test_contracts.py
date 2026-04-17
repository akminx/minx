import asyncio
import logging

import pytest

from minx_mcp.contracts import (
    INTERNAL_ERROR,
    INVALID_INPUT,
    InvalidInputError,
    fail,
    ok,
    wrap_async_tool_call,
    wrap_tool_call,
)


def test_ok_wraps_success_payload():
    assert ok({"updated": 1}) == {
        "success": True,
        "data": {"updated": 1},
        "error": None,
        "error_code": None,
    }


def test_fail_wraps_error_payload():
    assert fail("bad input", INVALID_INPUT) == {
        "success": False,
        "data": None,
        "error": "bad input",
        "error_code": INVALID_INPUT,
    }


def test_wrap_tool_call_converts_contract_error_to_failure_envelope():
    def raise_invalid_input():
        raise InvalidInputError("bad input")

    result = wrap_tool_call(raise_invalid_input)

    assert result == {
        "success": False,
        "data": None,
        "error": "bad input",
        "error_code": INVALID_INPUT,
    }


def test_wrap_tool_call_logs_unexpected_exception_and_returns_internal_error(caplog):
    caplog.set_level(logging.ERROR)

    def raise_runtime_error():
        raise RuntimeError("boom")

    result = wrap_tool_call(raise_runtime_error)

    assert result == {
        "success": False,
        "data": None,
        "error": "Internal server error",
        "error_code": INTERNAL_ERROR,
    }
    assert "boom" in caplog.text


def test_wrap_tool_call_emits_structured_fields_on_success(caplog):
    caplog.set_level(logging.INFO, logger="minx_mcp.contracts")

    wrap_tool_call(lambda: {"value": 1}, tool_name="example_tool")

    records = [record for record in caplog.records if record.name == "minx_mcp.contracts"]
    assert len(records) == 1
    record = records[0]
    assert record.tool == "example_tool"  # type: ignore[attr-defined]
    assert record.success is True  # type: ignore[attr-defined]
    assert isinstance(record.duration_ms, int)  # type: ignore[attr-defined]
    assert record.duration_ms >= 0  # type: ignore[attr-defined]


def test_wrap_tool_call_emits_structured_fields_on_contract_error(caplog):
    caplog.set_level(logging.WARNING, logger="minx_mcp.contracts")

    def raise_invalid_input():
        raise InvalidInputError("bad input")

    wrap_tool_call(raise_invalid_input, tool_name="example_tool")

    records = [record for record in caplog.records if record.name == "minx_mcp.contracts"]
    assert len(records) == 1
    record = records[0]
    assert record.tool == "example_tool"  # type: ignore[attr-defined]
    assert record.success is False  # type: ignore[attr-defined]
    assert record.error_code == INVALID_INPUT  # type: ignore[attr-defined]
    assert isinstance(record.duration_ms, int)  # type: ignore[attr-defined]


def test_wrap_tool_call_emits_structured_fields_on_unexpected_error(caplog):
    caplog.set_level(logging.WARNING, logger="minx_mcp.contracts")

    def raise_runtime_error():
        raise RuntimeError("boom")

    wrap_tool_call(raise_runtime_error, tool_name="example_tool")

    structured_records = [
        record
        for record in caplog.records
        if record.name == "minx_mcp.contracts"
        and getattr(record, "tool", None) == "example_tool"
    ]
    assert len(structured_records) == 1
    record = structured_records[0]
    assert record.success is False  # type: ignore[attr-defined]
    assert record.error_code == INTERNAL_ERROR  # type: ignore[attr-defined]
    assert isinstance(record.duration_ms, int)  # type: ignore[attr-defined]


def test_wrap_async_tool_call_emits_structured_fields_on_success(caplog):
    caplog.set_level(logging.INFO, logger="minx_mcp.contracts")

    async def success():
        return {"value": 1}

    result = asyncio.run(wrap_async_tool_call(success, tool_name="async_example"))

    assert result["success"] is True
    records = [record for record in caplog.records if record.name == "minx_mcp.contracts"]
    assert len(records) == 1
    record = records[0]
    assert record.tool == "async_example"  # type: ignore[attr-defined]
    assert record.success is True  # type: ignore[attr-defined]
    assert isinstance(record.duration_ms, int)  # type: ignore[attr-defined]


def test_wrap_async_tool_call_emits_structured_fields_on_error(caplog):
    caplog.set_level(logging.WARNING, logger="minx_mcp.contracts")

    async def boom():
        raise InvalidInputError("bad input")

    result = asyncio.run(wrap_async_tool_call(boom, tool_name="async_example"))

    assert result["success"] is False
    records = [record for record in caplog.records if record.name == "minx_mcp.contracts"]
    assert len(records) == 1
    record = records[0]
    assert record.tool == "async_example"  # type: ignore[attr-defined]
    assert record.success is False  # type: ignore[attr-defined]
    assert record.error_code == INVALID_INPUT  # type: ignore[attr-defined]


def test_wrap_tool_call_defaults_tool_name_to_empty(caplog):
    caplog.set_level(logging.INFO, logger="minx_mcp.contracts")

    wrap_tool_call(lambda: {"value": 1})

    records = [record for record in caplog.records if record.name == "minx_mcp.contracts"]
    assert len(records) == 1
    record = records[0]
    assert record.tool == ""  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _reset_contracts_logger():
    logger = logging.getLogger("minx_mcp.contracts")
    original_level = logger.level
    original_propagate = logger.propagate
    logger.propagate = True
    yield
    logger.setLevel(original_level)
    logger.propagate = original_propagate
