from __future__ import annotations

import json
from pathlib import Path

import pytest

from minx_mcp.contracts import CONFLICT, INVALID_INPUT
from minx_mcp.core.investigations import canonical_json_digest
from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from tests.helpers import MinxTestConfig, call_tool_sync, get_tool

ARG_DIGEST = "a" * 64
RESULT_DIGEST = "b" * 64
SECRET_VALUE = "AKIA" + "A" * 16


def _server(tmp_path: Path):
    return create_core_server(MinxTestConfig(tmp_path / "minx.db", tmp_path / "vault"))


def _step(
    *,
    step: int = 1,
    event_template: str = "investigation.step_logged",
    event_slots: dict[str, object] | None = None,
    tool: str = "finance_query",
    args_digest: str = ARG_DIGEST,
    result_digest: str = RESULT_DIGEST,
    latency_ms: int = 12,
) -> dict[str, object]:
    return {
        "step": step,
        "event_template": event_template,
        "event_slots": event_slots or {"row_count": 3},
        "tool": tool,
        "args_digest": args_digest,
        "result_digest": result_digest,
        "latency_ms": latency_ms,
    }


def test_canonical_json_digest_is_order_independent_raw_sha256() -> None:
    left = canonical_json_digest({"b": 2, "a": [1, {"c": "x"}]})
    right = canonical_json_digest({"a": [1, {"c": "x"}], "b": 2})

    assert left == right
    assert len(left) == 64
    assert left == left.lower()
    assert not left.startswith("sha256:")


def test_investigation_lifecycle_stores_render_citations_and_filters_history(tmp_path: Path) -> None:
    server = _server(tmp_path)
    start = get_tool(server, "start_investigation").fn
    append = get_tool(server, "append_investigation_step").fn
    complete = get_tool(server, "complete_investigation").fn
    history = get_tool(server, "investigation_history").fn
    get = get_tool(server, "investigation_get").fn

    created = call_tool_sync(start, "investigate", "Why did dining increase?", {"period": "2026-04"}, "hermes")

    assert created["success"] is True
    investigation_id = created["data"]["investigation_id"]
    assert created["data"]["response_template"] == "investigation.started"
    assert created["data"]["response_slots"]["status"] == "running"

    appended = call_tool_sync(append, investigation_id, _step())

    assert appended["success"] is True
    assert appended["data"]["response_template"] == "investigation.step_logged"
    assert appended["data"]["response_slots"]["tool"] == "finance_query"

    citations = [
        {"type": "memory", "id": 123},
        {"type": "tool_result_digest", "tool": "finance_query", "digest": RESULT_DIGEST},
    ]
    completed = call_tool_sync(
        complete,
        investigation_id,
        "succeeded",
        "Dining increased because restaurant spend rose.",
        citations,
        1,
        100,
        50,
        0.01,
        None,
    )

    assert completed["success"] is True
    assert completed["data"]["response_template"] == "investigation.completed"
    assert completed["data"]["response_slots"]["status"] == "succeeded"
    assert completed["data"]["response_slots"]["citation_count"] == 2
    assert completed["data"]["response_slots"]["cited_memory_count"] == 1

    run = call_tool_sync(get, investigation_id)["data"]["run"]
    assert run["answer_md"] == "Dining increased because restaurant spend rose."
    assert run["citation_refs"] == citations
    assert run["trajectory"][0]["result_digest"] == RESULT_DIGEST
    assert run["response_template"] == "investigation.completed"

    filtered = call_tool_sync(history, "investigate", "hermes", "succeeded", None, 30, 10)["data"]
    assert filtered["truncated"] is False
    assert [item["investigation_id"] for item in filtered["runs"]] == [investigation_id]

    empty = call_tool_sync(history, "investigate", "other-harness", "succeeded", None, 30, 10)["data"]
    assert empty["runs"] == []


def test_append_rejects_bad_digest_raw_output_and_terminal_runs(tmp_path: Path) -> None:
    server = _server(tmp_path)
    start = get_tool(server, "start_investigation").fn
    append = get_tool(server, "append_investigation_step").fn
    complete = get_tool(server, "complete_investigation").fn
    investigation_id = call_tool_sync(start, "investigate", "Probe", {}, "hermes")["data"]["investigation_id"]

    bad_digest = call_tool_sync(append, investigation_id, _step(args_digest="sha256:" + ARG_DIGEST))
    assert bad_digest["success"] is False
    assert bad_digest["error_code"] == INVALID_INPUT

    raw_output = call_tool_sync(append, investigation_id, _step(event_slots={"raw_output": "full rows"}))
    assert raw_output["success"] is False
    assert raw_output["error_code"] == INVALID_INPUT

    assert call_tool_sync(append, investigation_id, _step())["success"] is True
    assert call_tool_sync(complete, investigation_id, "failed", None, [], 1, 0, 0, None, "stopped")["success"] is True

    after_terminal = call_tool_sync(append, investigation_id, _step(step=2))
    assert after_terminal["success"] is False
    assert after_terminal["error_code"] == CONFLICT


def test_append_event_slots_cannot_override_lifecycle_response_slots(tmp_path: Path) -> None:
    server = _server(tmp_path)
    start = get_tool(server, "start_investigation").fn
    append = get_tool(server, "append_investigation_step").fn
    investigation_id = call_tool_sync(start, "investigate", "Probe", {}, "hermes")["data"]["investigation_id"]

    result = call_tool_sync(
        append,
        investigation_id,
        _step(
            event_slots={
                "investigation_id": 999,
                "status": "succeeded",
                "kind": "retro",
                "harness": "other-harness",
                "tool": "spoofed_tool",
                "action": "kept",
            }
        ),
    )

    slots = result["data"]["response_slots"]
    assert slots["investigation_id"] == investigation_id
    assert slots["status"] == "running"
    assert slots["kind"] == "investigate"
    assert slots["harness"] == "hermes"
    assert slots["tool"] == "finance_query"
    assert slots["action"] == "kept"


def test_confirmation_step_returns_confirmation_without_terminal_status(tmp_path: Path) -> None:
    server = _server(tmp_path)
    start = get_tool(server, "start_investigation").fn
    append = get_tool(server, "append_investigation_step").fn
    get = get_tool(server, "investigation_get").fn
    investigation_id = call_tool_sync(start, "investigate", "Should I promote this?", {}, "hermes")["data"][
        "investigation_id"
    ]

    result = call_tool_sync(
        append,
        investigation_id,
        _step(
            event_template="investigation.needs_confirmation",
            event_slots={"action": "memory_confirm", "risk": "promote_memory", "target_id": 123},
        ),
    )

    assert result["success"] is True
    assert result["data"]["response_template"] == "investigation.needs_confirmation"
    assert result["data"]["response_slots"]["action"] == "memory_confirm"
    assert call_tool_sync(get, investigation_id)["data"]["run"]["status"] == "running"


def test_redacts_persisted_text_and_blocks_non_redactable_secrets(tmp_path: Path) -> None:
    server = _server(tmp_path)
    start = get_tool(server, "start_investigation").fn
    complete = get_tool(server, "complete_investigation").fn

    created = call_tool_sync(start, "investigate", f"Review {SECRET_VALUE}", {"note": SECRET_VALUE}, "hermes")
    investigation_id = created["data"]["investigation_id"]
    completed = call_tool_sync(
        complete,
        investigation_id,
        "succeeded",
        f"Do not expose {SECRET_VALUE}",
        [{"type": "vault_path", "path": f"Notes/{SECRET_VALUE}.md"}],
        0,
        0,
        0,
        None,
        None,
    )

    assert completed["success"] is True
    conn = get_connection(tmp_path / "minx.db")
    row = conn.execute("SELECT question, context_json, answer_md, citation_refs_json FROM investigations").fetchone()
    assert SECRET_VALUE not in row["question"]
    assert SECRET_VALUE not in row["context_json"]
    assert SECRET_VALUE not in row["answer_md"]
    assert SECRET_VALUE not in row["citation_refs_json"]
    assert "[REDACTED:aws_access_key_id]" in row["question"]

    private_key = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    blocked = call_tool_sync(start, "investigate", private_key, {}, "hermes")
    assert blocked["success"] is False
    assert blocked["error_code"] == INVALID_INPUT


def test_log_investigation_convenience_wrapper_persists_terminal_row(tmp_path: Path) -> None:
    server = _server(tmp_path)
    log = get_tool(server, "log_investigation").fn

    result = call_tool_sync(
        log,
        "retro",
        "What changed this week?",
        {"period": "week"},
        "hermes",
        [_step()],
        "budget_exhausted",
        "Partial answer.",
        [{"type": "investigation", "id": 7}],
        1,
        10,
        20,
        None,
        None,
    )

    assert result["success"] is True
    assert result["data"]["response_template"] == "investigation.budget_exhausted"
    run = call_tool_sync(get_tool(server, "investigation_get").fn, result["data"]["investigation_id"])["data"]["run"]
    assert run["status"] == "budget_exhausted"
    assert run["trajectory"][0]["step"] == 1
    assert run["citation_refs"] == [{"type": "investigation", "id": 7}]


@pytest.mark.asyncio
async def test_investigation_resources_expose_recent_and_by_id(tmp_path: Path) -> None:
    server = _server(tmp_path)
    start = get_tool(server, "start_investigation").fn
    investigation_id = call_tool_sync(start, "investigate", "Resource probe", {}, "hermes")["data"][
        "investigation_id"
    ]

    resource_uris = {str(resource.uri) for resource in await server.list_resources()}
    template_uris = {template.uriTemplate for template in await server.list_resource_templates()}
    assert "investigation://recent" in resource_uris
    assert "investigation://{investigation_id}" in template_uris

    recent_contents = await server.read_resource("investigation://recent")
    recent = json.loads(next(iter(recent_contents)).content)
    assert recent["runs"][0]["investigation_id"] == investigation_id

    run_contents = await server.read_resource(f"investigation://{investigation_id}")
    run = json.loads(next(iter(run_contents)).content)["run"]
    assert run["investigation_id"] == investigation_id
