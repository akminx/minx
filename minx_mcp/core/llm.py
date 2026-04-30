from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minx_mcp.config import get_settings
from minx_mcp.contracts import LLMError
from minx_mcp.core.models import JSONLLMInterface
from minx_mcp.db import get_connection
from minx_mcp.preferences import get_preference

logger = logging.getLogger(__name__)


class LLMProviderError(LLMError):
    """Raised when the underlying provider call fails."""


MALFORMED_PROVIDER_RESPONSE_MESSAGE = "Provider returned malformed response envelope"


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


class OpenAICompatibleConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)
    timeout_seconds: float = Field(default=30.0, gt=0)
    provider_preferences: dict[str, Any] | None = None
    reasoning: dict[str, Any] | None = None


def _build_openai_compatible(config: dict[str, Any]) -> JSONLLMInterface:
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    parsed = _parse_openai_compatible_config(config)
    return OpenAICompatibleLLM(
        base_url=parsed.base_url,
        model=parsed.model,
        api_key_env=parsed.api_key_env,
        timeout_seconds=parsed.timeout_seconds,
        provider_preferences=parsed.provider_preferences,
        reasoning=parsed.reasoning,
    )


def _parse_openai_compatible_config(config: dict[str, Any]) -> OpenAICompatibleConfig:
    try:
        return OpenAICompatibleConfig.model_validate(_normalize_openai_compatible_config(config))
    except ValidationError as exc:
        raise LLMProviderError(
            f"Invalid LLM config for openai_compatible: {_format_validation_errors(exc)}"
        ) from exc


def _normalize_openai_compatible_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    for key in ("provider_preferences", "reasoning"):
        if key in normalized and not isinstance(normalized[key], dict):
            normalized.pop(key)
    return normalized


def _format_validation_errors(exc: ValidationError) -> str:
    messages = sorted(
        f"{'.'.join(str(part) for part in error['loc'])}: {error.get('msg', 'invalid value')}"
        for error in exc.errors()
        if error.get("loc")
    )
    return "; ".join(messages) if messages else "configuration did not match schema"


_PROVIDER_BUILDERS: dict[str, Callable[[dict[str, Any]], JSONLLMInterface | None]] = {
    "openai_compatible": _build_openai_compatible,
}


def create_llm(
    config: dict[str, Any] | None = None,
    *,
    db_path: str | Path | None = None,
) -> JSONLLMInterface | None:
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
