"""Slice 9 investigation lifecycle helpers."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection, Row
from typing import Any

from minx_mcp.contracts import ConflictError, InvalidInputError, NotFoundError
from minx_mcp.core.secret_scanner import SecretVerdictKind, redact_secrets
from minx_mcp.time_utils import utc_now_isoformat

KIND_VALUES = frozenset({"investigate", "plan", "retro", "onboard", "other"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "budget_exhausted"})
ALL_STATUSES = TERMINAL_STATUSES | {"running"}
STEP_EVENT_TEMPLATES = frozenset({"investigation.step_logged", "investigation.needs_confirmation"})
TERMINAL_RESPONSE_TEMPLATES = {
    "succeeded": "investigation.completed",
    "failed": "investigation.failed",
    "cancelled": "investigation.cancelled",
    "budget_exhausted": "investigation.budget_exhausted",
}
MAX_HISTORY_LIMIT = 1000
MAX_JSON_DEPTH = 4
MAX_TOP_LEVEL_KEYS = 32
MAX_STRING_BYTES = 1024
MAX_STEP_BYTES = 16 * 1024
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_RAW_OUTPUT_KEYS = frozenset(
    {
        "raw_output",
        "tool_output",
        "result_json",
        "result_rows",
        "transcript",
        "messages",
    }
)


def canonical_json_digest(value: Any) -> str:
    """Return a raw lowercase SHA-256 hex digest for canonical JSON."""
    normalized = _normalize_json_value(value, field_name="value", redact=False)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def start_investigation(
    conn: Connection,
    *,
    kind: str,
    question: str,
    context_json: dict[str, Any] | None,
    harness: str,
) -> dict[str, object]:
    normalized_kind = _normalize_kind(kind)
    normalized_harness = _require_non_empty(harness, "harness")
    normalized_question = _redact_text(_require_non_empty(question, "question"), "question")
    normalized_context = _normalize_json_object(context_json or {}, field_name="context_json", redact=True)
    now = utc_now_isoformat()
    response_slots: dict[str, object] = {
        "kind": normalized_kind,
        "harness": normalized_harness,
        "status": "running",
    }

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            """
            INSERT INTO investigations (
                harness,
                kind,
                question,
                context_json,
                status,
                trajectory_json,
                response_template,
                response_slots_json,
                citation_refs_json,
                started_at
            ) VALUES (?, ?, ?, ?, 'running', '[]', 'investigation.started', ?, '[]', ?)
            """,
            (
                normalized_harness,
                normalized_kind,
                normalized_question,
                _dump_json(normalized_context),
                _dump_json(response_slots),
                now,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("investigations insert did not return a row id")
        investigation_id = int(cur.lastrowid)
        response_slots["investigation_id"] = investigation_id
        conn.execute(
            "UPDATE investigations SET response_slots_json = ? WHERE id = ?",
            (_dump_json(response_slots), investigation_id),
        )
        result = {
            "investigation_id": investigation_id,
            "response_template": "investigation.started",
            "response_slots": response_slots,
        }
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    return result


def append_investigation_step(
    conn: Connection,
    *,
    investigation_id: int,
    step_json: dict[str, Any],
) -> dict[str, object]:
    normalized_id = _normalize_positive_int(investigation_id, "investigation_id")
    normalized_step = normalize_step_json(step_json)

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT id, kind, harness, status, trajectory_json FROM investigations WHERE id = ?",
            (normalized_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"investigation {normalized_id} not found")
        if str(row["status"]) != "running":
            raise ConflictError(
                f"investigation {normalized_id} is not running",
                data={"investigation_id": normalized_id, "status": str(row["status"])},
            )
        trajectory = _json_loads_list(row["trajectory_json"], "trajectory_json")
        trajectory.append(normalized_step)
        response_template = str(normalized_step["event_template"])
        response_slots: dict[str, object] = dict(normalized_step["event_slots"])
        response_slots.update(
            {
                "investigation_id": normalized_id,
                "kind": str(row["kind"]),
                "harness": str(row["harness"]),
                "status": "running",
                "step": int(normalized_step["step"]),
                "tool": str(normalized_step["tool"]),
            }
        )
        cur = conn.execute(
            """
            UPDATE investigations
            SET trajectory_json = ?,
                response_template = ?,
                response_slots_json = ?
            WHERE id = ? AND status = 'running'
            """,
            (_dump_json(trajectory), response_template, _dump_json(response_slots), normalized_id),
        )
        if int(cur.rowcount or 0) != 1:
            raise ConflictError(
                f"investigation {normalized_id} changed concurrently",
                data={"investigation_id": normalized_id},
            )
        result = {"ok": True, "response_template": response_template, "response_slots": response_slots}
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    return result


def complete_investigation(
    conn: Connection,
    *,
    investigation_id: int,
    status: str,
    answer_md: str | None,
    citation_refs: list[dict[str, Any]] | None,
    tool_call_count: int | None,
    token_input: int | None,
    token_output: int | None,
    cost_usd: float | None,
    error_message: str | None,
) -> dict[str, object]:
    normalized_id = _normalize_positive_int(investigation_id, "investigation_id")
    normalized_status = _normalize_terminal_status(status)
    normalized_answer = _normalize_optional_redacted_text(answer_md, "answer_md")
    normalized_citations = normalize_citation_refs(citation_refs or [])
    normalized_tool_calls = _normalize_optional_non_negative_int(tool_call_count, "tool_call_count")
    normalized_token_input = _normalize_optional_non_negative_int(token_input, "token_input")
    normalized_token_output = _normalize_optional_non_negative_int(token_output, "token_output")
    normalized_cost = _normalize_optional_non_negative_float(cost_usd, "cost_usd")
    normalized_error = _normalize_optional_redacted_text(error_message, "error_message")
    completed_at = utc_now_isoformat()

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT id, kind, harness, status FROM investigations WHERE id = ?",
            (normalized_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"investigation {normalized_id} not found")
        if str(row["status"]) != "running":
            raise ConflictError(
                f"investigation {normalized_id} is not running",
                data={"investigation_id": normalized_id, "status": str(row["status"])},
            )
        response_template = TERMINAL_RESPONSE_TEMPLATES[normalized_status]
        response_slots = _terminal_response_slots(
            row,
            status=normalized_status,
            citation_refs=normalized_citations,
            tool_call_count=normalized_tool_calls,
            cost_usd=normalized_cost,
        )
        cur = conn.execute(
            """
            UPDATE investigations
            SET status = ?,
                answer_md = ?,
                citation_refs_json = ?,
                tool_call_count = ?,
                token_input = ?,
                token_output = ?,
                cost_usd = ?,
                error_message = ?,
                completed_at = ?,
                response_template = ?,
                response_slots_json = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                normalized_status,
                normalized_answer,
                _dump_json(normalized_citations),
                normalized_tool_calls,
                normalized_token_input,
                normalized_token_output,
                normalized_cost,
                normalized_error,
                completed_at,
                response_template,
                _dump_json(response_slots),
                normalized_id,
            ),
        )
        if int(cur.rowcount or 0) != 1:
            raise ConflictError(
                f"investigation {normalized_id} changed concurrently",
                data={"investigation_id": normalized_id},
            )
        result = {
            "investigation_id": normalized_id,
            "response_template": response_template,
            "response_slots": response_slots,
        }
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    return result


def log_investigation(
    conn: Connection,
    *,
    kind: str,
    question: str,
    context_json: dict[str, Any] | None,
    harness: str,
    trajectory_json: list[dict[str, Any]] | None,
    status: str,
    answer_md: str | None,
    citation_refs: list[dict[str, Any]] | None,
    tool_call_count: int | None,
    token_input: int | None,
    token_output: int | None,
    cost_usd: float | None,
    error_message: str | None,
) -> dict[str, object]:
    normalized_kind = _normalize_kind(kind)
    normalized_harness = _require_non_empty(harness, "harness")
    normalized_question = _redact_text(_require_non_empty(question, "question"), "question")
    normalized_context = _normalize_json_object(context_json or {}, field_name="context_json", redact=True)
    normalized_trajectory = [normalize_step_json(step) for step in (trajectory_json or [])]
    normalized_status = _normalize_terminal_status(status)
    normalized_answer = _normalize_optional_redacted_text(answer_md, "answer_md")
    normalized_citations = normalize_citation_refs(citation_refs or [])
    normalized_tool_calls = _normalize_optional_non_negative_int(tool_call_count, "tool_call_count")
    normalized_token_input = _normalize_optional_non_negative_int(token_input, "token_input")
    normalized_token_output = _normalize_optional_non_negative_int(token_output, "token_output")
    normalized_cost = _normalize_optional_non_negative_float(cost_usd, "cost_usd")
    normalized_error = _normalize_optional_redacted_text(error_message, "error_message")
    now = utc_now_isoformat()
    response_template = TERMINAL_RESPONSE_TEMPLATES[normalized_status]

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            """
            INSERT INTO investigations (
                harness,
                kind,
                question,
                context_json,
                status,
                answer_md,
                trajectory_json,
                response_template,
                citation_refs_json,
                tool_call_count,
                token_input,
                token_output,
                cost_usd,
                started_at,
                completed_at,
                error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_harness,
                normalized_kind,
                normalized_question,
                _dump_json(normalized_context),
                normalized_status,
                normalized_answer,
                _dump_json(normalized_trajectory),
                response_template,
                _dump_json(normalized_citations),
                normalized_tool_calls,
                normalized_token_input,
                normalized_token_output,
                normalized_cost,
                now,
                now,
                normalized_error,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("investigations insert did not return a row id")
        investigation_id = int(cur.lastrowid)
        row = conn.execute(
            "SELECT id, kind, harness, status FROM investigations WHERE id = ?",
            (investigation_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("inserted investigation row was not readable")
        response_slots = _terminal_response_slots(
            row,
            status=normalized_status,
            citation_refs=normalized_citations,
            tool_call_count=normalized_tool_calls,
            cost_usd=normalized_cost,
        )
        conn.execute(
            "UPDATE investigations SET response_slots_json = ? WHERE id = ?",
            (_dump_json(response_slots), investigation_id),
        )
        result = {
            "investigation_id": investigation_id,
            "response_template": response_template,
            "response_slots": response_slots,
        }
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    return result


def investigation_history(
    conn: Connection,
    *,
    kind: str | None,
    harness: str | None,
    status: str | None,
    since: str | None,
    days: int,
    limit: int,
) -> dict[str, object]:
    normalized_kind = _normalize_optional_kind(kind)
    normalized_harness = _normalize_optional_text(harness)
    normalized_status = _normalize_optional_status(status)
    cutoff = _resolve_history_cutoff(since, days)
    normalized_limit = _normalize_limit(limit)
    rows = conn.execute(
        """
        SELECT *
        FROM investigations
        WHERE started_at >= ?
          AND (? IS NULL OR kind = ?)
          AND (? IS NULL OR harness = ?)
          AND (? IS NULL OR status = ?)
        ORDER BY started_at DESC, id DESC
        LIMIT ?
        """,
        (
            cutoff,
            normalized_kind,
            normalized_kind,
            normalized_harness,
            normalized_harness,
            normalized_status,
            normalized_status,
            normalized_limit + 1,
        ),
    ).fetchall()
    return {
        "runs": [_row_as_summary(row) for row in rows[:normalized_limit]],
        "truncated": len(rows) > normalized_limit,
    }


def investigation_get(conn: Connection, *, investigation_id: int) -> dict[str, object]:
    normalized_id = _normalize_positive_int(investigation_id, "investigation_id")
    row = conn.execute("SELECT * FROM investigations WHERE id = ?", (normalized_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"investigation {normalized_id} not found")
    return {"run": _row_as_detail(row)}


def recent_resource_payload(conn: Connection, *, limit: int = 20) -> dict[str, object]:
    return investigation_history(conn, kind=None, harness=None, status=None, since=None, days=30, limit=limit)


def investigation_resource_payload(conn: Connection, *, investigation_id: int) -> dict[str, object]:
    return investigation_get(conn, investigation_id=investigation_id)


def normalize_step_json(step_json: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(step_json, dict):
        raise InvalidInputError("step_json must be a JSON object")
    _reject_raw_output_keys(step_json, "step_json")
    serialized_step = json.dumps(
        step_json,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if len(serialized_step) > MAX_STEP_BYTES:
        raise InvalidInputError("step_json is too large")
    required = {"step", "event_template", "event_slots", "tool", "args_digest", "result_digest", "latency_ms"}
    missing = required - set(step_json)
    if missing:
        raise InvalidInputError(f"step_json missing required fields: {', '.join(sorted(missing))}")
    event_template = _require_non_empty(str(step_json["event_template"]), "event_template")
    if event_template not in STEP_EVENT_TEMPLATES:
        allowed = ", ".join(sorted(STEP_EVENT_TEMPLATES))
        raise InvalidInputError(f"event_template must be one of: {allowed}")
    tool = _normalize_tool_name(step_json["tool"])
    return {
        **{
            key: _normalize_json_value(value, field_name=f"step_json.{key}", redact=True)
            for key, value in step_json.items()
            if key not in required
        },
        "step": _normalize_positive_int(step_json["step"], "step"),
        "event_template": event_template,
        "event_slots": _normalize_json_object(step_json["event_slots"], field_name="event_slots", redact=True),
        "tool": tool,
        "args_digest": _normalize_digest(step_json["args_digest"], "args_digest"),
        "result_digest": _normalize_digest(step_json["result_digest"], "result_digest"),
        "latency_ms": _normalize_non_negative_int(step_json["latency_ms"], "latency_ms"),
    }


def normalize_citation_refs(citation_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(citation_refs, list):
        raise InvalidInputError("citation_refs must be a list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(citation_refs):
        if not isinstance(item, dict):
            raise InvalidInputError("citation_refs entries must be objects")
        ref_type = _require_non_empty(str(item.get("type", "")), f"citation_refs[{index}].type")
        if ref_type == "memory" or ref_type == "investigation":
            _require_keys(item, {"type", "id"}, f"citation_refs[{index}]")
            normalized.append(
                {
                    "type": ref_type,
                    "id": _normalize_positive_int(item["id"], f"citation_refs[{index}].id"),
                }
            )
        elif ref_type == "vault_path":
            _require_keys(item, {"type", "path"}, f"citation_refs[{index}]")
            normalized.append(
                {
                    "type": ref_type,
                    "path": _redact_text(_require_non_empty(str(item["path"]), "path"), "path"),
                }
            )
        elif ref_type == "tool_result_digest":
            _require_keys(item, {"type", "tool", "digest"}, f"citation_refs[{index}]")
            normalized.append(
                {
                    "type": ref_type,
                    "tool": _normalize_tool_name(item["tool"]),
                    "digest": _normalize_digest(item["digest"], "digest"),
                }
            )
        else:
            raise InvalidInputError(
                "citation_refs type must be one of: investigation, memory, tool_result_digest, vault_path"
            )
    return normalized


def _row_as_summary(row: Row) -> dict[str, object]:
    return {
        "investigation_id": int(row["id"]),
        "kind": str(row["kind"]),
        "harness": str(row["harness"]),
        "status": str(row["status"]),
        "started_at": str(row["started_at"]),
        "completed_at": row["completed_at"],
        "response_template": row["response_template"],
        "response_slots": _json_loads_dict(row["response_slots_json"], "response_slots_json"),
    }


def _row_as_detail(row: Row) -> dict[str, object]:
    data = _row_as_summary(row)
    data.update(
        {
            "question": str(row["question"]),
            "context_json": _json_loads_dict(row["context_json"], "context_json"),
            "answer_md": row["answer_md"],
            "trajectory": _json_loads_list(row["trajectory_json"], "trajectory_json"),
            "citation_refs": _json_loads_list(row["citation_refs_json"], "citation_refs_json"),
            "tool_call_count": row["tool_call_count"],
            "token_input": row["token_input"],
            "token_output": row["token_output"],
            "cost_usd": row["cost_usd"],
            "error_message": row["error_message"],
        }
    )
    return data


def _terminal_response_slots(
    row: Row,
    *,
    status: str,
    citation_refs: list[dict[str, Any]],
    tool_call_count: int | None,
    cost_usd: float | None,
) -> dict[str, object]:
    return {
        "investigation_id": int(row["id"]),
        "kind": str(row["kind"]),
        "harness": str(row["harness"]),
        "status": status,
        "tool_call_count": tool_call_count,
        "cost_usd": cost_usd,
        "citation_count": len(citation_refs),
        "cited_memory_count": sum(1 for ref in citation_refs if ref.get("type") == "memory"),
    }


def _normalize_kind(kind: str) -> str:
    normalized = _require_non_empty(kind, "kind").lower()
    if normalized not in KIND_VALUES:
        allowed = ", ".join(sorted(KIND_VALUES))
        raise InvalidInputError(f"kind must be one of: {allowed}")
    return normalized


def _normalize_optional_kind(kind: str | None) -> str | None:
    normalized = _normalize_optional_text(kind)
    return None if normalized is None else _normalize_kind(normalized)


def _normalize_terminal_status(status: str) -> str:
    normalized = _require_non_empty(status, "status").lower()
    if normalized not in TERMINAL_STATUSES:
        allowed = ", ".join(sorted(TERMINAL_STATUSES))
        raise InvalidInputError(f"status must be one of: {allowed}")
    return normalized


def _normalize_optional_status(status: str | None) -> str | None:
    normalized = _normalize_optional_text(status)
    if normalized is None:
        return None
    lowered = normalized.lower()
    if lowered not in ALL_STATUSES:
        allowed = ", ".join(sorted(ALL_STATUSES))
        raise InvalidInputError(f"status must be one of: {allowed}")
    return lowered


def _normalize_json_object(value: Any, *, field_name: str, redact: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidInputError(f"{field_name} must be a JSON object")
    if len(value) > MAX_TOP_LEVEL_KEYS:
        raise InvalidInputError(f"{field_name} has too many keys")
    normalized = _normalize_json_value(value, field_name=field_name, redact=redact)
    if not isinstance(normalized, dict):
        raise InvalidInputError(f"{field_name} must be a JSON object")
    return normalized


def _normalize_json_value(value: Any, *, field_name: str, redact: bool, depth: int = 0) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise InvalidInputError(f"{field_name} exceeds max depth")
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidInputError(f"{field_name} must not contain non-finite floats")
        return value
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_STRING_BYTES:
            raise InvalidInputError(f"{field_name} string is too large")
        return _redact_text(value, field_name) if redact else value
    if isinstance(value, list):
        return [
            _normalize_json_value(item, field_name=f"{field_name}[]", redact=redact, depth=depth + 1)
            for item in value
        ]
    if isinstance(value, dict):
        if len(value) > MAX_TOP_LEVEL_KEYS:
            raise InvalidInputError(f"{field_name} has too many keys")
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise InvalidInputError(f"{field_name} keys must be non-empty strings")
            if key in _RAW_OUTPUT_KEYS:
                raise InvalidInputError(f"{field_name} must not include raw output key {key!r}")
            normalized[key] = _normalize_json_value(
                item,
                field_name=f"{field_name}.{key}",
                redact=redact,
                depth=depth + 1,
            )
        return normalized
    raise InvalidInputError(f"{field_name} must contain only JSON-compatible values")


def _reject_raw_output_keys(value: Any, field_name: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _RAW_OUTPUT_KEYS:
                raise InvalidInputError(f"{field_name} must not include raw output key {key!r}")
            _reject_raw_output_keys(item, f"{field_name}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_raw_output_keys(item, f"{field_name}[{index}]")


def _normalize_digest(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise InvalidInputError(f"{field_name} must be a SHA-256 hex string")
    normalized = value.strip()
    if _DIGEST_RE.fullmatch(normalized) is None:
        raise InvalidInputError(f"{field_name} must be raw lowercase SHA-256 hex")
    return normalized


def _normalize_tool_name(value: Any) -> str:
    if not isinstance(value, str):
        raise InvalidInputError("tool must be a string")
    normalized = _require_non_empty(value, "tool")
    if _TOOL_NAME_RE.fullmatch(normalized) is None:
        raise InvalidInputError("tool must be a normalized tool name")
    return normalized


def _normalize_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError(f"{field_name} must be an integer")
    if value <= 0:
        raise InvalidInputError(f"{field_name} must be greater than 0")
    return int(value)


def _normalize_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError(f"{field_name} must be an integer")
    if value < 0:
        raise InvalidInputError(f"{field_name} must be non-negative")
    return int(value)


def _normalize_optional_non_negative_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _normalize_non_negative_int(value, field_name)


def _normalize_optional_non_negative_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise InvalidInputError(f"{field_name} must be a finite number")
    if float(value) < 0:
        raise InvalidInputError(f"{field_name} must be non-negative")
    return float(value)


def _normalize_optional_redacted_text(value: str | None, field_name: str) -> str | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    return _redact_text(normalized, field_name)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidInputError("value must be a string")
    normalized = value.strip()
    return normalized or None


def _require_non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise InvalidInputError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise InvalidInputError(f"{field_name} must not be empty")
    return normalized


def _redact_text(value: str, field_name: str) -> str:
    verdict = redact_secrets(value)
    if verdict.verdict is SecretVerdictKind.BLOCK:
        raise InvalidInputError(f"{field_name} contains a blocked secret")
    return verdict.text


def _require_keys(item: dict[str, Any], allowed_keys: set[str], field_name: str) -> None:
    keys = set(item)
    if keys != allowed_keys:
        raise InvalidInputError(f"{field_name} must contain exactly: {', '.join(sorted(allowed_keys))}")


def _resolve_history_cutoff(since: str | None, days: int) -> str:
    normalized_since = _normalize_optional_text(since)
    if normalized_since is not None:
        try:
            parsed = datetime.fromisoformat(normalized_since.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvalidInputError("since must be a valid ISO8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise InvalidInputError("since must include timezone information")
        return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if days <= 0:
        raise InvalidInputError("days must be greater than 0")
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return cutoff.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise InvalidInputError("limit must be an integer")
    if limit <= 0:
        raise InvalidInputError("limit must be greater than 0")
    return min(limit, MAX_HISTORY_LIMIT)


def _json_loads_dict(value: object, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"{field_name} must be valid JSON") from exc
    return parsed if isinstance(parsed, dict) else {}


def _json_loads_list(value: object, field_name: str) -> list[Any]:
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"{field_name} must be valid JSON") from exc
    return parsed if isinstance(parsed, list) else []


def _dump_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
