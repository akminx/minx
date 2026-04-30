"""Render template registry.

Implements 2026-04-29-render-template-registry.md and gives the
2026-04-28-mcp-render-contract.md its enforcement surface. Template IDs are
append-only contracts shared between Core and any harness (Hermes, dashboard,
etc.). Adding a slot is fine; changing the meaning of an existing ID is not —
mint a new ID instead.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RenderTemplate:
    id: str
    required_slots: frozenset[str]


def _t(id: str, *required: str) -> tuple[str, RenderTemplate]:
    return id, RenderTemplate(id=id, required_slots=frozenset(required))


RENDER_TEMPLATES: dict[str, RenderTemplate] = dict(
    [
        _t("finance_query.clarify.missing_filter", "field"),
        _t("finance_query.clarify.missing_date_range", "field"),
        _t("goal_parse.create.ready", "action", "goal_type"),
        _t("goal_parse.update.ready", "action", "goal_id"),
        _t("goal_parse.no_match.unsupported", "status"),
        _t("goal_parse.clarify.ambiguous_goal", "field"),
        _t("goal_parse.clarify.ambiguous_subject", "field"),
        _t("goal_parse.clarify.missing_goal", "field"),
        _t("goal_parse.clarify.missing_target", "field"),
        _t("goal_parse.clarify.vague_intent", "field"),
        _t("memory_capture.created_candidate", "memory_id", "capture_type", "subject"),
        _t("investigation.started", "kind", "harness", "status"),
        _t("investigation.step_logged", "investigation_id", "step_index"),
        _t("investigation.needs_confirmation", "investigation_id", "step_index"),
        _t("investigation.completed", "investigation_id", "status"),
        _t("investigation.failed", "investigation_id", "status"),
        _t("investigation.cancelled", "investigation_id", "status"),
        _t("investigation.budget_exhausted", "investigation_id", "status"),
    ]
)


# Convenience constants for call sites — string literals stay invalid in code.
FINANCE_QUERY_CLARIFY_MISSING_FILTER = "finance_query.clarify.missing_filter"
FINANCE_QUERY_CLARIFY_MISSING_DATE_RANGE = "finance_query.clarify.missing_date_range"
GOAL_PARSE_CREATE_READY = "goal_parse.create.ready"
GOAL_PARSE_UPDATE_READY = "goal_parse.update.ready"
GOAL_PARSE_NO_MATCH_UNSUPPORTED = "goal_parse.no_match.unsupported"
MEMORY_CAPTURE_CREATED_CANDIDATE = "memory_capture.created_candidate"
INVESTIGATION_STARTED = "investigation.started"
INVESTIGATION_STEP_LOGGED = "investigation.step_logged"
INVESTIGATION_NEEDS_CONFIRMATION = "investigation.needs_confirmation"
INVESTIGATION_COMPLETED = "investigation.completed"
INVESTIGATION_FAILED = "investigation.failed"
INVESTIGATION_CANCELLED = "investigation.cancelled"
INVESTIGATION_BUDGET_EXHAUSTED = "investigation.budget_exhausted"


def is_registered(template_id: str) -> bool:
    return template_id in RENDER_TEMPLATES


def required_slots(template_id: str) -> frozenset[str]:
    return RENDER_TEMPLATES[template_id].required_slots


def validate_slots(template_id: str, slots: dict[str, object]) -> None:
    """Raise ValueError if template_id is unknown or required slots are missing.

    Used by tests and by callers who want explicit validation. Production
    emit sites are expected to construct slot dicts that already satisfy the
    contract; this is a defense-in-depth check, not a runtime gate.
    """

    template = RENDER_TEMPLATES.get(template_id)
    if template is None:
        raise ValueError(f"unknown render template: {template_id}")
    missing = template.required_slots - slots.keys()
    if missing:
        raise ValueError(
            f"render template {template_id} missing required slots: {sorted(missing)}"
        )
