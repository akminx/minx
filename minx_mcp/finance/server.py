from __future__ import annotations

from mcp.server.fastmcp import FastMCP


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

SENSITIVE_TOOLS = ["sensitive_finance_query"]


def create_finance_server(service: object) -> FastMCP:
    mcp = FastMCP("minx-finance", stateless_http=True, json_response=True)

    @mcp.tool(name="safe_finance_summary")
    def safe_finance_summary() -> dict[str, object]:
        return service.safe_finance_summary()

    @mcp.tool(name="safe_finance_accounts")
    def safe_finance_accounts() -> dict[str, object]:
        return service.list_accounts()

    @mcp.tool(name="finance_import")
    def finance_import(
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
    ) -> dict[str, object]:
        return service.finance_import(source_ref, account_name, source_kind=source_kind)

    @mcp.tool(name="finance_categorize")
    def finance_categorize(transaction_ids: list[int], category_name: str) -> dict[str, int]:
        service.finance_categorize(transaction_ids, category_name)
        return {"updated": len(transaction_ids)}

    @mcp.tool(name="finance_add_category_rule")
    def finance_add_category_rule(
        category_name: str,
        match_kind: str,
        pattern: str,
    ) -> dict[str, str]:
        service.add_category_rule(category_name, match_kind, pattern)
        return {"status": "created", "category": category_name, "pattern": pattern}

    @mcp.tool(name="finance_anomalies")
    def finance_anomalies() -> dict[str, object]:
        return service.finance_anomalies()

    @mcp.tool(name="finance_job_status")
    def finance_job_status(job_id: str) -> dict[str, object] | None:
        return service.get_job(job_id)

    @mcp.tool(name="finance_generate_weekly_report")
    def finance_generate_weekly_report(period_start: str, period_end: str) -> dict[str, object]:
        return service.generate_weekly_report(period_start, period_end)

    @mcp.tool(name="finance_generate_monthly_report")
    def finance_generate_monthly_report(period_start: str, period_end: str) -> dict[str, object]:
        return service.generate_monthly_report(period_start, period_end)

    @mcp.tool(name="sensitive_finance_query")
    def sensitive_finance_query(
        limit: int = 50,
        session_ref: str | None = None,
    ) -> dict[str, object]:
        return service.sensitive_finance_query(limit=limit, session_ref=session_ref)

    return mcp
