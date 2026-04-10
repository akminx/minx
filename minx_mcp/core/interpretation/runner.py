from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from minx_mcp.core.interpretation.logging import log_interpretation_failure

T = TypeVar("T", bound=BaseModel)


async def run_interpretation(*, llm: object, prompt: str, result_model: type[T]) -> T:
    runner = getattr(llm, "run_json_prompt", None)
    if runner is None or not callable(runner):
        raise RuntimeError("Interpretation LLM must implement run_json_prompt")

    payload = await runner(prompt)
    try:
        data = json.loads(payload)
        return result_model.model_validate(data)
    except (TypeError, json.JSONDecodeError, ValidationError) as exc:
        prompt_summary = f"model={result_model.__name__} prompt_len={len(prompt)}"
        log_interpretation_failure(
            task=result_model.__name__,
            prompt_summary=prompt_summary,
            error=exc,
        )
        raise RuntimeError("Interpretation schema validation failed") from exc
