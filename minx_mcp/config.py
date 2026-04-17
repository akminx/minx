from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_HTTP_PORT = 8000


def _parse_http_port_env() -> int:
    raw = os.environ.get("MINX_HTTP_PORT")
    if raw is None:
        return _DEFAULT_HTTP_PORT
    stripped = raw.strip()
    if not stripped:
        raise ValueError("MINX_HTTP_PORT is set but empty; unset it or provide a decimal integer")
    try:
        return int(stripped, 10)
    except ValueError as exc:
        raise ValueError(
            f"MINX_HTTP_PORT must be a decimal integer, got {stripped!r} (from environment)"
        ) from exc


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    vault_path: Path
    staging_path: Path
    liteparse_bin: str
    http_host: str
    http_port: int
    default_transport: str


def get_settings() -> Settings:
    home = Path.home()
    data_dir = Path(os.environ.get("MINX_DATA_DIR", home / ".minx" / "data"))
    return Settings(
        data_dir=data_dir,
        db_path=Path(os.environ.get("MINX_DB_PATH", data_dir / "minx.db")),
        vault_path=Path(os.environ.get("MINX_VAULT_PATH", home / "Documents" / "minx-vault")),
        staging_path=Path(os.environ.get("MINX_STAGING_PATH", home / ".minx" / "staging")),
        liteparse_bin=os.environ.get("MINX_LITEPARSE_BIN", "lit"),
        http_host=os.environ.get("MINX_HTTP_HOST", "127.0.0.1"),
        http_port=_parse_http_port_env(),
        default_transport=os.environ.get("MINX_DEFAULT_TRANSPORT", "stdio"),
    )
