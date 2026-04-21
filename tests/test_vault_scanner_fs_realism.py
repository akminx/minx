"""Filesystem-realism tests for :class:`~minx_mcp.core.vault_scanner.VaultScanner`."""

from __future__ import annotations

from pathlib import Path

import pytest

from minx_mcp.core.vault_scanner import VaultScanner
from minx_mcp.db import get_connection
from minx_mcp.vault_reader import VaultReader
from tests.vault_fixtures import make_memory_note


def _scanner(tmp_path: Path):
    db_path = tmp_path / "minx.db"
    vault_root = tmp_path / "vault"
    conn = get_connection(db_path)
    reader = VaultReader(vault_root, ("Minx",))
    return conn, VaultScanner(conn, reader)


def test_scanner_symlink_mirror_does_not_double_index_memory_note(tmp_path: Path) -> None:
    """Symlink mirror: same inode must not produce two ``vault_index`` rows or two memories.

    ``iter_markdown_paths`` dedupes by resolved path, so a symlinked copy under ``Minx/Mirror/``
    is ignored once ``Minx/Memory/a.md`` is queued. Removing that dedupe would make the scanner
    visit duplicate ``VaultDocument`` paths and hit ``UNIQUE (vault_path)`` or double-sync the
    same memory identity.
    """
    vault = tmp_path / "vault"
    make_memory_note(
        vault,
        relative_path="Minx/Memory/a.md",
        memory_type="preference",
        subject="symlink_mirror_a",
        payload={"value": "one"},
    )
    mirror_dir = vault / "Minx" / "Mirror"
    mirror_dir.mkdir(parents=True, exist_ok=True)
    link = mirror_dir / "a.md"
    try:
        link.symlink_to(Path("..") / "Memory" / "a.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported on this FS")

    conn, scanner = _scanner(tmp_path)
    report = scanner.scan()

    rows = conn.execute(
        "SELECT vault_path, content_hash, memory_id FROM vault_index ORDER BY vault_path"
    ).fetchall()
    memory_rows = conn.execute("SELECT id FROM memories").fetchall()

    assert len(memory_rows) == 1
    paths = [str(r["vault_path"]) for r in rows]
    assert paths == ["Minx/Memory/a.md"]
    assert len(paths) == 1
    assert report.scanned == 1
    assert report.indexed == 1
    assert report.memory_syncs == 1
