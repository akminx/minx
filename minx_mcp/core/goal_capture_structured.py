from __future__ import annotations

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.goal_capture_utils import _supported_conversational_goal
from minx_mcp.core.goals import (
    GoalService,
    normalize_filter_names,
    normalize_goal_type,
    normalize_title,
    validate_goal_state,
)
from minx_mcp.core.models import (
    FinanceReadInterface,
    GoalCaptureResult,
    GoalCreateInput,
    GoalUpdateInput,
)
from minx_mcp.validation import (
    reject_unknown_keys as _reject_unknown_keys,
)
from minx_mcp.validation import (
    require_bool as _require_bool,
)
from minx_mcp.validation import (
    require_exact_keys as _require_exact_keys,
)
from minx_mcp.validation import (
    require_int as _require_int,
)
from minx_mcp.validation import (
    require_optional_str as _require_optional_str,
)
from minx_mcp.validation import (
    require_payload_object,
)
from minx_mcp.validation import (
    require_str as _require_str,
)
from minx_mcp.validation import (
    require_str_list as _require_str_list,
)


def _validate_structured_goal_input(
    *,
    structured_input: dict[str, object],
    finance_api: FinanceReadInterface,
    goal_service: GoalService,
) -> GoalCaptureResult:
    action = structured_input.get("action")
    payload = structured_input.get("payload")
    if action not in {"goal_create", "goal_update"}:
        raise InvalidInputError("structured_input.action must be goal_create or goal_update")
    payload = require_payload_object(payload, field_name="structured_input.payload")

    if action == "goal_create":
        create_result = _validate_structured_create_payload(payload, finance_api)
        if create_result is not None:
            return create_result
        return GoalCaptureResult(
            result_type="create",
            action="goal_create",
            payload=payload,
        )

    goal_id = structured_input.get("goal_id")
    if not isinstance(goal_id, int):
        raise InvalidInputError("structured_input.goal_id must be provided for updates")
    if "goal_id" in payload:
        raise InvalidInputError("goal_id must not appear inside payload")
    goal = goal_service.get_goal(goal_id)
    if not _supported_conversational_goal(goal):
        return GoalCaptureResult(
            result_type="no_match",
        )
    _validate_structured_update_payload(payload, goal_service=goal_service, goal_id=goal_id)
    return GoalCaptureResult(
        result_type="update",
        action="goal_update",
        goal_id=goal_id,
        payload=payload,
    )


def _validate_structured_create_payload(
    payload: dict[str, object],
    finance_api: FinanceReadInterface,
) -> GoalCaptureResult | None:
    _parse_goal_create_input(payload)
    goal_type = payload.get("goal_type")
    metric_type = payload.get("metric_type")
    domain = payload.get("domain")
    if not (goal_type == "spending_cap" and metric_type == "sum_below" and domain == "finance"):
        return GoalCaptureResult(
            result_type="no_match",
        )

    validate_goal_state(
        goal_type=normalize_goal_type(_require_str(payload, "goal_type")),
        title=normalize_title(_require_str(payload, "title")),
        target_value=_require_int(payload, "target_value"),
        metric_type=_require_str(payload, "metric_type"),
        period=_require_str(payload, "period"),
        domain=_require_str(payload, "domain"),
        starts_on=_require_str(payload, "starts_on"),
        ends_on=_require_optional_str(payload, "ends_on"),
        category_names=normalize_filter_names(
            _require_str_list(payload, "category_names"), "category_names"
        ),
        merchant_names=normalize_filter_names(
            _require_str_list(payload, "merchant_names"), "merchant_names"
        ),
        account_names=normalize_filter_names(
            _require_str_list(payload, "account_names"), "account_names"
        ),
    )

    _validate_canonical_names(
        finance_api.list_goal_category_names(),
        payload.get("category_names"),
        kind="category_names",
    )
    _validate_canonical_names(
        finance_api.list_spending_merchant_names(),
        payload.get("merchant_names"),
        kind="merchant_names",
        resolve=True,
        finance_api=finance_api,
    )
    _validate_canonical_names(
        finance_api.list_account_names(),
        payload.get("account_names"),
        kind="account_names",
    )
    return None


def _validate_structured_update_payload(
    payload: dict[str, object],
    *,
    goal_service: GoalService,
    goal_id: int,
) -> None:
    _parse_goal_update_input(payload)
    goal = goal_service.get_goal(goal_id)
    next_title = (
        goal.title if "title" not in payload else normalize_title(_require_str(payload, "title"))
    )
    next_target_value = (
        goal.target_value
        if "target_value" not in payload
        else _require_int(payload, "target_value")
    )
    next_status = goal.status if "status" not in payload else _require_str(payload, "status")
    clear_ends_on = payload.get("clear_ends_on", False)
    if not isinstance(clear_ends_on, bool):
        raise InvalidInputError("clear_ends_on must be a boolean")
    next_ends_on = (
        None
        if clear_ends_on
        else _require_optional_str(payload, "ends_on")
        if "ends_on" in payload
        else goal.ends_on
    )
    validate_goal_state(
        goal_type=goal.goal_type,
        title=next_title,
        target_value=next_target_value,
        metric_type=goal.metric_type,
        period=goal.period,
        domain=goal.domain,
        starts_on=goal.starts_on,
        ends_on=next_ends_on,
        category_names=goal.category_names,
        merchant_names=goal.merchant_names,
        account_names=goal.account_names,
        status=next_status,
    )


def _parse_goal_create_input(payload: dict[str, object]) -> GoalCreateInput:
    required_keys = {
        "goal_type",
        "title",
        "metric_type",
        "target_value",
        "period",
        "domain",
        "category_names",
        "merchant_names",
        "account_names",
        "starts_on",
        "ends_on",
        "notes",
    }
    _require_exact_keys(payload, required_keys, context="goal_create")
    return GoalCreateInput(
        goal_type=_require_str(payload, "goal_type"),
        title=_require_str(payload, "title"),
        metric_type=_require_str(payload, "metric_type"),
        target_value=_require_int(payload, "target_value"),
        period=_require_str(payload, "period"),
        domain=_require_str(payload, "domain"),
        category_names=_require_str_list(payload, "category_names"),
        merchant_names=_require_str_list(payload, "merchant_names"),
        account_names=_require_str_list(payload, "account_names"),
        starts_on=_require_str(payload, "starts_on"),
        ends_on=_require_optional_str(payload, "ends_on"),
        notes=_require_optional_str(payload, "notes"),
    )


def _parse_goal_update_input(payload: dict[str, object]) -> GoalUpdateInput:
    allowed_keys = {
        "title",
        "target_value",
        "status",
        "ends_on",
        "notes",
        "clear_ends_on",
        "clear_notes",
    }
    _reject_unknown_keys(payload, allowed_keys, context="goal_update")
    return GoalUpdateInput(
        title=_require_optional_str(payload, "title") if "title" in payload else None,
        target_value=_require_int(payload, "target_value") if "target_value" in payload else None,
        status=_require_optional_str(payload, "status") if "status" in payload else None,
        ends_on=_require_optional_str(payload, "ends_on") if "ends_on" in payload else None,
        notes=_require_optional_str(payload, "notes") if "notes" in payload else None,
        clear_ends_on=_require_bool(payload, "clear_ends_on", default=False),
        clear_notes=_require_bool(payload, "clear_notes", default=False),
    )


def _validate_canonical_names(
    valid_names: list[str],
    names: object,
    *,
    kind: str,
    resolve: bool = False,
    finance_api: FinanceReadInterface | None = None,
) -> None:
    if names is None:
        return
    if not isinstance(names, list) or not all(isinstance(item, str) for item in names):
        raise InvalidInputError(f"{kind} must be a list of strings")
    for name in names:
        if name not in valid_names:
            if resolve and finance_api is not None:
                from minx_mcp.core.goal_capture_nl import _resolve_exact_subject

                resolved = _resolve_exact_subject("merchant", name, finance_api)
                if resolved is not None:
                    continue
            raise InvalidInputError(f"{kind} must contain canonical names only")
