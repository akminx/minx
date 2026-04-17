"""Read-only access to markdown files inside an Obsidian-style vault.

Path resolution intentionally mirrors :class:`minx_mcp.vault_writer.VaultWriter`
(``_resolve`` logic duplicated here rather than refactored shared, to keep the
Slice 6 foundation diff small; behavior must stay aligned if either side
changes).

This module performs pure file I/O and frontmatter parsing only — no database
writes, no ``vault_index`` scanner persistence (Slice 6c). Markdown bodies use
``str.splitlines()`` so standalone ``\\r`` newline markers inside the body are
normalized to ``\\n``. Files are decoded with ``utf-8-sig`` so a UTF-8 BOM at
the start of a file is transparently stripped before frontmatter parsing —
some editors (notably Windows Notepad) emit a BOM that would otherwise prevent
``---`` from matching on line 1. ``content_hash`` is still computed over the
on-disk bytes, so BOM-prefixed and non-BOM versions of otherwise identical
content hash differently (intentional; the scanner treats them as distinct).

:class:`minx_mcp.vault_writer.VaultWriter` uses the same ``utf-8-sig`` decode
when reading a note for section replacement, so BOM-prefixed files stay
consistent with reader semantics.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from minx_mcp.contracts import InvalidInputError

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")


@dataclass(frozen=True)
class VaultDocument:
    relative_path: str
    frontmatter: dict[str, object]
    body: str
    content_hash: str


class VaultReader:
    def __init__(self, vault_root: Path, allowed_prefixes: tuple[str, ...]) -> None:
        self._vault_root = vault_root
        self._allowed_prefixes = allowed_prefixes

    def read_document(self, relative_path: str) -> VaultDocument:
        vault_physical = _vault_root_physical(self._vault_root)
        resolved = _resolve_vault_relative(
            self._vault_root,
            self._allowed_prefixes,
            relative_path,
        )
        if not resolved.is_file():
            raise InvalidInputError(
                f"Vault markdown not found: {resolved} (from relative path {relative_path!r})"
            )
        raw = resolved.read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidInputError(f"Vault file is not valid UTF-8: {resolved}") from exc
        rel = resolved.relative_to(vault_physical).as_posix()
        frontmatter, body = _parse_markdown_with_frontmatter(text, resolved)
        return VaultDocument(
            relative_path=rel,
            frontmatter=frontmatter,
            body=body,
            content_hash=content_hash,
        )

    def iter_documents(self, sub_prefix: str = "") -> Iterator[VaultDocument]:
        vault = _vault_root_physical(self._vault_root)
        bases = _iter_walk_bases(self._vault_root, self._allowed_prefixes, sub_prefix)
        md_paths: list[Path] = []
        for base in bases:
            if not base.exists():
                continue
            for path in base.rglob("*.md"):
                if not path.is_file():
                    continue
                physical = path.resolve()
                if not physical.is_relative_to(vault):
                    raise InvalidInputError("vault path resolves outside the vault root")
                md_paths.append(path)
        md_paths.sort(key=lambda p: p.resolve().relative_to(vault).as_posix())
        for path in md_paths:
            rel = path.resolve().relative_to(vault).as_posix()
            yield self.read_document(rel)


def _vault_root_physical(vault_root: Path) -> Path:
    return vault_root.resolve(strict=False)


def _resolve_vault_relative(
    vault_root: Path,
    allowed_prefixes: tuple[str, ...],
    relative_path: str,
) -> Path:
    normalized = Path(relative_path)
    if normalized.is_absolute():
        raise InvalidInputError("vault paths must be relative")
    if not normalized.parts or normalized.parts[0] not in allowed_prefixes:
        raise InvalidInputError("outside allowed vault prefixes")

    vault_physical = _vault_root_physical(vault_root)
    resolved = (vault_physical / normalized).resolve()
    if not resolved.is_relative_to(vault_physical):
        raise InvalidInputError("vault path resolves outside the vault root")
    allowed_root = (vault_physical / normalized.parts[0]).resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise InvalidInputError("outside allowed vault prefixes") from exc
    return resolved


def _iter_walk_bases(vault_root: Path, allowed_prefixes: tuple[str, ...], sub_prefix: str) -> list[Path]:
    vault_physical = _vault_root_physical(vault_root)
    trimmed = sub_prefix.replace("\\", "/").strip("/")
    if not trimmed:
        return [vault_physical / prefix for prefix in allowed_prefixes]
    rel = Path(trimmed)
    if not rel.parts or rel.parts[0] not in allowed_prefixes:
        raise InvalidInputError("outside allowed vault prefixes")
    base = (vault_physical / rel).resolve()
    if not base.is_relative_to(vault_physical):
        raise InvalidInputError("vault path resolves outside the vault root")
    allowed_root = (vault_physical / rel.parts[0]).resolve()
    try:
        base.relative_to(allowed_root)
    except ValueError as exc:
        raise InvalidInputError("outside allowed vault prefixes") from exc
    return [base]


def _parse_markdown_with_frontmatter(text: str, file_path: Path) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    close_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        raise InvalidInputError(
            f"Unclosed YAML frontmatter in {file_path} (starting line 1, no closing ---)"
        )
    fm_lines = lines[1:close_idx]
    body = "\n".join(lines[close_idx + 1 :])
    fm = _parse_frontmatter_lines(fm_lines, file_path, start_line_number=2)
    return fm, body


def _parse_frontmatter_lines(
    lines: list[str],
    file_path: Path,
    *,
    start_line_number: int,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for offset, line in enumerate(lines):
        lineno = start_line_number + offset
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.lstrip() != line:
            raise InvalidInputError(
                f"Indented frontmatter is not supported (nested mapping?) in {file_path} line {lineno}"
            )
        if ":" not in line:
            raise InvalidInputError(
                f"Invalid frontmatter line (expected key: value) in {file_path} line {lineno}"
            )
        key, _, value_part = line.partition(":")
        key = key.strip()
        value_raw = value_part.strip()
        if not _KEY_RE.match(key):
            raise InvalidInputError(f"Invalid frontmatter key {key!r} in {file_path} line {lineno}")
        if key in result:
            raise InvalidInputError(f"Duplicate frontmatter key {key!r} in {file_path} line {lineno}")
        result[key] = _parse_frontmatter_value(value_raw, file_path, lineno)
    return result


def _parse_frontmatter_value(value_raw: str, file_path: Path, lineno: int) -> object:
    if not value_raw:
        return ""
    if value_raw.startswith("{") and value_raw.endswith("}"):
        return value_raw
    if value_raw.startswith("["):
        if not value_raw.endswith("]"):
            raise InvalidInputError(
                f"Malformed flow list (unclosed '[') in {file_path} line {lineno}"
            )
        inner = value_raw[1:-1]
        return _parse_flow_list_items(inner, file_path, lineno)
    return _parse_scalar(value_raw, file_path, lineno)


def _parse_flow_list_items(inner: str, file_path: Path, lineno: int) -> list[object]:
    if not inner.strip():
        return []
    parts = _split_top_level_commas(inner)
    return [_parse_scalar(p.strip(), file_path, lineno) for p in parts]


def _split_top_level_commas(s: str) -> list[str]:
    parts: list[str] = []
    start = 0
    in_dq = False
    in_sq = False
    for i, ch in enumerate(s):
        if ch == '"' and not in_sq:
            in_dq = not in_dq
        elif ch == "'" and not in_dq:
            in_sq = not in_sq
        elif ch == "," and not in_dq and not in_sq:
            parts.append(s[start:i])
            start = i + 1
    parts.append(s[start:])
    return parts


def _parse_scalar(raw: str, file_path: Path, lineno: int) -> object:
    s = raw.strip()
    if not s:
        return ""
    if s.startswith("["):
        if not s.endswith("]"):
            raise InvalidInputError(
                f"Malformed flow list (unclosed '[') in {file_path} line {lineno}"
            )
        return _parse_flow_list_items(s[1:-1], file_path, lineno)
    if s.startswith("{") and s.endswith("}"):
        return s
    if s.startswith('"'):
        return _parse_double_quoted(s, file_path, lineno)
    if s.startswith("'"):
        return _parse_single_quoted(s, file_path, lineno)
    lower = s.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower == "null":
        return None
    if _INT_RE.fullmatch(s):
        return int(s)
    if _FLOAT_RE.fullmatch(s):
        return float(s)
    return s


def _parse_double_quoted(s: str, file_path: Path, lineno: int) -> str:
    if len(s) < 2 or not s.endswith('"'):
        raise InvalidInputError(f"Unclosed double-quoted string in {file_path} line {lineno}")
    out: list[str] = []
    i = 1
    while i < len(s) - 1:
        ch = s[i]
        if ch == "\\":
            if i + 1 >= len(s) - 1:
                raise InvalidInputError(f"Dangling escape in {file_path} line {lineno}")
            out.append(s[i + 1])
            i += 2
            continue
        if ch == '"':
            raise InvalidInputError(f"Unescaped \" inside string in {file_path} line {lineno}")
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_single_quoted(s: str, file_path: Path, lineno: int) -> str:
    if len(s) < 2 or not s.endswith("'"):
        raise InvalidInputError(f"Unclosed single-quoted string in {file_path} line {lineno}")
    inner = s[1:-1]
    if "'" in inner:
        raise InvalidInputError(f"Unescaped ' inside string in {file_path} line {lineno}")
    return inner
