from __future__ import annotations

import json
import logging

import httpx
import pytest
from pydantic import BaseModel

from minx_mcp.db import get_connection
from minx_mcp.preferences import set_preference


def test_create_llm_returns_none_when_configuration_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MINX_DB_PATH", str(tmp_path / "minx.db"))
    get_connection(tmp_path / "minx.db").close()

    from minx_mcp.core.llm import create_llm

    assert create_llm() is None


def test_create_llm_reads_preference_config_when_explicit_config_is_absent(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "minx.db"
    monkeypatch.setenv("MINX_DB_PATH", str(db_path))
    conn = get_connection(db_path)
    set_preference(
        conn,
        "core",
        "llm_config",
        {"provider": "fake", "model": "reviewer-v1"},
    )

    import minx_mcp.core.llm as llm

    monkeypatch.setattr(
        llm,
        "_PROVIDER_BUILDERS",
        {"fake": lambda config: _RecordingLLM(config)},
    )

    instance = llm.create_llm()

    assert isinstance(instance, _RecordingLLM)
    assert instance.config == {"provider": "fake", "model": "reviewer-v1"}


def test_create_llm_returns_none_when_provider_setup_fails(caplog):
    import minx_mcp.core.llm as llm

    def explode(_config):
        raise RuntimeError("provider init failed")

    original = llm._PROVIDER_BUILDERS
    try:
        llm._PROVIDER_BUILDERS = {"broken": explode}
        created = llm.create_llm({"provider": "broken"})
    finally:
        llm._PROVIDER_BUILDERS = original

    assert created is None
    assert "provider init failed" in caplog.text


def test_create_llm_reports_invalid_openai_compatible_config(caplog):
    from minx_mcp.core.llm import create_llm

    caplog.set_level(logging.WARNING, logger="minx_mcp.core.llm")
    created = create_llm(
        {
            "provider": "openai_compatible",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
        }
    )

    assert created is None
    assert "Invalid LLM config for openai_compatible" in caplog.text
    assert "base_url" in caplog.text
    assert "Field required" in caplog.text


def test_create_llm_ignores_invalid_optional_openai_compatible_maps():
    from minx_mcp.core.llm import create_llm
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    created = create_llm(
        {
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
            "provider_preferences": ["not", "a", "dict"],
            "reasoning": "not a dict",
        }
    )

    assert isinstance(created, OpenAICompatibleLLM)
    assert created.provider_preferences is None
    assert created.reasoning is None


def test_create_llm_returns_none_for_unknown_provider(caplog):
    from minx_mcp.core.llm import create_llm

    created = create_llm({"provider": "missing"})

    assert created is None
    assert "Unknown LLM provider" in caplog.text


@pytest.mark.asyncio
async def test_json_backed_llm_wraps_provider_exceptions():
    from minx_mcp.core.llm import JSONBackedLLM, LLMProviderError

    async def explode(_prompt: str) -> str:
        raise RuntimeError("provider boom")

    llm = JSONBackedLLM(explode)

    with pytest.raises(LLMProviderError):
        await llm.run_json_prompt("return json")


@pytest.mark.asyncio
async def test_json_backed_llm_serializes_dict_provider_output():
    from minx_mcp.core.llm import JSONBackedLLM

    async def respond(prompt: str) -> dict[str, object]:
        return {"prompt": prompt, "ok": True}

    llm = JSONBackedLLM(respond)

    assert json.loads(await llm.run_json_prompt("return json")) == {
        "prompt": "return json",
        "ok": True,
    }


def test_create_llm_builds_openai_compatible_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from minx_mcp.core.llm import create_llm
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    created = create_llm(
        {
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
        }
    )

    assert isinstance(created, OpenAICompatibleLLM)
    assert created.base_url == "https://api.example.com/v1"
    assert created.model == "gpt-4o-mini"
    assert created.api_key_env == "OPENAI_API_KEY"
    assert created.provider_preferences is None


@pytest.mark.asyncio
async def test_openai_compatible_llm_posts_chat_completion_and_returns_content(
    monkeypatch,
):
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("minx_mcp.core.llm_openai.httpx.AsyncClient", _FakeClient)

    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        timeout_seconds=15.0,
    )
    result = await llm.run_json_prompt("Return JSON.")

    assert result == '{"ok": true}'
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"] == {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Return JSON."}],
        "response_format": {"type": "json_object"},
    }


class _StructuredResult(BaseModel):
    ok: bool
    count: int


@pytest.mark.asyncio
async def test_openai_compatible_llm_posts_structured_prompt(monkeypatch):
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true, "count": 2}'}}]},
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        transport=httpx.MockTransport(handler),
    )

    result = await llm.run_structured_prompt("Return structured JSON.", _StructuredResult)

    body = captured["body"]
    assert isinstance(body, dict)
    assert result == {"ok": True, "count": 2}
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "_StructuredResult",
            "schema": _StructuredResult.model_json_schema(),
            "strict": True,
        },
    }


@pytest.mark.asyncio
async def test_openai_compatible_llm_includes_provider_preferences_when_configured(
    monkeypatch,
):
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "{}"}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("minx_mcp.core.llm_openai.httpx.AsyncClient", _FakeClient)

    llm = OpenAICompatibleLLM(
        base_url="https://openrouter.ai/api/v1",
        model="google/gemini-2.5-flash",
        api_key_env="OPENAI_API_KEY",
        provider_preferences={
            "only": ["deepinfra"],
            "quantizations": ["bf16"],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
    )

    await llm.run_json_prompt("Return JSON.")

    assert captured["json"]["provider"] == {
        "only": ["deepinfra"],
        "quantizations": ["bf16"],
        "allow_fallbacks": False,
        "require_parameters": True,
    }


@pytest.mark.asyncio
async def test_openai_compatible_llm_raises_provider_error_on_missing_key(monkeypatch):
    from minx_mcp.core.llm import LLMProviderError
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    )

    with pytest.raises(LLMProviderError, match="Missing API key"):
        await llm.run_json_prompt("Return JSON.")


@pytest.mark.asyncio
async def test_openai_compatible_llm_includes_status_and_body_on_http_error(monkeypatch):
    from minx_mcp.core.llm import LLMProviderError
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text='{"error": "rate limit exceeded"}')

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        await llm.run_json_prompt("Return JSON.")

    message = str(exc_info.value)
    assert "HTTP 429" in message
    assert "rate limit exceeded" in message


@pytest.mark.asyncio
async def test_openai_compatible_llm_rejects_empty_choices(monkeypatch):
    from minx_mcp.core.llm import LLMProviderError
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": []}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            return _FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("minx_mcp.core.llm_openai.httpx.AsyncClient", _FakeClient)

    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    )

    with pytest.raises(LLMProviderError, match="malformed response"):
        await llm.run_json_prompt("Return JSON.")


@pytest.mark.asyncio
async def test_openai_compatible_llm_rejects_missing_message_content(monkeypatch):
    from minx_mcp.core.llm import LLMProviderError
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            return _FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("minx_mcp.core.llm_openai.httpx.AsyncClient", _FakeClient)

    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    )

    with pytest.raises(LLMProviderError, match="malformed response"):
        await llm.run_json_prompt("Return JSON.")


@pytest.mark.asyncio
async def test_openai_compatible_llm_rejects_non_string_message_content(monkeypatch):
    from minx_mcp.core.llm import LLMProviderError
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": ["not", "a", "string"]}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            return _FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("minx_mcp.core.llm_openai.httpx.AsyncClient", _FakeClient)

    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    )

    with pytest.raises(LLMProviderError, match="malformed response"):
        await llm.run_json_prompt("Return JSON.")


class _RecordingLLM:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config

    async def run_json_prompt(self, prompt: str) -> str:
        return "{}"
