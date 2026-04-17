from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.interpretation.import_detection import detect_finance_source_kind
from minx_mcp.finance.import_models import GenericCSVMapping, ParsedImportBatch
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

_READ_CHUNK = 64 * 1024


def stream_snapshot_copy_and_hash(source_path: Path, dest_path: Path) -> str:
    """Copy ``source_path`` to ``dest_path`` in chunks while hashing the bytes written.

    Returns the SHA-256 hex digest of the copied bytes. Does not load the full file into memory.
    """
    digest = hashlib.sha256()
    with source_path.open("rb") as src, dest_path.open("wb") as dst:
        while True:
            chunk = src.read(_READ_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            dst.write(chunk)
    return digest.hexdigest()


def detect_source_kind(path: Path) -> str:
    return detect_finance_source_kind(path)


def _parse_kind_from_snapshot(
    snapshot_path: Path,
    account_name: str,
    kind: str,
    mapping: dict[str, object] | GenericCSVMapping | None,
) -> ParsedImportBatch:
    if kind == "robinhood_csv":
        return parse_robinhood_csv(snapshot_path, account_name)
    if kind == "dcu_csv":
        return parse_dcu_csv(snapshot_path, account_name)
    if kind == "dcu_pdf":
        return parse_dcu_pdf(snapshot_path, account_name)
    if kind == "discover_pdf":
        return parse_discover_pdf(snapshot_path, account_name)
    if kind == "generic_csv":
        if not mapping:
            raise InvalidInputError("generic_csv requires a saved mapping")
        return parse_generic_csv(
            snapshot_path,
            account_name,
            GenericCSVMapping.from_value(mapping),
        )
    raise InvalidInputError(f"Unsupported finance source kind: {kind}")


def parse_source_file(
    path: Path,
    account_name: str,
    source_kind: str | None = None,
    mapping: dict[str, object] | GenericCSVMapping | None = None,
    *,
    file_bytes: bytes | None = None,
    content_hash: str | None = None,
    snapshot_path: Path | None = None,
) -> ParsedImportBatch:
    if file_bytes is not None and snapshot_path is not None:
        raise InvalidInputError("cannot pass both file_bytes and snapshot_path")

    if snapshot_path is not None:
        if content_hash is None:
            raise InvalidInputError("content_hash is required when snapshot_path is set")
        kind = source_kind or detect_source_kind(snapshot_path)
        result = _parse_kind_from_snapshot(snapshot_path, account_name, kind, mapping)
        _validate_parsed_transactions(result)
        return replace(
            result,
            source_ref=str(path.resolve()),
            raw_fingerprint=content_hash,
        )

    if file_bytes is not None:
        if content_hash is None:
            content_hash = hashlib.sha256(file_bytes).hexdigest()
        with TemporaryDirectory() as temp_dir:
            sp = Path(temp_dir) / path.name
            sp.write_bytes(file_bytes)
            kind = source_kind or detect_source_kind(sp)
            result = _parse_kind_from_snapshot(sp, account_name, kind, mapping)
        _validate_parsed_transactions(result)
        return replace(
            result,
            source_ref=str(path.resolve()),
            raw_fingerprint=content_hash,
        )

    with TemporaryDirectory() as temp_dir:
        sp = Path(temp_dir) / path.name
        content_hash = stream_snapshot_copy_and_hash(path, sp)
        kind = source_kind or detect_source_kind(path)
        result = _parse_kind_from_snapshot(sp, account_name, kind, mapping)

    _validate_parsed_transactions(result)
    return replace(
        result,
        source_ref=str(path.resolve()),
        raw_fingerprint=content_hash,
    )


def _validate_parsed_transactions(parsed: ParsedImportBatch) -> None:
    for txn in parsed.transactions:
        if not isinstance(txn.amount_cents, int):
            raise InvalidInputError("parsed transactions must include integer amount_cents")
