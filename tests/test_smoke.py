from minx_mcp.config import get_settings


def test_settings_defaults_are_portable():
    settings = get_settings()
    assert settings.db_path.name == "minx.db"
    assert settings.default_transport == "stdio"


def test_package_version_exists():
    import minx_mcp

    assert minx_mcp.__version__ == "0.1.0"
