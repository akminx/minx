from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from minx_mcp import document_text


def test_document_text_caps_stderr_in_error_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(document_text, "_resolve_liteparse_binary", lambda _raw: "/bin/false")
    long_stderr = "e" * 3000

    def fake_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, "/bin/false", stderr=long_stderr)

    monkeypatch.setattr(document_text.subprocess, "run", fake_run)

    caplog.set_level(logging.WARNING)
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4")

    with pytest.raises(RuntimeError) as excinfo:
        document_text.extract_text(path)

    msg = str(excinfo.value)
    assert "e" * 600 not in msg
    assert len(msg) < 800
    assert "LiteParse failed" in msg

    assert any("LiteParse stderr (full)" in rec.message for rec in caplog.records)
