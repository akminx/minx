from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minx_mcp.core.events import emit_event
from minx_mcp.core.llm import JSONBackedLLM, LLMResponseError
from minx_mcp.core.models import (
    InsightCandidate,
    LLMReviewResult,
    ReviewContext,
)
from minx_mcp.db import get_connection
from minx_mcp.finance.read_api import (
    CategoryDelta,
    CategorySpending,
    ImportJobIssue,
    MerchantSpending,
    PeriodComparison,
    SpendingSummary,
    UncategorizedSummary,
)
from minx_mcp.preferences import set_preference
from minx_mcp.vault_writer import VaultWriter


@pytest.mark.asyncio
async def test_generate_daily_review_returns_detector_only_review_persists_insights_and_writes_note(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()

    vault_root = tmp_path / "vault"
    ctx = ReviewContext(
        db_path=db_path,
        finance_api=_finance_api_with_attention_items(),
        vault_writer=VaultWriter(vault_root, ("Minx",)),
        llm=None,
    )

    import minx_mcp.core.review as review

    monkeypatch.setattr(review, "create_llm", lambda config=None, db_path=None: None)

    artifact = await review.generate_daily_review("2026-03-15", ctx)

    assert artifact.date == "2026-03-15"
    assert artifact.llm_enriched is False
    assert len(artifact.timeline.entries) == 1
    assert len(artifact.insights) == 4
    assert "spent $60.00" in artifact.narrative
    assert artifact.next_day_focus == [
        "Categorize 2 uncategorized transactions",
        "Check finance import job job-failed",
        "Check finance import job job-stale",
    ]

    persisted = _read_persisted_insights(db_path)
    assert [(row["insight_type"], row["source"]) for row in persisted] == [
        ("finance.open_loop", "detector"),
        ("finance.open_loop", "detector"),
        ("finance.open_loop", "detector"),
        ("finance.spending_spike", "detector"),
    ]

    note_path = vault_root / "Minx" / "Reviews" / "2026-03-15-daily-review.md"
    assert note_path.exists()
    note = note_path.read_text()
    assert "# Daily Review — 2026-03-15" in note
    assert "## Summary" in note
    assert "## Timeline" in note
    assert "## Spending" in note
    assert "## Insights" in note
    assert "## Open Loops" in note
    assert "## Tomorrow's Focus" in note
    assert "finance.spending_spike:" in note


@pytest.mark.asyncio
async def test_generate_daily_review_uses_llm_when_available_and_only_persists_detector_rows(
    tmp_path,
):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()

    llm = _StaticLLM(
        LLMReviewResult(
            additional_insights=[
                InsightCandidate(
                    insight_type="finance.llm_observation",
                    dedupe_key="llm-extra-1",
                    summary="The spending spike looks concentrated in groceries.",
                    supporting_signals=["Groceries drove most of the increase."],
                    confidence=0.7,
                    severity="info",
                    actionability="suggestion",
                    source="llm",
                )
            ],
            narrative="The day ended with elevated spending and two finance follow-ups.",
            next_day_focus=["Review the grocery spike", "Resolve import failures"],
        )
    )
    ctx = ReviewContext(
        db_path=db_path,
        finance_api=_finance_api_with_attention_items(),
        vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
        llm=llm,
    )

    from minx_mcp.core.review import generate_daily_review

    artifact = await generate_daily_review("2026-03-15", ctx)

    assert artifact.llm_enriched is True
    assert artifact.narrative == "The day ended with elevated spending and two finance follow-ups."
    assert artifact.next_day_focus == [
        "Review the grocery spike",
        "Resolve import failures",
    ]
    assert [insight.source for insight in artifact.insights] == [
        "detector",
        "detector",
        "detector",
        "detector",
        "llm",
    ]

    persisted = _read_persisted_insights(db_path)
    assert len(persisted) == 4
    assert {row["source"] for row in persisted} == {"detector"}


@pytest.mark.asyncio
async def test_generate_daily_review_loads_llm_config_from_context_db(tmp_path, monkeypatch):
    default_db_path = tmp_path / "default.db"
    monkeypatch.setenv("MINX_DB_PATH", str(default_db_path))
    get_connection(default_db_path).close()

    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    set_preference(
        conn,
        "core",
        "llm_config",
        {"provider": "fake-review", "model": "reviewer-v1"},
    )
    conn.commit()

    import minx_mcp.core.llm as llm
    import minx_mcp.core.review as review

    builders = llm._PROVIDER_BUILDERS
    try:
        llm._PROVIDER_BUILDERS = {
            "fake-review": lambda config: _StaticLLM(
                LLMReviewResult(
                    additional_insights=[],
                    narrative=f"Loaded provider {config['model']}",
                    next_day_focus=["Review groceries"],
                )
            )
        }

        artifact = await review.generate_daily_review(
            "2026-03-15",
            ReviewContext(
                db_path=db_path,
                finance_api=_finance_api_with_attention_items(),
                vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
                llm=None,
            ),
        )
    finally:
        llm._PROVIDER_BUILDERS = builders

    assert artifact.llm_enriched is True
    assert artifact.narrative == "Loaded provider reviewer-v1"
    assert artifact.next_day_focus == ["Review groceries"]


@pytest.mark.asyncio
async def test_generate_daily_review_times_out_and_falls_back(tmp_path, monkeypatch):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()

    class SlowLLM:
        async def evaluate_review(self, timeline, spending, open_loops, detector_insights):
            await asyncio.sleep(0.05)
            raise AssertionError("timeout should fire first")

    import minx_mcp.core.review as review

    monkeypatch.setattr(review, "LLM_TIMEOUT_SECONDS", 0.001)

    artifact = await review.generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_finance_api_with_attention_items(),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=SlowLLM(),
        ),
    )

    assert artifact.llm_enriched is False
    assert "spent $60.00" in artifact.narrative


@pytest.mark.asyncio
async def test_generate_daily_review_falls_back_when_custom_llm_raises_unexpected_error(
    tmp_path,
    caplog,
):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()

    class BrokenLLM:
        async def evaluate_review(self, timeline, spending, open_loops, detector_insights):
            raise RuntimeError("custom llm boom")

    from minx_mcp.core.review import generate_daily_review

    artifact = await generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_finance_api_with_attention_items(),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=BrokenLLM(),
        ),
    )

    assert artifact.llm_enriched is False
    assert "spent $60.00" in artifact.narrative
    assert "custom llm boom" in caplog.text


@pytest.mark.asyncio
async def test_generate_daily_review_falls_back_when_llm_output_is_malformed(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()

    async def malformed(_prompt: str) -> str:
        return "{not-json}"

    from minx_mcp.core.review import generate_daily_review

    artifact = await generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_finance_api_with_attention_items(),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=JSONBackedLLM(malformed),
        ),
    )

    assert artifact.llm_enriched is False
    assert artifact.next_day_focus == [
        "Categorize 2 uncategorized transactions",
        "Check finance import job job-failed",
        "Check finance import job job-stale",
    ]


@pytest.mark.asyncio
async def test_generate_daily_review_handles_quiet_day(tmp_path, monkeypatch):
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    import minx_mcp.core.review as review

    monkeypatch.setattr(review, "create_llm", lambda config=None, db_path=None: None)

    artifact = await review.generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_quiet_finance_api(),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=None,
        ),
    )

    assert artifact.llm_enriched is False
    assert artifact.insights == []
    assert artifact.next_day_focus == []
    assert "Quiet day" in artifact.narrative


@pytest.mark.asyncio
async def test_generate_daily_review_raises_when_event_query_fails(tmp_path, monkeypatch):
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    import minx_mcp.core.review as review

    def boom(conn, review_date, finance_api=None):
        raise RuntimeError("timeline failed")

    monkeypatch.setattr(review, "build_read_models", boom)

    with pytest.raises(RuntimeError, match="timeline failed"):
        await review.generate_daily_review(
            "2026-03-15",
            ReviewContext(
                db_path=db_path,
                finance_api=_quiet_finance_api(),
                vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
                llm=None,
            ),
        )


@pytest.mark.asyncio
async def test_generate_daily_review_raises_when_finance_read_api_fails(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()

    class BrokenFinanceAPI:
        def get_spending_summary(self, start_date, end_date):
            raise RuntimeError("finance read failed")

        def get_uncategorized(self, start_date, end_date):
            raise AssertionError("should not continue after spending failure")

        def get_import_job_issues(self):
            raise AssertionError("should not continue after spending failure")

        def get_period_comparison(self, current_start, current_end, prior_start, prior_end):
            raise AssertionError("should not continue after spending failure")

    from minx_mcp.core.review import generate_daily_review

    with pytest.raises(RuntimeError, match="finance read failed"):
        await generate_daily_review(
            "2026-03-15",
            ReviewContext(
                db_path=db_path,
                finance_api=BrokenFinanceAPI(),
                vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
                llm=None,
            ),
        )


@pytest.mark.asyncio
async def test_generate_daily_review_logs_vault_write_failure_and_still_returns(
    tmp_path,
    caplog,
):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()

    class BrokenVaultWriter:
        def write_markdown(self, relative_path: str, content: str) -> Path:
            raise OSError("disk full")

    from minx_mcp.core.review import generate_daily_review

    artifact = await generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_finance_api_with_attention_items(),
            vault_writer=BrokenVaultWriter(),
            llm=None,
        ),
    )

    assert artifact.date == "2026-03-15"
    assert "disk full" in caplog.text


@pytest.mark.asyncio
async def test_generate_daily_review_force_false_is_idempotent_and_overwrites_same_note(
    tmp_path,
):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()

    vault_root = tmp_path / "vault"
    ctx = ReviewContext(
        db_path=db_path,
        finance_api=_finance_api_with_attention_items(),
        vault_writer=VaultWriter(vault_root, ("Minx",)),
        llm=None,
    )

    from minx_mcp.core.review import generate_daily_review

    first = await generate_daily_review("2026-03-15", ctx)
    second = await generate_daily_review("2026-03-15", ctx)

    assert first.insights == second.insights
    assert len(_read_persisted_insights(db_path)) == 4
    assert (vault_root / "Minx" / "Reviews" / "2026-03-15-daily-review.md").exists()


@pytest.mark.asyncio
async def test_generate_daily_review_force_true_replaces_persisted_detector_rows(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()

    from minx_mcp.core.review import generate_daily_review

    await generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_finance_api_with_attention_items(),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=None,
        ),
    )
    await generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_finance_api_without_open_loops(),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=None,
        ),
        force=True,
    )

    persisted = _read_persisted_insights(db_path)
    assert [(row["insight_type"], row["dedupe_key"]) for row in persisted] == [
        ("finance.spending_spike", "2026-03-15:spending_spike:groceries"),
    ]


@pytest.mark.asyncio
async def test_generate_daily_review_force_true_preserves_prior_rows_when_replacement_fails(
    tmp_path,
    monkeypatch,
    caplog,
):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()

    import minx_mcp.core.review as review

    await review.generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_finance_api_with_attention_items(),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=None,
        ),
    )
    before = _read_persisted_insights(db_path)

    def fail_insert(conn, review_date, insights):
        raise RuntimeError("replace failed")

    monkeypatch.setattr(review, "_insert_detector_insights", fail_insert)

    artifact = await review.generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_finance_api_without_open_loops(),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=None,
        ),
        force=True,
    )

    after = _read_persisted_insights(db_path)
    assert artifact.date == "2026-03-15"
    assert before == after
    assert "replace failed" in caplog.text


class _StaticLLM:
    def __init__(self, result: LLMReviewResult) -> None:
        self._result = result

    async def evaluate_review(self, timeline, spending, open_loops, detector_insights):
        return self._result


class _FinanceAPIDouble:
    def __init__(
        self,
        *,
        spending_summary: SpendingSummary,
        uncategorized: UncategorizedSummary,
        import_job_issues: list[ImportJobIssue],
        comparison: PeriodComparison,
    ) -> None:
        self._spending_summary = spending_summary
        self._uncategorized = uncategorized
        self._import_job_issues = import_job_issues
        self._comparison = comparison

    def get_spending_summary(self, start_date: str, end_date: str) -> SpendingSummary:
        return self._spending_summary

    def get_uncategorized(self, start_date: str, end_date: str) -> UncategorizedSummary:
        return self._uncategorized

    def get_import_job_issues(self) -> list[ImportJobIssue]:
        return list(self._import_job_issues)

    def get_period_comparison(
        self,
        current_start: str,
        current_end: str,
        prior_start: str,
        prior_end: str,
    ) -> PeriodComparison:
        return self._comparison


def _finance_api_with_attention_items() -> _FinanceAPIDouble:
    return _FinanceAPIDouble(
        spending_summary=SpendingSummary(
            total_spent_cents=6000,
            by_category=[
                CategorySpending("Groceries", 4000),
                CategorySpending("Dining Out", 2000),
            ],
            top_merchants=[
                MerchantSpending("HEB", 4000, 1),
                MerchantSpending("Cafe", 2000, 1),
            ],
        ),
        uncategorized=UncategorizedSummary(
            transaction_count=2,
            total_spent_cents=3525,
        ),
        import_job_issues=[
            ImportJobIssue(
                job_id="job-failed",
                issue_kind="failed",
                status="failed",
                source_ref="/imports/a.csv",
                updated_at="2026-03-15 09:00:00",
                error_message="bad csv",
            ),
            ImportJobIssue(
                job_id="job-stale",
                issue_kind="stale",
                status="running",
                source_ref="/imports/b.csv",
                updated_at="2026-03-15 09:30:00",
                error_message=None,
            ),
        ],
        comparison=PeriodComparison(
            current_total_spent_cents=6000,
            prior_total_spent_cents=4000,
            category_deltas=[
                CategoryDelta("Groceries", 4000, 2000, 2000),
                CategoryDelta("Dining Out", 2000, 2000, 0),
            ],
        ),
    )


def _finance_api_without_open_loops() -> _FinanceAPIDouble:
    return _FinanceAPIDouble(
        spending_summary=SpendingSummary(
            total_spent_cents=6000,
            by_category=[CategorySpending("Groceries", 4000)],
            top_merchants=[MerchantSpending("HEB", 4000, 1)],
        ),
        uncategorized=UncategorizedSummary(
            transaction_count=0,
            total_spent_cents=0,
        ),
        import_job_issues=[],
        comparison=PeriodComparison(
            current_total_spent_cents=6000,
            prior_total_spent_cents=4000,
            category_deltas=[CategoryDelta("Groceries", 4000, 2000, 2000)],
        ),
    )


def _quiet_finance_api() -> _FinanceAPIDouble:
    return _FinanceAPIDouble(
        spending_summary=SpendingSummary(
            total_spent_cents=0,
            by_category=[],
            top_merchants=[],
        ),
        uncategorized=UncategorizedSummary(
            transaction_count=0,
            total_spent_cents=0,
        ),
        import_job_issues=[],
        comparison=PeriodComparison(
            current_total_spent_cents=0,
            prior_total_spent_cents=0,
            category_deltas=[],
        ),
    )


def _read_persisted_insights(db_path: Path) -> list[dict[str, object]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT insight_type, dedupe_key, summary, source, review_date
            FROM insights
            ORDER BY insight_type ASC, dedupe_key ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _seed_event(conn, *, occurred_at: str) -> None:
    event_id = emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at=occurred_at,
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
    assert event_id is not None
