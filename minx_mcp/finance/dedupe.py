from __future__ import annotations

import hashlib

from minx_mcp.finance.import_models import ParsedTransaction


def fingerprint_transaction(account_id: int, transaction: ParsedTransaction) -> str:
    raw = "|".join(
        [
            str(account_id),
            str(transaction.posted_at),
            str(transaction.description),
            str(int(transaction.amount_cents)),
            str(transaction.external_id or ""),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()
