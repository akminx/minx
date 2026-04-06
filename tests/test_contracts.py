import logging

from minx_mcp.contracts import (
    INTERNAL_ERROR,
    INVALID_INPUT,
    InvalidInputError,
    fail,
    ok,
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
