from __future__ import annotations

import json
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from minx_mcp.core.interpretation.logging import log_interpretation_failure
from minx_mcp.core.llm import LLMProviderError
from minx_mcp.core.models import JSONLLMInterface

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class StructuredPromptLLMInterface(Protocol):
    async def run_structured_prompt(self, prompt: str, result_model: type[BaseModel]) -> Any: ...


async def run_interpretation[T: BaseModel](
    *,
    llm: JSONLLMInterface | StructuredPromptLLMInterface,
    prompt: str,
    result_model: type[T],
) -> T:
    if isinstance(llm, StructuredPromptLLMInterface):
        try:
            payload = await llm.run_structured_prompt(prompt, result_model)
            return _validate_interpretation_payload(payload, result_model)
        except LLMProviderError:
            if isinstance(llm, JSONLLMInterface):
                payload = await llm.run_json_prompt(prompt)
                return _validate_interpretation_payload(payload, result_model)
            raise
        except (TypeError, json.JSONDecodeError, ValidationError) as exc:
            _log_schema_validation_failure(prompt=prompt, result_model=result_model, error=exc)
            raise RuntimeError("Interpretation schema validation failed") from exc
        except Exception as exc:
            prompt_summary = f"model={result_model.__name__} prompt_len={len(prompt)}"
            log_interpretation_failure(
                task=result_model.__name__,
                prompt_summary=prompt_summary,
                error=type(exc).__name__,
            )
            raise

    if not isinstance(llm, JSONLLMInterface):
        raise RuntimeError(
            "Interpretation LLM must implement run_json_prompt or run_structured_prompt"
        )

    try:
        payload = await llm.run_json_prompt(prompt)
        return _validate_interpretation_payload(payload, result_model)
    except (TypeError, json.JSONDecodeError, ValidationError) as exc:
        _log_schema_validation_failure(prompt=prompt, result_model=result_model, error=exc)
        raise RuntimeError("Interpretation schema validation failed") from exc
    except Exception as exc:
        prompt_summary = f"model={result_model.__name__} prompt_len={len(prompt)}"
        log_interpretation_failure(
            task=result_model.__name__,
            prompt_summary=prompt_summary,
            error=type(exc).__name__,
        )
        raise


def _validate_interpretation_payload[T: BaseModel](payload: Any, result_model: type[T]) -> T:
    if isinstance(payload, result_model):
        return payload
    if isinstance(payload, str):
        data = json.loads(payload)
        return result_model.model_validate(data)
    return result_model.model_validate(payload)


def _log_schema_validation_failure[T: BaseModel](
    *,
    prompt: str,
    result_model: type[T],
    error: Exception,
) -> None:
    prompt_summary = f"model={result_model.__name__} prompt_len={len(prompt)}"
    # Validation and decode errors can embed raw model output, which may
    # include user text. Log only the exception type.
    log_interpretation_failure(
        task=result_model.__name__,
        prompt_summary=prompt_summary,
        error=type(error).__name__,
    )
