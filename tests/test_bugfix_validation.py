"""Tests validating three specific bug fixes:

1. Thread-safety in FinanceService — _uncategorized_id uses thread-local storage.
2. Connection scope in _finance_query — LLM call happens outside 'with service:' block.
3. GoalCaptureOption title template removed — accepts arbitrary string titles.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from minx_mcp.core.models import GoalCaptureOption
from minx_mcp.core.query_models import FinanceQueryFilters, FinanceQueryPlan
from minx_mcp.finance import server as finance_server
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


class _RecordingFinanceService:
    """Minimal FinanceServiceLike that records context-manager entries/exits.

    Only the surfaces _finance_query touches in the NL/structured paths are
    implemented; everything else raises so a regression that takes a different
    branch fails loudly.
    """

    def __init__(self, db_path: Path, *, events: list[str]) -> None:
        self._db_path = db_path
        self._events = events
        self._entered = False

    @property
    def db_path(self) -> Path:
        return self._db_path

    def __enter__(self) -> _RecordingFinanceService:
        self._events.append("service.enter")
        self._entered = True
        return self

    def __exit__(self, *exc: object) -> None:
        self._events.append("service.exit")
        self._entered = False

    def list_account_names(self) -> list[str]:
        return []

    def list_transaction_category_names(self) -> list[str]:
        return []

    def list_spending_merchant_names(self) -> list[str]:
        return []

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected FinanceService access: {name}")


class _StubJSONLLM:
    async def run_json_prompt(self, prompt: str) -> str:
        return "{}"


def _stub_finance_query_plan(needs_clarification: bool = True) -> FinanceQueryPlan:
    if needs_clarification:
        return FinanceQueryPlan(
            intent="spending_total",
            filters=FinanceQueryFilters(),
            confidence=0.5,
            needs_clarification=True,
            clarification_type="missing_filter",
            clarification_template="finance_query.clarify.missing_filter",
            clarification_slots={"reason": "test"},
            question="which window?",
        )
    return FinanceQueryPlan(
        intent="spending_total",
        filters=FinanceQueryFilters(start_date="2026-01-01", end_date="2026-01-07"),
        confidence=0.9,
    )


def test_finance_query_nl_path_calls_llm_before_entering_service(tmp_path, monkeypatch):
    """The LLM call (interpret_finance_query) must complete before the service
    context manager is entered for query execution.

    Recorded via a spy on interpret_finance_query and a recording service
    __enter__/__exit__ — no source-text inspection.
    """
    events: list[str] = []
    service = _RecordingFinanceService(tmp_path / "minx.db", events=events)

    async def recording_interpret(**_kwargs: object) -> FinanceQueryPlan:
        events.append("llm.call")
        return _stub_finance_query_plan(needs_clarification=True)

    monkeypatch.setattr(finance_server, "interpret_finance_query", recording_interpret)

    result = asyncio.run(
        _finance_query(
            service,
            intent=None,
            filters=None,
            natural_query=None,
            message="how much did I spend?",
            review_date="2026-01-08",
            session_ref=None,
            limit=10,
            llm=_StubJSONLLM(),
        )
    )

    assert result["result_type"] == "clarify"
    # Clarification path: LLM is called and the service is never entered.
    assert events == ["llm.call"], events


def test_finance_query_nl_path_enters_service_only_after_llm(tmp_path, monkeypatch):
    """When the LLM resolves to a fully-specified plan, the service is entered
    *after* the LLM returns — the LLM await must not happen inside `with service:`.
    """
    events: list[str] = []
    service = _RecordingFinanceService(tmp_path / "minx.db", events=events)

    async def recording_interpret(**_kwargs: object) -> FinanceQueryPlan:
        assert not service._entered, "LLM was awaited while service context was open"
        events.append("llm.call")
        return _stub_finance_query_plan(needs_clarification=False)

    monkeypatch.setattr(finance_server, "interpret_finance_query", recording_interpret)

    def stub_execute(*_args: object, **_kwargs: object) -> dict[str, object]:
        events.append("execute")
        return {"result_type": "summary"}

    monkeypatch.setattr(finance_server, "_execute_finance_query_plan", stub_execute)

    result = asyncio.run(
        _finance_query(
            service,
            intent=None,
            filters=None,
            natural_query=None,
            message="spending in Q1",
            review_date="2026-01-08",
            session_ref=None,
            limit=10,
            llm=_StubJSONLLM(),
        )
    )

    assert result == {"result_type": "summary"}
    assert events == ["llm.call", "service.enter", "execute", "service.exit"], events


def test_finance_query_structured_path_enters_service_without_llm(tmp_path, monkeypatch):
    """The structured (intent != None) path enters the service immediately and
    never invokes the LLM."""
    events: list[str] = []
    service = _RecordingFinanceService(tmp_path / "minx.db", events=events)

    async def fail_interpret(**_kwargs: object) -> FinanceQueryPlan:
        raise AssertionError("structured path must not invoke the LLM")

    monkeypatch.setattr(finance_server, "interpret_finance_query", fail_interpret)
    monkeypatch.setattr(
        finance_server,
        "_validate_structured_finance_filters",
        lambda _service, _filters: {},
    )

    def stub_execute(*_args: object, **_kwargs: object) -> dict[str, object]:
        events.append("execute")
        return {"result_type": "summary"}

    monkeypatch.setattr(finance_server, "_execute_finance_query_plan", stub_execute)

    result = asyncio.run(
        _finance_query(
            service,
            intent="spending_total",
            filters={"start_date": "2026-01-01", "end_date": "2026-01-07"},
            natural_query=None,
            message=None,
            review_date=None,
            session_ref=None,
            limit=10,
            llm=_StubJSONLLM(),
        )
    )

    assert result == {"result_type": "summary"}
    assert events == ["service.enter", "execute", "service.exit"], events


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
