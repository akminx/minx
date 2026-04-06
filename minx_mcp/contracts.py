from __future__ import annotations

import logging
from typing import Any, Callable

INVALID_INPUT = "INVALID_INPUT"
NOT_FOUND = "NOT_FOUND"
CONFLICT = "CONFLICT"
INTERNAL_ERROR = "INTERNAL_ERROR"

logger = logging.getLogger(__name__)


class MinxContractError(Exception):
    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class InvalidInputError(MinxContractError):
    def __init__(self, message: str) -> None:
        super().__init__(message, INVALID_INPUT)


class NotFoundError(MinxContractError):
    def __init__(self, message: str) -> None:
        super().__init__(message, NOT_FOUND)


class ConflictError(MinxContractError):
    def __init__(self, message: str) -> None:
        super().__init__(message, CONFLICT)


def ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None, "error_code": None}


def fail(message: str, error_code: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": message, "error_code": error_code}


def wrap_tool_call(fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        return ok(fn())
    except MinxContractError as exc:
        return fail(exc.message, exc.error_code)
    except Exception:
        logger.exception("Unexpected exception in MCP tool")
        return fail("Internal server error", INTERNAL_ERROR)
