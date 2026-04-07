from pathlib import Path

from minx_mcp.transport import build_transport_config, run_server
from minx_mcp import document_text


def test_transport_config_supports_stdio_and_http():
    stdio = build_transport_config("stdio", "127.0.0.1", 8000)
    http = build_transport_config("http", "127.0.0.1", 8000)

    assert stdio["transport"] == "stdio"
    assert http["transport"] == "streamable-http"


def test_extract_text_uses_configured_liteparse_binary(monkeypatch, tmp_path):
    calls = {}

    class Settings:
        liteparse_bin = "custom-lit"

    class CompletedProcess:
        stdout = "parsed text"

    def fake_get_settings():
        return Settings()

    def fake_run(args, capture_output, check, text):
        calls["args"] = args
        calls["capture_output"] = capture_output
        calls["check"] = check
        calls["text"] = text
        return CompletedProcess()

    monkeypatch.setattr(document_text, "get_settings", fake_get_settings)
    monkeypatch.setattr(document_text.shutil, "which", lambda _: "/usr/local/bin/custom-lit")
    monkeypatch.setattr(document_text.subprocess, "run", fake_run)

    source = tmp_path / "statement.pdf"
    source.write_text("placeholder")

    result = document_text.extract_text(Path(source))

    assert result == "parsed text"
    assert calls == {
        "args": ["custom-lit", str(source)],
        "capture_output": True,
        "check": True,
        "text": True,
    }


def test_run_server_treats_keyboard_interrupt_as_clean_shutdown():
    events = {"run_called": False}

    class Settings:
        host = ""
        port = 0

    class FakeMCP:
        settings = Settings()

        def run(self, *, transport):
            events["run_called"] = True
            raise KeyboardInterrupt()

    run_server(FakeMCP(), transport="http", host="127.0.0.1", port=8765)

    assert events["run_called"] is True
