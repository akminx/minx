"""Write access to markdown files inside an Obsidian-style vault.

Files are read with ``utf-8-sig`` to transparently strip UTF-8 BOM (matches
:class:`minx_mcp.vault_reader.VaultReader`). Writes always emit bare UTF-8 (no
BOM).

Concurrent read-modify-write operations on the same note path use a sibling
lock file and ``fcntl.flock`` so updates serialize across processes.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO, Any

from minx_mcp.contracts import ConflictError, InvalidInputError
from minx_mcp.core.secret_scanner import scan_for_secrets

logger = logging.getLogger(__name__)

_LOCK_ACQUIRE_TIMEOUT_S = 8.0
_LOCK_POLL_SLEEP_S = 0.05


class StagedVaultWrite:
    """A vault-file write that has been prepared but not yet published.

    The new bytes have been written to a sibling temp file while holding the
    per-file lock. Callers can either :meth:`commit` (atomic rename into place
    and release the lock) or :meth:`abort` (delete the temp and release the
    lock).

    This two-phase API lets callers persist related DB state atomically in
    between: commit the DB transaction **before** :meth:`commit` so that a
    failed DB commit leaves the vault file untouched, eliminating split-brain
    between SQLite rows and vault markdown.

    Instances should be used as context managers. If ``__exit__`` runs without
    a prior :meth:`commit`, the write is aborted — no partial file ever
    appears on disk.
    """

    def __init__(
        self,
        *,
        target: Path,
        temp_path: Path,
        content: str,
        content_hash: str,
        lock_handle: IO[str],
        lock_path: Path,
    ) -> None:
        self._target = target
        self._temp_path: Path | None = temp_path
        self._content = content
        self._content_hash = content_hash
        self._lock_handle: IO[str] | None = lock_handle
        self._lock_path = lock_path
        self._committed = False

    @property
    def target(self) -> Path:
        return self._target

    @property
    def content(self) -> str:
        return self._content

    @property
    def content_hash(self) -> str:
        """SHA-256 of the to-be-written UTF-8 bytes.

        Equal to the hash ``VaultReader`` would compute on the new file,
        available *before* :meth:`commit` so callers can stamp database rows
        (e.g. ``vault_index.content_hash``) without re-reading the file.
        """
        return self._content_hash

    @property
    def is_finalized(self) -> bool:
        return self._temp_path is None

    def commit(self) -> None:
        """Atomically rename the staged temp file into place and release the lock."""
        if self.is_finalized:
            raise RuntimeError("staged vault write has already been finalized")
        temp_path = self._temp_path
        if temp_path is None:
            raise RuntimeError("internal: staged write missing temp_path after is_finalized check")
        try:
            temp_path.replace(self._target)
            self._committed = True
        finally:
            self._temp_path = None
            self._release_lock()

    def abort(self) -> None:
        """Discard the staged write and release the lock."""
        if self.is_finalized:
            return
        temp_path = self._temp_path
        self._temp_path = None
        try:
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError as exc:  # pragma: no cover - defensive
                    logger.warning("staged vault write: temp cleanup failed for %s: %s", temp_path, exc)
        finally:
            self._release_lock()

    def _release_lock(self) -> None:
        lock_handle = self._lock_handle
        self._lock_handle = None
        if lock_handle is None:
            return
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()

    def __enter__(self) -> StagedVaultWrite:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if not self._committed:
            self.abort()


class VaultWriter:
    def __init__(self, vault_root: Path, allowed_roots: tuple[str, ...]) -> None:
        self.vault_root = vault_root
        self.allowed_roots = allowed_roots

    def resolve_path(self, relative_path: str) -> Path:
        return self._resolve(relative_path)

    def write_markdown(self, relative_path: str, content: str) -> Path:
        path = self._resolve(relative_path)
        _scan_markdown_frontmatter_for_secrets(content)
        _scan_markdown_body_for_secrets(content)
        self._locked_write(path, lambda _existing: content)
        return path

    def replace_section(self, relative_path: str, heading: str, body: str) -> Path:
        path = self._resolve(relative_path)
        _scan_text_for_vault_body_secrets(heading, field="heading")
        _scan_text_for_vault_body_secrets(body, field="body")
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
        """Immediate-commit variant; equivalent to :meth:`stage_replace_frontmatter` + commit."""
        with self.stage_replace_frontmatter(relative_path, frontmatter) as staged:
            staged.commit()
            return staged.target

    def stage_replace_frontmatter(
        self,
        relative_path: str,
        frontmatter: dict[str, object],
    ) -> StagedVaultWrite:
        """Prepare a frontmatter replacement; the lock is held until commit/abort.

        Returns a :class:`StagedVaultWrite` that the caller must finalize
        exactly once (via commit or abort, ideally through ``with``).
        """
        path = self._resolve(relative_path)
        scan_frontmatter_for_secrets(frontmatter)
        lock_handle, lock_path = self._acquire_lock(path)
        try:
            if path.exists():
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    current_text = handle.read()
            else:
                current_text = ""
            newline = _detect_newline(current_text)
            frontmatter_text = _serialize_frontmatter(frontmatter, newline=newline)
            body = _body_after_frontmatter(current_text)
            _scan_text_for_vault_body_secrets(body, field="body")
            new_text = f"{frontmatter_text}{body}"
            temp_path = self._stage_write(path, new_text)
        except Exception:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lock_handle.close()
            raise
        content_hash = hashlib.sha256(new_text.encode("utf-8")).hexdigest()
        return StagedVaultWrite(
            target=path,
            temp_path=temp_path,
            content=new_text,
            content_hash=content_hash,
            lock_handle=lock_handle,
            lock_path=lock_path,
        )

    def _acquire_lock(self, path: Path) -> tuple[IO[str], Path]:
        lock_path = self._lock_path(path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + _LOCK_ACQUIRE_TIMEOUT_S
        lock_handle = lock_path.open("a+", encoding="utf-8")
        try:
            while True:
                try:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ConflictError("vault file is locked by another writer") from None
                    time.sleep(_LOCK_POLL_SLEEP_S)
        except Exception:
            lock_handle.close()
            raise
        return lock_handle, lock_path

    def _lock_path(self, path: Path) -> Path:
        return path.parent / f".{path.name}.lock"

    def _locked_write(self, path: Path, transform: Callable[[str], str]) -> None:
        lock_handle, _ = self._acquire_lock(path)
        try:
            if path.exists():
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    current_text = handle.read()
            else:
                current_text = ""
            new_text = transform(current_text)
            self._atomic_write_text(path, new_text)
        finally:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lock_handle.close()

    def _stage_write(self, path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w",
            dir=path.parent,
            delete=False,
            encoding="utf-8",
            newline="",
        ) as handle:
            handle.write(content)
            return Path(handle.name)

    def _atomic_write_text(self, path: Path, content: str) -> None:
        temp_path: Path | None = None
        try:
            temp_path = self._stage_write(path, content)
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


def scan_frontmatter_for_secrets(frontmatter: dict[str, object]) -> None:
    detected: set[str] = set()
    locations: list[dict[str, object]] = []
    for key, value in frontmatter.items():
        field = str(key) if isinstance(key, str) and key else "[INVALID_KEY]"
        key_verdict = scan_for_secrets(field)
        if key_verdict.findings:
            field = "[REDACTED_KEY]"
            for finding in key_verdict.findings:
                detected.add(finding.kind)
                locations.append({"field": field, "start": finding.start, "end": finding.end})
        if isinstance(key, str) and key and "\n" not in key and ":" not in key:
            value_text = _serialize_yaml_scalar(value)
            value_verdict = scan_for_secrets(value_text)
            if value_verdict.findings:
                for finding in value_verdict.findings:
                    detected.add(finding.kind)
                    locations.append({"field": field, "start": finding.start, "end": finding.end})
    if detected:
        raise InvalidInputError(
            "Secret detected in vault frontmatter",
            data={
                "kind": "secret_detected",
                "verdict": "block",
                "surface": "vault_frontmatter",
                "detected_kinds": sorted(detected),
                "locations": locations,
            },
        )


def _scan_markdown_frontmatter_for_secrets(content: str) -> None:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        return
    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    frontmatter_text = "".join(lines if end_index is None else lines[: end_index + 1])
    verdict = scan_for_secrets(frontmatter_text)
    if not verdict.findings:
        return
    raise InvalidInputError(
        "Secret detected in vault frontmatter",
        data={
            "kind": "secret_detected",
            "verdict": "block",
            "surface": "vault_frontmatter",
            "detected_kinds": sorted({finding.kind for finding in verdict.findings}),
            "locations": [
                {"field": "frontmatter", "start": finding.start, "end": finding.end}
                for finding in verdict.findings
            ],
        },
    )


def _scan_markdown_body_for_secrets(content: str) -> None:
    body = _body_after_frontmatter(content)
    _scan_text_for_vault_body_secrets(body, field="body")


def _scan_text_for_vault_body_secrets(text: str, *, field: str) -> None:
    verdict = scan_for_secrets(text)
    if not verdict.findings:
        return
    raise InvalidInputError(
        "Secret detected in vault body",
        data={
            "kind": "secret_detected",
            "verdict": "block",
            "surface": "vault_body",
            "detected_kinds": sorted({finding.kind for finding in verdict.findings}),
            "locations": [
                {"field": field, "start": finding.start, "end": finding.end}
                for finding in verdict.findings
            ],
        },
    )


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
