from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

INVALID_INPUT = "INVALID_INPUT"
NOT_FOUND = "NOT_FOUND"
CONFLICT = "CONFLICT"
INTERNAL_ERROR = "INTERNAL_ERROR"
LLM_ERROR = "LLM_ERROR"

logger = logging.getLogger(__name__)


class ToolResponse(TypedDict, total=False):
    success: bool
    data: Any
    error: str | None
    error_code: str | None


class MinxContractError(Exception):
    def __init__(self, message: str, error_code: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.data = data


class InvalidInputError(MinxContractError):
    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(message, INVALID_INPUT, data)


class NotFoundError(MinxContractError):
    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(message, NOT_FOUND, data)


class ConflictError(MinxContractError):
    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(message, CONFLICT, data)


class LLMError(MinxContractError):
    """Raised when an LLM provider call, response shape, or schema validation fails.

    Maps to ``error_code = "LLM_ERROR"``. Clients should treat this as
    potentially-transient: retry with backoff, fall back to a deterministic
    path, or surface a user-visible "AI subsystem unavailable" message.
    It is *not* ``INTERNAL_ERROR``, which means an unexpected bug on the
    server side.
    """

    code = LLM_ERROR

    def __init__(self, message: str = "LLM error", data: Any | None = None) -> None:
        super().__init__(message, LLM_ERROR, data)


def ok(data: Any) -> ToolResponse:
    return {"success": True, "data": data, "error": None, "error_code": None}


def fail(message: str, error_code: str, data: Any | None = None) -> ToolResponse:
    return {"success": False, "data": data, "error": message, "error_code": error_code}


def _handle_tool_error(exc: Exception, tool_name: str, start: float) -> ToolResponse:
    duration_ms = int((time.monotonic() - start) * 1000)
    if isinstance(exc, MinxContractError):
        logger.warning(
            "tool call failed",
            extra={
                "tool": tool_name,
                "duration_ms": duration_ms,
                "success": False,
                "error_code": exc.error_code,
            },
        )
        return fail(exc.message, exc.error_code, exc.data)
    logger.exception("Unexpected exception in MCP tool")
    logger.warning(
        "tool call failed",
        extra={
            "tool": tool_name,
            "duration_ms": duration_ms,
            "success": False,
            "error_code": INTERNAL_ERROR,
        },
    )
    return fail("Internal server error", INTERNAL_ERROR)


def wrap_tool_call(fn: Callable[[], Any], tool_name: str = "") -> ToolResponse:
    start = time.monotonic()
    try:
        result = ok(fn())
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "tool call",
            extra={"tool": tool_name, "duration_ms": duration_ms, "success": True},
        )
    except Exception as exc:
        return _handle_tool_error(exc, tool_name, start)
    else:
        return result


async def wrap_async_tool_call(
    fn: Callable[[], Awaitable[Any]], tool_name: str = ""
) -> ToolResponse:
    start = time.monotonic()
    try:
        result = ok(await fn())
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "tool call",
            extra={"tool": tool_name, "duration_ms": duration_ms, "success": True},
        )
    except Exception as exc:
        return _handle_tool_error(exc, tool_name, start)
    else:
        return result
