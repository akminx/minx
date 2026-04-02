from __future__ import annotations

from pathlib import Path


class VaultWriter:
    def __init__(self, vault_root: Path, allowed_roots: tuple[str, ...]) -> None:
        self.vault_root = vault_root
        self.allowed_roots = allowed_roots

    def write_markdown(self, relative_path: str, content: str) -> Path:
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def replace_section(self, relative_path: str, heading: str, body: str) -> Path:
        path = self._resolve(relative_path)
        text = path.read_text() if path.exists() else ""
        marker = f"## {heading}"
        blocks = text.split(marker)
        replacement = f"{marker}\n\n{body.strip()}\n"

        if len(blocks) == 1:
            new_text = f"{text.rstrip()}\n\n{replacement}\n".strip() + "\n"
        else:
            before = blocks[0].rstrip()
            remainder = blocks[1]
            next_heading = remainder.find("\n## ")
            tail = remainder[next_heading:] if next_heading != -1 else ""
            new_text = f"{before}\n\n{replacement}{tail.lstrip()}"

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text)
        return path

    def _resolve(self, relative_path: str) -> Path:
        normalized = Path(relative_path)
        if normalized.is_absolute():
            raise ValueError("vault paths must be relative")
        if not normalized.parts or normalized.parts[0] not in self.allowed_roots:
            raise ValueError("outside allowed vault roots")
        return self.vault_root / normalized
