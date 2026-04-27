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


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _parse_optional_int_env(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw.strip(), 10)
    except ValueError as exc:
        raise ValueError(f"{name} must be a decimal integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _parse_int_env(name: str, default: int) -> int:
    value = _parse_optional_int_env(name)
    return default if value is None else value


def _parse_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


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
    vault_scan_on_snapshot: bool
    openrouter_api_key: str | None
    embedding_model: str
    embedding_dimensions: int | None
    embedding_request_timeout_s: float
    embedding_max_cost_microusd: int


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
        vault_scan_on_snapshot=_parse_bool_env("MINX_VAULT_SCAN_ON_SNAPSHOT", False),
        openrouter_api_key=os.environ.get("MINX_OPENROUTER_API_KEY") or None,
        embedding_model=os.environ.get("MINX_EMBEDDING_MODEL", "openai/text-embedding-3-small"),
        embedding_dimensions=_parse_optional_int_env("MINX_EMBEDDING_DIMENSIONS"),
        embedding_request_timeout_s=_parse_float_env("MINX_EMBEDDING_REQUEST_TIMEOUT_S", 30.0),
        embedding_max_cost_microusd=_parse_int_env("MINX_EMBEDDING_MAX_COST_MICROUSD", 10_000),
    )
