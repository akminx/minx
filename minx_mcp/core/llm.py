from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from minx_mcp.config import get_settings
from minx_mcp.core.models import (
    DailyTimeline,
    GoalProgress,
    InsightCandidate,
    LLMInterface,
    LLMReviewResult,
    OpenLoopsSnapshot,
    SpendingSnapshot,
)
from minx_mcp.db import get_connection
from minx_mcp.preferences import get_preference

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Base exception for handled LLM-layer failures."""


class LLMProviderError(LLMError):
    """Raised when the underlying provider call fails."""


class LLMResponseError(LLMError):
    """Raised when provider output cannot be normalized."""


MALFORMED_PROVIDER_RESPONSE_MESSAGE = "Provider returned malformed response envelope"


class _InsightCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_type: str
    dedupe_key: str
    summary: str
    supporting_signals: list[str]
    confidence: float
    severity: str
    actionability: str
    source: str


class _LLMReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    additional_insights: list[_InsightCandidatePayload]
    narrative: str
    next_day_focus: list[str]


class JSONBackedLLM:
    def __init__(
        self,
        runner: Callable[[str], Awaitable[str | dict[str, Any]]],
    ) -> None:
        self._runner = runner

    async def run_json_prompt(self, prompt: str) -> str:
        try:
            response = await self._runner(prompt)
        except LLMError:
            raise
        except Exception as exc:  # pragma: no cover - exercised via tests
            raise LLMProviderError(str(exc)) from exc

        if isinstance(response, str):
            return response
        return json.dumps(response)

    async def evaluate_review(
        self,
        timeline: DailyTimeline,
        spending: SpendingSnapshot,
        open_loops: OpenLoopsSnapshot,
        detector_insights: list[InsightCandidate],
        goal_progress: list[GoalProgress] | None = None,
    ) -> LLMReviewResult:
        prompt = _render_review_prompt(
            timeline=timeline,
            spending=spending,
            open_loops=open_loops,
            detector_insights=detector_insights,
            goal_progress=goal_progress or [],
        )
        try:
            response = await self._runner(prompt)
        except LLMError:
            raise
        except Exception as exc:  # pragma: no cover - exercised via tests
            raise LLMProviderError(str(exc)) from exc
        return normalize_review_result(response)


def _build_openai_compatible(config: dict[str, Any]) -> LLMInterface:
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    return OpenAICompatibleLLM(
        base_url=str(config["base_url"]),
        model=str(config["model"]),
        api_key_env=str(config["api_key_env"]),
        timeout_seconds=float(config.get("timeout_seconds", 30.0)),
    )


_PROVIDER_BUILDERS: dict[str, Callable[[dict[str, Any]], LLMInterface | None]] = {
    "openai_compatible": _build_openai_compatible,
}


def create_llm(
    config: dict | None = None,
    *,
    db_path: str | Path | None = None,
) -> LLMInterface | None:
    resolved = config if config is not None else _load_default_config(db_path=db_path)
    if not isinstance(resolved, dict) or not resolved:
        return None

    provider_name = resolved.get("provider")
    if not isinstance(provider_name, str) or not provider_name:
        logger.warning("LLM config missing provider; falling back to template review")
        return None

    builder = _PROVIDER_BUILDERS.get(provider_name)
    if builder is None:
        logger.warning(
            "Unknown LLM provider %s; falling back to template review",
            provider_name,
        )
        return None

    try:
        return builder(resolved)
    except Exception as exc:
        logger.warning(
            "LLM provider setup failed for %s: %s",
            provider_name,
            exc,
        )
        return None


def normalize_review_result(payload: str | dict[str, Any]) -> LLMReviewResult:
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError as exc:
        raise LLMResponseError("LLM response was not valid JSON") from exc

    try:
        validated = _LLMReviewPayload.model_validate(data)
    except ValidationError as exc:
        raise LLMResponseError("LLM response did not match the review schema") from exc

    return LLMReviewResult(
        additional_insights=[
            InsightCandidate(
                insight_type=item.insight_type,
                dedupe_key=item.dedupe_key,
                summary=item.summary,
                supporting_signals=item.supporting_signals,
                confidence=item.confidence,
                severity=item.severity,
                actionability=item.actionability,
                source=item.source,
            )
            for item in validated.additional_insights
        ],
        narrative=validated.narrative,
        next_day_focus=validated.next_day_focus,
    )


def extract_openai_message_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise LLMProviderError(MALFORMED_PROVIDER_RESPONSE_MESSAGE)

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMProviderError(MALFORMED_PROVIDER_RESPONSE_MESSAGE)

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise LLMProviderError(MALFORMED_PROVIDER_RESPONSE_MESSAGE)

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise LLMProviderError(MALFORMED_PROVIDER_RESPONSE_MESSAGE)

    content = message.get("content")
    if not isinstance(content, str):
        raise LLMProviderError(MALFORMED_PROVIDER_RESPONSE_MESSAGE)

    return content


def _load_default_config(db_path: str | Path | None = None) -> dict[str, Any] | None:
    import sqlite3 as _sqlite3

    resolved_db_path = Path(db_path) if db_path is not None else get_settings().db_path
    conn = get_connection(resolved_db_path)
    try:
        config = get_preference(conn, "core", "llm_config", None)
        return config if isinstance(config, dict) else None
    except _sqlite3.OperationalError as exc:
        # Expected when the preferences table doesn't exist yet (fresh DB or missing migration).
        logger.debug("core/llm_config preference table not available: %s", exc)
        return None
    except Exception as exc:
        # Unexpected: DB corruption or programming error — log at WARNING so it surfaces.
        logger.warning("Unable to load core/llm_config preference (unexpected error): %s", exc)
        return None
    finally:
        conn.close()


def _render_review_prompt(
    *,
    timeline: DailyTimeline,
    spending: SpendingSnapshot,
    open_loops: OpenLoopsSnapshot,
    detector_insights: list[InsightCandidate],
    goal_progress: list[GoalProgress] | None = None,
) -> str:
    timeline_lines = [
        f"- {entry.occurred_at} | {entry.domain} | {entry.summary}" for entry in timeline.entries
    ] or ["- No timeline events."]
    open_loop_lines = [
        f"- {loop.loop_type} | {loop.severity} | {loop.description}" for loop in open_loops.loops
    ] or ["- No open loops."]
    detector_lines = [
        f"- {insight.severity} | {insight.summary}" for insight in detector_insights
    ] or ["- No detector insights."]

    goal_lines = [
        (
            f"- {goal.title} | status={goal.status} | actual={goal.actual_value} | "
            f"target={goal.target_value} | summary={goal.summary}"
        )
        for goal in (goal_progress or [])
    ] or ["- No active goals."]

    return "\n".join(
        [
            "Generate a daily review as JSON.",
            "Return keys: additional_insights, narrative, next_day_focus.",
            f"Review date: {timeline.date}",
            "Timeline:",
            *timeline_lines,
            (
                "Spending: "
                f"total={spending.total_spent_cents}, "
                f"vs_prior_week_pct={spending.vs_prior_week_pct}, "
                f"by_category={json.dumps(spending.by_category, sort_keys=True)}"
            ),
            "Open loops:",
            *open_loop_lines,
            "Goals:",
            *goal_lines,
            "Detector insights:",
            *detector_lines,
        ]
    )
