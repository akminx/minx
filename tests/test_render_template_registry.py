"""Tests for the render template registry.

Implements the validation surface promised by spec
2026-04-29-render-template-registry.md and gives 2026-04-28-mcp-render-contract.md
its enforcement teeth. Adding a new template to the registry should be the
forcing function for any Core call site that emits it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from minx_mcp.core import render_templates as rt
from minx_mcp.core.investigations import (
    KIND_VALUES,
    STEP_EVENT_TEMPLATES,
    TERMINAL_RESPONSE_TEMPLATES,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


SPEC_INITIAL_IDS = frozenset(
    {
        "finance_query.clarify.missing_filter",
        "finance_query.clarify.missing_date_range",
        "goal_parse.create.ready",
        "goal_parse.update.ready",
        "goal_parse.no_match.unsupported",
        "goal_parse.clarify.ambiguous_goal",
        "goal_parse.clarify.ambiguous_subject",
        "goal_parse.clarify.missing_goal",
        "goal_parse.clarify.missing_target",
        "goal_parse.clarify.vague_intent",
        "memory_capture.created_candidate",
        "investigation.started",
        "investigation.step_logged",
        "investigation.needs_confirmation",
        "investigation.completed",
        "investigation.failed",
        "investigation.cancelled",
        "investigation.budget_exhausted",
    }
)


def test_registry_covers_spec_initial_contents() -> None:
    assert set(rt.RENDER_TEMPLATES) >= SPEC_INITIAL_IDS


def test_template_ids_match_their_keys() -> None:
    for key, template in rt.RENDER_TEMPLATES.items():
        assert key == template.id


def test_required_slots_are_strings() -> None:
    for template in rt.RENDER_TEMPLATES.values():
        for slot in template.required_slots:
            assert isinstance(slot, str) and slot
            # Slot names are JSON keys; keep them simple identifiers.
            assert re.fullmatch(r"[a-z][a-z0-9_]*", slot)


def test_validate_slots_passes_when_required_present() -> None:
    rt.validate_slots(
        rt.MEMORY_CAPTURE_CREATED_CANDIDATE,
        {"memory_id": 1, "capture_type": "thought", "subject": "x", "extra": "ok"},
    )


def test_validate_slots_rejects_unknown_template() -> None:
    with pytest.raises(ValueError, match="unknown render template"):
        rt.validate_slots("not.a.template", {})


def test_validate_slots_rejects_missing_required() -> None:
    with pytest.raises(ValueError, match="missing required slots"):
        rt.validate_slots(rt.GOAL_PARSE_CREATE_READY, {"action": "goal_create"})


def test_investigation_terminal_templates_match_registry() -> None:
    for tmpl in TERMINAL_RESPONSE_TEMPLATES.values():
        assert rt.is_registered(tmpl)


def test_investigation_step_templates_match_registry() -> None:
    for tmpl in STEP_EVENT_TEMPLATES:
        assert rt.is_registered(tmpl)


def test_kind_values_immutable_contract() -> None:
    # The Hermes /minx-onboard-entity bug shipped because a skill emitted a
    # kind value not in this set. Lock the contract here so a future rename
    # is a deliberate decision, not a silent break.
    assert frozenset({"investigate", "plan", "retro", "onboard", "other"}) == KIND_VALUES


def test_no_unregistered_template_literals_in_core() -> None:
    """Walk core code; flag literals that look like template IDs but aren't registered.

    Catches the onboard_entity-class drift: a string literal like
    ``"investigation.foo"`` slipped into call code without a registry entry.
    """

    pattern = re.compile(
        r"\"((?:finance_query|goal_parse|memory_capture|investigation)\.[a-z0-9_.]+)\""
    )
    offenders: list[tuple[Path, int, str]] = []
    core_dir = REPO_ROOT / "minx_mcp" / "core"
    for path in core_dir.rglob("*.py"):
        if path.name == "render_templates.py":
            continue
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in pattern.finditer(line):
                template_id = match.group(1)
                if not rt.is_registered(template_id):
                    offenders.append((path, line_no, template_id))
    assert offenders == [], (
        "Unregistered template-shaped literals found; add to RENDER_TEMPLATES or "
        f"remove the literal: {offenders}"
    )
