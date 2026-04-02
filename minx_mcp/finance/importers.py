from __future__ import annotations

from pathlib import Path

from minx_mcp.finance.parsers.dcu import parse_dcu_csv, parse_dcu_pdf
from minx_mcp.finance.parsers.discover import parse_discover_pdf
from minx_mcp.finance.parsers.generic_csv import parse_generic_csv
from minx_mcp.finance.parsers.robinhood_gold import parse_robinhood_csv


def detect_source_kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith("robinhood_transactions.csv"):
        return "robinhood_csv"
    if "free checking transactions.csv" in name:
        return "dcu_csv"
    if "discover" in name and path.suffix.lower() == ".pdf":
        return "discover_pdf"
    if name.startswith("stmt_") and path.suffix.lower() == ".pdf":
        return "dcu_pdf"
    raise ValueError(f"Could not detect finance source for {path}")


def parse_source_file(
    path: Path,
    account_name: str,
    source_kind: str | None = None,
    mapping: dict[str, object] | None = None,
) -> dict[str, object]:
    kind = source_kind or detect_source_kind(path)
    if kind == "robinhood_csv":
        return parse_robinhood_csv(path, account_name)
    if kind == "dcu_csv":
        return parse_dcu_csv(path, account_name)
    if kind == "dcu_pdf":
        return parse_dcu_pdf(path, account_name)
    if kind == "discover_pdf":
        return parse_discover_pdf(path, account_name)
    if kind == "generic_csv":
        if not mapping:
            raise ValueError("generic_csv requires a saved mapping")
        return parse_generic_csv(path, account_name, mapping)
    raise ValueError(f"Unsupported finance source kind: {kind}")
