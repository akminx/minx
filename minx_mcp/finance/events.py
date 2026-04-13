from __future__ import annotations

from typing import Literal

from minx_mcp.event_payloads import EventPayload


class TransactionsImportedPayload(EventPayload):
    account_name: str
    account_id: int
    job_id: str
    transaction_count: int
    total_cents: int
    source_kind: str


class TransactionsCategorizedPayload(EventPayload):
    count: int
    categories: list[str]


class ReportGeneratedPayload(EventPayload):
    report_type: Literal["weekly", "monthly"]
    period_start: str
    period_end: str
    vault_path: str


class AnomaliesDetectedPayload(EventPayload):
    count: int
    total_cents: int


FINANCE_EVENT_PAYLOADS: dict[str, type[EventPayload]] = {
    "finance.transactions_imported": TransactionsImportedPayload,
    "finance.transactions_categorized": TransactionsCategorizedPayload,
    "finance.report_generated": ReportGeneratedPayload,
    "finance.anomalies_detected": AnomaliesDetectedPayload,
}
