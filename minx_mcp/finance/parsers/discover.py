from __future__ import annotations

import re
from pathlib import Path

from minx_mcp.document_text import extract_text


def parse_discover_pdf(path: Path, account_name: str) -> dict[str, object]:
    text = extract_text(path)
    transactions: list[dict[str, object | None]] = []
    for line in text.splitlines():
        match = re.match(
            (
                r"^(?P<trans>\d{2}/\d{2}/\d{2})\s+\d{2}/\d{2}/\d{2}\s+"
                r"(?P<desc>.+?)\s+\$\s*(?P<amount>\d+\.\d{2})\s+(?P<category>.+)$"
            ),
            line.strip(),
        )
        if not match:
            continue
        month, day, year = match.group("trans").split("/")
        description = match.group("desc")
        transactions.append(
            {
                "posted_at": f"20{year}-{month}-{day}",
                "description": description,
                "amount": -float(match.group("amount")),
                "merchant": description,
                "category_hint": match.group("category").lower(),
                "external_id": None,
            }
        )
    return {
        "account_name": account_name,
        "source_type": "pdf",
        "source_ref": str(path),
        "raw_fingerprint": f"path:{path.name}",
        "transactions": transactions,
    }
