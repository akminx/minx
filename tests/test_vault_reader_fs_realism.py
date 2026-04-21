"""Filesystem-realism tests for :class:`~minx_mcp.vault_reader.VaultReader`.

Exercises ``read_document`` / ``iter_markdown_paths`` against BOM handling,
newline variants, symlinks, case-folding, and sort order — behavior that
depends on the host OS and on-disk bytes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.vault_reader import VaultReader
from tests.vault_fixtures import vault_note


def _reader(vault_root: Path) -> VaultReader:
    return VaultReader(vault_root, allowed_prefixes=("Minx",))


def _try_symlink(link: Path, target: str | Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unsupported: {exc}")


def test_read_document_strips_utf8_bom_for_frontmatter_and_body(tmp_path: Path) -> None:
    """BOM prefix: ``---`` matches line 1 after decode; body must not start with U+FEFF."""
    raw_text = (
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.fact.bom_note\n"
        "memory_type: fact\n"
        "subject: bom_note\n"
        "marker: 1\n"
        "---\n"
        "Hello BOM\n"
    )
    path = tmp_path / "Minx/Memory/bom.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + raw_text.encode("utf-8"))

    doc = _reader(tmp_path).read_document("Minx/Memory/bom.md")
    assert doc.frontmatter["subject"] == "bom_note"
    assert doc.frontmatter["marker"] == 1
    assert doc.body == "Hello BOM"
    assert not doc.body.startswith("\ufeff")
    assert "\ufeff" not in doc.body


def test_read_document_crlf_line_endings_normalize_via_splitlines(tmp_path: Path) -> None:
    """CRLF on disk: frontmatter parses; body lines become ``\\n``-joined (no ``\\r``)."""
    crlf = (
        b"---\r\n"
        b"k: one\r\n"
        b"---\r\n"
        b"First line\r\n"
        b"Second line\r\n"
    )
    path = tmp_path / "Minx/Memory/crlf.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(crlf)

    doc = _reader(tmp_path).read_document("Minx/Memory/crlf.md")
    assert doc.frontmatter == {"k": "one"}
    assert doc.body == "First line\nSecond line"
    assert "\r" not in doc.body


def test_read_document_mixed_crlf_and_lf_between_frontmatter_and_body(tmp_path: Path) -> None:
    """LF fences + CRLF body, and CRLF fences + LF body: both halves parse."""
    mem = tmp_path / "Minx/Memory"
    mem.mkdir(parents=True, exist_ok=True)

    lf_fm_crlf_body = (
        b"---\n"
        b"section: top\n"
        b"---\n"
        b"alpha\r\n"
        b"beta\r\n"
    )
    (mem / "mixed_lf_fm.md").write_bytes(lf_fm_crlf_body)

    crlf_fm_lf_body = (
        b"---\r\n"
        b"section: bottom\r\n"
        b"---\r\n"
        b"gamma\n"
        b"delta\n"
    )
    (mem / "mixed_crlf_fm.md").write_bytes(crlf_fm_lf_body)

    reader = _reader(tmp_path)
    doc_a = reader.read_document("Minx/Memory/mixed_lf_fm.md")
    assert doc_a.frontmatter == {"section": "top"}
    assert doc_a.body == "alpha\nbeta"

    doc_b = reader.read_document("Minx/Memory/mixed_crlf_fm.md")
    assert doc_b.frontmatter == {"section": "bottom"}
    assert doc_b.body == "gamma\ndelta"


def test_read_document_bare_cr_line_endings_splitlines_normalizes(tmp_path: Path) -> None:
    """Standalone ``\\r`` as line separator: ``splitlines()`` still splits; body uses ``\\n`` joins."""
    raw = (
        b"---\r"
        b"\n"
        b"x: 1\r"
        b"\n"
        b"---\r"
        b"\n"
        b"only\r"
        b"rows\r"
    )
    path = tmp_path / "Minx/Memory/bare_cr.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)

    doc = _reader(tmp_path).read_document("Minx/Memory/bare_cr.md")
    assert doc.frontmatter == {"x": 1}
    assert doc.body == "only\nrows"


def test_read_document_symlink_inside_allowed_prefix_targets_real_file(tmp_path: Path) -> None:
    """Symlink under ``Minx/Memory`` to a sibling ``.md`` reads the same bytes as the real file."""
    vault_note(
        tmp_path,
        "Minx/Memory/real.md",
        frontmatter={"type": "note", "scope": "core", "id": 9},
        body="Same inode story\n",
    )
    link = tmp_path / "Minx/Memory/link.md"
    _try_symlink(link, "real.md")

    reader = _reader(tmp_path)
    via_real = reader.read_document("Minx/Memory/real.md")
    via_link = reader.read_document("Minx/Memory/link.md")
    assert via_link.frontmatter == via_real.frontmatter
    assert via_link.body == via_real.body
    assert via_link.content_hash == via_real.content_hash
    assert via_link.relative_path == "Minx/Memory/real.md"


def test_read_document_symlink_escaping_vault_root_rejected(tmp_path: Path) -> None:
    """Symlink target resolving outside ``vault_root`` raises ``InvalidInputError``.

    Note: with ``vault_root == tmp_path``, a relative target ``../../outside.md`` from
    ``Minx/Memory`` still resolves *under* ``tmp_path``. The target file must live outside
    ``tmp_path`` (e.g. ``tmp_path.parent``), reachable via one more ``..`` component.
    """
    outside = tmp_path.parent / f"vault_reader_fs_outside_{id(tmp_path)}.md"
    outside.write_text("exfil\n", encoding="utf-8")
    mem = tmp_path / "Minx/Memory"
    mem.mkdir(parents=True, exist_ok=True)
    evil = mem / "evil.md"
    _try_symlink(evil, Path("..") / ".." / ".." / outside.name)

    reader = _reader(tmp_path)
    with pytest.raises(InvalidInputError, match="outside the vault root"):
        reader.read_document("Minx/Memory/evil.md")


def test_read_document_case_path_on_case_insensitive_fs(tmp_path: Path) -> None:
    """Wrong-case path: on APFS/HFS+ default (case-insensitive), ``resolve()`` finds the file.

    On Darwin with a typical case-insensitive volume, ``relative_path`` follows the *requested*
    spelling (e.g. ``case.md``), not necessarily the on-disk casing (``Case.md``).
    """
    vault_note(
        tmp_path,
        "Minx/Memory/Case.md",
        frontmatter={"type": "note", "scope": "core", "label": "mixed"},
        body="Body text\n",
    )
    probe = tmp_path / "Minx/Memory/.case_probe_MIXED"
    probe.write_text("x", encoding="utf-8")
    case_insensitive = (tmp_path / "Minx/Memory/.case_probe_mixed").exists()
    probe.unlink()

    reader = _reader(tmp_path)
    if case_insensitive:
        doc = reader.read_document("Minx/Memory/case.md")
        assert doc.frontmatter.get("label") == "mixed"
        assert "Body text" in doc.body
        assert doc.relative_path == "Minx/Memory/case.md"
        assert (tmp_path / "Minx/Memory/Case.md").exists()
    else:
        with pytest.raises(InvalidInputError, match="not found"):
            reader.read_document("Minx/Memory/case.md")


def test_iter_markdown_paths_sorted_by_posix_relative_path_ascii(tmp_path: Path) -> None:
    """Sort key is ``relative_to(vault).as_posix()``: ASCII order ``A`` < ``C`` < ``b``."""
    for name in ("A.md", "b.md", "C.md"):
        vault_note(
            tmp_path,
            f"Minx/Memory/{name}",
            frontmatter={"type": "note", "scope": "core", "file": name},
            body=f"content-{name}\n",
        )

    paths = list(_reader(tmp_path).iter_markdown_paths("Minx/Memory"))
    assert paths == ["Minx/Memory/A.md", "Minx/Memory/C.md", "Minx/Memory/b.md"]


def test_read_document_utf8_bom_frontmatter_only_note_closes_sentinel(tmp_path: Path) -> None:
    """BOM + closing ``---`` with no trailing body lines: regression guard for BOM strip + fence."""
    raw_text = "---\nstandalone: true\n---\n"
    path = tmp_path / "Minx/Memory/bom_fm_only.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + raw_text.encode("utf-8"))

    doc = _reader(tmp_path).read_document("Minx/Memory/bom_fm_only.md")
    assert doc.frontmatter == {"standalone": True}
    assert doc.body == ""
