from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from minx_mcp.core.llm import (
    LLMProviderError,
    MALFORMED_PROVIDER_RESPONSE_MESSAGE,
    _render_review_prompt,
    extract_openai_message_content,
    normalize_review_result,
)
from minx_mcp.core.models import (
    DailyTimeline,
    GoalProgress,
    InsightCandidate,
    LLMReviewResult,
    OpenLoopsSnapshot,
    SpendingSnapshot,
)


@dataclass(frozen=True)
class OpenAICompatibleLLM:
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float = 30.0

    async def run_json_prompt(self, prompt: str) -> str:
        payload = await self._post_chat_completion(prompt)
        return extract_openai_message_content(payload)

    async def evaluate_review(
        self,
        timeline: DailyTimeline,
        spending: SpendingSnapshot,
        open_loops: OpenLoopsSnapshot,
        detector_insights: list[InsightCandidate],
        goal_progress: list[GoalProgress] | None = None,
    ) -> LLMReviewResult:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise LLMProviderError(
                f"Missing API key environment variable: {self.api_key_env}"
            )

        prompt = _render_review_prompt(
            timeline=timeline,
            spending=spending,
            open_loops=open_loops,
            detector_insights=detector_insights,
            goal_progress=goal_progress or [],
        )
        payload = await self._post_chat_completion(prompt)
        content = extract_openai_message_content(payload)
        return normalize_review_result(content)

    async def _post_chat_completion(self, prompt: str) -> dict:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise LLMProviderError(
                f"Missing API key environment variable: {self.api_key_env}"
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"},
                    },
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
