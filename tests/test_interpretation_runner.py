from __future__ import annotations

import pytest

from minx_mcp.core.interpretation.models import GoalCaptureInterpretation
from minx_mcp.core.interpretation.runner import run_interpretation


class _StubLLM:
    async def run_json_prompt(self, prompt: str) -> str:
        assert prompt == "test"
        return '{"intent":"create","confidence":0.91}'


class _BadLLM:
    async def run_json_prompt(self, prompt: str) -> str:
        assert prompt == "test"
        return '{"intent":"unknown"}'


@pytest.mark.asyncio
async def test_run_interpretation_parses_typed_json_result() -> None:
    result = await run_interpretation(
        llm=_StubLLM(),
        prompt="test",
        result_model=GoalCaptureInterpretation,
    )

    assert result.intent == "create"
    assert result.confidence == 0.91


@pytest.mark.asyncio
async def test_run_interpretation_raises_on_schema_mismatch() -> None:
    with pytest.raises(RuntimeError, match="schema"):
        await run_interpretation(
            llm=_BadLLM(),
            prompt="test",
            result_model=GoalCaptureInterpretation,
        )
