from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from minx_mcp.config import get_settings


def extract_text(path: Path) -> str:
    settings = get_settings()
    binary = settings.liteparse_bin
    if not shutil.which(binary):
        raise FileNotFoundError(
            f"LiteParse binary '{binary}' not found on PATH. "
            f"Install it or set MINX_LITEPARSE_BIN to the correct path."
        )
    try:
        proc = subprocess.run(
            [binary, str(path)],
            capture_output=True,
            check=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"LiteParse failed on {path.name}: {exc.stderr.strip()}"
        ) from exc
    return proc.stdout
