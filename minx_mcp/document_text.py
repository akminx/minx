from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from minx_mcp.config import get_settings

_LOG = logging.getLogger(__name__)
_STDERR_PREVIEW_CHARS = 512


def _resolve_liteparse_binary(raw: str) -> str:
    """Return an absolute path when ``raw`` names an existing file; otherwise the configured string.

    When the binary is resolved only via ``PATH``, keep the configured name so
    ``argv[0]`` matches prior behavior (harnesses and tests often mock ``which``).
    """
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    if shutil.which(raw):
        return raw
    raise FileNotFoundError(
        f"LiteParse binary {raw!r} not found on PATH and is not an existing file. "
        "Install it or set MINX_LITEPARSE_BIN to the correct path."
    )


def extract_text(path: Path) -> str:
    settings = get_settings()
    binary = _resolve_liteparse_binary(settings.liteparse_bin)
    try:
        proc = subprocess.run(
            [binary, str(path)],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"LiteParse timed out after 30s on {path.name}") from exc
    except subprocess.CalledProcessError as exc:
        full = (exc.stderr or "").strip()
        preview = full[:_STDERR_PREVIEW_CHARS] + "…" if len(full) > _STDERR_PREVIEW_CHARS else full
        if full:
            _LOG.warning("LiteParse stderr (full) for %s: %s", path.name, full)
        raise RuntimeError(f"LiteParse failed on {path.name}: {preview}") from exc
    return proc.stdout
