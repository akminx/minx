"""Shared vault-note and clock fixtures for tests.

Motivation: ``tests/test_vault_reconciler.py`` and ``tests/test_vault_scanner.py``
together contain ~50 hand-rolled frontmatter string literals of the form
``"---\\ntype: minx-memory\\nscope: core\\n..."``. Every one is a chance to
mis-indent, forget a key, or diverge from what the production
:class:`~minx_mcp.vault_writer.VaultWriter` emits. These helpers produce
frontmatter in the same shape the writer canonicalizes to, so fixture notes
round-trip through the reader without surprises.

These helpers are intentionally additive — existing tests are not being
rewritten to use them. They exist so **new** tests (and any test you happen
to be touching anyway) stay small and don't drift from the real frontmatter
grammar.

The emitter mirrors the subset of YAML that
:func:`minx_mcp.vault_reader._parse_frontmatter_value` understands:
plain scalars, single-quoted strings (for values containing colons or
leading special chars), bracketed flow lists, and brace-wrapped JSON-ish
mappings (e.g. ``payload_json``). Anything outside that subset raises
:class:`ValueError` at fixture-build time rather than letting a malformed
note silently reach the reader.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

__all__ = [
    "FrozenClock",
    "frozen_clock",
    "make_memory_note",
    "vault_note",
]


# ---------------------------------------------------------------------------
# Frontmatter serialization
# ---------------------------------------------------------------------------


_NEEDS_QUOTING_CHARS = frozenset(":#[]{}&*!|>'\"%@`")


def _emit_scalar(value: Any) -> str:
    """Render a single frontmatter value in a form the reader accepts.

    Strings that contain any YAML metacharacter are wrapped in single quotes
    (with embedded single quotes doubled per YAML spec). Booleans, ints, and
    floats pass through as their ``str()`` representation — matching the
    writer's emit and the reader's scalar coercion.
    """

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        if value == "":
            return "''"
        if any(ch in _NEEDS_QUOTING_CHARS for ch in value) or value[0].isspace():
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        return value
    raise ValueError(
        f"vault_fixtures cannot emit scalar of type {type(value).__name__}: {value!r}"
    )


def _emit_list(items: list[Any]) -> str:
    parts: list[str] = []
    for item in items:
        parts.append(_emit_scalar(item))
    return "[" + ", ".join(parts) + "]"


def _emit_value(value: Any) -> str:
    if isinstance(value, list):
        return _emit_list(value)
    if isinstance(value, dict):
        # payload_json et al. round-trip through the reader as the literal
        # "{...}" string; the reconciler then JSON-decodes it. Match that.
        return json.dumps(value, sort_keys=True)
    return _emit_scalar(value)


def _emit_frontmatter(fm: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for key, value in fm.items():
        if not key or not key[0].isalpha() and key[0] != "_":
            raise ValueError(f"invalid frontmatter key: {key!r}")
        lines.append(f"{key}: {_emit_value(value)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Vault note builders
# ---------------------------------------------------------------------------


def vault_note(
    vault_root: Path,
    relative_path: str,
    *,
    frontmatter: Mapping[str, Any],
    body: str = "",
) -> Path:
    """Write ``---\\n<fm>\\n---\\n<body>\\n`` to ``vault_root/relative_path``.

    Parent directories are created as needed. Returns the absolute path so
    callers can mutate or unlink the file. Body defaults to empty string —
    tests exercising frontmatter-only behavior shouldn't have to pass a body.
    """

    fm_block = _emit_frontmatter(frontmatter)
    target = vault_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    text = f"---\n{fm_block}\n---\n{body}"
    if not text.endswith("\n"):
        text += "\n"
    target.write_text(text, encoding="utf-8")
    return target


def make_memory_note(
    vault_root: Path,
    *,
    relative_path: str | None = None,
    scope: str = "core",
    memory_type: str,
    subject: str,
    payload: Mapping[str, Any],
    memory_id: int | None = None,
    sync_base_updated_at: str | None = None,
    extra_frontmatter: Mapping[str, Any] | None = None,
    body: str = "",
) -> Path:
    """Write a ``type: minx-memory`` note using the canonical key order.

    ``payload`` is emitted into ``payload_json`` as a JSON-ish scalar the
    reconciler will decode. ``memory_id`` and ``sync_base_updated_at`` are
    only emitted when provided so you can build both "new" and "known"
    notes without sprinkling ``None`` into the produced YAML.

    If ``relative_path`` is omitted, defaults to
    ``Minx/Memory/{subject}.md`` under the canonical ``Minx`` prefix.
    """

    fm: dict[str, Any] = {
        "type": "minx-memory",
        "scope": scope,
        "memory_key": f"{scope}.{memory_type}.{subject}",
        "memory_type": memory_type,
        "subject": subject,
    }
    if memory_id is not None:
        fm["memory_id"] = int(memory_id)
    if sync_base_updated_at is not None:
        fm["sync_base_updated_at"] = sync_base_updated_at
    fm["payload_json"] = dict(payload)
    if extra_frontmatter:
        for key, value in extra_frontmatter.items():
            if key in fm:
                raise ValueError(
                    f"extra_frontmatter key {key!r} collides with a canonical memory key"
                )
            fm[key] = value

    if relative_path is None:
        relative_path = f"Minx/Memory/{subject}.md"
    return vault_note(vault_root, relative_path, frontmatter=fm, body=body)


# ---------------------------------------------------------------------------
# Frozen clock
# ---------------------------------------------------------------------------


@dataclass
class FrozenClock:
    """Test handle for a pinned wall-clock instant.

    The clock is monkey-patched onto specific modules by :func:`frozen_clock`
    because Python has no single "time source" — each module imports its
    own ``datetime`` / ``date``. Only modules that actually read wall-clock
    time need patching; we list them explicitly rather than swapping the
    stdlib so the patch surface is obvious.
    """

    instant: datetime

    @property
    def iso(self) -> str:
        return self.instant.isoformat()

    @property
    def date_iso(self) -> str:
        return self.instant.date().isoformat()


@contextmanager
def frozen_clock(
    monkeypatch: pytest.MonkeyPatch,
    instant: str | datetime = "2026-03-15T12:00:00+00:00",
) -> Iterator[FrozenClock]:
    """Pin ``date.today()`` and ``datetime.now(tz)`` for time-sensitive tests.

    Usage::

        def test_something(monkeypatch):
            with frozen_clock(monkeypatch, "2026-03-15T12:00:00+00:00") as clock:
                # code under test sees clock.instant / clock.date_iso

    Currently targets modules we've verified read wall-clock time:
    ``minx_mcp.core.server`` (``date.today()`` in goal_create default).
    Add new targets to ``_PATCH_TARGETS`` as needed; each target must
    import ``date`` / ``datetime`` directly (not ``from .. import date``
    from somewhere that itself aliases).

    Yields a :class:`FrozenClock` holder so tests can reference the pinned
    instant without reconstructing it from strings.
    """

    pinned = datetime.fromisoformat(instant) if isinstance(instant, str) else instant
    if pinned.tzinfo is None:
        pinned = pinned.replace(tzinfo=UTC)

    class _FrozenDate(date):
        @classmethod
        def today(cls) -> date:
            return pinned.date()

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            if tz is None:
                return pinned.replace(tzinfo=None)
            return pinned.astimezone(tz)

        @classmethod
        def utcnow(cls) -> datetime:  # type: ignore[override]
            return pinned.replace(tzinfo=None)

    # Narrow, explicit patch list — see docstring. Expand deliberately.
    _PATCH_TARGETS = [
        ("minx_mcp.core.server", "date", _FrozenDate),
    ]
    for module_path, attr, replacement in _PATCH_TARGETS:
        # monkeypatch.setattr with a string target fails if the attribute
        # doesn't exist; that's the early-warning we want if a module
        # stops importing date/datetime directly.
        monkeypatch.setattr(f"{module_path}.{attr}", replacement, raising=True)

    yield FrozenClock(instant=pinned)
