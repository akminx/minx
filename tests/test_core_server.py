from __future__ import annotations

from pathlib import Path

import pytest

from minx_mcp.core.events import emit_event
from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection


class _TestConfig:
    def __init__(self, db_path: Path, vault_path: Path) -> None:
        self._db_path = db_path
        self._vault_path = vault_path

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def vault_path(self) -> Path:
        return self._vault_path


def test_daily_review_tool_returns_structured_result(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-15T15:00:00Z",
        entity_ref="batch-1",
        source="tests",
        payload={
            "account_name": "Checking",
            "account_id": 1,
            "job_id": "job-1",
            "transaction_count": 3,
            "total_cents": -6000,
            "source_kind": "csv",
        },
    )
    conn.commit()
    conn.close()

    vault_path = tmp_path / "vault"
    config = _TestConfig(db_path, vault_path)

    from minx_mcp.core.server import _daily_review

    result = _daily_review(config, "2026-03-15", False)

    assert result["date"] == "2026-03-15"
    assert isinstance(result["narrative"], str)
    assert isinstance(result["next_day_focus"], list)
    assert isinstance(result["insight_count"], int)
    assert result["llm_enriched"] is False
    assert isinstance(result["markdown"], str)
    assert "# Daily Review" in result["markdown"]

    note_path = vault_path / "Minx" / "Reviews" / "2026-03-15-daily-review.md"
    assert note_path.exists()


def test_daily_review_tool_validates_bad_date(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    config = _TestConfig(db_path, tmp_path / "vault")

    from minx_mcp.contracts import InvalidInputError
    from minx_mcp.core.server import _daily_review

    with pytest.raises(InvalidInputError, match="valid ISO date"):
        _daily_review(config, "not-a-date", False)


def test_daily_review_tool_defaults_to_today(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    config = _TestConfig(db_path, tmp_path / "vault")

    from minx_mcp.core.server import _daily_review

    result = _daily_review(config, None, False)
    assert result["date"] is not None
    assert isinstance(result["date"], str)
