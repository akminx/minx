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


# captured_thought is intentionally omitted so quick captures use permissive
# unknown-type validation until they graduate to a stricter schema.
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

    Known memory_type: parses via the model and re-serializes with
    ``exclude_unset=True`` so fields the caller did NOT provide stay out of
    the stored JSON. Previously we used ``exclude_none=False``, which
    expanded ``{}`` into ``{"note": null, "category": null, ...}`` — that
    bloated rows, changed downstream event shapes, and broke any consumer
    using ``"key" in payload`` as a presence check.

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
    return parsed.model_dump(exclude_unset=True)


def coerce_prior_payload_to_schema(
    memory_type: str, prior_payload: dict[str, object]
) -> dict[str, object]:
    """Best-effort normalize a legacy/pre-schema payload into the current model.

    Used by ``MemoryService.ingest_proposals`` when merging a new proposal
    onto an existing row: the existing row may pre-date the pydantic models
    (Slice 6a data) and contain unknown keys that would fail the strict
    ``extra="forbid"`` validator. We drop those keys rather than failing the
    whole merge, so stale junk decays out naturally on the first merge rather
    than compounding forever.

    Unknown memory_type: returns the payload unchanged (permissive, same as
    ``validate_memory_payload``).
    """
    model = PAYLOAD_MODELS.get(memory_type)
    if model is None:
        return prior_payload
    # Build a filtered dict restricted to the model's known field names, then
    # validate. Any values that are the wrong TYPE are also dropped (e.g. a
    # legacy string where we now expect int), falling back to an empty dict
    # if everything is unusable.
    known_fields = set(model.model_fields.keys())
    filtered: dict[str, object] = {k: v for k, v in prior_payload.items() if k in known_fields}
    try:
        parsed = model.model_validate(filtered)
    except ValidationError:
        return {}
    return parsed.model_dump(exclude_unset=True)
