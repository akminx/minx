from __future__ import annotations

import hashlib


def fingerprint_transaction(account_name: str, transaction: dict[str, object]) -> str:
    raw = "|".join(
        [
            account_name,
            str(transaction["posted_at"]),
            str(transaction["description"]),
            f"{float(transaction['amount']):.2f}",
            str(transaction.get("external_id") or ""),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()
