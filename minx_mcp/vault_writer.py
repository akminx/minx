"""Write access to markdown files inside an Obsidian-style vault.

Files are read with ``utf-8-sig`` to transparently strip UTF-8 BOM (matches
:class:`minx_mcp.vault_reader.VaultReader`). Writes always emit bare UTF-8 (no
BOM).

Concurrent read-modify-write operations on the same note path use a sibling
lock file and ``fcntl.flock`` so updates serialize across processes.
"""

from __future__ import annotations

import fcntl
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile

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
                current_text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
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
