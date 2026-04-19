"""Write access to markdown files inside an Obsidian-style vault.

Files are read with ``utf-8-sig`` to transparently strip UTF-8 BOM (matches
:class:`minx_mcp.vault_reader.VaultReader`). Writes always emit bare UTF-8 (no
BOM).

Concurrent read-modify-write operations on the same note path use a sibling
lock file and ``fcntl.flock`` so updates serialize across processes.
"""

from __future__ import annotations

import fcntl
import json
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from minx_mcp.contracts import ConflictError, InvalidInputError

_LOCK_ACQUIRE_TIMEOUT_S = 8.0
_LOCK_POLL_SLEEP_S = 0.05


class VaultWriter:
    def __init__(self, vault_root: Path, allowed_roots: tuple[str, ...]) -> None:
        self.vault_root = vault_root
        self.allowed_roots = allowed_roots

    def resolve_path(self, relative_path: str) -> Path:
        return self._resolve(relative_path)

    def write_markdown(self, relative_path: str, content: str) -> Path:
        path = self._resolve(relative_path)
        self._locked_write(path, lambda _existing: content)
        return path

    def replace_section(self, relative_path: str, heading: str, body: str) -> Path:
        path = self._resolve(relative_path)
        marker = f"## {heading}"
        replacement = f"{marker}\n\n{body.strip()}\n"

        def transform(text: str) -> str:
            lines = text.splitlines()
            start_line, end_line = self._find_section_bounds(lines, marker)
            if start_line is None:
                return f"{text.rstrip()}\n\n{replacement}\n".strip() + "\n"
            before = "\n".join(lines[:start_line]).rstrip()
            tail = "\n".join(lines[end_line:]).lstrip()
            return f"{before}\n\n{replacement}{tail}".strip() + "\n"

        self._locked_write(path, transform)
        return path

    def replace_frontmatter(self, relative_path: str, frontmatter: dict[str, object]) -> Path:
        path = self._resolve(relative_path)

        def transform(text: str) -> str:
            newline = _detect_newline(text)
            frontmatter_text = _serialize_frontmatter(frontmatter, newline=newline)
            body = _body_after_frontmatter(text)
            return f"{frontmatter_text}{body}"

        self._locked_write(path, transform)
        return path

    def _lock_path(self, path: Path) -> Path:
        return path.parent / f".{path.name}.lock"

    def _locked_write(self, path: Path, transform: Callable[[str], str]) -> None:
        lock_path = self._lock_path(path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + _LOCK_ACQUIRE_TIMEOUT_S
        with open(lock_path, "a+", encoding="utf-8") as lock_handle:
            while True:
                try:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ConflictError("vault file is locked by another writer") from None
                    time.sleep(_LOCK_POLL_SLEEP_S)
            try:
                if path.exists():
                    with path.open("r", encoding="utf-8-sig", newline="") as handle:
                        current_text = handle.read()
                else:
                    current_text = ""
                new_text = transform(current_text)
                self._atomic_write_text(path, new_text)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _atomic_write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                dir=path.parent,
                delete=False,
                encoding="utf-8",
                newline="",
            ) as handle:
                handle.write(content)
                temp_path = Path(handle.name)
            temp_path.replace(path)
        except Exception:  # Broad except is intentional: must clean up temp file for any failure type before re-raising
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
            raise

    def _find_section_bounds(self, lines: list[str], marker: str) -> tuple[int | None, int]:
        in_fence = False
        start_line: int | None = None

        for index, line in enumerate(lines):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if line == marker:
                start_line = index
                break

        if start_line is None:
            return None, len(lines)

        in_fence = False
        for index in range(start_line + 1, len(lines)):
            line = lines[index]
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if line.startswith("## "):
                return start_line, index

        return start_line, len(lines)

    def _resolve(self, relative_path: str) -> Path:
        normalized = Path(relative_path)
        if normalized.is_absolute():
            raise InvalidInputError("vault paths must be relative")
        if not normalized.parts or normalized.parts[0] not in self.allowed_roots:
            raise InvalidInputError("outside allowed vault roots")

        vault_physical = self.vault_root.resolve(strict=False)
        resolved = (vault_physical / normalized).resolve()
        if not resolved.is_relative_to(vault_physical):
            raise InvalidInputError("vault path resolves outside the vault root")
        allowed_root = (vault_physical / normalized.parts[0]).resolve()
        try:
            resolved.relative_to(allowed_root)
        except ValueError as exc:
            raise InvalidInputError("outside allowed vault roots") from exc
        return resolved


def _body_after_frontmatter(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[index + 1 :])
    return text


def _serialize_frontmatter(frontmatter: dict[str, object], *, newline: str = "\n") -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        if not isinstance(key, str) or not key:
            raise InvalidInputError("frontmatter keys must be non-empty strings")
        if "\n" in key or ":" in key:
            raise InvalidInputError("frontmatter keys must be simple YAML keys")
        lines.append(f"{key}: {_serialize_yaml_scalar(value)}")
    lines.append("---")
    return newline.join(lines) + newline


def _detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _serialize_yaml_scalar(value: Any) -> str:
    if isinstance(value, (dict, list)):
        dumped = json.dumps(value, sort_keys=True).replace("'", "''")
        return f"'{dumped}'"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value)
    if _needs_double_quotes(text):
        return '"' + _escape_double_quoted_string(text) + '"'
    return text


def _escape_double_quoted_string(text: str) -> str:
    escaped: list[str] = []
    for ch in text:
        if ch == "\\":
            escaped.append("\\\\")
        elif ch == '"':
            escaped.append('\\"')
        elif ch == "\n":
            escaped.append("\\n")
        elif ch == "\r":
            escaped.append("\\r")
        elif ch == "\t":
            escaped.append("\\t")
        elif ord(ch) < 0x20:
            raise InvalidInputError("frontmatter string values cannot contain control characters")
        else:
            escaped.append(ch)
    return "".join(escaped)


def _needs_double_quotes(text: str) -> bool:
    if text == "":
        return True
    if text != text.strip():
        return True
    if any(ch in text for ch in "\n\r\t:#"):
        return True
    lowered = text.lower()
    return lowered in {"true", "false", "null"} or text in {"---", "..."}
