from __future__ import annotations

import pytest

from minx_mcp.core.models import (
    DailyTimeline,
    OpenLoopsSnapshot,
    SpendingSnapshot,
    TimelineEntry,
)
from minx_mcp.db import get_connection
from minx_mcp.preferences import set_preference


def test_create_llm_returns_none_when_configuration_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MINX_DB_PATH", str(tmp_path / "minx.db"))
    get_connection(tmp_path / "minx.db").close()

    from minx_mcp.core.llm import create_llm

    assert create_llm() is None


def test_create_llm_reads_preference_config_when_explicit_config_is_absent(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "minx.db"
    monkeypatch.setenv("MINX_DB_PATH", str(db_path))
    conn = get_connection(db_path)
    set_preference(
        conn,
        "core",
        "llm_config",
        {"provider": "fake", "model": "reviewer-v1"},
    )

    import minx_mcp.core.llm as llm

    monkeypatch.setattr(
        llm,
        "_PROVIDER_BUILDERS",
        {"fake": lambda config: _RecordingLLM(config)},
    )

    instance = llm.create_llm()

    assert isinstance(instance, _RecordingLLM)
    assert instance.config == {"provider": "fake", "model": "reviewer-v1"}


def test_create_llm_returns_none_when_provider_setup_fails(caplog):
    import minx_mcp.core.llm as llm

    def explode(_config):
        raise RuntimeError("provider init failed")

    original = llm._PROVIDER_BUILDERS
    try:
        llm._PROVIDER_BUILDERS = {"broken": explode}
        created = llm.create_llm({"provider": "broken"})
    finally:
        llm._PROVIDER_BUILDERS = original

    assert created is None
    assert "provider init failed" in caplog.text


def test_create_llm_returns_none_for_unknown_provider(caplog):
    from minx_mcp.core.llm import create_llm

    created = create_llm({"provider": "missing"})

    assert created is None
    assert "Unknown LLM provider" in caplog.text


def test_normalize_review_result_parses_valid_json_output():
    from minx_mcp.core.llm import normalize_review_result

    result = normalize_review_result(
        """
        {
          "additional_insights": [
            {
              "insight_type": "finance.open_loop",
              "dedupe_key": "2026-03-15:open_loop:failed_import_job:job-1",
              "summary": "Import job job-1 failed for /imports/a.csv",
              "supporting_signals": ["Import job job-1 failed for /imports/a.csv"],
              "confidence": 0.9,
              "severity": "warning",
              "actionability": "action_needed",
              "source": "llm"
            }
          ],
          "narrative": "A finance import needs attention.",
          "next_day_focus": ["Check failed finance import job job-1"]
        }
        """
    )

    assert result.narrative == "A finance import needs attention."
    assert result.next_day_focus == ["Check failed finance import job job-1"]
    assert len(result.additional_insights) == 1
    assert result.additional_insights[0].source == "llm"


def test_normalize_review_result_rejects_malformed_json():
    from minx_mcp.core.llm import LLMResponseError, normalize_review_result

    with pytest.raises(LLMResponseError):
        normalize_review_result("{not-json}")


@pytest.mark.asyncio
async def test_json_llm_evaluate_review_wraps_provider_exceptions():
    from minx_mcp.core.llm import JSONBackedLLM, LLMProviderError

    async def explode(_prompt: str) -> str:
        raise RuntimeError("provider boom")

    llm = JSONBackedLLM(explode)

    with pytest.raises(LLMProviderError):
        await llm.evaluate_review(
            timeline=_timeline(),
            spending=_spending(),
            open_loops=OpenLoopsSnapshot(date="2026-03-15", loops=[]),
            detector_insights=[],
        )


@pytest.mark.asyncio
async def test_json_llm_evaluate_review_normalizes_valid_provider_output():
    from minx_mcp.core.llm import JSONBackedLLM

    async def respond(_prompt: str) -> str:
        return """
        {
          "additional_insights": [],
          "narrative": "Quiet day overall.",
          "next_day_focus": []
        }
        """

    llm = JSONBackedLLM(respond)

    result = await llm.evaluate_review(
        timeline=_timeline(),
        spending=_spending(),
        open_loops=OpenLoopsSnapshot(date="2026-03-15", loops=[]),
        detector_insights=[],
    )

    assert result.narrative == "Quiet day overall."
    assert result.additional_insights == []
    assert result.next_day_focus == []


def test_create_llm_builds_openai_compatible_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from minx_mcp.core.llm import create_llm
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    created = create_llm(
        {
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
        }
    )

    assert isinstance(created, OpenAICompatibleLLM)
    assert created.base_url == "https://api.example.com/v1"
    assert created.model == "gpt-4o-mini"
    assert created.api_key_env == "OPENAI_API_KEY"


@pytest.mark.asyncio
async def test_openai_compatible_llm_posts_chat_completion_and_normalizes_json(
    monkeypatch,
):
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"additional_insights": [], '
                                '"narrative": "Goal-aware review.", '
                                '"next_day_focus": ["Stay under budget"]}'
                            )
                        }
                    }
                ]
            }

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("minx_mcp.core.llm_openai.httpx.AsyncClient", _FakeClient)

    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        timeout_seconds=15.0,
    )
    result = await llm.evaluate_review(
        timeline=_timeline(),
        spending=_spending(),
        open_loops=OpenLoopsSnapshot(date="2026-03-15", loops=[]),
        detector_insights=[],
    )

    assert result.narrative == "Goal-aware review."
    assert result.next_day_focus == ["Stay under budget"]
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_openai_compatible_llm_raises_provider_error_on_missing_key(monkeypatch):
    from minx_mcp.core.llm import LLMProviderError
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    )

    with pytest.raises(LLMProviderError, match="Missing API key"):
        await llm.evaluate_review(
            timeline=_timeline(),
            spending=_spending(),
            open_loops=OpenLoopsSnapshot(date="2026-03-15", loops=[]),
            detector_insights=[],
        )


@pytest.mark.asyncio
async def test_openai_compatible_llm_rejects_empty_choices(monkeypatch):
    from minx_mcp.core.llm import LLMProviderError
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": []}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            return _FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("minx_mcp.core.llm_openai.httpx.AsyncClient", _FakeClient)

    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    )

    with pytest.raises(LLMProviderError, match="malformed response"):
        await llm.evaluate_review(
            timeline=_timeline(),
            spending=_spending(),
            open_loops=OpenLoopsSnapshot(date="2026-03-15", loops=[]),
            detector_insights=[],
        )


@pytest.mark.asyncio
async def test_openai_compatible_llm_rejects_missing_message_content(monkeypatch):
    from minx_mcp.core.llm import LLMProviderError
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            return _FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("minx_mcp.core.llm_openai.httpx.AsyncClient", _FakeClient)

    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    )

    with pytest.raises(LLMProviderError, match="malformed response"):
        await llm.evaluate_review(
            timeline=_timeline(),
            spending=_spending(),
            open_loops=OpenLoopsSnapshot(date="2026-03-15", loops=[]),
            detector_insights=[],
        )


@pytest.mark.asyncio
async def test_openai_compatible_llm_rejects_non_string_message_content(monkeypatch):
    from minx_mcp.core.llm import LLMProviderError
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": ["not", "a", "string"]}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            return _FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("minx_mcp.core.llm_openai.httpx.AsyncClient", _FakeClient)

    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    )

    with pytest.raises(LLMProviderError, match="malformed response"):
        await llm.evaluate_review(
            timeline=_timeline(),
            spending=_spending(),
            open_loops=OpenLoopsSnapshot(date="2026-03-15", loops=[]),
            detector_insights=[],
        )


class _RecordingLLM:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config

    async def evaluate_review(
        self, timeline, spending, open_loops, detector_insights, goal_progress=None
    ):
        raise NotImplementedError


def _timeline() -> DailyTimeline:
    return DailyTimeline(
        date="2026-03-15",
        entries=[
            TimelineEntry(
                occurred_at="2026-03-15T12:00:00Z",
                domain="finance",
                event_type="finance.transactions_imported",
                summary="Imported 3 transactions",
                entity_ref="batch-1",
            )
        ],
    )


def _spending() -> SpendingSnapshot:
    return SpendingSnapshot(
        date="2026-03-15",
        total_spent_cents=4200,
        by_category={"Groceries": 4200},
        top_merchants=[("HEB", 4200)],
        vs_prior_week_pct=12.5,
        uncategorized_count=0,
        uncategorized_total_cents=0,
    )
