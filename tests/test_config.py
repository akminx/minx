from __future__ import annotations

import pytest

from minx_mcp.config import get_settings


def test_settings_defaults_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINX_HTTP_PORT", raising=False)
    settings = get_settings()
    assert settings.http_port == 8000


def test_settings_rejects_nonnumeric_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINX_HTTP_PORT", "not-a-port")
    with pytest.raises(ValueError, match="MINX_HTTP_PORT"):
        get_settings()
