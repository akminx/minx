"""Vault-to-SQLite scanner for Slice 6c durable memory."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from sqlite3 import Connection
from typing import Any

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.memory_payloads import validate_memory_payload
from minx_mcp.core.memory_service import MemoryService
from minx_mcp.core.vault_memory_frontmatter import RESERVED_MEMORY_KEYS
from minx_mcp.time_utils import utc_now_isoformat
from minx_mcp.vault_reader import VaultDocument, VaultReader

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


class VaultScanner:
    def __init__(
        self,
        conn: Connection,
        vault_reader: VaultReader,
        memory_service: MemoryService,
        *,
        scope_prefix: str = "Minx",
    ) -> None:
        self._conn = conn
        self._vault_reader = vault_reader
        self._memory_service = memory_service
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
                warnings.append(f"{relative_path}: vault walk skipped: {exc}")
                skipped_paths.add(relative_path)
        return True, documents, skipped_paths

    def _index_documents(
        self,
        scan_token: str,
        documents: list[VaultDocument],
        counts: _ScanCounts,
        warnings: list[str],
    ) -> None:
        """Phase 2: write all index rows; must be called inside a transaction."""
        for doc in documents:
            counts.scanned += 1
            prior = self._conn.execute(
                "SELECT id, content_hash, memory_id FROM vault_index WHERE vault_path = ?",
                (doc.relative_path,),
            ).fetchone()
            note_type = _optional_str(doc.frontmatter.get("type"))
            scope = _parse_note_scope(doc.frontmatter)
            metadata_json = json.dumps(doc.frontmatter, sort_keys=True)
            memory_id: int | None = None
            changed = prior is None or str(prior["content_hash"]) != doc.content_hash

            clear_memory_id = False
            if note_type == "minx-memory" and changed:
                sync_result = self._sync_memory_note(doc, warnings)
                memory_id = sync_result.memory_id
                clear_memory_id = sync_result.clear_memory_id
                if memory_id is not None:
                    counts.memory_syncs += 1
            elif prior is not None:
                memory_id = int(prior["memory_id"]) if prior["memory_id"] is not None else None
                if memory_id is not None and note_type != "minx-memory":
                    warnings.append(
                        f"{doc.relative_path}: note type is not minx-memory; "
                        "cleared stale vault_index pointer"
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
                warnings.append(
                    f"{row['vault_path']}: vault index orphaned for memory_id={int(memory_id)}"
                )
                memory = self._conn.execute(
                    "SELECT status, source FROM memories WHERE id = ?",
                    (int(memory_id),),
                ).fetchone()
                if (
                    memory is not None
                    and str(memory["status"]) == "active"
                    and str(memory["source"]) == "vault_sync"
                ):
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
            scope, memory_type, subject = _parse_memory_identity(doc.frontmatter)
            payload = _parse_memory_payload(doc.frontmatter)
            payload = validate_memory_payload(memory_type, payload)
        except InvalidInputError as exc:
            warnings.append(f"{doc.relative_path}: invalid minx-memory frontmatter: {exc}")
            # Current file no longer satisfies the sync contract. Do not leave
            # vault_index pointing at a memory that this note no longer identifies.
            return _MemorySyncResult(memory_id=None, clear_memory_id=True)

        row = self._conn.execute(
            """
            SELECT *
            FROM memories
            WHERE memory_type = ? AND scope = ? AND subject = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (memory_type, scope, subject),
        ).fetchone()
        if row is None:
            return _MemorySyncResult(
                memory_id=self._create_vault_memory(doc, memory_type, scope, subject, payload)
            )

        status = str(row["status"])
        memory_id = int(row["id"])
        if status in {"rejected", "expired"}:
            warnings.append(
                f"{doc.relative_path}: terminal memory {memory_id} has status={status}; skipped"
            )
            # Terminal: clear the stale memory_id pointer in vault_index.
            return _MemorySyncResult(memory_id=None, clear_memory_id=True)
        if status == "candidate":
            cur = self._conn.execute(
                """
                UPDATE memories
                SET status = 'active',
                    confidence = 1.0,
                    payload_json = ?,
                    source = 'vault_sync',
                    updated_at = datetime('now'),
                    last_confirmed_at = datetime('now')
                WHERE id = ? AND status = 'candidate'
                """,
                (json.dumps(payload, sort_keys=True), memory_id),
            )
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
                {"payload": payload},
            )
            self._insert_memory_event(
                memory_id,
                "vault_synced",
                _vault_event_payload(doc, "confirm_and_update"),
            )
            return _MemorySyncResult(memory_id=memory_id)

        cur = self._conn.execute(
            """
            UPDATE memories
            SET payload_json = ?,
                source = CASE WHEN source = '' THEN 'vault_sync' ELSE source END,
                updated_at = datetime('now')
            WHERE id = ? AND status = 'active'
            """,
            (json.dumps(payload, sort_keys=True), memory_id),
        )
        if cur.rowcount == 0:
            warnings.append(
                f"{doc.relative_path}: concurrent status change on memory_id={memory_id}"
                f" (expected status=active); skipped"
            )
            return _MemorySyncResult(memory_id=None, clear_memory_id=True)
        self._insert_memory_event(memory_id, "payload_updated", {"payload": payload})
        self._insert_memory_event(memory_id, "vault_synced", _vault_event_payload(doc, "update"))
        return _MemorySyncResult(memory_id=memory_id)

    def _create_vault_memory(
        self,
        doc: VaultDocument,
        memory_type: str,
        scope: str,
        subject: str,
        payload: dict[str, object],
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO memories (
                memory_type, scope, subject, confidence, status,
                payload_json, source, reason, created_at, updated_at, last_confirmed_at
            ) VALUES (?, ?, ?, 1.0, 'active', ?, 'vault_sync', ?, datetime('now'), datetime('now'), datetime('now'))
            """,
            (
                memory_type,
                scope,
                subject,
                json.dumps(payload, sort_keys=True),
                f"vault sync from {doc.relative_path}",
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("memories insert did not return a row id")
        memory_id = int(cur.lastrowid)
        self._insert_memory_event(memory_id, "created", {})
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


def _parse_memory_identity(frontmatter: dict[str, object]) -> tuple[str, str, str]:
    scope = _parse_note_scope(frontmatter, strict_alias_match=True, required=True)
    memory_type = _required_str(frontmatter, "memory_type")
    memory_key = _required_str(frontmatter, "memory_key")
    parts = memory_key.split(".", 2)
    if len(parts) != 3:
        raise InvalidInputError("memory_key must have format {scope}.{memory_type}.{subject}")
    key_scope, key_memory_type, key_subject = (p.strip() for p in parts)
    if key_scope != scope:
        raise InvalidInputError("memory_key scope does not match note scope")
    if key_memory_type != memory_type:
        raise InvalidInputError("memory_key memory_type does not match memory_type")
    subject = _optional_str(frontmatter.get("subject"))
    if subject is not None and subject.strip() != key_subject:
        raise InvalidInputError("subject does not match memory_key")
    if not key_subject:
        raise InvalidInputError("subject is required")
    return scope, memory_type, key_subject


def _parse_memory_payload(frontmatter: dict[str, object]) -> dict[str, object]:
    raw_payload = frontmatter.get("payload_json", frontmatter.get("value_json"))
    if raw_payload is not None:
        if isinstance(raw_payload, dict):
            return dict(raw_payload)
        if isinstance(raw_payload, str):
            try:
                parsed = json.loads(raw_payload)
            except json.JSONDecodeError as exc:
                raise InvalidInputError("payload_json must be valid JSON") from exc
            if not isinstance(parsed, dict):
                raise InvalidInputError("payload_json must be a JSON object")
            return dict(parsed)
        raise InvalidInputError("payload_json must be a JSON object or JSON string")
    return {str(k): v for k, v in frontmatter.items() if k not in RESERVED_MEMORY_KEYS}


def _required_str(
    frontmatter: dict[str, object],
    key: str,
    *,
    fallback_key: str | None = None,
) -> str:
    value = _optional_str(frontmatter.get(key))
    if value is None and fallback_key is not None:
        value = _optional_str(frontmatter.get(fallback_key))
    if value is None or not value.strip():
        raise InvalidInputError(f"{key} is required")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _parse_note_scope(
    frontmatter: dict[str, object],
    *,
    strict_alias_match: bool = False,
    required: bool = False,
) -> str | None:
    scope = _optional_str(frontmatter.get("scope"))
    domain = _optional_str(frontmatter.get("domain"))
    scope_text = scope.strip() if scope is not None else ""
    domain_text = domain.strip() if domain is not None else ""

    if scope_text:
        if strict_alias_match and domain_text and domain_text != scope_text:
            raise InvalidInputError("scope and domain must match")
        return scope_text
    if domain_text:
        return domain_text
    if not required:
        return None
    raise InvalidInputError("scope is required")


def _terminal_memory_status(conn: Connection, memory_id: int) -> str | None:
    row = conn.execute("SELECT status FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        return None
    status = str(row["status"])
    if status in {"rejected", "expired"}:
        return status
    return None


def _vault_event_payload(doc: VaultDocument, change: str) -> dict[str, object]:
    return {
        "vault_path": doc.relative_path,
        "content_hash": doc.content_hash,
        "change": change,
    }
