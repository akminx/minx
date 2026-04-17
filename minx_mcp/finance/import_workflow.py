"""Finance CSV/file import: validation, job envelope, parse, persist, rules, events."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from sqlite3 import Connection
from typing import Any, Protocol

from minx_mcp.contracts import InvalidInputError
from minx_mcp.finance.dedupe import fingerprint_transaction
from minx_mcp.finance.import_models import ParsedImportBatch, ParsedTransaction
from minx_mcp.finance.importers import (
    SUPPORTED_SOURCE_KINDS,
    detect_source_kind,
    parse_source_file,
    stream_snapshot_copy_and_hash,
)
from minx_mcp.finance.normalization import normalize_merchant
from minx_mcp.jobs import mark_completed, mark_failed, mark_running, submit_job
from minx_mcp.money import format_decimal_cents
from minx_mcp.preferences import get_csv_mapping

logger = logging.getLogger(__name__)


class FinanceImportHost(Protocol):
    """Narrow surface used by `run_finance_import` (implemented by `FinanceService`)."""

    @property
    def conn(self) -> Connection: ...

    import_root: Path

    def _account(self, account_name: str) -> Any: ...

    def _insert_batch(self, account_id: int, parsed: ParsedImportBatch) -> int: ...

    def _insert_transaction(
        self, account_id: int, batch_id: int, txn: ParsedTransaction
    ) -> int: ...

    def apply_category_rules(self, batch_id: int | None = None, *, commit: bool = True) -> None: ...

    def _emit_finance_event(
        self,
        *,
        event_type: str,
        entity_ref: str | None,
        payload: dict[str, object],
    ) -> int: ...


def _validate_import_inputs(
    host: FinanceImportHost,
    source_ref: str,
    account_name: str,
    source_kind: str | None,
    mapping: dict[str, object] | None,
) -> tuple[Path, Path, Any, str, dict[str, object] | None]:
    """Validate and resolve common import inputs.

    Returns (resolved_path, canonical_source_path, account, effective_source_kind,
    effective_mapping).  Raises InvalidInputError on any validation failure.
    """
    path = Path(source_ref)
    if not path.is_file():
        raise InvalidInputError("source_ref must point to an existing file")
    resolved_path = path.resolve()
    canonical_source_path = _canonicalize_existing_path(
        resolved_path,
        anchor=host.import_root,
    )
    try:
        canonical_source_path.relative_to(host.import_root)
    except ValueError as exc:
        raise InvalidInputError("source_ref must be inside the allowed import root") from exc
    account = host._account(account_name)
    if source_kind is not None and source_kind not in SUPPORTED_SOURCE_KINDS:
        raise InvalidInputError(f"Unsupported finance source kind: {source_kind}")
    effective_source_kind = source_kind or detect_source_kind(canonical_source_path)
    effective_mapping = mapping
    if effective_source_kind == "generic_csv" and effective_mapping is None:
        profile_name = str(account["import_profile"]) if account["import_profile"] else account_name
        effective_mapping = get_csv_mapping(host.conn, profile_name)
        if effective_mapping is None and profile_name != account_name:
            effective_mapping = get_csv_mapping(host.conn, account_name)
    return resolved_path, canonical_source_path, account, effective_source_kind, effective_mapping


def run_finance_import(
    host: FinanceImportHost,
    source_ref: str,
    account_name: str,
    source_kind: str | None = None,
    mapping: dict[str, object] | None = None,
) -> dict[str, object]:
    resolved_path, canonical_source_path, account, effective_source_kind, effective_mapping = (
        _validate_import_inputs(host, source_ref, account_name, source_kind, mapping)
    )
    if effective_source_kind == "generic_csv" and effective_mapping is None:
        raise InvalidInputError(f"No saved generic CSV mapping for account: {account_name}")
    account_id = int(account["id"])
    canonical_source_ref = str(canonical_source_path)

    fd, snap_tmp = tempfile.mkstemp(
        suffix=resolved_path.suffix,
        prefix="minx_fin_snap_",
    )
    os.close(fd)
    snapshot_path = Path(snap_tmp)
    try:
        content_hash = stream_snapshot_copy_and_hash(resolved_path, snapshot_path)
        idempotency_key = hashlib.sha256(
            f"{account_name}|{canonical_source_ref}|{content_hash}".encode()
        ).hexdigest()
        job = submit_job(
            host.conn, "finance_import", "system", canonical_source_ref, idempotency_key
        )
        if job["status"] in {"completed", "running"}:
            return {"job_id": job["id"], "status": job["status"], "result": job["result"]}

        savepoint_active = False
        try:
            # Parse before opening the write transaction so slow sources (e.g. PDF via
            # subprocess) do not hold RESERVED/PENDING locks on the finance DB for the
            # whole parse window. Job failure handling still runs via this try's except.
            parsed = parse_source_file(
                canonical_source_path,
                account_name,
                effective_source_kind,
                effective_mapping,
                snapshot_path=snapshot_path,
                content_hash=content_hash,
            )

            mark_running(host.conn, str(job["id"]), commit=False)
            host.conn.execute("SAVEPOINT finance_import")
            savepoint_active = True

            batch_id = host._insert_batch(account_id, parsed)
            inserted = 0
            skipped = 0
            total_cents = 0

            for txn in parsed.transactions:
                fingerprint = fingerprint_transaction(account_id, txn)
                existing = host.conn.execute(
                    "SELECT transaction_id FROM finance_transaction_dedupe WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                if existing:
                    skipped += 1
                    continue

                tx_id = host._insert_transaction(account_id, batch_id, txn)
                host.conn.execute(
                    "INSERT INTO finance_transaction_dedupe (fingerprint, transaction_id) VALUES (?, ?)",
                    (fingerprint, tx_id),
                )
                inserted += 1
                total_cents += txn.amount_cents

            host.conn.execute(
                "UPDATE finance_import_batches SET inserted_count = ?, skipped_count = ? WHERE id = ?",
                (inserted, skipped, batch_id),
            )
            host.conn.execute(
                "UPDATE finance_accounts SET last_imported_at = datetime('now') WHERE id = ?",
                (account_id,),
            )
            host.apply_category_rules(batch_id=batch_id, commit=False)
            host._emit_finance_event(
                event_type="finance.transactions_imported",
                entity_ref=str(batch_id),
                payload={
                    "account_name": account_name,
                    "account_id": account_id,
                    "job_id": str(job["id"]),
                    "transaction_count": inserted,
                    "total_cents": total_cents,
                    "source_kind": effective_source_kind,
                },
            )
            host.conn.execute("RELEASE SAVEPOINT finance_import")
            savepoint_active = False

            result: dict[str, object] = {
                "batch_id": batch_id,
                "inserted": inserted,
                "skipped": skipped,
            }
            mark_completed(host.conn, str(job["id"]), result)
            return {"job_id": job["id"], "status": "completed", "result": result}
        except Exception as exc:
            if host.conn.in_transaction:
                if savepoint_active:
                    host.conn.execute("ROLLBACK TO SAVEPOINT finance_import")
                    host.conn.execute("RELEASE SAVEPOINT finance_import")
                else:
                    host.conn.rollback()
            mark_failed(host.conn, str(job["id"]), str(exc))
            raise
    finally:
        snapshot_path.unlink(missing_ok=True)


def preview_finance_import(
    host: FinanceImportHost,
    source_ref: str,
    account_name: str,
    source_kind: str | None = None,
    mapping: dict[str, object] | None = None,
) -> dict[str, object]:
    _resolved_path, canonical_source_path, _account, effective_source_kind, effective_mapping = (
        _validate_import_inputs(host, source_ref, account_name, source_kind, mapping)
    )
    if effective_source_kind == "generic_csv" and effective_mapping is None:
        return {
            "preview": {
                "result_type": "clarify",
                "reason": "missing_mapping",
                "source_kind": effective_source_kind,
            }
        }

    parsed = parse_source_file(
        canonical_source_path,
        account_name,
        effective_source_kind,
        effective_mapping,
    )
    return {
        "preview": {
            "result_type": "preview",
            "source_kind": effective_source_kind,
            "sample_transactions": [
                {
                    "posted_at": txn.posted_at,
                    "description": txn.description,
                    "merchant": normalize_merchant(txn.merchant),
                    "raw_merchant": txn.merchant,
                    "amount": format_decimal_cents(txn.amount_cents),
                }
                for txn in parsed.transactions[:10]
            ],
        }
    }


def _canonicalize_existing_path(path: Path, *, anchor: Path | None = None) -> Path:
    """Resolve path with case-insensitive segments.

    When an anchor is supplied and the path is inside it, only descend through the
    relative subpath rather than scanning from the filesystem root.
    """
    resolved = path.resolve()
    if not resolved.is_absolute():
        return resolved

    current = Path(resolved.anchor)
    parts: tuple[str, ...]

    if anchor is not None:
        anchored_root = anchor.resolve()
        try:
            relative = resolved.relative_to(anchored_root)
        except ValueError:
            return resolved
        else:
            current = anchored_root
            parts = relative.parts
    else:
        parts = tuple(resolved.parts[1:])

    for part in parts:
        actual_part = part
        try:
            for child in current.iterdir():
                if child.name.casefold() == part.casefold():
                    actual_part = child.name
                    break
        except OSError as exc:
            logger.warning(
                "Cannot list directory %s during path canonicalization: %s", current, exc
            )
        current = current / actual_part
    return current
