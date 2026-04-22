"""Backfill ``memories.content_fingerprint`` for rows that predate Slice 6g.

Run once after pulling Slice 6g. Rows written by migrations 001-019 have
``content_fingerprint = NULL`` because the column did not exist. Post-Slice-6g
write paths emit fingerprints on insert and update, but historical rows still
need one-time retrofill so they participate in ``ingest_proposals``'
content-equivalence dedup lookup and rejected-prior check (§7.2 of the
Slice 6g spec).

Usage:
    python -m scripts.backfill_memory_fingerprints [DB_PATH] [--force]

``DB_PATH`` defaults to ``MINX_DB_PATH`` from the environment (or the default
settings path if unset). The operation is idempotent: re-running is a no-op
for rows that already have a valid fingerprint.

Two-pass algorithm (spec §8.4):
  Pass 1 — read all live rows + all terminal rows under a single
           ``BEGIN IMMEDIATE`` lock, recompute every row's fingerprint
           **from payload_json** (never trust the stored value for
           bucketing), and bucket by the recomputed hash.
  Pass 2 — within the same transaction, write new fingerprints for
           buckets with no live-vs-live collision; skip the writes for
           collision buckets and record them in the summary.

Exits 0 on a clean run. Exits 2 if either (a) any live-vs-live collision
buckets were recorded — the operator must resolve them manually via
reject/expire before re-running — or (b) any row's stored
``content_fingerprint`` disagrees with the value recomputed from
``payload_json`` and ``--force`` was not passed (stale fingerprint, likely
from a pre-Slice-6g manual edit or a prior partial backfill); re-run with
``--force`` to overwrite those stale values. Both failure modes are logged
with row IDs so the operator can inspect specific rows before retrying.

The full two-pass algorithm is wrapped in ``try / except BaseException``
so ``KeyboardInterrupt`` (Ctrl-C) rolls back cleanly and releases the
writer lock. SIGKILL cannot run Python cleanup; SQLite WAL replay
handles that on the next connection open.

**Do not** call any MCP tool that writes to ``memories`` (including the
daily snapshot pipeline) while the backfill is running — it holds the
SQLite writer lock for the full pass.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import TypedDict

from minx_mcp.config import get_settings
from minx_mcp.core.fingerprint import content_fingerprint
from minx_mcp.core.memory_payloads import coerce_prior_payload_to_schema
from minx_mcp.core.memory_service import _memory_fingerprint_input
from minx_mcp.db import get_connection

logger = logging.getLogger("minx.backfill.memory_fingerprints")

# Spec §8.1: log one info line per 500 rows processed during Pass 1.
# Chunking affects only progress logging, not transaction boundaries.
_PROGRESS_CHUNK_SIZE = 500


class _Record(TypedDict):
    id: int
    memory_type: str
    scope: str
    subject: str
    status: str
    computed_fp: str
    stored_fp: str | None


class _Collision(TypedDict):
    fingerprint: str
    row_ids: list[int]
    memory_type: str
    scope: str
    subject: str


def _compute_fingerprint_for_row(
    memory_type: str,
    scope: str,
    subject: str,
    payload_json: str,
) -> str:
    """Coerce the row's payload then fingerprint it.

    Coercion errors degrade gracefully to the §5.2 degraded-dedup path
    (``content_fingerprint(memory_type, scope, subject, "", "")``), so a
    single corrupt row cannot abort the backfill.
    """
    try:
        raw_payload = json.loads(payload_json) if payload_json else {}
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        coerced = coerce_prior_payload_to_schema(memory_type, raw_payload)
    except Exception:
        coerced = {}

    try:
        parts = _memory_fingerprint_input(
            memory_type,
            coerced,
            scope=scope,
            subject=subject,
        )
    except Exception:
        parts = (memory_type, scope, subject, "", "")

    return content_fingerprint(*parts)


def _run_backfill(conn: sqlite3.Connection, *, force: bool) -> int:
    """Execute the two-pass backfill. Returns 0 (clean) or 2 (collisions)."""
    start = time.perf_counter()
    logger.info("backfill: acquiring writer lock (BEGIN IMMEDIATE)")
    conn.execute("BEGIN IMMEDIATE")
    try:
        records: list[_Record] = []

        def _collect(select_sql: str, phase: str) -> None:
            """Pass-1 helper: read rows, compute fingerprints, log progress.

            Emits one ``INFO`` line per :data:`_PROGRESS_CHUNK_SIZE` rows
            processed so an operator watching a long backfill on a large
            DB can tell the job is making progress (spec §8.1 progress
            logging). Chunking is cosmetic only — it does not break the
            single transaction.
            """
            for processed, row in enumerate(conn.execute(select_sql), start=1):
                computed = _compute_fingerprint_for_row(
                    memory_type=str(row["memory_type"]),
                    scope=str(row["scope"]),
                    subject=str(row["subject"]),
                    payload_json=str(row["payload_json"] or ""),
                )
                stored = row["content_fingerprint"]
                records.append(
                    _Record(
                        id=int(row["id"]),
                        memory_type=str(row["memory_type"]),
                        scope=str(row["scope"]),
                        subject=str(row["subject"]),
                        status=str(row["status"]),
                        computed_fp=computed,
                        stored_fp=None if stored is None else str(stored),
                    )
                )
                if processed % _PROGRESS_CHUNK_SIZE == 0:
                    logger.info(
                        "backfill: %s pass — processed %d rows (elapsed=%.3fs)",
                        phase,
                        processed,
                        time.perf_counter() - start,
                    )

        _collect(
            """
            SELECT id, memory_type, scope, subject, status, payload_json, content_fingerprint
            FROM memories
            WHERE status IN ('candidate', 'active')
            ORDER BY id ASC
            """,
            phase="live",
        )
        _collect(
            """
            SELECT id, memory_type, scope, subject, status, payload_json, content_fingerprint
            FROM memories
            WHERE status IN ('rejected', 'expired')
            ORDER BY id ASC
            """,
            phase="terminal",
        )

        if not records:
            conn.commit()
            elapsed = time.perf_counter() - start
            logger.info("backfill: no memories rows present (elapsed=%.3fs)", elapsed)
            return 0

        # Bucket by recomputed fingerprint (ground truth).
        buckets: dict[str, list[_Record]] = defaultdict(list)
        for rec in records:
            buckets[rec["computed_fp"]].append(rec)

        collisions: list[_Collision] = []
        writes: list[tuple[str, int]] = []
        skipped_stale_without_force: list[int] = []

        for fp, bucket in buckets.items():
            live_in_bucket = [r for r in bucket if r["status"] in {"candidate", "active"}]
            if len(live_in_bucket) >= 2:
                # Live-vs-live collision: mark all rows in the bucket as
                # "do not write this run".
                first = live_in_bucket[0]
                collisions.append(
                    _Collision(
                        fingerprint=fp,
                        row_ids=sorted(r["id"] for r in live_in_bucket),
                        memory_type=first["memory_type"],
                        scope=first["scope"],
                        subject=first["subject"],
                    )
                )
                continue

            # Safe bucket — apply per-row write decision.
            for rec in bucket:
                stored = rec["stored_fp"]
                if stored is None:
                    writes.append((fp, rec["id"]))
                elif stored == fp:
                    continue
                else:
                    if force:
                        writes.append((fp, rec["id"]))
                    else:
                        skipped_stale_without_force.append(rec["id"])

        rows_written = 0
        for fp, row_id in writes:
            conn.execute(
                "UPDATE memories SET content_fingerprint = ? WHERE id = ?",
                (fp, row_id),
            )
            rows_written += 1

        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise

    elapsed = time.perf_counter() - start
    logger.info(
        "backfill: rows_scanned=%d rows_written=%d "
        "collisions=%d stale_skipped=%d elapsed=%.3fs",
        len(records),
        rows_written,
        len(collisions),
        len(skipped_stale_without_force),
        elapsed,
    )

    if collisions:
        for c in collisions:
            logger.warning(
                "collision: fingerprint=%s memory_type=%s scope=%s subject=%s row_ids=%s",
                c["fingerprint"],
                c["memory_type"],
                c["scope"],
                c["subject"],
                c["row_ids"],
            )
        logger.warning(
            "backfill: %d collision(s) detected — resolve by rejecting/"
            "expiring duplicates via MCP, then re-run. Exit code 2.",
            len(collisions),
        )
        return 2

    if skipped_stale_without_force:
        logger.warning(
            "backfill: %d row(s) have stale stored fingerprints that do "
            "not match payload_json. Re-run with --force to overwrite. "
            "Skipped row_ids=%s",
            len(skipped_stale_without_force),
            skipped_stale_without_force,
        )
        return 2

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "db_path",
        nargs="?",
        type=Path,
        help="SQLite database path (defaults to MINX_DB_PATH / settings default).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite existing content_fingerprint values that disagree with "
            "what payload_json would produce. Default is to skip such rows "
            "and exit non-zero so the operator can investigate."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity (default: INFO).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db_path: Path = args.db_path if args.db_path else get_settings().db_path
    logger.info("backfill: db_path=%s force=%s", db_path, args.force)

    conn = get_connection(db_path)
    try:
        return _run_backfill(conn, force=args.force)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
