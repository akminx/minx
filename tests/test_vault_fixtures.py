"""Smoke tests for ``tests/vault_fixtures``.

These don't exercise production code — they exercise the fixtures
themselves, so a broken builder is caught here rather than showing up
as confusing failures in downstream tests that use it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minx_mcp.core.vault_memory_frontmatter import parse_memory_identity, parse_memory_payload
from minx_mcp.vault_reader import VaultReader

from .vault_fixtures import frozen_clock, make_memory_note, vault_note


def _read(vault_root: Path, relative: Path) -> object:
    reader = VaultReader(vault_root, allowed_prefixes=("Minx",))
    return reader.read_document(str(relative))


def test_make_memory_note_round_trips_through_reader(tmp_path: Path) -> None:
    note_path = make_memory_note(
        tmp_path,
        scope="core",
        memory_type="preference",
        subject="tea",
        payload={"kind": "earl-grey", "strength": 3},
        memory_id=42,
        sync_base_updated_at="2026-03-15T12:00:00+00:00",
        body="Preferred tea for morning meetings.",
    )

    document = _read(tmp_path, note_path.relative_to(tmp_path))
    identity = parse_memory_identity(document.frontmatter)
    payload = parse_memory_payload(document.frontmatter, allow_implicit=False)

    assert identity is not None
    assert identity.scope == "core"
    assert identity.memory_type == "preference"
    assert identity.subject == "tea"
    assert identity.memory_id == 42
    assert payload == {"kind": "earl-grey", "strength": 3}
    assert document.body.strip() == "Preferred tea for morning meetings."


def test_make_memory_note_omits_optional_fields(tmp_path: Path) -> None:
    note_path = make_memory_note(
        tmp_path,
        scope="core",
        memory_type="fact",
        subject="capital",
        payload={"value": "Paris"},
    )
    text = note_path.read_text(encoding="utf-8")
    assert "memory_id" not in text
    assert "sync_base_updated_at" not in text


def test_vault_note_supports_flow_lists_and_quoting(tmp_path: Path) -> None:
    note_path = vault_note(
        tmp_path,
        "Minx/Notes/quirks.md",
        frontmatter={
            "type": "minx-memory",
            "scope": "core",
            "tags": ["alpha", "beta with space", "needs: quoting"],
            "note": "has: colon",
        },
    )
    document = _read(tmp_path, note_path.relative_to(tmp_path))
    assert document.frontmatter["tags"] == ["alpha", "beta with space", "needs: quoting"]
    assert document.frontmatter["note"] == "has: colon"


def test_vault_note_rejects_colliding_extra_keys(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="collides"):
        make_memory_note(
            tmp_path,
            scope="core",
            memory_type="fact",
            subject="x",
            payload={},
            extra_frontmatter={"scope": "override"},
        )


def test_frozen_clock_pins_goal_tools_today(monkeypatch: pytest.MonkeyPatch) -> None:
    from minx_mcp.core.tools import goals as goal_tools_module

    with frozen_clock(monkeypatch, "2026-03-15T12:00:00+00:00") as clock:
        assert goal_tools_module.date.today().isoformat() == "2026-03-15"
        assert clock.date_iso == "2026-03-15"
        assert clock.iso.startswith("2026-03-15T12:00:00")
