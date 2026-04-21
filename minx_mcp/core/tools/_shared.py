"""Shared helpers used by more than one tool-registration module.

Anything here must be used by >=2 of the sibling ``tools/*.py`` modules.
Single-domain helpers belong in their own module, not here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from minx_mcp.contracts import InvalidInputError
from minx_mcp.validation import resolve_date_or_today

__all__ = [
    "CoreServiceConfig",
    "coerce_limit",
    "resolve_review_date",
]


class CoreServiceConfig(Protocol):
    @property
    def db_path(self) -> Path: ...

    @property
    def vault_path(self) -> Path: ...


def resolve_review_date(review_date: str | None) -> str:
    return resolve_date_or_today(review_date, field_name="review_date")


def coerce_limit(value: int, *, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInputError("limit must be an integer")
    if value < 1 or value > maximum:
        raise InvalidInputError(f"limit must be between 1 and {maximum}")
    return value
