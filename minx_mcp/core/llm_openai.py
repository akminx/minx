from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel

from minx_mcp.core.llm import (
    MALFORMED_PROVIDER_RESPONSE_MESSAGE,
    LLMProviderError,
    extract_openai_message_content,
)

# OpenRouter encourages clients to identify themselves so it can attribute
# usage and route around bad providers. Harmless on direct OpenAI too.
_OPENROUTER_REFERER = "https://github.com/akminx/minx-mcp"
_OPENROUTER_TITLE = "Minx MCP"


@dataclass(frozen=True)
class OpenAICompatibleLLM:
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float = 30.0
    provider_preferences: dict[str, Any] | None = None
    reasoning: dict[str, Any] | None = None
    # Optional httpx transport for tests; production leaves this None.
    transport: Any = None

    async def run_json_prompt(self, prompt: str) -> str:
        payload = await self._post_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return extract_openai_message_content(payload)

    async def run_structured_prompt(
        self,
        prompt: str,
        result_model: type[BaseModel],
    ) -> dict[str, Any]:
        payload = await self._post_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": result_model.__name__,
                    "schema": result_model.model_json_schema(),
                    "strict": True,
                },
            },
        )
        content = extract_openai_message_content(payload)
        return result_model.model_validate_json(content).model_dump()

    async def run_tool_calling_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> ToolCallingTurn:
        """Run one chat turn with tools. Returns either tool_calls or final content.

        Preserves OpenRouter `reasoning_details` from the assistant message so the
        caller can feed it back on the next turn (Nemotron-3-Super requires this
        for multi-turn reasoning continuity).
        """

        request_extras: dict[str, Any] = {"tools": tools, "tool_choice": tool_choice}
        payload = await self._post_chat_completion(
            messages=messages,
            response_format=None,
            request_extras=request_extras,
        )
        message = _extract_assistant_message(payload)
        tool_calls_raw = message.get("tool_calls")
        tool_calls: list[ToolCall] = []
        if isinstance(tool_calls_raw, list):
            for raw in tool_calls_raw:
                if not isinstance(raw, dict):
                    continue
                fn = raw.get("function") or {}
                if not isinstance(fn, dict):
                    continue
                name = fn.get("name")
                args = fn.get("arguments")
                if not isinstance(name, str):
                    continue
                try:
                    parsed_args = (
                        json.loads(args) if isinstance(args, str) and args else {}
                    )
                except json.JSONDecodeError as exc:
                    raise LLMProviderError(
                        f"tool_calls arguments not valid JSON for {name}: {exc}"
                    ) from exc
                if not isinstance(parsed_args, dict):
                    parsed_args = {}
                tool_calls.append(
                    ToolCall(
                        id=str(raw.get("id") or ""),
                        name=name,
                        arguments=parsed_args,
                    )
                )

        content = message.get("content") if isinstance(message.get("content"), str) else None
        reasoning_details = message.get("reasoning_details")
        return ToolCallingTurn(
            tool_calls=tool_calls,
            content=content,
            reasoning_details=reasoning_details if isinstance(reasoning_details, list) else None,
            raw_assistant_message=message,
        )

    async def _post_chat_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None,
        request_extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise LLMProviderError(f"Missing API key environment variable: {self.api_key_env}")

        request_json: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if response_format is not None:
            request_json["response_format"] = response_format
        if self.provider_preferences:
            request_json["provider"] = self.provider_preferences
        if self.reasoning is not None:
            request_json["reasoning"] = self.reasoning
        if request_extras:
            request_json.update(request_extras)

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "HTTP-Referer": _OPENROUTER_REFERER,
                        "X-Title": _OPENROUTER_TITLE,
                    },
                    json=request_json,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMProviderError(str(exc)) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMProviderError(MALFORMED_PROVIDER_RESPONSE_MESSAGE) from exc
        if not isinstance(payload, dict):
            raise LLMProviderError(MALFORMED_PROVIDER_RESPONSE_MESSAGE)
        return payload


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolCallingTurn:
    tool_calls: list[ToolCall]
    content: str | None
    reasoning_details: list[Any] | None
    # Preserved verbatim so the caller can echo it on the next request.
    raw_assistant_message: dict[str, Any]


def _extract_assistant_message(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMProviderError(MALFORMED_PROVIDER_RESPONSE_MESSAGE)
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMProviderError(MALFORMED_PROVIDER_RESPONSE_MESSAGE)
    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMProviderError(MALFORMED_PROVIDER_RESPONSE_MESSAGE)
    return message
