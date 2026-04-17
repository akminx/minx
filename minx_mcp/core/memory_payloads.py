"""Pydantic schemas for canonical memory ``payload_json`` objects (Slice 6)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minx_mcp.contracts import InvalidInputError


class _ExtraForbidModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PreferencePayload(_ExtraForbidModel):
    """User-stated preferences."""

    note: str | None = None
    category: str | None = None
    value: str | None = None


class PatternPayload(_ExtraForbidModel):
    """Detected recurring behavior."""

    note: str | None = None
    frequency: str | None = None  # e.g. "weekly", "monthly"
    observed_count: int | None = Field(default=None, ge=0)
    signal: str | None = None  # detector-provided signal name


class EntityFactPayload(_ExtraForbidModel):
    """Knowledge about entities (merchants, places, foods)."""

    note: str | None = None
    category: str | None = None  # e.g. "grocery", "restaurant"
    aliases: list[str] | None = None


class ConstraintPayload(_ExtraForbidModel):
    """Limits, budgets, rules."""

    note: str | None = None
    kind: str | None = None  # e.g. "budget", "dietary"
    limit_value: str | None = None  # stringly-typed to avoid decimal/unit confusion
    unit: str | None = None  # e.g. "USD/week", "grams/day"


PAYLOAD_MODELS: dict[str, type[_ExtraForbidModel]] = {
    "preference": PreferencePayload,
    "pattern": PatternPayload,
    "entity_fact": EntityFactPayload,
    "constraint": ConstraintPayload,
}


def validate_memory_payload(memory_type: str, payload: dict[str, object]) -> dict[str, object]:
    """Validate payload against the registered model for memory_type.

    Unknown memory_type is permissive: returns the payload unchanged (future
    types — e.g. 'identity' that a future slice introduces — should not be
    rejected just because this module has not yet registered a schema).

    Known memory_type: parses via the model, re-serializes to dict
    (exclude_unset=False) so the returned payload has predictable shape.
    Raises InvalidInputError on validation failure with a human-readable
    message naming the offending field.
    """
    model = PAYLOAD_MODELS.get(memory_type)
    if model is None:
        return payload
    try:
        parsed = model.model_validate(payload)
    except ValidationError as exc:
        # Compact pydantic error message for the envelope; the full
        # structured error is discarded to avoid leaking internals.
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise InvalidInputError(
            f"invalid payload for memory_type={memory_type!r}: {problems}"
        ) from exc
    return parsed.model_dump(exclude_none=False)
