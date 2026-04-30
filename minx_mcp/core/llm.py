from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

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


def _build_openai_compatible(config: dict[str, Any]) -> JSONLLMInterface:
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    provider_preferences = config.get("provider_preferences")
    reasoning = config.get("reasoning")
    return OpenAICompatibleLLM(
        base_url=str(config["base_url"]),
        model=str(config["model"]),
        api_key_env=str(config["api_key_env"]),
        timeout_seconds=float(config.get("timeout_seconds", 30.0)),
        provider_preferences=provider_preferences if isinstance(provider_preferences, dict) else None,
        reasoning=reasoning if isinstance(reasoning, dict) else None,
    )


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
