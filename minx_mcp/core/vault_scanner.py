"""Vault-to-SQLite scanner for Slice 6c durable memory."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from sqlite3 import Connection, IntegrityError

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.memory_fingerprints import memory_content_fingerprint
from minx_mcp.core.memory_secret_scanning import prepare_validated_memory_write
from minx_mcp.core.secret_scanner import scan_for_secrets
from minx_mcp.core.vault_memory_frontmatter import (
    MemoryIdentity,
    optional_str,
    parse_memory_identity,
    parse_memory_payload,
    parse_note_scope,
)
from minx_mcp.time_utils import utc_now_isoformat
from minx_mcp.vault_reader import VaultDocument, VaultReader
from minx_mcp.vault_writer import scan_frontmatter_for_secrets

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VaultScanReport:
    scanned: int
    indexed: int
    updated: int
    unchanged: int
    orphaned: int
    memory_syncs: int
    warnings: list[str]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class _ScanCounts:
    scanned: int = 0
    indexed: int = 0
    updated: int = 0
    unchanged: int = 0
    orphaned: int = 0
    memory_syncs: int = 0


@dataclass(frozen=True)
class _MemorySyncResult:
    """Result of attempting to sync a minx-memory note into SQLite.

    Attributes:
        memory_id: Set when a memory was successfully synced (created or updated).
            None when sync failed or skipped.
        clear_memory_id: True when memory_id should be set to NULL in vault_index
            because the note no longer identifies a live syncable memory. False
            means preserve the existing pointer.
    """

    memory_id: int | None
    clear_memory_id: bool = False


@dataclass(frozen=True)
class _IndexEntry:
    """Subset of ``vault_index`` columns needed by the indexing phase."""

    id: int
    content_hash: str
    memory_id: int | None


class VaultScanner:
    def __init__(
        self,
        conn: Connection,
        vault_reader: VaultReader,
        *,
        scope_prefix: str = "Minx",
    ) -> None:
        self._conn = conn
        self._vault_reader = vault_reader
        self._scope_prefix = scope_prefix

    def scan(self, *, dry_run: bool = False) -> VaultScanReport:
        scan_token = utc_now_isoformat()
        counts = _ScanCounts()
        warnings: list[str] = []

        # Phase 1: pure file walk — no DB writes, no transaction held.
        walk_complete, documents, skipped_paths = self._walk_vault(warnings)

        if dry_run:
            # Compute report against a rolled-back transaction so no state persists.
            started_transaction = not self._conn.in_transaction
            if started_transaction:
                self._conn.execute("BEGIN IMMEDIATE")
            else:
                self._conn.execute("SAVEPOINT vault_scan")
            try:
                self._index_documents(scan_token, documents, counts, warnings)
                self._touch_skipped_paths(scan_token, skipped_paths)
                if walk_complete:
                    self._delete_orphans(scan_token, counts, warnings)
            finally:
                if started_transaction:
                    self._conn.rollback()
                else:
                    self._conn.execute("ROLLBACK TO SAVEPOINT vault_scan")
                    self._conn.execute("RELEASE SAVEPOINT vault_scan")
        else:
            # Phase 2: BEGIN IMMEDIATE, all DB writes, commit.
            started_transaction = not self._conn.in_transaction
            if started_transaction:
                self._conn.execute("BEGIN IMMEDIATE")
            else:
                self._conn.execute("SAVEPOINT vault_scan")
            try:
                self._index_documents(scan_token, documents, counts, warnings)
                self._touch_skipped_paths(scan_token, skipped_paths)
                if walk_complete:
                    self._delete_orphans(scan_token, counts, warnings)
            except Exception:
                if started_transaction:
                    self._conn.rollback()
                else:
                    self._conn.execute("ROLLBACK TO SAVEPOINT vault_scan")
                    self._conn.execute("RELEASE SAVEPOINT vault_scan")
                raise
            else:
                if started_transaction:
                    self._conn.commit()
                else:
                    self._conn.execute("RELEASE SAVEPOINT vault_scan")

        report = VaultScanReport(
            scanned=counts.scanned,
            indexed=counts.indexed,
            updated=counts.updated,
            unchanged=counts.unchanged,
            orphaned=counts.orphaned,
            memory_syncs=counts.memory_syncs,
            warnings=warnings,
        )
        logger.info(
            "vault scan completed",
            extra={
                "scanned": report.scanned,
                "indexed": report.indexed,
                "updated": report.updated,
                "unchanged": report.unchanged,
                "orphaned": report.orphaned,
                "memory_syncs": report.memory_syncs,
                "warning_count": len(report.warnings),
            },
        )
        for warning in warnings:
            logger.warning("vault scan warning: %s", warning[:256])
        return report

    def _walk_vault(
        self,
        warnings: list[str],
    ) -> tuple[bool, list[VaultDocument], set[str]]:
        """Phase 1: read all vault documents without holding any DB lock.

        Per-file read errors warn and skip that file but do not fail the walk;
        only failures enumerating the vault tree itself mark the walk incomplete
        and suppress orphan cleanup.
        """
        documents: list[VaultDocument] = []
        try:
            paths = list(self._vault_reader.iter_markdown_paths(self._scope_prefix))
        except (InvalidInputError, OSError) as exc:
            warnings.append(f"{self._scope_prefix}: vault walk failed: {exc}")
            return False, [], set()
        skipped_paths: set[str] = set()
        for relative_path in paths:
            try:
                documents.append(self._vault_reader.read_document(relative_path))
            except (InvalidInputError, OSError) as exc:
                warnings.append(f"{relative_path}: vault walk skipped: {_sanitize_scan_exception(exc)}")
                skipped_paths.add(relative_path)
        return True, documents, skipped_paths

    def _preload_index(self, documents: list[VaultDocument]) -> dict[str, _IndexEntry]:
        """Fetch ``vault_index`` rows for the current walk in one query.

        Replaces the previous per-document ``SELECT ... WHERE vault_path = ?``,
        which was an O(n) round-trip pattern that dominated scan time for vaults
        in the thousands of files.
        """
        if not documents:
            return {}
        paths = [doc.relative_path for doc in documents]
        # SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999 (pre-3.32) or
        # 32766+ on modern builds, but chunking keeps us safe on older
        # libraries and prevents pathological query sizes.
        chunk_size = 500
        rows_by_path: dict[str, _IndexEntry] = {}
        for start in range(0, len(paths), chunk_size):
            chunk = paths[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            # Safe: IN list length matches chunk; only "?" tokens are interpolated; paths are bound.
            rows = self._conn.execute(
                f"SELECT id, vault_path, content_hash, memory_id FROM vault_index WHERE vault_path IN ({placeholders})",  # noqa: S608
                chunk,
            ).fetchall()
            for row in rows:
                rows_by_path[str(row["vault_path"])] = _IndexEntry(
                    id=int(row["id"]),
                    content_hash=str(row["content_hash"]),
                    memory_id=(int(row["memory_id"]) if row["memory_id"] is not None else None),
                )
        return rows_by_path

    def _index_documents(
        self,
        scan_token: str,
        documents: list[VaultDocument],
        counts: _ScanCounts,
        warnings: list[str],
    ) -> None:
        """Phase 2: write all index rows; must be called inside a transaction."""
        prior_by_path = self._preload_index(documents)
        for doc in documents:
            counts.scanned += 1
            try:
                scan_frontmatter_for_secrets(doc.frontmatter)
            except InvalidInputError:
                warnings.append(f"{doc.relative_path}: secret detected in vault frontmatter; skipped")
                continue
            prior = prior_by_path.get(doc.relative_path)
            note_type = optional_str(doc.frontmatter.get("type"))
            scope = parse_note_scope(doc.frontmatter)
            metadata_json = json.dumps(doc.frontmatter, sort_keys=True)
            memory_id: int | None = None
            changed = prior is None or prior.content_hash != doc.content_hash

            clear_memory_id = False
            if note_type == "minx-memory" and changed:
                sync_result = self._sync_memory_note(doc, warnings)
                memory_id = sync_result.memory_id
                clear_memory_id = sync_result.clear_memory_id
                if memory_id is not None:
                    counts.memory_syncs += 1
            elif prior is not None:
                memory_id = prior.memory_id
                if memory_id is not None and note_type != "minx-memory":
                    warnings.append(
                        f"{doc.relative_path}: note type is not minx-memory; cleared stale vault_index pointer"
                    )
                    clear_memory_id = True
                    if not changed:
                        counts.updated += 1
                        self._conn.execute(
                            """
                            UPDATE vault_index
                            SET last_scanned_at = ?,
                                memory_id = NULL
                            WHERE vault_path = ?
                            """,
                            (scan_token, doc.relative_path),
                        )
                        continue
                elif not changed and memory_id is not None:
                    terminal_status = _terminal_memory_status(self._conn, memory_id)
                    if terminal_status is not None:
                        warnings.append(
                            f"{doc.relative_path}: terminal memory {memory_id} "
                            f"has status={terminal_status}; cleared stale vault_index pointer"
                        )
                        counts.updated += 1
                        self._conn.execute(
                            """
                            UPDATE vault_index
                            SET last_scanned_at = ?,
                                memory_id = NULL
                            WHERE vault_path = ?
                            """,
                            (scan_token, doc.relative_path),
                        )
                        continue

            if prior is None:
                counts.indexed += 1
                self._conn.execute(
                    """
                    INSERT INTO vault_index (
                        vault_path, note_type, scope, content_hash,
                        last_scanned_at, metadata_json, memory_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc.relative_path,
                        note_type,
                        scope,
                        doc.content_hash,
                        scan_token,
                        metadata_json,
                        memory_id,
                    ),
                )
            elif changed:
                counts.updated += 1
                if clear_memory_id:
                    # The current note no longer identifies a live syncable memory.
                    self._conn.execute(
                        """
                        UPDATE vault_index
                        SET note_type = ?,
                            scope = ?,
                            content_hash = ?,
                            last_scanned_at = ?,
                            metadata_json = ?,
                            memory_id = NULL
                        WHERE vault_path = ?
                        """,
                        (
                            note_type,
                            scope,
                            doc.content_hash,
                            scan_token,
                            metadata_json,
                            doc.relative_path,
                        ),
                    )
                else:
                    self._conn.execute(
                        """
                        UPDATE vault_index
                        SET note_type = ?,
                            scope = ?,
                            content_hash = ?,
                            last_scanned_at = ?,
                            metadata_json = ?,
                            memory_id = COALESCE(?, memory_id)
                        WHERE vault_path = ?
                        """,
                        (
                            note_type,
                            scope,
                            doc.content_hash,
                            scan_token,
                            metadata_json,
                            memory_id,
                            doc.relative_path,
                        ),
                    )
            else:
                counts.unchanged += 1
                self._conn.execute(
                    "UPDATE vault_index SET last_scanned_at = ? WHERE vault_path = ?",
                    (scan_token, doc.relative_path),
                )

    def _delete_orphans(
        self,
        scan_token: str,
        counts: _ScanCounts,
        warnings: list[str],
    ) -> None:
        rows = self._conn.execute(
            """
            SELECT vault_path, memory_id
            FROM vault_index
            WHERE last_scanned_at != ?
            ORDER BY vault_path
            """,
            (scan_token,),
        ).fetchall()
        for row in rows:
            counts.orphaned += 1
            memory_id = row["memory_id"]
            if memory_id is not None:
                warnings.append(f"{row['vault_path']}: vault index orphaned for memory_id={int(memory_id)}")
                memory = self._conn.execute(
                    "SELECT status, source FROM memories WHERE id = ?",
                    (int(memory_id),),
                ).fetchone()
                if memory is not None and str(memory["status"]) == "active" and str(memory["source"]) == "vault_sync":
                    self._insert_memory_event(
                        int(memory_id),
                        "vault_synced",
                        {"change": "orphaned", "previous_vault_path": str(row["vault_path"])},
                    )
        self._conn.execute("DELETE FROM vault_index WHERE last_scanned_at != ?", (scan_token,))

    def _touch_skipped_paths(self, scan_token: str, skipped_paths: set[str]) -> None:
        """Prevent known-existing but unreadable files from being orphaned."""
        for relative_path in sorted(skipped_paths):
            self._conn.execute(
                "UPDATE vault_index SET last_scanned_at = ? WHERE vault_path = ?",
                (scan_token, relative_path),
            )

    def _sync_memory_note(self, doc: VaultDocument, warnings: list[str]) -> _MemorySyncResult:
        try:
            identity = parse_memory_identity(doc.frontmatter)
            payload = parse_memory_payload(doc.frontmatter, allow_implicit=True)
            prepared = prepare_validated_memory_write(
                memory_type=identity.memory_type,
                scope=identity.scope,
                subject=identity.subject,
                payload=payload,
                source="vault_sync",
                reason=f"vault sync from {doc.relative_path}",
            )
            payload = prepared.payload
            reason = prepared.reason
            redaction_payload = prepared.redaction_payload
        except InvalidInputError as exc:
            warnings.append(f"{doc.relative_path}: invalid minx-memory frontmatter: {exc}")
            # Current file no longer satisfies the sync contract. Do not leave
            # vault_index pointing at a memory that this note no longer identifies.
            return _MemorySyncResult(memory_id=None, clear_memory_id=True)
        fingerprint = memory_content_fingerprint(
            identity.memory_type,
            payload,
            scope=identity.scope,
            subject=identity.subject,
        )

        row = self._conn.execute(
            """
            SELECT *
            FROM memories
            WHERE memory_type = ? AND scope = ? AND subject = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (identity.memory_type, identity.scope, identity.subject),
        ).fetchone()
        if row is None:
            return _MemorySyncResult(
                memory_id=self._create_vault_memory(
                    doc,
                    identity,
                    payload,
                    fingerprint=fingerprint,
                    reason=reason,
                    redaction_payload=redaction_payload,
                    warnings=warnings,
                )
            )

        status = str(row["status"])
        memory_id = int(row["id"])
        if status in {"rejected", "expired"}:
            warnings.append(f"{doc.relative_path}: terminal memory {memory_id} has status={status}; skipped")
            # Terminal: clear the stale memory_id pointer in vault_index.
            return _MemorySyncResult(memory_id=None, clear_memory_id=True)
        if status == "candidate":
            try:
                cur = self._conn.execute(
                    """
                    UPDATE memories
                    SET status = 'active',
                        confidence = 1.0,
                        payload_json = ?,
                        content_fingerprint = ?,
                        source = 'vault_sync',
                        updated_at = datetime('now'),
                        last_confirmed_at = datetime('now')
                    WHERE id = ? AND status = 'candidate'
                    """,
                    (json.dumps(payload, sort_keys=True), fingerprint, memory_id),
                )
            except IntegrityError as exc:
                _append_content_fingerprint_warning(
                    self._conn,
                    warnings,
                    doc.relative_path,
                    fingerprint,
                    exclude_memory_id=memory_id,
                    exc=exc,
                )
                return _MemorySyncResult(memory_id=None)
            if cur.rowcount == 0:
                warnings.append(
                    f"{doc.relative_path}: concurrent status change on memory_id={memory_id}"
                    f" (expected status=candidate); skipped"
                )
                return _MemorySyncResult(memory_id=None, clear_memory_id=True)
            self._insert_memory_event(memory_id, "confirmed", {"reason": "vault note exists"})
            self._insert_memory_event(
                memory_id,
                "payload_updated",
                _event_payload_with_redaction({"payload": payload}, redaction_payload),
            )
            self._insert_memory_event(
                memory_id,
                "vault_synced",
                _vault_event_payload(doc, "confirm_and_update"),
            )
            return _MemorySyncResult(memory_id=memory_id)

        try:
            cur = self._conn.execute(
                """
                UPDATE memories
                SET payload_json = ?,
                    content_fingerprint = ?,
                    source = CASE WHEN source = '' THEN 'vault_sync' ELSE source END,
                    updated_at = datetime('now')
                WHERE id = ? AND status = 'active'
                """,
                (json.dumps(payload, sort_keys=True), fingerprint, memory_id),
            )
        except IntegrityError as exc:
            _append_content_fingerprint_warning(
                self._conn,
                warnings,
                doc.relative_path,
                fingerprint,
                exclude_memory_id=memory_id,
                exc=exc,
            )
            return _MemorySyncResult(memory_id=None)
        if cur.rowcount == 0:
            warnings.append(
                f"{doc.relative_path}: concurrent status change on memory_id={memory_id}"
                f" (expected status=active); skipped"
            )
            return _MemorySyncResult(memory_id=None, clear_memory_id=True)
        self._insert_memory_event(
            memory_id,
            "payload_updated",
            _event_payload_with_redaction({"payload": payload}, redaction_payload),
        )
        self._insert_memory_event(memory_id, "vault_synced", _vault_event_payload(doc, "update"))
        return _MemorySyncResult(memory_id=memory_id)

    def _create_vault_memory(
        self,
        doc: VaultDocument,
        identity: MemoryIdentity,
        payload: dict[str, object],
        *,
        fingerprint: str,
        reason: str,
        redaction_payload: dict[str, object] | None,
        warnings: list[str],
    ) -> int | None:
        try:
            cur = self._conn.execute(
                """
                INSERT INTO memories (
                    memory_type, scope, subject, confidence, status,
                    payload_json, source, reason, content_fingerprint,
                    created_at, updated_at, last_confirmed_at
                ) VALUES (
                    ?, ?, ?, 1.0, 'active', ?, 'vault_sync', ?, ?,
                    datetime('now'), datetime('now'), datetime('now')
                )
                """,
                (
                    identity.memory_type,
                    identity.scope,
                    identity.subject,
                    json.dumps(payload, sort_keys=True),
                    reason,
                    fingerprint,
                ),
            )
        except IntegrityError as exc:
            _append_content_fingerprint_warning(
                self._conn,
                warnings,
                doc.relative_path,
                fingerprint,
                exclude_memory_id=None,
                exc=exc,
            )
            return None
        if cur.lastrowid is None:
            raise RuntimeError("memories insert did not return a row id")
        memory_id = int(cur.lastrowid)
        self._insert_memory_event(memory_id, "created", redaction_payload or {})
        self._insert_memory_event(memory_id, "promoted", {})
        self._insert_memory_event(memory_id, "vault_synced", _vault_event_payload(doc, "create"))
        return memory_id

    def _insert_memory_event(
        self,
        memory_id: int,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO memory_events (memory_id, event_type, payload_json, actor, created_at)
            VALUES (?, ?, ?, 'vault_sync', datetime('now'))
            """,
            (memory_id, event_type, json.dumps(payload, sort_keys=True)),
        )


def _terminal_memory_status(conn: Connection, memory_id: int) -> str | None:
    row = conn.execute("SELECT status FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        return None
    status = str(row["status"])
    if status in {"rejected", "expired"}:
        return status
    return None


def _append_content_fingerprint_warning(
    conn: Connection,
    warnings: list[str],
    relative_path: str,
    fingerprint: str,
    *,
    exclude_memory_id: int | None,
    exc: IntegrityError,
) -> None:
    row = conn.execute(
        """
        SELECT id
        FROM memories
        WHERE content_fingerprint = ?
          AND status IN ('candidate', 'active')
          AND (? IS NULL OR id != ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (fingerprint, exclude_memory_id, exclude_memory_id),
    ).fetchone()
    if row is None:
        raise exc
    warnings.append(
        f"{relative_path}: content_fingerprint conflicts with live memory_id={int(row['id'])}; skipped"
    )


def _sanitize_scan_exception(exc: Exception) -> str:
    message = str(exc)
    if scan_for_secrets(message).findings:
        return "error contained secret-shaped content"
    return message


def _event_payload_with_redaction(
    payload: dict[str, object],
    redaction_payload: dict[str, object] | None,
) -> dict[str, object]:
    if redaction_payload is None:
        return payload
    return {**payload, **redaction_payload}


def _vault_event_payload(doc: VaultDocument, change: str) -> dict[str, object]:
    return {
        "vault_path": doc.relative_path,
        "content_hash": doc.content_hash,
        "change": change,
    }
