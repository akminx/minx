from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.vault_reader import VaultReader


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_read_document_with_frontmatter_and_body(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    rel = Path("Minx") / "note.md"
    raw = (
        b"---\n"
        b'title: "hello: world"\n'
        b"count: 3\n"
        b"ratio: 1.5\n"
        b"flag: true\n"
        b"empty: null\n"
        b"tags: [a, b, \"c d\"]\n"
        b"---\n"
        b"Body line one\n"
        b"Body line two\n"
    )
    _write_bytes(root / rel, raw)
    reader = VaultReader(root, ("Minx",))
    doc = reader.read_document(rel.as_posix())
    assert doc.relative_path == rel.as_posix()
    assert doc.body == "Body line one\nBody line two"
    assert doc.frontmatter["title"] == "hello: world"
    assert doc.frontmatter["count"] == 3
    assert doc.frontmatter["ratio"] == 1.5
    assert doc.frontmatter["flag"] is True
    assert doc.frontmatter["empty"] is None
    assert doc.frontmatter["tags"] == ["a", "b", "c d"]
    assert doc.content_hash == hashlib.sha256(raw).hexdigest()


def test_read_document_without_frontmatter(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    rel = Path("Minx") / "plain.md"
    raw = b"Just markdown\nno banner\n"
    _write_bytes(root / rel, raw)
    reader = VaultReader(root, ("Minx",))
    doc = reader.read_document(rel.as_posix())
    assert doc.frontmatter == {}
    assert doc.body == raw.decode()
    assert doc.content_hash == hashlib.sha256(raw).hexdigest()


def test_read_document_accepts_crlf_closing_delimiter(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    rel = Path("Minx") / "crlf.md"
    raw = b"---\r\nkey: val\r\n---\r\nbody\r\nhere\r\n"
    _write_bytes(root / rel, raw)
    reader = VaultReader(root, ("Minx",))
    doc = reader.read_document(rel.as_posix())
    assert doc.frontmatter == {"key": "val"}
    assert doc.body == "body\nhere"


def test_read_document_flow_mapping_returned_as_raw_string(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    rel = Path("Minx") / "raw.md"
    raw = b"---\nextra: {nested: not-supported}\n---\n"
    _write_bytes(root / rel, raw)
    reader = VaultReader(root, ("Minx",))
    doc = reader.read_document(rel.as_posix())
    assert doc.frontmatter["extra"] == "{nested: not-supported}"


def test_read_document_rejects_unclosed_frontmatter(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    rel = Path("Minx") / "bad.md"
    raw = b"---\nkey: val\nstill open\n"
    _write_bytes(root / rel, raw)
    reader = VaultReader(root, ("Minx",))
    with pytest.raises(InvalidInputError, match="Unclosed YAML frontmatter"):
        reader.read_document(rel.as_posix())


def test_read_document_rejects_invalid_key(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    rel = Path("Minx") / "badkey.md"
    raw = b"---\n9bad: x\n---\n"
    _write_bytes(root / rel, raw)
    reader = VaultReader(root, ("Minx",))
    with pytest.raises(InvalidInputError, match="Invalid frontmatter key"):
        reader.read_document(rel.as_posix())


def test_read_document_rejects_indented_nested_mapping(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    rel = Path("Minx") / "nested.md"
    raw = b"---\nparent: val\n  child: x\n---\n"
    _write_bytes(root / rel, raw)
    reader = VaultReader(root, ("Minx",))
    with pytest.raises(InvalidInputError, match="Indented frontmatter"):
        reader.read_document(rel.as_posix())


def test_read_document_missing_file(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    reader = VaultReader(root, ("Minx",))
    with pytest.raises(InvalidInputError, match="not found"):
        reader.read_document("Minx/nope.md")


def test_read_document_rejects_absolute_path(tmp_path: Path) -> None:
    reader = VaultReader(tmp_path / "vault", ("Minx",))
    with pytest.raises(InvalidInputError, match="relative"):
        reader.read_document("/etc/passwd")


def test_read_document_rejects_disallowed_prefix(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "Minx").mkdir(parents=True)
    (root / "Other").mkdir(parents=True)
    (root / "Other" / "x.md").write_text("x", encoding="utf-8")
    reader = VaultReader(root, ("Minx",))
    with pytest.raises(InvalidInputError, match="outside allowed vault prefixes"):
        reader.read_document("Other/x.md")


def test_read_document_rejects_escape_via_dotdot(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    outside = tmp_path / "outside-secret.md"
    outside.write_text("secret", encoding="utf-8")
    (root / "Minx").mkdir(parents=True)
    reader = VaultReader(root, ("Minx",))
    rel = "Minx/../../outside-secret.md"
    with pytest.raises(InvalidInputError, match="outside allowed vault prefixes"):
        reader.read_document(rel)


def test_read_document_strips_utf8_bom_before_frontmatter(tmp_path: Path) -> None:
    """Files written by some editors (e.g. Windows Notepad) start with a UTF-8 BOM.

    The decoder must treat a leading ``\\xef\\xbb\\xbf`` transparently, otherwise
    the frontmatter delimiter ``---`` fails to match on line 1 and the entire
    file is returned as body with ``frontmatter == {}`` — silently dropping the
    note's metadata. ``content_hash`` is still computed over the raw bytes so
    the BOM vs. non-BOM versions of the "same" note remain distinct.
    """
    root = tmp_path / "vault"
    rel = Path("Minx") / "bom.md"
    frontmatter_and_body = (
        b"---\ntype: minx-memory\nmemory_key: finance.starbucks\n---\nHello\n"
    )
    raw = b"\xef\xbb\xbf" + frontmatter_and_body
    _write_bytes(root / rel, raw)
    reader = VaultReader(root, ("Minx",))
    doc = reader.read_document(rel.as_posix())
    assert doc.frontmatter == {
        "type": "minx-memory",
        "memory_key": "finance.starbucks",
    }
    assert doc.body == "Hello"
    assert doc.content_hash == hashlib.sha256(raw).hexdigest()

    rel2 = Path("Minx") / "no_bom.md"
    _write_bytes(root / rel2, frontmatter_and_body)
    doc2 = reader.read_document(rel2.as_posix())
    assert doc2.frontmatter == doc.frontmatter
    assert doc2.body == doc.body
    assert doc2.content_hash != doc.content_hash


def test_iter_documents_sorted_and_sub_prefix(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    _write_bytes(root / "Minx" / "z.md", b"z")
    _write_bytes(root / "Minx" / "a" / "inner.md", b"i")
    _write_bytes(root / "Minx" / "b.md", b"b")
    (root / "Minx" / "skip.txt").write_text("t", encoding="utf-8")
    reader = VaultReader(root, ("Minx",))
    paths = [d.relative_path for d in reader.iter_documents()]
    assert paths == ["Minx/a/inner.md", "Minx/b.md", "Minx/z.md"]

    sub_paths = [d.relative_path for d in reader.iter_documents("Minx/a")]
    assert sub_paths == ["Minx/a/inner.md"]


def test_iter_documents_skips_missing_sub_prefix_branch(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "Minx").mkdir(parents=True)
    reader = VaultReader(root, ("Minx",))
    assert list(reader.iter_documents("Minx/missing")) == []
