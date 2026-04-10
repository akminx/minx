from __future__ import annotations

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.goal_capture import (
    _resolve_exact_subject,
    _supported_conversational_goal,
    capture_goal_message,
)
from minx_mcp.core.goals import (
    GoalService,
    _normalize_filter_names,
    _normalize_goal_type,
    _normalize_title,
    _validate_goal_state,
)
from minx_mcp.core.models import FinanceReadInterface, GoalCaptureResult, GoalCreateInput, GoalUpdateInput


async def parse_goal_input(
    *,
    finance_api: FinanceReadInterface,
    goal_service: GoalService,
    review_date: str,
    goals,
    message: str | None = None,
    structured_input: dict | None = None,
    llm: object | None = None,
) -> GoalCaptureResult:
    if (message is None) == (structured_input is None):
        raise InvalidInputError("exactly one of message or structured_input must be provided")
    if message is not None:
        return await capture_goal_message(
            message=message,
            review_date=review_date,
            finance_api=finance_api,
            goals=goals,
            llm=llm,
        )
    if not isinstance(structured_input, dict):
        raise InvalidInputError("structured_input must be an object")
    return _validate_structured_goal_input(
        structured_input=structured_input,
        finance_api=finance_api,
        goal_service=goal_service,
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
    if not isinstance(payload, dict):
        raise InvalidInputError("structured_input.payload must be an object")

    if action == "goal_create":
        create_result = _validate_structured_create_payload(payload, finance_api)
        if create_result is not None:
            return create_result
        return GoalCaptureResult(
            result_type="create",
            action="goal_create",
            payload=payload,
            assistant_message="I can create that goal.",
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
            assistant_message="That goal family is not supported for conversational updates.",
        )
    _validate_structured_update_payload(payload, goal_service=goal_service, goal_id=goal_id)
    return GoalCaptureResult(
        result_type="update",
        action="goal_update",
        goal_id=goal_id,
        payload=payload,
        assistant_message=f"I can update {goal.title}.",
    )


def _validate_structured_create_payload(
    payload: dict[str, object],
    finance_api: FinanceReadInterface,
) -> GoalCaptureResult | None:
    _validate_goal_create_input_shape(payload)
    goal_type = payload.get("goal_type")
    metric_type = payload.get("metric_type")
    domain = payload.get("domain")
    if not (
        goal_type == "spending_cap"
        and metric_type == "sum_below"
        and domain == "finance"
    ):
        return GoalCaptureResult(
            result_type="no_match",
            assistant_message="That goal family is not supported for conversational creation.",
        )

    _validate_goal_state(
        goal_type=_normalize_goal_type(_require_str(payload, "goal_type")),
        title=_normalize_title(_require_str(payload, "title")),
        target_value=_require_int(payload, "target_value"),
        metric_type=_require_str(payload, "metric_type"),
        period=_require_str(payload, "period"),
        domain=_require_str(payload, "domain"),
        starts_on=_require_str(payload, "starts_on"),
        ends_on=_require_optional_str(payload, "ends_on"),
        category_names=_normalize_filter_names(
            _require_str_list(payload, "category_names"), "category_names"
        ),
        merchant_names=_normalize_filter_names(
            _require_str_list(payload, "merchant_names"), "merchant_names"
        ),
        account_names=_normalize_filter_names(
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
    _validate_goal_update_input_shape(payload)
    goal = goal_service.get_goal(goal_id)
    next_title = goal.title if "title" not in payload else _normalize_title(_require_str(payload, "title"))
    next_target_value = (
        goal.target_value if "target_value" not in payload else _require_int(payload, "target_value")
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
    _validate_goal_state(
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


def _validate_goal_create_input_shape(payload: dict[str, object]) -> None:
    try:
        GoalCreateInput(**payload)
    except TypeError as exc:
        raise InvalidInputError(f"invalid structured goal_create payload: {exc}") from exc


def _validate_goal_update_input_shape(payload: dict[str, object]) -> None:
    try:
        GoalUpdateInput(**payload)
    except TypeError as exc:
        raise InvalidInputError(f"invalid structured goal_update payload: {exc}") from exc


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise InvalidInputError(f"{key} must be a string")
    return value


def _require_optional_str(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidInputError(f"{key} must be a string when provided")
    return value


def _require_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInputError(f"{key} must be an integer")
    return value


def _require_str_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidInputError(f"{key} must be a list of strings")
    return value


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
                resolved = _resolve_exact_subject("merchant", name, finance_api)
                if resolved == name:
                    continue
            raise InvalidInputError(f"{kind} must contain canonical names only")
