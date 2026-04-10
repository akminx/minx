from __future__ import annotations

import pytest

from minx_mcp.core.llm import LLMProviderError
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


class _StructuredPromptLLM:
    async def run_structured_prompt(self, prompt: str, result_model: type[GoalCaptureInterpretation]):
        assert prompt == "test"
        assert result_model is GoalCaptureInterpretation
        return {"intent": "create", "confidence": 0.92}


class _FallbackStructuredPromptLLM:
    async def run_structured_prompt(self, prompt: str, result_model: type[GoalCaptureInterpretation]):
        assert prompt == "test"
        assert result_model is GoalCaptureInterpretation
        raise LLMProviderError("json_schema unsupported")

    async def run_json_prompt(self, prompt: str) -> str:
        assert prompt == "test"
        return '{"intent":"create","confidence":0.93}'


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


@pytest.mark.asyncio
async def test_run_interpretation_does_not_log_validation_error_input_values(caplog) -> None:
    class _EchoingLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            assert prompt == "test"
            return (
                '{"intent":"create","confidence":'
                '"show me everything at Whole Foods last month"}'
            )

    with pytest.raises(RuntimeError, match="schema"):
        await run_interpretation(
            llm=_EchoingLLM(),
            prompt="test",
            result_model=GoalCaptureInterpretation,
        )

    assert "Whole Foods" not in caplog.text
    assert "ValidationError" in caplog.text


@pytest.mark.asyncio
async def test_run_interpretation_uses_structured_prompt_when_available() -> None:
    result = await run_interpretation(
        llm=_StructuredPromptLLM(),
        prompt="test",
        result_model=GoalCaptureInterpretation,
    )

    assert result.intent == "create"
    assert result.confidence == 0.92


@pytest.mark.asyncio
async def test_run_interpretation_falls_back_to_json_prompt_when_structured_prompt_is_unsupported() -> None:
    result = await run_interpretation(
        llm=_FallbackStructuredPromptLLM(),
        prompt="test",
        result_model=GoalCaptureInterpretation,
    )

    assert result.intent == "create"
    assert result.confidence == 0.93
