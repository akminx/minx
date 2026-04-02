from minx_mcp.config import get_settings


def test_settings_defaults_are_portable():
    settings = get_settings()
    assert settings.db_path.name == "minx.db"
    assert settings.default_transport == "stdio"


def test_settings_honor_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("MINX_DB_PATH", str(tmp_path / "custom.db"))
    monkeypatch.setenv("MINX_HTTP_PORT", "9001")

    settings = get_settings()

    assert settings.db_path == tmp_path / "custom.db"
    assert settings.http_port == 9001


def test_package_version_exists():
    import minx_mcp

    assert minx_mcp.__version__ == "0.1.0"
