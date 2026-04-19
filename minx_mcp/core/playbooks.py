"""Slice 8a playbook audit helpers and registry metadata."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection, Row
from time import monotonic

from minx_mcp.contracts import InvalidInputError, NotFoundError, PlaybookConflictError
from minx_mcp.time_utils import utc_now_isoformat

_TRIGGER_TYPES = frozenset({"cron", "event", "manual"})
_TERMINAL_STATUSES = frozenset({"skipped", "succeeded", "failed"})
_HISTORY_MAX_LIMIT = 1000
_KNOWN_TOOL_NAMESPACES = frozenset({"core", "finance", "meals", "training"})
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaybookDefinition:
    id: str
    name: str
    description: str
    recommended_schedule: str
    required_tools: tuple[str, ...]
    conditions_description: str
    requires_confirmation: bool

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["required_tools"] = list(self.required_tools)
        return data


PLAYBOOK_REGISTRY: tuple[PlaybookDefinition, ...] = (
    PlaybookDefinition(
        id="daily_review",
        name="Daily Review",
        description="Generate and persist the daily review note from snapshot data.",
        recommended_schedule="0 21 * * *",
        required_tools=(
            "core.get_daily_snapshot",
            "core.get_insight_history",
            "core.persist_note",
            "core.start_playbook_run",
            "core.complete_playbook_run",
        ),
        conditions_description="Run nightly to summarize the day and capture open loops.",
        requires_confirmation=False,
    ),
    PlaybookDefinition(
        id="weekly_report",
        name="Weekly Finance Report",
        description="Render the deterministic weekly finance report and record an audit row.",
        recommended_schedule="0 10 * * 1",
        required_tools=(
            "finance.finance_generate_weekly_report",
            "core.log_playbook_run",
        ),
        conditions_description="Run Mondays to generate the previous full-week finance summary.",
        requires_confirmation=False,
    ),
    PlaybookDefinition(
        id="wiki_update",
        name="Wiki Maintenance",
        description="Refresh wiki pages with current memory + snapshot context.",
        recommended_schedule="after daily_review",
        required_tools=(
            "core.memory_list",
            "core.get_daily_snapshot",
            "core.persist_note",
            "core.vault_replace_section",
            "core.start_playbook_run",
            "core.complete_playbook_run",
        ),
        conditions_description="Run after daily review when memory/wiki deltas are present.",
        requires_confirmation=False,
    ),
    PlaybookDefinition(
        id="memory_review",
        name="Memory Candidate Review",
        description="Surface pending memory candidates for explicit user confirmation.",
        recommended_schedule="0 9 * * *",
        required_tools=(
            "core.get_pending_memory_candidates",
            "core.memory_confirm",
            "core.memory_reject",
            "core.start_playbook_run",
            "core.complete_playbook_run",
        ),
        conditions_description="Run daily to keep candidate backlog small and explicit.",
        requires_confirmation=True,
    ),
    PlaybookDefinition(
        id="goal_nudge",
        name="Goal Check-In Nudge",
        description="Identify at-risk goals and post a bounded check-in nudge.",
        recommended_schedule="0 12 * * *",
        required_tools=(
            "core.goal_list",
            "core.get_goal_trajectory",
            "core.persist_note",
            "core.start_playbook_run",
            "core.complete_playbook_run",
        ),
        conditions_description="Run daily; only act when trajectory indicates attention needed.",
        requires_confirmation=True,
    ),
)


def playbook_registry_payload() -> dict[str, object]:
    return {"playbooks": [definition.as_dict() for definition in PLAYBOOK_REGISTRY]}


def start_playbook_run(
    conn: Connection,
    *,
    playbook_id: str,
    harness: str,
    trigger_type: str,
    trigger_ref: str | None,
) -> int:
    started = monotonic()
    normalized_playbook_id = _require_non_empty(playbook_id, "playbook_id")
    normalized_harness = _require_non_empty(harness, "harness")
    normalized_trigger_type = _normalize_trigger_type(trigger_type)
    normalized_trigger_ref = _normalize_optional_text(trigger_ref)
    triggered_at = utc_now_isoformat()

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            """
            INSERT INTO playbook_runs (
                playbook_id,
                harness,
                triggered_at,
                trigger_type,
                trigger_ref,
                status
            ) VALUES (?, ?, ?, ?, ?, 'running')
            """,
            (
                normalized_playbook_id,
                normalized_harness,
                triggered_at,
                normalized_trigger_type,
                normalized_trigger_ref,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("playbook_runs insert did not return a row id")
        run_id = int(cur.lastrowid)
        conn.commit()
        _log_playbook_event(
            message="playbook run started",
            playbook_id=normalized_playbook_id,
            run_id=run_id,
            trigger_type=normalized_trigger_type,
            status="running",
            started=started,
        )
        return run_id
    except sqlite3.IntegrityError as exc:
        if conn.in_transaction:
            conn.rollback()
        if _running_row_exists(
            conn,
            playbook_id=normalized_playbook_id,
            trigger_type=normalized_trigger_type,
            trigger_ref=normalized_trigger_ref,
        ):
            raise PlaybookConflictError(
                "A matching playbook run is already in-flight",
                data={
                    "playbook_id": normalized_playbook_id,
                    "trigger_type": normalized_trigger_type,
                    "trigger_ref": normalized_trigger_ref,
                },
            ) from exc
        raise
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def complete_playbook_run(
    conn: Connection,
    *,
    run_id: int,
    status: str,
    conditions_met: bool,
    action_taken: bool,
    result_json: str | None,
    error_message: str | None,
) -> int:
    started = monotonic()
    normalized_run_id = _normalize_run_id(run_id)
    normalized_status = _normalize_terminal_status(status)
    normalized_result_json = _normalize_result_json(result_json)
    normalized_error = _normalize_optional_text(error_message)
    completed_at = utc_now_isoformat()

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT id, status, playbook_id, trigger_type FROM playbook_runs WHERE id = ?",
            (normalized_run_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"playbook run {normalized_run_id} not found")
        if str(row["status"]) != "running":
            raise PlaybookConflictError(
                f"playbook run {normalized_run_id} is not running",
                data={"run_id": normalized_run_id, "status": str(row["status"])},
            )

        cur = conn.execute(
            """
            UPDATE playbook_runs
            SET status = ?,
                conditions_met = ?,
                action_taken = ?,
                result_json = ?,
                error_message = ?,
                completed_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                normalized_status,
                1 if conditions_met else 0,
                1 if action_taken else 0,
                normalized_result_json,
                normalized_error,
                completed_at,
                normalized_run_id,
            ),
        )
        if int(cur.rowcount or 0) != 1:
            raise PlaybookConflictError(
                f"playbook run {normalized_run_id} changed concurrently",
                data={"run_id": normalized_run_id},
            )
        conn.commit()
        _log_playbook_event(
            message="playbook run completed",
            playbook_id=str(row["playbook_id"]),
            run_id=normalized_run_id,
            trigger_type=str(row["trigger_type"]),
            status=normalized_status,
            started=started,
        )
        return normalized_run_id
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def log_playbook_run(
    conn: Connection,
    *,
    playbook_id: str,
    harness: str,
    trigger_type: str,
    trigger_ref: str | None,
    status: str,
    conditions_met: bool,
    action_taken: bool,
    result_json: str | None,
    error_message: str | None,
) -> int:
    started = monotonic()
    normalized_playbook_id = _require_non_empty(playbook_id, "playbook_id")
    normalized_harness = _require_non_empty(harness, "harness")
    normalized_trigger_type = _normalize_trigger_type(trigger_type)
    normalized_trigger_ref = _normalize_optional_text(trigger_ref)
    normalized_status = _normalize_terminal_status(status)
    normalized_result_json = _normalize_result_json(result_json)
    normalized_error = _normalize_optional_text(error_message)
    now = utc_now_isoformat()

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            """
            INSERT INTO playbook_runs (
                playbook_id,
                harness,
                triggered_at,
                trigger_type,
                trigger_ref,
                status
            ) VALUES (?, ?, ?, ?, ?, 'running')
            """,
            (
                normalized_playbook_id,
                normalized_harness,
                now,
                normalized_trigger_type,
                normalized_trigger_ref,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("playbook_runs insert did not return a row id")
        run_id = int(cur.lastrowid)
        conn.execute(
            """
            UPDATE playbook_runs
            SET status = ?,
                conditions_met = ?,
                action_taken = ?,
                result_json = ?,
                error_message = ?,
                completed_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                normalized_status,
                1 if conditions_met else 0,
                1 if action_taken else 0,
                normalized_result_json,
                normalized_error,
                now,
                run_id,
            ),
        )
        conn.commit()
        _log_playbook_event(
            message="playbook run logged",
            playbook_id=normalized_playbook_id,
            run_id=run_id,
            trigger_type=normalized_trigger_type,
            status=normalized_status,
            started=started,
        )
        return run_id
    except sqlite3.IntegrityError as exc:
        if conn.in_transaction:
            conn.rollback()
        if _running_row_exists(
            conn,
            playbook_id=normalized_playbook_id,
            trigger_type=normalized_trigger_type,
            trigger_ref=normalized_trigger_ref,
        ):
            raise PlaybookConflictError(
                "A matching playbook run is already in-flight",
                data={
                    "playbook_id": normalized_playbook_id,
                    "trigger_type": normalized_trigger_type,
                    "trigger_ref": normalized_trigger_ref,
                },
            ) from exc
        raise
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def playbook_history(
    conn: Connection,
    *,
    playbook_id: str | None,
    harness: str | None,
    status: str | None,
    since: str | None,
    days: int,
    limit: int,
) -> dict[str, object]:
    normalized_playbook = _normalize_optional_text(playbook_id)
    normalized_harness = _normalize_optional_text(harness)
    normalized_status = _normalize_history_status(status)
    cutoff = _resolve_history_cutoff(since, days)
    normalized_limit = _normalize_limit(limit)

    clauses = ["triggered_at >= ?"]
    params: list[object] = [cutoff]

    if normalized_playbook is not None:
        clauses.append("playbook_id = ?")
        params.append(normalized_playbook)
    if normalized_harness is not None:
        clauses.append("harness = ?")
        params.append(normalized_harness)
    if normalized_status is not None:
        clauses.append("status = ?")
        params.append(normalized_status)

    where_sql = " AND ".join(clauses)
    query = (
        "SELECT * FROM playbook_runs "
        f"WHERE {where_sql} "
        "ORDER BY triggered_at DESC, id DESC "
        "LIMIT ?"
    )
    params.append(normalized_limit + 1)
    rows = conn.execute(query, tuple(params)).fetchall()

    truncated = len(rows) > normalized_limit
    visible_rows = rows[:normalized_limit]
    return {
        "runs": [_history_row_as_dict(row) for row in visible_rows],
        "truncated": truncated,
    }


def playbook_reconcile_crashed(
    conn: Connection,
    *,
    stale_after_minutes: int,
) -> dict[str, object]:
    started = monotonic()
    if stale_after_minutes <= 0:
        raise InvalidInputError("stale_after_minutes must be greater than 0")
    modifier = f"-{stale_after_minutes} minutes"
    completed_at = utc_now_isoformat()

    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            """
            SELECT id, playbook_id, trigger_type
            FROM playbook_runs
            WHERE status = 'running'
              AND julianday(triggered_at) <= julianday('now', ?)
            ORDER BY id
            """,
            (modifier,),
        ).fetchall()
        run_ids = [int(row["id"]) for row in rows]
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            conn.execute(
                f"""
                UPDATE playbook_runs
                SET status = 'failed',
                    conditions_met = 0,
                    action_taken = 0,
                    error_message = 'harness crash suspected',
                    completed_at = ?
                WHERE id IN ({placeholders})
                """,
                (completed_at, *run_ids),
            )
        conn.commit()
        for row in rows:
            _log_playbook_event(
                message="playbook run reconciled",
                playbook_id=str(row["playbook_id"]),
                run_id=int(row["id"]),
                trigger_type=str(row["trigger_type"]),
                status="failed",
                started=started,
            )
        return {"reconciled": len(run_ids), "run_ids": run_ids}
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def _running_row_exists(
    conn: Connection,
    *,
    playbook_id: str,
    trigger_type: str,
    trigger_ref: str | None,
) -> bool:
    row = conn.execute(
        """
        SELECT id
        FROM playbook_runs
        WHERE playbook_id = ?
          AND trigger_type = ?
          AND COALESCE(trigger_ref, '') = COALESCE(?, '')
          AND status = 'running'
        LIMIT 1
        """,
        (playbook_id, trigger_type, trigger_ref),
    ).fetchone()
    return row is not None


def _history_row_as_dict(row: Row) -> dict[str, object]:
    data: dict[str, object] = {
        "id": int(row["id"]),
        "playbook_id": str(row["playbook_id"]),
        "harness": str(row["harness"]),
        "triggered_at": str(row["triggered_at"]),
        "trigger_type": str(row["trigger_type"]),
        "trigger_ref": row["trigger_ref"],
        "status": str(row["status"]),
        "conditions_met": _nullable_bool(row["conditions_met"]),
        "action_taken": _nullable_bool(row["action_taken"]),
        "error_message": row["error_message"],
        "completed_at": row["completed_at"],
    }
    result_json = row["result_json"]
    if result_json is not None:
        try:
            data["result_json"] = json.loads(str(result_json))
        except json.JSONDecodeError:
            data["result_json"] = str(result_json)
    else:
        data["result_json"] = None
    return data


def _nullable_bool(value: object | None) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    try:
        return bool(int(str(value).strip()))
    except ValueError:
        return bool(value)


def _normalize_run_id(run_id: int) -> int:
    if run_id <= 0:
        raise InvalidInputError("run_id must be greater than 0")
    return run_id


def _normalize_trigger_type(trigger_type: str) -> str:
    normalized = _require_non_empty(trigger_type, "trigger_type").lower()
    if normalized not in _TRIGGER_TYPES:
        allowed = ", ".join(sorted(_TRIGGER_TYPES))
        raise InvalidInputError(f"trigger_type must be one of: {allowed}")
    return normalized


def _normalize_terminal_status(status: str) -> str:
    normalized = _require_non_empty(status, "status").lower()
    if normalized not in _TERMINAL_STATUSES:
        allowed = ", ".join(sorted(_TERMINAL_STATUSES))
        raise InvalidInputError(f"status must be one of: {allowed}")
    return normalized


def _normalize_history_status(status: str | None) -> str | None:
    normalized = _normalize_optional_text(status)
    if normalized is None:
        return None
    lowered = normalized.lower()
    allowed = _TERMINAL_STATUSES | {"running"}
    if lowered not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise InvalidInputError(f"status must be one of: {allowed_text}")
    return lowered


def _normalize_result_json(result_json: str | None) -> str | None:
    normalized = _normalize_optional_text(result_json)
    if normalized is None:
        return None
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise InvalidInputError("result_json must be valid JSON when provided") from exc
    return json.dumps(parsed, sort_keys=True)


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
    if limit <= 0:
        raise InvalidInputError("limit must be greater than 0")
    return min(limit, _HISTORY_MAX_LIMIT)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if normalized else None


def _log_playbook_event(
    *,
    message: str,
    playbook_id: str,
    run_id: int,
    trigger_type: str,
    status: str,
    started: float,
) -> None:
    logger.info(
        message,
        extra={
            "playbook_id": playbook_id,
            "run_id": run_id,
            "trigger_type": trigger_type,
            "status": status,
            "duration_ms": int((monotonic() - started) * 1000),
        },
    )


def _require_non_empty(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise InvalidInputError(f"{field_name} must be non-empty")
    return normalized


def _validate_registry() -> None:
    ids: set[str] = set()
    for definition in PLAYBOOK_REGISTRY:
        if definition.id in ids:
            raise RuntimeError(f"duplicate playbook id in registry: {definition.id}")
        ids.add(definition.id)
        for tool_ref in definition.required_tools:
            if "." not in tool_ref:
                raise RuntimeError(
                    f"playbook {definition.id} required_tools entries must be namespaced: {tool_ref}"
                )
            namespace, tool_name = tool_ref.split(".", 1)
            if namespace not in _KNOWN_TOOL_NAMESPACES:
                raise RuntimeError(
                    f"playbook {definition.id} has unknown tool namespace {namespace!r}"
                )
            if not tool_name:
                raise RuntimeError(
                    f"playbook {definition.id} has invalid required tool reference {tool_ref!r}"
                )


_validate_registry()
