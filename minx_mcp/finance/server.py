from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol, Self

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import InvalidInputError, NotFoundError, wrap_tool_call
from minx_mcp.core.interpretation.finance_query import interpret_finance_query
from minx_mcp.core.llm import create_llm
from minx_mcp.money import cents_to_dollars
from minx_mcp.finance.importers import SUPPORTED_SOURCE_KINDS


SAFE_TOOLS = [
    "safe_finance_summary",
    "safe_finance_accounts",
    "finance_import",
    "finance_categorize",
    "finance_add_category_rule",
    "finance_anomalies",
    "finance_job_status",
    "finance_generate_weekly_report",
    "finance_generate_monthly_report",
]

SENSITIVE_TOOLS = ["sensitive_finance_query", "finance_query"]
MAX_SENSITIVE_QUERY_LIMIT = 500
SUPPORTED_RULE_MATCH_KINDS = {"merchant_contains"}


class FinanceServiceLike(Protocol):
    def __enter__(self) -> Self: ...
    def __exit__(self, *exc: object) -> None: ...
    def safe_finance_summary(self) -> dict[str, object]: ...
    def list_accounts(self) -> dict[str, object]: ...
    def list_account_names(self) -> list[str]: ...
    def list_transaction_category_names(self) -> list[str]: ...
    def list_spending_merchant_names(self) -> list[str]: ...
    def finance_import(
        self,
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
    ) -> dict[str, object]: ...
    def missing_transaction_ids(self, transaction_ids: list[int]) -> list[int]: ...
    def finance_categorize(self, transaction_ids: list[int], category_name: str) -> int: ...
    def add_category_rule(self, category_name: str, match_kind: str, pattern: str) -> None: ...
    def finance_anomalies(self) -> dict[str, object]: ...
    def get_job(self, job_id: str) -> dict[str, object]: ...
    def generate_weekly_report(self, period_start: str, period_end: str) -> dict[str, object]: ...
    def generate_monthly_report(self, period_start: str, period_end: str) -> dict[str, object]: ...
    def sensitive_finance_query(
        self,
        limit: int = 50,
        session_ref: str | None = None,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        category_name: str | None = None,
        merchant: str | None = None,
        account_name: str | None = None,
        description_contains: str | None = None,
    ) -> dict[str, object]: ...
    def get_filtered_spending_total(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        category_name: str | None = None,
        merchant: str | None = None,
        account_name: str | None = None,
        description_contains: str | None = None,
    ) -> int: ...
    def get_filtered_transaction_count(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        category_name: str | None = None,
        merchant: str | None = None,
        account_name: str | None = None,
        description_contains: str | None = None,
    ) -> int: ...


def create_finance_server(service: FinanceServiceLike, llm: object | None = None) -> FastMCP:
    mcp = FastMCP("minx-finance", stateless_http=True, json_response=True)

    @mcp.tool(name="safe_finance_summary")
    def safe_finance_summary() -> dict[str, object]:
        return wrap_tool_call(lambda: _safe_finance_summary(service))

    @mcp.tool(name="safe_finance_accounts")
    def safe_finance_accounts() -> dict[str, object]:
        return wrap_tool_call(lambda: _safe_finance_accounts(service))

    @mcp.tool(name="finance_import")
    def finance_import(
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
    ) -> dict[str, object]:
        return wrap_tool_call(
            lambda: _finance_import(service, source_ref, account_name, source_kind)
        )

    @mcp.tool(name="finance_categorize")
    def finance_categorize(transaction_ids: list[int], category_name: str) -> dict[str, object]:
        return wrap_tool_call(
            lambda: _finance_categorize(service, transaction_ids, category_name)
        )

    @mcp.tool(name="finance_add_category_rule")
    def finance_add_category_rule(
        category_name: str,
        match_kind: str,
        pattern: str,
    ) -> dict[str, str]:
        return wrap_tool_call(
            lambda: _finance_add_category_rule(service, category_name, match_kind, pattern)
        )

    @mcp.tool(name="finance_anomalies")
    def finance_anomalies() -> dict[str, object]:
        return wrap_tool_call(lambda: _finance_anomalies(service))

    @mcp.tool(name="finance_job_status")
    def finance_job_status(job_id: str) -> dict[str, object]:
        return wrap_tool_call(lambda: _finance_job_status(service, job_id))

    @mcp.tool(name="finance_generate_weekly_report")
    def finance_generate_weekly_report(period_start: str, period_end: str) -> dict[str, object]:
        return wrap_tool_call(
            lambda: _finance_generate_weekly_report(service, period_start, period_end)
        )

    @mcp.tool(name="finance_generate_monthly_report")
    def finance_generate_monthly_report(period_start: str, period_end: str) -> dict[str, object]:
        return wrap_tool_call(
            lambda: _finance_generate_monthly_report(service, period_start, period_end)
        )

    @mcp.tool(name="sensitive_finance_query")
    def sensitive_finance_query(
        limit: int = 50,
        session_ref: str | None = None,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        category_name: str | None = None,
        merchant: str | None = None,
        account_name: str | None = None,
        description_contains: str | None = None,
    ) -> dict[str, object]:
        return wrap_tool_call(
            lambda: _sensitive_finance_query(
                service,
                limit,
                session_ref,
                start_date,
                end_date,
                category_name,
                merchant,
                account_name,
                description_contains,
            )
        )

    @mcp.tool(name="finance_query")
    def finance_query(
        message: str,
        review_date: str | None = None,
        session_ref: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        return wrap_tool_call(
            lambda: _finance_query(
                service,
                message=message,
                review_date=review_date,
                session_ref=session_ref,
                limit=limit,
                llm=llm,
            )
        )

    return mcp


def _safe_finance_summary(service: FinanceServiceLike) -> dict[str, object]:
    with service:
        return service.safe_finance_summary()


def _safe_finance_accounts(service: FinanceServiceLike) -> dict[str, object]:
    with service:
        return service.list_accounts()


def _finance_import(
    service: FinanceServiceLike,
    source_ref: str,
    account_name: str,
    source_kind: str | None,
) -> dict[str, object]:
    _require_non_empty("account_name", account_name)
    _validate_source_ref(source_ref)
    if source_kind is not None and source_kind not in SUPPORTED_SOURCE_KINDS:
        raise InvalidInputError(f"Unsupported finance source kind: {source_kind}")
    with service:
        return service.finance_import(source_ref, account_name, source_kind=source_kind)


def _finance_categorize(
    service: FinanceServiceLike,
    transaction_ids: list[int],
    category_name: str,
) -> dict[str, object]:
    _require_non_empty("category_name", category_name)
    if not transaction_ids:
        raise InvalidInputError("transaction_ids must be a non-empty list")
    if any(transaction_id <= 0 for transaction_id in transaction_ids):
        raise InvalidInputError("transaction_ids must contain only positive integers")
    with service:
        missing = service.missing_transaction_ids(transaction_ids)
        if missing:
            missing_list = ", ".join(str(transaction_id) for transaction_id in missing)
            raise NotFoundError(f"Unknown finance transaction ids: {missing_list}")
        updated = service.finance_categorize(transaction_ids, category_name)
        return {"updated": updated}


def _finance_add_category_rule(
    service: FinanceServiceLike,
    category_name: str,
    match_kind: str,
    pattern: str,
) -> dict[str, str]:
    _require_non_empty("category_name", category_name)
    if match_kind not in SUPPORTED_RULE_MATCH_KINDS:
        supported = ", ".join(sorted(SUPPORTED_RULE_MATCH_KINDS))
        raise InvalidInputError(
            f"Unsupported match_kind: {match_kind}. Expected one of: {supported}"
        )
    if not pattern.strip():
        raise InvalidInputError("pattern must not be empty")
    with service:
        service.add_category_rule(category_name, match_kind, pattern)
        return {"status": "created", "category": category_name, "pattern": pattern}


def _finance_anomalies(service: FinanceServiceLike) -> dict[str, object]:
    with service:
        return service.finance_anomalies()


def _finance_job_status(service: FinanceServiceLike, job_id: str) -> dict[str, object]:
    _require_non_empty("job_id", job_id)
    with service:
        return service.get_job(job_id)


def _finance_generate_weekly_report(
    service: FinanceServiceLike,
    period_start: str,
    period_end: str,
) -> dict[str, object]:
    _validate_date_window(period_start, period_end)
    with service:
        return service.generate_weekly_report(period_start, period_end)


def _finance_generate_monthly_report(
    service: FinanceServiceLike,
    period_start: str,
    period_end: str,
) -> dict[str, object]:
    _validate_date_window(period_start, period_end)
    with service:
        return service.generate_monthly_report(period_start, period_end)


def _sensitive_finance_query(
    service: FinanceServiceLike,
    limit: int,
    session_ref: str | None,
    start_date: str | None,
    end_date: str | None,
    category_name: str | None,
    merchant: str | None,
    account_name: str | None,
    description_contains: str | None,
) -> dict[str, object]:
    if limit < 1 or limit > MAX_SENSITIVE_QUERY_LIMIT:
        raise InvalidInputError(f"limit must be between 1 and {MAX_SENSITIVE_QUERY_LIMIT}")
    with service:
        return service.sensitive_finance_query(
            limit=limit,
            session_ref=session_ref,
            start_date=start_date,
            end_date=end_date,
            category_name=category_name,
            merchant=merchant,
            account_name=account_name,
            description_contains=description_contains,
        )


def _finance_query(
    service: FinanceServiceLike,
    *,
    message: str,
    review_date: str | None,
    session_ref: str | None,
    limit: int,
    llm: object | None,
) -> dict[str, object]:
    _require_non_empty("message", message)
    if limit < 1 or limit > MAX_SENSITIVE_QUERY_LIMIT:
        raise InvalidInputError(f"limit must be between 1 and {MAX_SENSITIVE_QUERY_LIMIT}")

    effective_review_date = review_date or date.today().isoformat()
    _validate_iso_date(effective_review_date, field_name="review_date")
    resolved_llm = _resolve_finance_query_llm(service, llm)

    with service:
        plan = interpret_finance_query(
            message=message,
            review_date=effective_review_date,
            finance_api=service,
            llm=resolved_llm,
        )
        if plan.needs_clarification:
            return {
                "result_type": "clarify",
                "intent": plan.intent,
                "filters": plan.filters.to_public_dict(),
                "confidence": plan.confidence,
                "clarification_type": plan.clarification_type,
                "question": plan.question,
                "options": plan.options,
            }

        filters = plan.filters.to_public_dict()
        if plan.intent == "list_transactions":
            result = service.sensitive_finance_query(
                limit=limit,
                session_ref=session_ref,
                **filters,
            )
            return {
                "result_type": "query",
                "intent": plan.intent,
                "filters": filters,
                "confidence": plan.confidence,
                "transactions": result["transactions"],
            }
        if plan.intent == "sum_spending":
            total_cents = service.get_filtered_spending_total(**filters)
            return {
                "result_type": "query",
                "intent": plan.intent,
                "filters": filters,
                "confidence": plan.confidence,
                "total_spent": cents_to_dollars(total_cents),
            }
        if plan.intent == "count_transactions":
            total_count = service.get_filtered_transaction_count(**filters)
            return {
                "result_type": "query",
                "intent": plan.intent,
                "filters": filters,
                "confidence": plan.confidence,
                "transaction_count": total_count,
            }
    raise InvalidInputError(f"Unsupported finance query intent: {plan.intent}")


def _resolve_finance_query_llm(service: FinanceServiceLike, llm: object | None) -> object:
    if llm is not None:
        return llm

    configured = create_llm(db_path=getattr(service, "_db_path", None))
    if configured is None or not callable(getattr(configured, "run_json_prompt", None)):
        raise InvalidInputError("finance_query requires a configured JSON-capable LLM")
    return configured


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise InvalidInputError(f"{name} must not be empty")


def _validate_source_ref(source_ref: str) -> None:
    path = Path(source_ref)
    if not path.is_file():
        raise InvalidInputError("source_ref must point to an existing file")


def _validate_iso_date(value: str, *, field_name: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInputError(f"{field_name} must be a valid ISO date") from exc


def _validate_date_window(period_start: str, period_end: str) -> None:
    try:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
    except ValueError as exc:
        raise InvalidInputError("Invalid ISO date") from exc
    if start > end:
        raise InvalidInputError("period_start must be on or before period_end")
