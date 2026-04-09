from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

INVALID_INPUT = "INVALID_INPUT"
NOT_FOUND = "NOT_FOUND"
CONFLICT = "CONFLICT"
INTERNAL_ERROR = "INTERNAL_ERROR"

logger = logging.getLogger(__name__)


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


def ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None, "error_code": None}


def fail(message: str, error_code: str, data: Any | None = None) -> dict[str, Any]:
    return {"success": False, "data": data, "error": message, "error_code": error_code}


def wrap_tool_call(fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        return ok(fn())
    except MinxContractError as exc:
        return fail(exc.message, exc.error_code, exc.data)
    except Exception:
        logger.exception("Unexpected exception in MCP tool")
        return fail("Internal server error", INTERNAL_ERROR)


async def wrap_async_tool_call(fn: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    try:
        return ok(await fn())
    except MinxContractError as exc:
        return fail(exc.message, exc.error_code, exc.data)
    except Exception:
        logger.exception("Unexpected exception in MCP tool")
        return fail("Internal server error", INTERNAL_ERROR)
