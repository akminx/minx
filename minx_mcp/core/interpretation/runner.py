from __future__ import annotations

import json
from typing import Any
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from minx_mcp.core.interpretation.logging import log_interpretation_failure
from minx_mcp.core.llm import LLMProviderError

T = TypeVar("T", bound=BaseModel)


async def run_interpretation(*, llm: object, prompt: str, result_model: type[T]) -> T:
    structured_runner = getattr(llm, "run_structured_prompt", None)
    if structured_runner is not None and callable(structured_runner):
        try:
            payload = await structured_runner(prompt, result_model)
            return _validate_interpretation_payload(payload, result_model)
        except LLMProviderError:
            runner = getattr(llm, "run_json_prompt", None)
            if runner is not None and callable(runner):
                payload = await runner(prompt)
                return _validate_interpretation_payload(payload, result_model)
            raise
        except (TypeError, json.JSONDecodeError, ValidationError) as exc:
            prompt_summary = f"model={result_model.__name__} prompt_len={len(prompt)}"
            error_repr: object = type(exc).__name__
            log_interpretation_failure(
                task=result_model.__name__,
                prompt_summary=prompt_summary,
                error=error_repr,
            )
            raise RuntimeError("Interpretation schema validation failed") from exc
        except Exception as exc:
            prompt_summary = f"model={result_model.__name__} prompt_len={len(prompt)}"
            log_interpretation_failure(
                task=result_model.__name__,
                prompt_summary=prompt_summary,
                error=type(exc).__name__,
            )
            raise

    runner = getattr(llm, "run_json_prompt", None)
    if runner is None or not callable(runner):
        raise RuntimeError("Interpretation LLM must implement run_json_prompt or run_structured_prompt")

    try:
        payload = await runner(prompt)
        return _validate_interpretation_payload(payload, result_model)
    except (TypeError, json.JSONDecodeError, ValidationError) as exc:
        prompt_summary = f"model={result_model.__name__} prompt_len={len(prompt)}"
        # Validation and decode errors can embed raw model output, which may
        # include user text. Log only the exception type.
        error_repr: object = type(exc).__name__
        log_interpretation_failure(
            task=result_model.__name__,
            prompt_summary=prompt_summary,
            error=error_repr,
        )
        raise RuntimeError("Interpretation schema validation failed") from exc
    except Exception as exc:
        prompt_summary = f"model={result_model.__name__} prompt_len={len(prompt)}"
        log_interpretation_failure(
            task=result_model.__name__,
            prompt_summary=prompt_summary,
            error=type(exc).__name__,
        )
        raise


def _validate_interpretation_payload(payload: Any, result_model: type[T]) -> T:
    if isinstance(payload, result_model):
        return payload
    if isinstance(payload, str):
        data = json.loads(payload)
        return result_model.model_validate(data)
    return result_model.model_validate(payload)
