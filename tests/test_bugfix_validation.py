"""Tests validating three specific bug fixes:

1. Thread-safety in FinanceService — _uncategorized_id uses thread-local storage.
2. Connection scope in _finance_query — LLM call happens outside 'with service:' block.
3. GoalCaptureOption title template removed — accepts arbitrary string titles.
"""

from __future__ import annotations

import inspect
import threading
from pathlib import Path

import pytest

from minx_mcp.core.models import GoalCaptureOption
from minx_mcp.finance.server import _finance_query
from minx_mcp.finance.service import FinanceService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(tmp_path: Path) -> FinanceService:
    return FinanceService(tmp_path / "minx.db", tmp_path / "vault")


# ---------------------------------------------------------------------------
# Thread-safety — _uncategorized_id / thread-local storage
# ---------------------------------------------------------------------------


def test_finance_service_has_thread_local(tmp_path):
    """FinanceService must expose self._local as a threading.local() object."""
    service = _make_service(tmp_path)
    assert hasattr(service, "_local"), "FinanceService must have a _local attribute"
    assert isinstance(service._local, threading.local)


def test_uncategorized_id_cached_per_thread(tmp_path):
    """Each thread independently resolves and caches the uncategorized category id."""
    service = _make_service(tmp_path)
    results: dict[int, int] = {}
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            category_id = service._uncategorized_id()
            results[idx] = category_id
        except Exception as exc:
            errors.append(exc)
        finally:
            # Close the thread-owned sqlite connection inside its owning thread.
            # sqlite3's default check_same_thread=True forbids closing from
            # another thread, and BaseService.close() only affects the current
            # context; skipping this leaks the connection and surfaces as a
            # PytestUnraisableExceptionWarning under -W error.
            service.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"
    assert len(results) == 5
    # All threads should resolve the same id value
    unique_ids = set(results.values())
    assert len(unique_ids) == 1, f"Expected one unique id, got {unique_ids}"


def test_thread_local_caches_value_independently(tmp_path):
    """Each thread's thread-local cache is independent — no shared state leak."""
    service = _make_service(tmp_path)
    cached_values: list[int | None] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            # First call populates thread-local cache
            first = service._uncategorized_id()
            # Read the cached value from thread-local directly
            cached = getattr(service._local, "uncategorized_category_id", None)
            with lock:
                cached_values.append(cached)
            # Second call should return the same cached value
            second = service._uncategorized_id()
            assert first == second
        finally:
            service.close()  # close this thread's owned connection

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every thread should have populated its own cache slot
    assert len(cached_values) == 4
    assert all(v is not None for v in cached_values), "Thread-local cache was not populated"
    # All cached values should be the same integer
    assert len(set(cached_values)) == 1, "Different threads resolved different category ids"


def test_concurrent_uncategorized_id_no_race(tmp_path):
    """Hammer _uncategorized_id from many threads concurrently; no exceptions expected."""
    service = _make_service(tmp_path)
    barrier = threading.Barrier(10)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            barrier.wait()  # synchronise start to maximise contention
            for _ in range(20):
                service._uncategorized_id()
        except Exception as exc:
            errors.append(exc)
        finally:
            service.close()  # close this thread's owned connection

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Race condition produced errors: {errors}"


# ---------------------------------------------------------------------------
# Connection scope — _finance_query NL path does not hold connection over LLM
# ---------------------------------------------------------------------------


def test_finance_query_nl_path_calls_llm_outside_connection_context():
    """Structural test: verify the NL path in _finance_query awaits interpret_finance_query
    before opening the 'with service:' connection context for query execution.

    The fix ensures the LLM call is NOT wrapped in 'with service:', so we inspect
    the source to verify the expected ordering of statements.
    """
    source = inspect.getsource(_finance_query)

    # The LLM call — interpret_finance_query — must appear in the source.
    assert "interpret_finance_query" in source, "_finance_query must call interpret_finance_query"

    # The NL result check (needs_clarification) must appear before the final 'with service:'
    assert "needs_clarification" in source, "_finance_query must check needs_clarification"

    # Find line positions to verify ordering: LLM call precedes 'with service:' for NL path.
    lines = source.splitlines()

    llm_call_line = next(
        (i for i, ln in enumerate(lines) if "interpret_finance_query" in ln),
        None,
    )
    # The final 'with service:' that opens the connection for query execution
    with_service_lines = [i for i, ln in enumerate(lines) if "with service:" in ln]

    assert llm_call_line is not None, "interpret_finance_query call not found in source"
    assert with_service_lines, "'with service:' block not found in _finance_query source"

    # The last 'with service:' block (used for NL query execution) must come AFTER the LLM call
    last_with_service = max(with_service_lines)
    assert llm_call_line < last_with_service, (
        "LLM call (interpret_finance_query) must appear before the final 'with service:' block. "
        f"LLM call at line {llm_call_line}, last 'with service:' at line {last_with_service}"
    )


def test_finance_query_structured_path_uses_with_service():
    """The structured (intent != None) path still uses 'with service:' as expected."""
    source = inspect.getsource(_finance_query)

    # intent is not None branch — must still have a with service: block
    assert "if intent is not None:" in source
    assert "with service:" in source


def test_finance_query_nl_path_validates_before_connection():
    """Validation calls (_validate_date_range, _validate_optional_text_filters)
    happen after the LLM call but before 'with service:', keeping the connection
    scope tight around only the final DB query.
    """
    source = inspect.getsource(_finance_query)
    lines = source.splitlines()

    llm_call_line = next(
        (i for i, ln in enumerate(lines) if "interpret_finance_query" in ln),
        None,
    )
    validate_date_line = next(
        (
            i
            for i, ln in enumerate(lines)
            if "_validate_date_range" in ln and i > (llm_call_line or 0)
        ),
        None,
    )
    # The last 'with service:' is the NL query execution block
    with_service_lines = [i for i, ln in enumerate(lines) if "with service:" in ln]
    last_with_service = max(with_service_lines) if with_service_lines else None

    assert llm_call_line is not None
    assert validate_date_line is not None, (
        "_validate_date_range must be called after LLM in NL path"
    )
    assert last_with_service is not None

    assert llm_call_line < validate_date_line < last_with_service, (
        "Expected: LLM call → validation → 'with service:', but ordering was wrong. "
        f"LLM={llm_call_line}, validate={validate_date_line}, with service={last_with_service}"
    )


# ---------------------------------------------------------------------------
# GoalCaptureOption — rigid title template removed
# ---------------------------------------------------------------------------


def _make_category_option(title: str | None = None) -> GoalCaptureOption:
    payload: dict[str, object] = {"category_names": ["Dining Out"]}
    if title is not None:
        payload["title"] = title
    return GoalCaptureOption(
        kind="category",
        label="Dining Out",
        category_name="Dining Out",
        payload_fragment=payload,
    )


def _make_merchant_option(title: str | None = None) -> GoalCaptureOption:
    payload: dict[str, object] = {"merchant_names": ["Coffee Shop"]}
    if title is not None:
        payload["title"] = title
    return GoalCaptureOption(
        kind="merchant",
        label="Coffee Shop",
        merchant_name="Coffee Shop",
        payload_fragment=payload,
    )


def test_goal_capture_option_accepts_custom_category_title():
    """Category option accepts a custom title string not matching the old rigid pattern."""
    opt = _make_category_option(title="My Custom Cap")
    assert opt.payload_fragment is not None
    assert opt.payload_fragment["title"] == "My Custom Cap"


def test_goal_capture_option_accepts_custom_merchant_title():
    """Merchant option accepts a custom title string not matching the old rigid pattern."""
    opt = _make_merchant_option(title="Weekly Coffee Budget")
    assert opt.payload_fragment is not None
    assert opt.payload_fragment["title"] == "Weekly Coffee Budget"


def test_goal_capture_option_accepts_payload_without_title_field():
    """payload_fragment without a title key is valid — title is optional."""
    opt = _make_category_option(title=None)
    assert opt.payload_fragment is not None
    assert "title" not in opt.payload_fragment


def test_goal_capture_option_rejects_non_string_title():
    """payload_fragment with a non-string title raises ValueError."""
    payload: dict[str, object] = {"category_names": ["Dining Out"], "title": 123}
    with pytest.raises(ValueError, match="title"):
        GoalCaptureOption(
            kind="category",
            label="Dining Out",
            category_name="Dining Out",
            payload_fragment=payload,
        )


def test_goal_capture_option_rejects_none_title_in_payload():
    """payload_fragment with title=None (explicitly set) raises ValueError."""
    payload: dict[str, object] = {"category_names": ["Dining Out"], "title": None}
    with pytest.raises(ValueError, match="title"):
        GoalCaptureOption(
            kind="category",
            label="Dining Out",
            category_name="Dining Out",
            payload_fragment=payload,
        )


def test_goal_capture_option_various_custom_titles_all_succeed():
    """A range of custom title strings are all accepted without error."""
    custom_titles = [
        "Dining Out Spending Cap",  # old pattern still works
        "My Grocery Budget",  # different pattern
        "Q2 Travel Limit",  # date-prefixed
        "coffee",  # lowercase, short
        "A" * 100,  # long string
        "Spending Cap — Dining Out 🍕",  # unicode/emoji
    ]
    for title in custom_titles:
        opt = _make_category_option(title=title)
        assert opt.payload_fragment is not None
        assert opt.payload_fragment["title"] == title, f"title mismatch for: {title!r}"


def test_goal_capture_option_goal_kind_accepts_any_title():
    """Goal-kind options accept any string title (unrelated to spending cap format)."""
    opt = GoalCaptureOption(
        kind="goal",
        goal_id=42,
        title="Completely Custom Goal Title",
        label="My Goal",
    )
    assert opt.title == "Completely Custom Goal Title"
