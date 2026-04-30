"""Tests for OpenAICompatibleLLM tool-calling and OpenRouter integration.

Covers the surface the Hermes investigation loop drives through an
OpenAI-compatible provider:
- request shape (tools, tool_choice, provider, reasoning, OpenRouter headers)
- response parsing (tool_calls with JSON-string arguments, reasoning_details
  carried through verbatim, content-only final answer path)
- error handling (malformed tool_call arguments, missing assistant message)
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from minx_mcp.core.llm import LLMProviderError
from minx_mcp.core.llm_openai import OpenAICompatibleLLM


def _make_llm(
    handler,
    *,
    provider_preferences: dict[str, Any] | None = None,
    reasoning: dict[str, Any] | None = None,
    monkeypatch: pytest.MonkeyPatch,
) -> OpenAICompatibleLLM:
    monkeypatch.setenv("FAKE_OPENROUTER_KEY", "sk-or-v1-test")
    return OpenAICompatibleLLM(
        base_url="https://openrouter.ai/api/v1",
        model="google/gemini-2.5-flash",
        api_key_env="FAKE_OPENROUTER_KEY",
        provider_preferences=provider_preferences,
        reasoning=reasoning,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_tool_calling_turn_parses_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "memory_search",
                                        "arguments": json.dumps(
                                            {"query": "dining drift", "limit": 10}
                                        ),
                                    },
                                }
                            ],
                            "reasoning_details": [
                                {"type": "reasoning.text", "text": "consider memory first"}
                            ],
                        }
                    }
                ]
            },
        )

    llm = _make_llm(
        handler,
        provider_preferences={"data_collection": "deny", "require_parameters": True},
        reasoning={"effort": "medium"},
        monkeypatch=monkeypatch,
    )
    turn = await llm.run_tool_calling_turn(
        messages=[{"role": "user", "content": "why did dining go up?"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "memory_search",
                    "description": "Search durable memories.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                        "required": ["query"],
                    },
                },
            }
        ],
    )

    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "memory_search"
    assert call.arguments == {"query": "dining drift", "limit": 10}
    assert turn.content is None
    assert turn.reasoning_details == [
        {"type": "reasoning.text", "text": "consider memory first"}
    ]
    assert turn.raw_assistant_message["role"] == "assistant"

    # Request shape
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["authorization"] == "Bearer sk-or-v1-test"
    assert captured["headers"]["http-referer"] == "https://github.com/akminx/minx-mcp"
    assert captured["headers"]["x-title"] == "Minx MCP"
    assert captured["body"]["model"] == "google/gemini-2.5-flash"
    assert captured["body"]["tool_choice"] == "auto"
    assert captured["body"]["tools"][0]["function"]["name"] == "memory_search"
    assert captured["body"]["provider"] == {
        "data_collection": "deny",
        "require_parameters": True,
    }
    assert captured["body"]["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_tool_calling_turn_accepts_dict_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_dict",
                                    "type": "function",
                                    "function": {
                                        "name": "memory_search",
                                        "arguments": {"query": "dining drift", "limit": 5},
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    llm = _make_llm(handler, monkeypatch=monkeypatch)

    turn = await llm.run_tool_calling_turn(
        messages=[{"role": "user", "content": "why did dining go up?"}],
        tools=[],
    )

    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].arguments == {"query": "dining drift", "limit": 5}


@pytest.mark.asyncio
async def test_tool_calling_turn_returns_final_content(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Dining rose because of three new restaurants.",
                            "tool_calls": [],
                        }
                    }
                ]
            },
        )

    llm = _make_llm(handler, monkeypatch=monkeypatch)
    turn = await llm.run_tool_calling_turn(
        messages=[{"role": "user", "content": "summarize"}],
        tools=[],
        tool_choice="none",
    )
    assert turn.tool_calls == []
    assert turn.content == "Dining rose because of three new restaurants."


@pytest.mark.asyncio
async def test_tool_calling_turn_rejects_malformed_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_bad",
                                    "type": "function",
                                    "function": {
                                        "name": "memory_search",
                                        "arguments": "{not valid json",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    llm = _make_llm(handler, monkeypatch=monkeypatch)
    with pytest.raises(LLMProviderError, match="not valid JSON"):
        await llm.run_tool_calling_turn(
            messages=[{"role": "user", "content": "x"}], tools=[]
        )


@pytest.mark.asyncio
async def test_tool_calling_turn_rejects_non_object_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_bad",
                                    "type": "function",
                                    "function": {
                                        "name": "memory_search",
                                        "arguments": json.dumps(["not", "an", "object"]),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    llm = _make_llm(handler, monkeypatch=monkeypatch)

    with pytest.raises(LLMProviderError, match="arguments must be a JSON object"):
        await llm.run_tool_calling_turn(
            messages=[{"role": "user", "content": "x"}], tools=[]
        )


@pytest.mark.asyncio
async def test_tool_calling_turn_skips_malformed_tool_call_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                "not a dict",
                                {"function": "not a dict"},
                                {"function": {"name": 42, "arguments": "{}"}},
                                {
                                    "id": "call_ok",
                                    "type": "function",
                                    "function": {
                                        "name": "memory_search",
                                        "arguments": "{}",
                                    },
                                },
                            ],
                        }
                    }
                ]
            },
        )

    llm = _make_llm(handler, monkeypatch=monkeypatch)
    turn = await llm.run_tool_calling_turn(
        messages=[{"role": "user", "content": "x"}], tools=[]
    )
    assert [c.name for c in turn.tool_calls] == ["memory_search"]


@pytest.mark.asyncio
async def test_run_json_prompt_still_works_after_refactor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"intent": "summarize"}',
                        }
                    }
                ]
            },
        )

    llm = _make_llm(handler, monkeypatch=monkeypatch)
    out = await llm.run_json_prompt("classify this")
    assert out == '{"intent": "summarize"}'
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "tools" not in captured["body"]


@pytest.mark.asyncio
async def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAKE_OPENROUTER_KEY", raising=False)

    def handler(_req: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    llm = OpenAICompatibleLLM(
        base_url="https://openrouter.ai/api/v1",
        model="google/gemini-2.5-flash",
        api_key_env="FAKE_OPENROUTER_KEY",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMProviderError, match="Missing API key"):
        await llm.run_tool_calling_turn(
            messages=[{"role": "user", "content": "x"}], tools=[]
        )
