from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from minx_mcp.core.detectors import DETECTORS, Detector
from minx_mcp.core.models import (
    DailyTimeline,
    InsightCandidate,
    OpenLoopsSnapshot,
    ReadModels,
    SpendingSnapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_read_models(finance_api=None) -> ReadModels:
    return ReadModels(
        timeline=DailyTimeline(date="2026-04-12", entries=[]),
        spending=SpendingSnapshot(
            date="2026-04-12",
            total_spent_cents=0,
            by_category={},
            top_merchants=[],
            vs_prior_week_pct=None,
            uncategorized_count=0,
            uncategorized_total_cents=0,
        ),
        open_loops=OpenLoopsSnapshot(date="2026-04-12", loops=[]),
        goal_progress=[],
        finance_api=finance_api,
    )


def _make_insight() -> InsightCandidate:
    return InsightCandidate(
        insight_type="test.noop",
        dedupe_key="2026-04-12:test:noop",
        summary="noop",
        supporting_signals=[],
        confidence=1.0,
        severity="info",
        actionability="suggestion",
        source="detector",
    )


# ---------------------------------------------------------------------------
# Detector Dataclass
# ---------------------------------------------------------------------------

class TestDetectorDataclass:
    def test_all_detectors_have_unique_keys(self):
        keys = [d.key for d in DETECTORS]
        assert len(keys) == len(set(keys)), "Duplicate detector keys found"

    def test_all_detectors_have_non_empty_tags(self):
        for detector in DETECTORS:
            assert detector.tags, f"Detector '{detector.key}' has empty tags"

    def test_detector_keys_match_expected_dot_namespace_pattern(self):
        for detector in DETECTORS:
            assert "." in detector.key, (
                f"Detector key '{detector.key}' does not follow 'namespace.name' pattern"
            )
            namespace, name = detector.key.split(".", 1)
            assert namespace, f"Detector '{detector.key}' has empty namespace"
            assert name, f"Detector '{detector.key}' has empty name after dot"

    def test_all_current_detectors_are_enabled_by_default(self):
        for detector in DETECTORS:
            assert detector.enabled_by_default is True, (
                f"Detector '{detector.key}' has enabled_by_default=False"
            )

    def test_known_detector_keys_are_present(self):
        keys = {d.key for d in DETECTORS}
        expected = {
            "finance.spending_spike",
            "finance.open_loops",
            "nutrition.low_protein",
            "nutrition.skipped_meals",
            "training.adherence_drop",
            "training.volume_stalled",
            "training.recovery_risk",
            "cross.training_nutrition_mismatch",
            "core.goal_drift",
            "finance.category_drift",
            "finance.goal_risk",
        }
        assert expected == keys


# ---------------------------------------------------------------------------
# enabled_by_default Filtering
# ---------------------------------------------------------------------------

class TestEnabledByDefaultFiltering:
    def test_disabled_detector_is_skipped_by_run_detectors(self):
        called = []

        def spy_fn(read_models):
            called.append("disabled_detector")
            return []

        disabled = Detector(
            key="test.disabled",
            fn=spy_fn,
            enabled_by_default=False,
            tags=frozenset({"test"}),
        )

        import minx_mcp.core.snapshot as snapshot_module

        original_detectors = snapshot_module.DETECTORS
        patched = list(DETECTORS) + [disabled]
        with patch.object(snapshot_module, "DETECTORS", patched):
            from minx_mcp.core.snapshot import _run_detectors
            _run_detectors(_make_read_models())

        assert "disabled_detector" not in called, (
            "Disabled detector fn was called but should have been skipped"
        )

    def test_enabled_detectors_are_all_called(self):
        called = []

        def make_spy(key):
            def fn(read_models):
                called.append(key)
                return []
            return fn

        enabled_a = Detector(
            key="test.alpha",
            fn=make_spy("test.alpha"),
            enabled_by_default=True,
            tags=frozenset({"test"}),
        )
        enabled_b = Detector(
            key="test.beta",
            fn=make_spy("test.beta"),
            enabled_by_default=True,
            tags=frozenset({"test"}),
        )
        disabled = Detector(
            key="test.gamma",
            fn=make_spy("test.gamma"),
            enabled_by_default=False,
            tags=frozenset({"test"}),
        )

        import minx_mcp.core.snapshot as snapshot_module

        patched = [enabled_a, enabled_b, disabled]
        with patch.object(snapshot_module, "DETECTORS", patched):
            from minx_mcp.core.snapshot import _run_detectors
            _run_detectors(_make_read_models())

        assert "test.alpha" in called
        assert "test.beta" in called
        assert "test.gamma" not in called

    def test_disabled_detector_results_are_excluded_from_output(self):
        def enabled_fn(read_models):
            return [_make_insight()]

        def disabled_fn(read_models):
            return [_make_insight()]

        import minx_mcp.core.snapshot as snapshot_module

        patched = [
            Detector(key="test.enabled", fn=enabled_fn, enabled_by_default=True, tags=frozenset({"test"})),
            Detector(key="test.disabled", fn=disabled_fn, enabled_by_default=False, tags=frozenset({"test"})),
        ]
        with patch.object(snapshot_module, "DETECTORS", patched):
            from minx_mcp.core.snapshot import _run_detectors
            results = _run_detectors(_make_read_models())

        assert len(results) == 1


# ---------------------------------------------------------------------------
# Detector Tags
# ---------------------------------------------------------------------------

class TestDetectorTags:
    def test_filter_by_finance_tag(self):
        finance_detectors = [d for d in DETECTORS if "finance" in d.tags]
        assert len(finance_detectors) > 0
        for d in finance_detectors:
            assert "finance" in d.tags

    def test_filter_by_goals_tag(self):
        goal_detectors = [d for d in DETECTORS if "goals" in d.tags]
        assert len(goal_detectors) > 0
        for d in goal_detectors:
            assert "goals" in d.tags

    def test_tags_are_frozensets(self):
        for detector in DETECTORS:
            assert isinstance(detector.tags, frozenset), (
                f"Detector '{detector.key}' tags is not a frozenset"
            )

    def test_tags_are_immutable(self):
        for detector in DETECTORS:
            with pytest.raises((AttributeError, TypeError)):
                detector.tags.add("mutation_attempt")  # type: ignore[attr-defined]

    def test_multi_tag_detectors_appear_in_both_filtered_sets(self):
        finance_keys = {d.key for d in DETECTORS if "finance" in d.tags}
        goal_keys = {d.key for d in DETECTORS if "goals" in d.tags}
        overlap = finance_keys & goal_keys
        assert len(overlap) > 0, (
            "Expected at least one detector tagged with both 'finance' and 'goals'"
        )
        for key in overlap:
            detector = next(d for d in DETECTORS if d.key == key)
            assert "finance" in detector.tags
            assert "goals" in detector.tags

    def test_detector_dataclass_is_frozen(self):
        d = DETECTORS[0]
        with pytest.raises((AttributeError, TypeError)):
            d.enabled_by_default = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# FinanceSeeder Integration
# ---------------------------------------------------------------------------

class TestFinanceSeedHelper:
    def test_seeder_creates_queryable_transactions(self, seeder, db_conn):
        tx_id = seeder.transaction(posted_at="2026-04-12", amount_cents=-1500)
        assert tx_id > 0
        row = db_conn.execute(
            "SELECT id, amount_cents FROM finance_transactions WHERE id = ?", (tx_id,)
        ).fetchone()
        assert row is not None
        assert row["amount_cents"] == -1500

    def test_batch_ids_auto_increment(self, seeder):
        id1 = seeder.batch()
        id2 = seeder.batch()
        id3 = seeder.batch()
        assert id1 < id2 < id3
        assert id2 == id1 + 1
        assert id3 == id1 + 2

    def test_batch_starts_at_one(self, seeder):
        first_id = seeder.batch()
        assert first_id == 1

    def test_category_id_returns_valid_id(self, seeder, db_conn):
        cid = seeder.category_id("Groceries")
        assert isinstance(cid, int)
        assert cid > 0
        row = db_conn.execute(
            "SELECT name FROM finance_categories WHERE id = ?", (cid,)
        ).fetchone()
        assert row is not None
        assert row["name"] == "Groceries"

    def test_account_id_returns_valid_id_for_default_account(self, seeder, db_conn):
        aid = seeder.account_id()
        assert isinstance(aid, int)
        assert aid > 0
        row = db_conn.execute(
            "SELECT name FROM finance_accounts WHERE id = ?", (aid,)
        ).fetchone()
        assert row is not None
        assert row["name"] == "DCU"

    def test_transaction_with_explicit_batch_id(self, seeder, db_conn):
        batch_id = seeder.batch()
        tx_id = seeder.transaction(
            posted_at="2026-04-12",
            amount_cents=-500,
            batch_id=batch_id,
        )
        row = db_conn.execute(
            "SELECT batch_id FROM finance_transactions WHERE id = ?", (tx_id,)
        ).fetchone()
        assert row["batch_id"] == batch_id

    def test_multiple_transactions_have_distinct_ids(self, seeder):
        ids = [seeder.transaction(posted_at="2026-04-12", amount_cents=-100) for _ in range(5)]
        assert len(ids) == len(set(ids))

    def test_goal_seeder_returns_valid_goal_id(self, seeder, db_conn):
        goal_id = seeder.goal(title="Test Budget", target_value=50_000, category_names=["Groceries"])
        assert goal_id > 0
        row = db_conn.execute(
            "SELECT title FROM goals WHERE id = ?", (goal_id,)
        ).fetchone()
        assert row is not None
        assert row["title"] == "Test Budget"


# ---------------------------------------------------------------------------
# Duplicate Query Elimination
# ---------------------------------------------------------------------------

class TestDuplicateQueryElimination:
    def test_get_uncategorized_called_exactly_once(self, db_conn):
        from minx_mcp.core.read_models import build_read_models
        from minx_mcp.finance.read_api import UncategorizedSummary

        mock_api = MagicMock()
        mock_api.get_uncategorized.return_value = UncategorizedSummary(
            transaction_count=0,
            total_spent_cents=0,
        )
        mock_api.get_spending_summary.return_value = MagicMock(
            total_spent_cents=0,
            by_category=[],
            top_merchants=[],
        )
        mock_api.get_period_comparison.return_value = MagicMock(
            current_total_spent_cents=0,
            prior_total_spent_cents=0,
        )
        mock_api.get_import_job_issues.return_value = []
        mock_api.list_account_names.return_value = []
        mock_api.list_goal_category_names.return_value = []
        mock_api.list_spending_merchant_names.return_value = []

        build_read_models(db_conn, "2026-04-12", finance_api=mock_api)

        mock_api.get_uncategorized.assert_called_once()
