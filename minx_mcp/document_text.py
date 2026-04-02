from __future__ import annotations

import subprocess
from pathlib import Path

from minx_mcp.config import get_settings


def extract_text(path: Path) -> str:
    settings = get_settings()
    proc = subprocess.run(
        [settings.liteparse_bin, str(path)],
        capture_output=True,
        check=True,
        text=True,
    )
    return proc.stdout
