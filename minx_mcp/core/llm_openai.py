from __future__ import annotations

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


@dataclass(frozen=True)
class OpenAICompatibleLLM:
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float = 30.0
    provider_preferences: dict[str, Any] | None = None

    async def run_json_prompt(self, prompt: str) -> str:
        payload = await self._post_chat_completion(prompt)
        return extract_openai_message_content(payload)

    async def run_structured_prompt(
        self,
        prompt: str,
        result_model: type[BaseModel],
    ) -> dict[str, Any]:
        payload = await self._post_chat_completion(
            prompt,
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

    async def _post_chat_completion(
        self,
        prompt: str,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise LLMProviderError(f"Missing API key environment variable: {self.api_key_env}")

        request_json: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": response_format or {"type": "json_object"},
        }
        if self.provider_preferences:
            request_json["provider"] = self.provider_preferences

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
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
