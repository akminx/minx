from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile


class VaultWriter:
    def __init__(self, vault_root: Path, allowed_roots: tuple[str, ...]) -> None:
        self.vault_root = vault_root
        self.allowed_roots = allowed_roots

    def resolve_path(self, relative_path: str) -> Path:
        return self._resolve(relative_path)

    def write_markdown(self, relative_path: str, content: str) -> Path:
        path = self._resolve(relative_path)
        self._atomic_write_text(path, content)
        return path

    def replace_section(self, relative_path: str, heading: str, body: str) -> Path:
        path = self._resolve(relative_path)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        marker = f"## {heading}"
        replacement = f"{marker}\n\n{body.strip()}\n"
        lines = text.splitlines()
        start_line, end_line = self._find_section_bounds(lines, marker)

        if start_line is None:
            new_text = f"{text.rstrip()}\n\n{replacement}\n".strip() + "\n"
        else:
            before = "\n".join(lines[:start_line]).rstrip()
            tail = "\n".join(lines[end_line:]).lstrip()
            new_text = f"{before}\n\n{replacement}{tail}".strip() + "\n"

        self._atomic_write_text(path, new_text)
        return path

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
            raise ValueError("vault paths must be relative")
        if not normalized.parts or normalized.parts[0] not in self.allowed_roots:
            raise ValueError("outside allowed vault roots")

        vault_root = self.vault_root.resolve()
        allowed_root = (vault_root / normalized.parts[0]).resolve()
        resolved = (vault_root / normalized).resolve()
        try:
            resolved.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError("outside allowed vault roots") from exc
        return resolved
