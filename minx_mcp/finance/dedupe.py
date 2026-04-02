from __future__ import annotations

import hashlib


def fingerprint_transaction(account_id: int, transaction: dict[str, object]) -> str:
    raw = "|".join(
        [
            str(account_id),
            str(transaction["posted_at"]),
            str(transaction["description"]),
            f"{float(transaction['amount']):.2f}",
            str(transaction.get("external_id") or ""),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()
