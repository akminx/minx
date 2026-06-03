from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Protocol, Self

from mcp.server.fastmcp import FastMCP
from pydantic import StrictInt

from minx_mcp.contracts import (
    InvalidInputError,
    NotFoundError,
    ToolResponse,
    wrap_async_tool_call,
    wrap_tool_call,
)
from minx_mcp.core.interpretation.finance_query import interpret_finance_query
from minx_mcp.core.llm import LLMProviderError, create_llm
from minx_mcp.core.models import JSONLLMInterface
from minx_mcp.db import scoped_connection
from minx_mcp.finance.importers import SUPPORTED_SOURCE_KINDS, detect_source_kind
from minx_mcp.money import cents_to_display_dollars
from minx_mcp.transport import health_payload
from minx_mcp.validation import (
    require_non_empty as _require_non_empty,
)
from minx_mcp.validation import (
    require_payload_object,
    require_str,
    resolve_date_or_today,
    validate_limit,
)
from minx_mcp.validation import (
    validate_date_window as _validate_date_window,
)
from minx_mcp.validation import (
    validate_optional_date_range as _validate_date_range,
)

SAFE_TOOLS = [
    "safe_finance_summary",
    "safe_finance_accounts",
    "finance_stage_email_statement",
    "finance_import",
    "finance_import_preview",
    "finance_categorize",
    "finance_add_category_rule",
    "finance_anomalies",
    "finance_monitoring",
    "finance_job_status",
    "finance_generate_weekly_report",
    "finance_generate_monthly_report",
]

SENSITIVE_TOOLS = ["sensitive_finance_query", "finance_query"]
MAX_SENSITIVE_QUERY_LIMIT = 500
SUPPORTED_RULE_MATCH_KINDS = {"merchant_contains"}


class _ScopingFinanceQueryReadAPI:
    """Read-only name lists for finance_query interpretation (no open handle across LLM await)."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def list_transaction_category_names(self) -> list[str]:
        with scoped_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM finance_categories ORDER BY name ASC"
            ).fetchall()
            return [str(row["name"]) for row in rows]

    def list_spending_merchant_names(self) -> list[str]:
        with scoped_connection(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT merchant
                FROM finance_transactions
                WHERE amount_cents < 0
                  AND COALESCE(TRIM(merchant), '') != ''
                ORDER BY merchant ASC
                """
            ).fetchall()
            return [str(row["merchant"]) for row in rows]

    def list_account_names(self) -> list[str]:
        with scoped_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM finance_accounts ORDER BY name ASC"
            ).fetchall()
            return [str(row["name"]) for row in rows]


class FinanceServiceLike(Protocol):
    import_root: Path

    @property
    def db_path(self) -> Path: ...

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
        mapping: dict[str, object] | None = None,
    ) -> dict[str, object]: ...
    def finance_import_preview(
        self,
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
        mapping: dict[str, object] | None = None,
    ) -> dict[str, object]: ...
    def missing_transaction_ids(self, transaction_ids: list[int]) -> list[int]: ...
    def finance_categorize(self, transaction_ids: list[int], category_name: str) -> int: ...
    def add_category_rule(self, category_name: str, match_kind: str, pattern: str) -> None: ...
    def finance_anomalies(self) -> dict[str, object]: ...
    def finance_monitoring(self, *, period_start: str, period_end: str) -> dict[str, object]: ...
    def get_job(self, job_id: str) -> dict[str, object]: ...
    def generate_weekly_report(self, period_start: str, period_end: str) -> dict[str, object]: ...
    def generate_monthly_report(self, period_start: str, period_end: str) -> dict[str, object]: ...
    def sensitive_finance_query(
        self,
        limit: int = 50,
        session_ref: str | None = None,
        audit_tool_name: str = "sensitive_finance_query",
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
        session_ref: str | None = None,
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
        session_ref: str | None = None,
    ) -> int: ...


def create_finance_server(
    service: FinanceServiceLike,
    llm: JSONLLMInterface | None = None,
) -> FastMCP:
    mcp = FastMCP("minx-finance", stateless_http=True, json_response=True)

    @mcp.tool(name="safe_finance_summary")
    def safe_finance_summary() -> ToolResponse:
        return wrap_tool_call(
            lambda: _safe_finance_summary(service),
            tool_name="safe_finance_summary",
        )

    @mcp.tool(name="safe_finance_accounts")
    def safe_finance_accounts() -> ToolResponse:
        return wrap_tool_call(
            lambda: _safe_finance_accounts(service),
            tool_name="safe_finance_accounts",
        )

    @mcp.tool(name="finance_stage_email_statement")
    def finance_stage_email_statement(
        issuer: str,
        query: str | None = None,
        himalaya_account: str = "minx",
        folder: str = "INBOX",
        limit: StrictInt = 5,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _finance_stage_email_statement(
                service,
                issuer=issuer,
                query=query,
                himalaya_account=himalaya_account,
                folder=folder,
                limit=limit,
            ),
            tool_name="finance_stage_email_statement",
        )

    @mcp.tool(name="finance_import")
    def finance_import(
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
        mapping: dict[str, object] | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _finance_import(service, source_ref, account_name, source_kind, mapping),
            tool_name="finance_import",
        )

    @mcp.tool(name="finance_import_preview")
    def finance_import_preview(
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
        mapping: dict[str, object] | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _finance_import_preview(service, source_ref, account_name, source_kind, mapping),
            tool_name="finance_import_preview",
        )

    @mcp.tool(name="finance_categorize")
    def finance_categorize(transaction_ids: list[int], category_name: str) -> ToolResponse:
        return wrap_tool_call(
            lambda: _finance_categorize(service, transaction_ids, category_name),
            tool_name="finance_categorize",
        )

    @mcp.tool(name="finance_add_category_rule")
    def finance_add_category_rule(
        category_name: str,
        match_kind: str,
        pattern: str,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _finance_add_category_rule(service, category_name, match_kind, pattern),
            tool_name="finance_add_category_rule",
        )

    @mcp.tool(name="finance_anomalies")
    def finance_anomalies() -> ToolResponse:
        return wrap_tool_call(
            lambda: _finance_anomalies(service),
            tool_name="finance_anomalies",
        )

    @mcp.tool(name="finance_monitoring")
    def finance_monitoring(period_start: str, period_end: str) -> ToolResponse:
        return wrap_tool_call(
            lambda: _finance_monitoring(service, period_start, period_end),
            tool_name="finance_monitoring",
        )

    @mcp.tool(name="finance_job_status")
    def finance_job_status(job_id: str) -> ToolResponse:
        return wrap_tool_call(
            lambda: _finance_job_status(service, job_id),
            tool_name="finance_job_status",
        )

    @mcp.tool(name="finance_generate_weekly_report")
    def finance_generate_weekly_report(period_start: str, period_end: str) -> ToolResponse:
        return wrap_tool_call(
            lambda: _finance_generate_weekly_report(service, period_start, period_end),
            tool_name="finance_generate_weekly_report",
        )

    @mcp.tool(name="finance_generate_monthly_report")
    def finance_generate_monthly_report(period_start: str, period_end: str) -> ToolResponse:
        return wrap_tool_call(
            lambda: _finance_generate_monthly_report(service, period_start, period_end),
            tool_name="finance_generate_monthly_report",
        )

    @mcp.tool(name="sensitive_finance_query")
    def sensitive_finance_query(
        limit: StrictInt = 50,
        session_ref: str | None = None,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        category_name: str | None = None,
        merchant: str | None = None,
        account_name: str | None = None,
        description_contains: str | None = None,
    ) -> ToolResponse:
        """Read-only, audit-logged query over the user's own transaction records.

        Returns transaction-level rows filtered by the supplied date range,
        category, merchant, account, or description text (capped at
        MAX_SENSITIVE_QUERY_LIMIT). It only reads local finance data and never
        writes, exports, or transmits it; every call is recorded under
        audit_tool_name for the audit trail. Marked "sensitive" because the rows
        include detailed personal spending, so results should stay within the
        user's own session.
        """
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
            ),
            tool_name="sensitive_finance_query",
        )

    @mcp.resource("health://status")
    def health_status() -> str:
        return health_payload("minx-finance")

    @mcp.tool(name="finance_query")
    async def finance_query(
        message: str | None = None,
        review_date: str | None = None,
        session_ref: str | None = None,
        limit: StrictInt = 50,
        *,
        intent: str | None = None,
        filters: dict[str, object] | None = None,
        natural_query: str | None = None,
    ) -> ToolResponse:
        return await wrap_async_tool_call(
            lambda: _finance_query(
                service,
                intent=intent,
                filters=filters,
                natural_query=natural_query,
                message=message,
                review_date=review_date,
                session_ref=session_ref,
                limit=limit,
                llm=llm,
            ),
            tool_name="finance_query",
        )

    return mcp


def _safe_finance_summary(service: FinanceServiceLike) -> dict[str, object]:
    with service:
        return service.safe_finance_summary()


def _safe_finance_accounts(service: FinanceServiceLike) -> dict[str, object]:
    with service:
        return service.list_accounts()


def _finance_stage_email_statement(
    service: FinanceServiceLike,
    *,
    issuer: str,
    query: str | None,
    himalaya_account: str,
    folder: str,
    limit: int,
) -> dict[str, object]:
    issuer_key = _normalize_email_issuer(issuer)
    _require_non_empty("himalaya_account", himalaya_account)
    _require_non_empty("folder", folder)
    limit = validate_limit(limit, maximum=25)
    search_query = query.strip() if query and query.strip() else _default_email_query(issuer_key)
    staging_root = service.import_root / "email" / resolve_date_or_today(None)
    envelopes = _himalaya_envelope_search(
        account=himalaya_account,
        folder=folder,
        query=search_query,
        limit=limit,
    )
    staged_files: list[dict[str, object]] = []
    for envelope in envelopes[:limit]:
        message_id = _message_id(envelope)
        message_dir = staging_root / _safe_path_part(message_id)
        before = _files_under(message_dir)
        message_dir.mkdir(parents=True, exist_ok=True)
        _himalaya_download_attachments(
            account=himalaya_account,
            folder=folder,
            message_id=message_id,
            downloads_dir=message_dir,
        )
        for path in sorted(_files_under(message_dir) - before):
            if path.suffix.lower() not in {".csv", ".pdf"}:
                continue
            staged_files.append(
                {
                    "path": str(path),
                    "filename": path.name,
                    "message_id": message_id,
                    "source_kind": _detect_stage_source_kind(path),
                    "account_name": _account_name_for_issuer(issuer_key),
                }
            )
    return {
        "issuer": issuer_key,
        "query": search_query,
        "message_count": len(envelopes),
        "staged_files": staged_files,
        "next_step": "Run finance_import_preview for each staged file before importing.",
    }


def _finance_import(
    service: FinanceServiceLike,
    source_ref: str,
    account_name: str,
    source_kind: str | None,
    mapping: dict[str, object] | None,
) -> dict[str, object]:
    _require_non_empty("account_name", account_name)
    _validate_source_ref(source_ref)
    if source_kind is not None and source_kind not in SUPPORTED_SOURCE_KINDS:
        raise InvalidInputError(f"Unsupported finance source kind: {source_kind}")
    with service:
        return service.finance_import(
            source_ref,
            account_name,
            source_kind=source_kind,
            mapping=mapping,
        )


def _finance_import_preview(
    service: FinanceServiceLike,
    source_ref: str,
    account_name: str,
    source_kind: str | None,
    mapping: dict[str, object] | None,
) -> dict[str, object]:
    _require_non_empty("account_name", account_name)
    _validate_source_ref(source_ref)
    if source_kind is not None and source_kind not in SUPPORTED_SOURCE_KINDS:
        raise InvalidInputError(f"Unsupported finance source kind: {source_kind}")
    with service:
        return service.finance_import_preview(
            source_ref,
            account_name,
            source_kind=source_kind,
            mapping=mapping,
        )


def _normalize_email_issuer(value: str) -> str:
    _require_non_empty("issuer", value)
    issuer = value.strip().casefold().replace(" ", "_").replace("-", "_")
    aliases = {
        "robinhood": "robinhood",
        "robinhood_gold": "robinhood",
        "discover": "discover",
        "dcu": "dcu",
        "digital_federal_credit_union": "dcu",
    }
    if issuer not in aliases:
        raise InvalidInputError("issuer must be one of: robinhood, discover, dcu")
    return aliases[issuer]


def _default_email_query(issuer: str) -> str:
    if issuer == "robinhood":
        return "from robinhood order by date desc"
    if issuer == "discover":
        return "from discover order by date desc"
    if issuer == "dcu":
        return "from dcu order by date desc"
    raise InvalidInputError("issuer must be one of: robinhood, discover, dcu")


def _account_name_for_issuer(issuer: str) -> str:
    return {
        "robinhood": "Robinhood Gold",
        "discover": "Discover",
        "dcu": "DCU",
    }[issuer]


def _himalaya_envelope_search(
    *,
    account: str,
    folder: str,
    query: str,
    limit: int,
) -> list[dict[str, object]]:
    cmd = [
        "himalaya",
        "envelope",
        "list",
        "--account",
        account,
        "--folder",
        folder,
        "--page-size",
        str(limit),
        "--output",
        "json",
        *query.split(),
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise InvalidInputError(f"himalaya envelope search failed: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise InvalidInputError("himalaya envelope search returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise InvalidInputError("himalaya envelope search returned an unexpected payload")
    return [item for item in payload if isinstance(item, dict)]


def _himalaya_download_attachments(
    *,
    account: str,
    folder: str,
    message_id: str,
    downloads_dir: Path,
) -> None:
    cmd = [
        "himalaya",
        "attachment",
        "download",
        "--account",
        account,
        "--folder",
        folder,
        "--downloads-dir",
        str(downloads_dir),
        "--output",
        "json",
        message_id,
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise InvalidInputError(f"himalaya attachment download failed: {result.stderr.strip()}")


def _message_id(envelope: dict[str, object]) -> str:
    for key in ("id", "uid", "message_id"):
        value = envelope.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise InvalidInputError("himalaya envelope was missing an id")


def _safe_path_part(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return safe[:80] or "message"


def _files_under(path: Path) -> set[Path]:
    if not path.exists():
        return set()
    return {item for item in path.rglob("*") if item.is_file()}


def _detect_stage_source_kind(path: Path) -> str | None:
    try:
        return detect_source_kind(path)
    except InvalidInputError:
        return None


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


def _finance_monitoring(
    service: FinanceServiceLike,
    period_start: str,
    period_end: str,
) -> dict[str, object]:
    _validate_date_window(period_start, period_end)
    with service:
        return service.finance_monitoring(period_start=period_start, period_end=period_end)


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
    limit: object,
    session_ref: str | None,
    start_date: str | None,
    end_date: str | None,
    category_name: str | None,
    merchant: str | None,
    account_name: str | None,
    description_contains: str | None,
) -> dict[str, object]:
    validated_limit = validate_limit(limit, maximum=MAX_SENSITIVE_QUERY_LIMIT)
    _validate_date_range(start_date, end_date)
    _validate_optional_text_filters(
        category_name=category_name,
        merchant=merchant,
        account_name=account_name,
        description_contains=description_contains,
    )
    with service:
        return service.sensitive_finance_query(
            limit=validated_limit,
            session_ref=session_ref,
            audit_tool_name="sensitive_finance_query",
            start_date=start_date,
            end_date=end_date,
            category_name=category_name,
            merchant=merchant,
            account_name=account_name,
            description_contains=description_contains,
        )


async def _finance_query(
    service: FinanceServiceLike,
    *,
    intent: str | None,
    filters: dict[str, object] | None,
    natural_query: str | None,
    message: str | None,
    review_date: str | None,
    session_ref: str | None,
    limit: object,
    llm: JSONLLMInterface | None,
) -> dict[str, object]:
    validated_limit = validate_limit(limit, maximum=MAX_SENSITIVE_QUERY_LIMIT)

    if natural_query is not None and message is not None:
        raise InvalidInputError("message and natural_query may not both be provided")
    effective_message = natural_query if natural_query is not None else message

    if intent is not None and effective_message is not None:
        raise InvalidInputError("structured and natural finance_query inputs may not be mixed")
    if intent is None and effective_message is None:
        raise InvalidInputError("finance_query requires either structured or natural input")

    if intent is not None:
        with service:
            validated_filters = _validate_structured_finance_filters(service, filters)
            return _execute_finance_query_plan(
                service,
                intent=intent,
                filters=validated_filters,
                confidence=1.0,
                session_ref=session_ref,
                limit=validated_limit,
            )

    if effective_message is None:
        raise RuntimeError("internal: natural finance_query requires a message")
    _require_non_empty("message" if message is not None else "natural_query", effective_message)
    effective_review_date = resolve_date_or_today(review_date, field_name="review_date")
    resolved_llm = _resolve_finance_query_llm(service, llm)

    read_api = _ScopingFinanceQueryReadAPI(service.db_path)
    plan = await interpret_finance_query(
        message=effective_message,
        review_date=effective_review_date,
        finance_api=read_api,
        llm=resolved_llm,
    )
    if plan.needs_clarification:
        return {
            "result_type": "clarify",
            "intent": plan.intent,
            "filters": plan.filters.to_public_dict(),
            "confidence": plan.confidence,
            "clarification_type": plan.clarification_type,
            "clarification_template": plan.clarification_template,
            "clarification_slots": plan.clarification_slots,
            "question": plan.question,
            "options": plan.options,
        }

    validated_filters = plan.filters.to_public_dict()
    _validate_date_range(validated_filters.get("start_date"), validated_filters.get("end_date"))
    _validate_optional_text_filters(
        category_name=validated_filters.get("category_name"),
        merchant=validated_filters.get("merchant"),
        account_name=validated_filters.get("account_name"),
        description_contains=validated_filters.get("description_contains"),
    )
    with service:
        _validate_structured_filter_canonical_values(service, validated_filters)
        return _execute_finance_query_plan(
            service,
            intent=plan.intent,
            filters=validated_filters,
            confidence=plan.confidence,
            session_ref=session_ref,
            limit=validated_limit,
        )


def _validate_structured_finance_filters(
    service: FinanceServiceLike,
    filters: dict[str, object] | None,
) -> dict[str, str]:
    allowed_keys = {
        "start_date",
        "end_date",
        "category_name",
        "merchant",
        "account_name",
        "description_contains",
    }
    if filters is None:
        return {}
    filters = require_payload_object(filters, field_name="filters")
    unknown_keys = set(filters) - allowed_keys
    if unknown_keys:
        unknown_list = ", ".join(sorted(unknown_keys))
        raise InvalidInputError(f"Unknown finance_query filter keys: {unknown_list}")
    normalized: dict[str, str] = {}
    for key in filters:
        value = require_str(filters, key)
        if not value.strip():
            raise InvalidInputError(f"{key} must not be blank")
        normalized[key] = value
    _validate_date_range(normalized.get("start_date"), normalized.get("end_date"))
    _validate_optional_text_filters(
        category_name=normalized.get("category_name"),
        merchant=normalized.get("merchant"),
        account_name=normalized.get("account_name"),
        description_contains=normalized.get("description_contains"),
    )
    _validate_structured_filter_canonical_values(service, normalized)
    return normalized


def _validate_structured_filter_canonical_values(
    service: FinanceServiceLike,
    filters: dict[str, str],
) -> None:
    category_name = filters.get("category_name")
    if category_name is not None and category_name not in service.list_transaction_category_names():
        raise InvalidInputError("category_name must be a known canonical category name")

    merchant = filters.get("merchant")
    if merchant is not None and merchant not in service.list_spending_merchant_names():
        raise InvalidInputError("merchant must be a known canonical merchant name")

    account_name = filters.get("account_name")
    if account_name is not None and account_name not in service.list_account_names():
        raise InvalidInputError("account_name must be a known canonical account name")


def _execute_finance_query_plan(
    service: FinanceServiceLike,
    *,
    intent: str,
    filters: dict[str, str],
    confidence: float,
    session_ref: str | None,
    limit: int,
) -> dict[str, object]:
    if intent == "list_transactions":
        result = service.sensitive_finance_query(
            limit=limit,
            session_ref=session_ref,
            audit_tool_name="finance_query",
            **filters,
        )
        return {
            "result_type": "query",
            "intent": intent,
            "filters": filters,
            "confidence": confidence,
            "transactions": result["transactions"],
        }
    if intent == "sum_spending":
        total_cents = service.get_filtered_spending_total(
            session_ref=session_ref,
            **filters,
        )
        return {
            "result_type": "query",
            "intent": intent,
            "filters": filters,
            "confidence": confidence,
            "total_spent": cents_to_display_dollars(total_cents),
        }
    if intent == "count_transactions":
        total_count = service.get_filtered_transaction_count(
            session_ref=session_ref,
            **filters,
        )
        return {
            "result_type": "query",
            "intent": intent,
            "filters": filters,
            "confidence": confidence,
            "transaction_count": total_count,
        }
    raise InvalidInputError(f"Unsupported finance query intent: {intent}")


def _resolve_finance_query_llm(
    service: FinanceServiceLike,
    llm: JSONLLMInterface | None,
) -> JSONLLMInterface:
    if llm is not None:
        if not isinstance(llm, JSONLLMInterface):
            raise InvalidInputError("finance_query requires a configured JSON-capable LLM")
        return llm

    try:
        configured = create_llm(db_path=service.db_path)
    except LLMProviderError as exc:
        raise InvalidInputError("finance_query requires a configured JSON-capable LLM") from exc
    if configured is None:
        raise InvalidInputError("finance_query requires a configured JSON-capable LLM")
    return configured


def _validate_source_ref(source_ref: str) -> None:
    path = Path(source_ref)
    if not path.is_file():
        raise InvalidInputError("source_ref must point to an existing file")


def _validate_optional_text_filters(
    *,
    category_name: str | None,
    merchant: str | None,
    account_name: str | None,
    description_contains: str | None,
) -> None:
    for field_name, value in (
        ("category_name", category_name),
        ("merchant", merchant),
        ("account_name", account_name),
        ("description_contains", description_contains),
    ):
        if value is not None and not value.strip():
            raise InvalidInputError(f"{field_name} must not be blank")
