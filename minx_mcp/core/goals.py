from __future__ import annotations

from datetime import date
import json
from sqlite3 import Connection, Row

from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.models import GoalCreateInput, GoalRecord, GoalUpdateInput

_VALID_METRIC_TYPES = {"sum_below", "sum_above", "count_below", "count_above"}
_VALID_PERIODS = {"daily", "weekly", "monthly", "rolling_28d"}
_VALID_DOMAINS = {"finance"}
_VALID_STATUSES = {"active", "paused", "archived", "completed"}


class GoalService:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def create_goal(self, payload: GoalCreateInput) -> GoalRecord:
        normalized_goal_type = normalize_goal_type(payload.goal_type)
        normalized_title = normalize_title(payload.title)
        normalized_category_names = normalize_filter_names(payload.category_names, "category_names")
        normalized_merchant_names = normalize_filter_names(payload.merchant_names, "merchant_names")
        normalized_account_names = normalize_filter_names(payload.account_names, "account_names")
        validate_goal_state(
            goal_type=normalized_goal_type,
            title=normalized_title,
            target_value=payload.target_value,
            metric_type=payload.metric_type,
            period=payload.period,
            domain=payload.domain,
            starts_on=payload.starts_on,
            ends_on=payload.ends_on,
            category_names=normalized_category_names,
            merchant_names=normalized_merchant_names,
            account_names=normalized_account_names,
        )
        filters_json = json.dumps(
            {
                "category_names": normalized_category_names,
                "merchant_names": normalized_merchant_names,
                "account_names": normalized_account_names,
            }
        )
        cursor = self._conn.execute(
            """
            INSERT INTO goals (
                goal_type, title, status, metric_type, target_value, period, domain,
                filters_json, starts_on, ends_on, notes, created_at, updated_at
            ) VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                normalized_goal_type,
                normalized_title,
                payload.metric_type,
                payload.target_value,
                payload.period,
                payload.domain,
                filters_json,
                payload.starts_on,
                payload.ends_on,
                payload.notes,
            ),
        )
        self._conn.commit()
        return self.get_goal(cursor.lastrowid or 0)

    def get_goal(self, goal_id: int) -> GoalRecord:
        row = self._conn.execute(
            "SELECT * FROM goals WHERE id = ?", (goal_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Goal {goal_id} not found")
        return _row_to_record(row)

    def list_goals(self, status: str | None = None) -> list[GoalRecord]:
        normalized_status = _normalize_optional_status_filter(status)
        if normalized_status is not None:
            rows = self._conn.execute(
                "SELECT * FROM goals WHERE status = ? ORDER BY id", (normalized_status,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM goals WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def update_goal(self, goal_id: int, payload: GoalUpdateInput) -> GoalRecord:
        current = self.get_goal(goal_id)
        next_title = current.title if payload.title is None else normalize_title(payload.title)
        next_target_value = (
            current.target_value if payload.target_value is None else payload.target_value
        )
        next_status = current.status if payload.status is None else payload.status
        next_ends_on = (
            None
            if payload.clear_ends_on
            else payload.ends_on if payload.ends_on is not None
            else current.ends_on
        )
        validate_goal_state(
            goal_type=current.goal_type,
            title=next_title,
            target_value=next_target_value,
            metric_type=current.metric_type,
            period=current.period,
            domain=current.domain,
            starts_on=current.starts_on,
            ends_on=next_ends_on,
            category_names=current.category_names,
            merchant_names=current.merchant_names,
            account_names=current.account_names,
            status=next_status,
        )

        updates: list[str] = []
        params: list[object] = []
        if payload.title is not None:
            updates.append("title = ?")
            params.append(next_title)
        if payload.target_value is not None:
            updates.append("target_value = ?")
            params.append(payload.target_value)
        if payload.status is not None:
            updates.append("status = ?")
            params.append(payload.status)
        if payload.clear_ends_on:
            updates.append("ends_on = NULL")
        elif payload.ends_on is not None:
            updates.append("ends_on = ?")
            params.append(next_ends_on)
        if payload.clear_notes:
            updates.append("notes = NULL")
        elif payload.notes is not None:
            updates.append("notes = ?")
            params.append(payload.notes)
        if not updates:
            return current
        updates.append("updated_at = datetime('now')")
        params.append(goal_id)
        self._conn.execute(
            f"UPDATE goals SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self._conn.commit()
        return self.get_goal(goal_id)

    def archive_goal(self, goal_id: int) -> GoalRecord:
        return self.update_goal(goal_id, GoalUpdateInput(status="archived"))

    def list_active_goals(self, review_date: str) -> list[GoalRecord]:
        rows = self._conn.execute(
            """
            SELECT * FROM goals
            WHERE status = 'active'
              AND starts_on <= ?
              AND (ends_on IS NULL OR ends_on >= ?)
            ORDER BY id
            """,
            (review_date, review_date),
        ).fetchall()
        return [_row_to_record(row) for row in rows]


def validate_goal_state(
    *,
    goal_type: str,
    title: str,
    target_value: int,
    metric_type: str,
    period: str,
    domain: str,
    starts_on: str,
    ends_on: str | None,
    category_names: list[str],
    merchant_names: list[str],
    account_names: list[str],
    status: str | None = None,
) -> None:
    if not goal_type:
        raise InvalidInputError("goal_type must be non-empty")
    if not title:
        raise InvalidInputError("title must be non-empty")
    if target_value <= 0:
        raise InvalidInputError("target_value must be a positive integer")
    if metric_type not in _VALID_METRIC_TYPES:
        raise InvalidInputError(
            f"Invalid metric_type: {metric_type}; "
            f"must be one of {sorted(_VALID_METRIC_TYPES)}"
        )
    if period not in _VALID_PERIODS:
        raise InvalidInputError(
            f"Invalid period: {period}; "
            f"must be one of {sorted(_VALID_PERIODS)}"
        )
    if domain not in _VALID_DOMAINS:
        raise InvalidInputError(
            f"Invalid domain: {domain}; "
            f"must be one of {sorted(_VALID_DOMAINS)}"
        )
    if status is not None and status not in _VALID_STATUSES:
        raise InvalidInputError(
            f"Invalid status: {status}; "
            f"must be one of {sorted(_VALID_STATUSES)}"
        )

    starts_on_date = _validate_iso_date(starts_on, "starts_on")
    ends_on_date = _validate_optional_iso_date(ends_on, "ends_on")
    if ends_on_date is not None and ends_on_date < starts_on_date:
        raise InvalidInputError("ends_on must not be earlier than starts_on")

    if not category_names and not merchant_names and not account_names:
        raise InvalidInputError(
            "Finance goals must have at least one finance filter "
            "(category_names, merchant_names, or account_names)"
        )


def _normalize_optional_status_filter(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "":
        raise InvalidInputError("status must be non-empty when provided")
    if value not in _VALID_STATUSES:
        raise InvalidInputError(
            f"Invalid status: {value}; must be one of {sorted(_VALID_STATUSES)}"
        )
    return value


def normalize_title(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise InvalidInputError("title must be non-empty")
    return normalized


def normalize_goal_type(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise InvalidInputError("goal_type must be non-empty")
    return normalized


def normalize_filter_names(names: list[str], field_name: str) -> list[str]:
    normalized = [name.strip() for name in names]
    for member in normalized:
        if not member:
            raise InvalidInputError(f"{field_name} must not contain blank entries")
    return normalized


def _validate_iso_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInputError(f"{field_name} must be a valid ISO date") from exc


def _validate_optional_iso_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    return _validate_iso_date(value, field_name)


def _row_to_record(row: Row) -> GoalRecord:
    filters = json.loads(row["filters_json"])
    return GoalRecord(
        id=int(row["id"]),
        goal_type=str(row["goal_type"]),
        title=str(row["title"]),
        status=str(row["status"]),
        metric_type=str(row["metric_type"]),
        target_value=int(row["target_value"]),
        period=str(row["period"]),
        domain=str(row["domain"]),
        category_names=filters.get("category_names", []),
        merchant_names=filters.get("merchant_names", []),
        account_names=filters.get("account_names", []),
        starts_on=str(row["starts_on"]),
        ends_on=row["ends_on"],
        notes=row["notes"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
