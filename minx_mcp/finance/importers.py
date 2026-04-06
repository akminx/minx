from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from minx_mcp.contracts import InvalidInputError
from minx_mcp.finance.parsers.dcu import parse_dcu_csv, parse_dcu_pdf
from minx_mcp.finance.parsers.discover import parse_discover_pdf
from minx_mcp.finance.parsers.generic_csv import parse_generic_csv
from minx_mcp.finance.parsers.robinhood_gold import parse_robinhood_csv

SUPPORTED_SOURCE_KINDS = (
    "robinhood_csv",
    "dcu_csv",
    "dcu_pdf",
    "discover_pdf",
    "generic_csv",
)


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
    raise InvalidInputError(f"Could not detect finance source for {path}")


def parse_source_file(
    path: Path,
    account_name: str,
    source_kind: str | None = None,
    mapping: dict[str, object] | None = None,
    *,
    file_bytes: bytes | None = None,
    content_hash: str | None = None,
) -> dict[str, object]:
    if file_bytes is None:
        file_bytes = path.read_bytes()
    if content_hash is None:
        content_hash = hashlib.sha256(file_bytes).hexdigest()

    kind = source_kind or detect_source_kind(path)
    with TemporaryDirectory() as temp_dir:
        snapshot_path = Path(temp_dir) / path.name
        snapshot_path.write_bytes(file_bytes)

        if kind == "robinhood_csv":
            result = parse_robinhood_csv(snapshot_path, account_name)
        elif kind == "dcu_csv":
            result = parse_dcu_csv(snapshot_path, account_name)
        elif kind == "dcu_pdf":
            result = parse_dcu_pdf(snapshot_path, account_name)
        elif kind == "discover_pdf":
            result = parse_discover_pdf(snapshot_path, account_name)
        elif kind == "generic_csv":
            if not mapping:
                raise InvalidInputError("generic_csv requires a saved mapping")
            result = parse_generic_csv(snapshot_path, account_name, mapping)
        else:
            raise InvalidInputError(f"Unsupported finance source kind: {kind}")

    _validate_parsed_transactions(result)
    result["source_ref"] = str(path.resolve())
    result["raw_fingerprint"] = content_hash
    return result


def _validate_parsed_transactions(parsed: dict[str, object]) -> None:
    transactions = parsed.get("transactions", [])
    if not isinstance(transactions, list):
        raise InvalidInputError("parsed transactions must be a list")
    for txn in transactions:
        if not isinstance(txn, dict):
            raise InvalidInputError("parsed transactions must be objects")
        if "amount_cents" not in txn or not isinstance(txn["amount_cents"], int):
            raise InvalidInputError("parsed transactions must include integer amount_cents")
