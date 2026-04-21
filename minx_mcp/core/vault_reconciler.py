"""Bounded vault-frontmatter reconciliation for Slice 6f memory notes.

The reconciler walks the vault, and for each ``type: minx-memory`` note:

1. Parses identity + payload (via the shared frontmatter parser).
2. Acquires the per-file vault write lock.
3. **Stages** the new canonical frontmatter bytes on disk (temp file).
4. Opens a DB transaction and mutates ``memories`` + ``memory_events``.
5. Upserts ``vault_index`` with the pre-computed content hash of the staged
   bytes.
6. Commits the DB transaction.
7. Atomically publishes the staged bytes by rename.

If any step up to and including the DB commit fails, the staged temp file is
aborted and the vault file is untouched. This eliminates the prior
split-brain risk where the file could be written but the DB rolled back.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection, Row

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.memory_payloads import validate_memory_payload
from minx_mcp.core.vault_memory_frontmatter import (
    MemoryIdentity,
    optional_str,
    parse_memory_identity,
    parse_memory_payload,
    parse_optional_int,
)
from minx_mcp.vault_reader import VaultDocument, VaultReader
from minx_mcp.vault_writer import StagedVaultWrite, VaultWriter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VaultReconcileWarning:
    kind: str
    vault_path: str
    message: str
    memory_id: int | None = None
    memory_key: str | None = None
    db_updated_at: str | None = None
    sync_base_updated_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class VaultReconcileReport:
    scanned: int
    applied: int
    created: int
    confirmed: int
    updated: int
    skipped: int
    conflicts: int
    warnings: list[VaultReconcileWarning]

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["warnings"] = [warning.as_dict() for warning in self.warnings]
        return data


@dataclass
class _ReconcileCounts:
    scanned: int = 0
    created: int = 0
    confirmed: int = 0
    updated: int = 0
    skipped: int = 0
    conflicts: int = 0

    @property
    def applied(self) -> int:
        return self.created + self.confirmed + self.updated


@dataclass(frozen=True)
class _ApplyResult:
    outcome: str
    memory_id: int
    updated_at: str
    payload: dict[str, object]
    warning: VaultReconcileWarning | None = None


class VaultReconciler:
    def __init__(
        self,
        conn: Connection,
        vault_reader: VaultReader,
        vault_writer: VaultWriter,
        *,
        scope_prefix: str = "Minx",
    ) -> None:
        self._conn = conn
        self._vault_reader = vault_reader
        self._vault_writer = vault_writer
        self._scope_prefix = scope_prefix

    def reconcile(self, *, dry_run: bool = False) -> VaultReconcileReport:
        counts = _ReconcileCounts()
        warnings: list[VaultReconcileWarning] = []
        try:
            paths = list(self._vault_reader.iter_markdown_paths(self._scope_prefix))
        except (InvalidInputError, OSError) as exc:
            warning = VaultReconcileWarning(
                kind="walk_failed",
                vault_path=self._scope_prefix,
                message=f"vault walk failed: {exc}",
            )
            return VaultReconcileReport(
                scanned=0,
                applied=0,
                created=0,
                confirmed=0,
                updated=0,
                skipped=0,
                conflicts=0,
                warnings=[warning],
            )

        for relative_path in paths:
            try:
                doc = self._vault_reader.read_document(relative_path)
            except (InvalidInputError, OSError) as exc:
                counts.scanned += 1
                _skip_with_warning(
                    counts,
                    warnings,
                    VaultReconcileWarning(
                        kind="invalid_note",
                        vault_path=relative_path,
                        message=f"vault note could not be read: {exc}",
                    ),
                )
                continue
            if optional_str(doc.frontmatter.get("type")) != "minx-memory":
                continue
            counts.scanned += 1
            self._reconcile_one(doc, counts, warnings, dry_run=dry_run)

        report = VaultReconcileReport(
            scanned=counts.scanned,
            applied=counts.applied,
            created=counts.created,
            confirmed=counts.confirmed,
            updated=counts.updated,
            skipped=counts.skipped,
            conflicts=counts.conflicts,
            warnings=warnings,
        )
        logger.info(
            "vault reconcile completed",
            extra={
                "scanned": report.scanned,
                "applied": report.applied,
                "created_count": report.created,
                "confirmed_count": report.confirmed,
                "updated_count": report.updated,
                "skipped_count": report.skipped,
                "conflict_count": report.conflicts,
                "warning_count": len(report.warnings),
            },
        )
        return report

    def _reconcile_one(
        self,
        doc: VaultDocument,
        counts: _ReconcileCounts,
        warnings: list[VaultReconcileWarning],
        *,
        dry_run: bool,
    ) -> None:
        try:
            identity = parse_memory_identity(doc.frontmatter)
            payload = parse_memory_payload(
                doc.frontmatter,
                allow_implicit=identity.memory_id is None,
            )
            payload = validate_memory_payload(identity.memory_type, payload)
        except InvalidInputError as exc:
            _skip_with_warning(
                counts,
                warnings,
                VaultReconcileWarning(
                    kind="invalid_note",
                    vault_path=doc.relative_path,
                    message=str(exc),
                ),
            )
            return

        if dry_run:
            self._reconcile_one_dry_run(doc, identity, payload, counts, warnings)
            return

        self._reconcile_one_apply(doc, identity, payload, counts, warnings)

    def _reconcile_one_dry_run(
        self,
        doc: VaultDocument,
        identity: MemoryIdentity,
        payload: dict[str, object],
        counts: _ReconcileCounts,
        warnings: list[VaultReconcileWarning],
    ) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            result = self._apply_db_side(doc, identity, payload)
            if result.warning is not None:
                warnings.append(result.warning)
                if result.warning.kind == "conflict":
                    counts.conflicts += 1
            _count_outcome(counts, result.outcome)
        except _SkipNoteError as exc:
            _skip_with_warning(counts, warnings, exc.warning)
        finally:
            if self._conn.in_transaction:
                self._conn.rollback()

    def _reconcile_one_apply(
        self,
        doc: VaultDocument,
        identity: MemoryIdentity,
        payload: dict[str, object],
        counts: _ReconcileCounts,
        warnings: list[VaultReconcileWarning],
    ) -> None:
        # Step 1: stage vault write (hold lock, prepare temp file). This must
        # happen before opening the DB transaction so the lock wait doesn't
        # block with an open transaction.
        canonical_preview = _canonical_frontmatter(
            identity,
            memory_id=identity.memory_id if identity.memory_id is not None else 0,
            payload=payload,
            updated_at=identity.sync_base_updated_at or "",
        )
        refresh_needed_hint = not _frontmatter_equals_expected(doc.frontmatter, canonical_preview)

        staged: StagedVaultWrite | None = None

        try:
            # Step 2: DB mutations inside a transaction.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                result = self._apply_db_side(doc, identity, payload)
                if result.warning is not None:
                    warnings.append(result.warning)
                    if result.warning.kind == "conflict":
                        counts.conflicts += 1

                canonical = _canonical_frontmatter(
                    identity,
                    memory_id=result.memory_id,
                    payload=result.payload,
                    updated_at=result.updated_at,
                )
                skip_write = _frontmatter_refresh_not_needed(doc.frontmatter, identity, result) or (
                    not refresh_needed_hint and _frontmatter_equals_expected(doc.frontmatter, canonical)
                )

                if skip_write:
                    resolved = self._vault_writer.resolve_path(doc.relative_path)
                    content_hash = _sha256_file(resolved)
                else:
                    try:
                        staged = self._vault_writer.stage_replace_frontmatter(doc.relative_path, canonical)
                    except Exception as exc:
                        raise _SkipNoteError(
                            VaultReconcileWarning(
                                kind="write_failed",
                                vault_path=doc.relative_path,
                                message=f"frontmatter refresh failed: {exc}",
                                memory_id=result.memory_id,
                                memory_key=identity.memory_key,
                            )
                        ) from exc
                    resolved = staged.target
                    content_hash = staged.content_hash

                self._upsert_vault_index(
                    doc,
                    resolved,
                    canonical,
                    result.memory_id,
                    identity.scope,
                    content_hash=content_hash,
                )
                self._conn.commit()
            except _SkipNoteError as exc:
                if self._conn.in_transaction:
                    self._conn.rollback()
                if staged is not None:
                    staged.abort()
                    staged = None
                _skip_with_warning(counts, warnings, exc.warning)
                return
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                if staged is not None:
                    staged.abort()
                    staged = None
                raise

            # Step 3: DB has committed. Atomically publish the staged vault
            # bytes. If the rename fails here the DB is ahead of the vault by
            # one reconcile cycle; the next run converges because the note on
            # disk still parses but has stale sync_base_updated_at.
            if staged is not None:
                try:
                    staged.commit()
                except Exception as exc:
                    logger.exception(
                        "vault rename failed after DB commit; will self-heal on next reconcile",
                        extra={
                            "vault_path": doc.relative_path,
                            "memory_id": result.memory_id,
                        },
                    )
                    warnings.append(
                        VaultReconcileWarning(
                            kind="write_failed",
                            vault_path=doc.relative_path,
                            message=(f"vault publish failed after DB commit; next reconcile will retry: {exc}"),
                            memory_id=result.memory_id,
                            memory_key=identity.memory_key,
                        )
                    )
                    # DB state was still committed; count the outcome.
                finally:
                    staged = None
            _count_outcome(counts, result.outcome)
        finally:
            if staged is not None:
                staged.abort()

    def _apply_db_side(
        self,
        doc: VaultDocument,
        identity: MemoryIdentity,
        payload: dict[str, object],
    ) -> _ApplyResult:
        row = self._resolve_row(identity, doc.relative_path)
        if row is None:
            return self._create_memory(doc, identity, payload)

        status = str(row["status"])
        memory_id = int(row["id"])
        if status in {"rejected", "expired"}:
            raise _SkipNoteError(
                VaultReconcileWarning(
                    kind="terminal_state",
                    vault_path=doc.relative_path,
                    message=(f"memory {memory_id} is {status}; vault edits do not resurrect terminal memories"),
                    memory_id=memory_id,
                    memory_key=identity.memory_key,
                )
            )
        if status == "candidate":
            self._check_candidate_conflict(row, identity, doc.relative_path)
            return self._confirm_candidate(doc, identity, row, payload)

        self._check_active_conflict(row, identity, doc.relative_path)
        prior_payload = _parse_payload_json(str(row["payload_json"]))
        if _canonical_payload_json(prior_payload) == _canonical_payload_json(payload):
            return _ApplyResult(
                outcome="skipped",
                memory_id=memory_id,
                updated_at=str(row["updated_at"]),
                payload=prior_payload,
            )
        cur = self._conn.execute(
            """
            UPDATE memories
            SET payload_json = ?, updated_at = datetime('now')
            WHERE id = ? AND status = 'active'
            """,
            (_canonical_payload_json(payload), memory_id),
        )
        if cur.rowcount != 1:
            raise _SkipNoteError(
                VaultReconcileWarning(
                    kind="conflict",
                    vault_path=doc.relative_path,
                    message="memory status changed before active payload update",
                    memory_id=memory_id,
                    memory_key=identity.memory_key,
                )
            )
        self._insert_memory_event(memory_id, "payload_updated", {"payload": payload})
        self._insert_memory_event(memory_id, "vault_synced", _vault_event_payload(doc, "update"))
        updated = self._get_memory_row(memory_id)
        return _ApplyResult(
            outcome="updated",
            memory_id=memory_id,
            updated_at=str(updated["updated_at"]),
            payload=payload,
        )

    def _resolve_row(self, identity: MemoryIdentity, vault_path: str) -> Row | None:
        if identity.memory_id is not None:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id = ?",
                (identity.memory_id,),
            ).fetchone()
            if row is None:
                raise _SkipNoteError(
                    VaultReconcileWarning(
                        kind="missing_memory",
                        vault_path=vault_path,
                        message=f"memory_id {identity.memory_id} does not exist",
                        memory_id=identity.memory_id,
                        memory_key=identity.memory_key,
                    )
                )
            _require_identity_match(row, identity, vault_path)
            return row

        live = self._conn.execute(
            """
            SELECT *
            FROM memories
            WHERE memory_type = ? AND scope = ? AND subject = ?
              AND status IN ('candidate', 'active')
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (identity.memory_type, identity.scope, identity.subject),
        ).fetchone()
        if live is not None:
            return live

        terminal = self._conn.execute(
            """
            SELECT *
            FROM memories
            WHERE memory_type = ? AND scope = ? AND subject = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (identity.memory_type, identity.scope, identity.subject),
        ).fetchone()
        if terminal is not None and str(terminal["status"]) in {"rejected", "expired"}:
            return terminal
        return None

    def _check_active_conflict(
        self,
        row: Row,
        identity: MemoryIdentity,
        vault_path: str,
    ) -> None:
        db_updated_at = str(row["updated_at"])
        if identity.sync_base_updated_at is not None:
            if db_updated_at != identity.sync_base_updated_at:
                raise _SkipNoteError(_conflict_warning(row, identity, vault_path))
            return
        if identity.memory_id is not None:
            raise _SkipNoteError(
                VaultReconcileWarning(
                    kind="conflict",
                    vault_path=vault_path,
                    message="sync_base_updated_at is required when updating an active memory by memory_id",
                    memory_id=int(row["id"]),
                    memory_key=identity.memory_key,
                    db_updated_at=db_updated_at,
                )
            )
        if str(row["source"]) != "vault_sync":
            raise _SkipNoteError(
                VaultReconcileWarning(
                    kind="conflict",
                    vault_path=vault_path,
                    message="vault-authored note without sync_base_updated_at cannot update non-vault memory",
                    memory_id=int(row["id"]),
                    memory_key=identity.memory_key,
                    db_updated_at=db_updated_at,
                )
            )

    def _check_candidate_conflict(
        self,
        row: Row,
        identity: MemoryIdentity,
        vault_path: str,
    ) -> None:
        if identity.sync_base_updated_at is None:
            return
        if str(row["updated_at"]) != identity.sync_base_updated_at:
            raise _SkipNoteError(_conflict_warning(row, identity, vault_path))

    def _create_memory(
        self,
        doc: VaultDocument,
        identity: MemoryIdentity,
        payload: dict[str, object],
    ) -> _ApplyResult:
        cur = self._conn.execute(
            """
            INSERT INTO memories (
                memory_type, scope, subject, confidence, status,
                payload_json, source, reason, created_at, updated_at, last_confirmed_at
            ) VALUES (?, ?, ?, 1.0, 'active', ?, 'vault_sync', ?, datetime('now'), datetime('now'), datetime('now'))
            """,
            (
                identity.memory_type,
                identity.scope,
                identity.subject,
                _canonical_payload_json(payload),
                f"vault reconcile from {doc.relative_path}",
            ),
        )
        if cur.lastrowid is None:
            raise _SkipNoteError(
                VaultReconcileWarning(
                    kind="write_failed",
                    vault_path=doc.relative_path,
                    message="memory insert did not return a row id",
                    memory_key=identity.memory_key,
                )
            )
        memory_id = int(cur.lastrowid)
        self._insert_memory_event(memory_id, "created", {})
        self._insert_memory_event(memory_id, "promoted", {})
        self._insert_memory_event(memory_id, "vault_synced", _vault_event_payload(doc, "create"))
        row = self._get_memory_row(memory_id)
        return _ApplyResult(
            outcome="created",
            memory_id=memory_id,
            updated_at=str(row["updated_at"]),
            payload=payload,
        )

    def _confirm_candidate(
        self,
        doc: VaultDocument,
        identity: MemoryIdentity,
        row: Row,
        payload: dict[str, object],
    ) -> _ApplyResult:
        memory_id = int(row["id"])
        prior_payload = _parse_payload_json(str(row["payload_json"]))
        payload_changed = _canonical_payload_json(prior_payload) != _canonical_payload_json(payload)
        stale_candidate = identity.sync_base_updated_at is None and _row_updated_after_note_mtime(
            row, self._vault_writer.resolve_path(doc.relative_path)
        )
        cur = self._conn.execute(
            """
            UPDATE memories
            SET status = 'active',
                confidence = 1.0,
                payload_json = ?,
                updated_at = datetime('now'),
                last_confirmed_at = datetime('now')
            WHERE id = ? AND status = 'candidate'
            """,
            (_canonical_payload_json(payload), memory_id),
        )
        if cur.rowcount != 1:
            raise _SkipNoteError(
                VaultReconcileWarning(
                    kind="conflict",
                    vault_path=doc.relative_path,
                    message="memory status changed before candidate confirmation",
                    memory_id=memory_id,
                    memory_key=identity.memory_key,
                )
            )
        self._insert_memory_event(memory_id, "confirmed", {"reason": "vault note exists"})
        if payload_changed:
            self._insert_memory_event(memory_id, "payload_updated", {"payload": payload})
        self._insert_memory_event(
            memory_id,
            "vault_synced",
            _vault_event_payload(doc, "confirm_and_update"),
        )
        updated = self._get_memory_row(memory_id)
        warning = None
        if stale_candidate:
            warning = VaultReconcileWarning(
                kind="conflict",
                vault_path=doc.relative_path,
                message="candidate was updated after the vault note was materialized",
                memory_id=memory_id,
                memory_key=identity.memory_key,
                db_updated_at=str(updated["updated_at"]),
            )
        return _ApplyResult(
            outcome="confirmed",
            memory_id=memory_id,
            updated_at=str(updated["updated_at"]),
            payload=payload,
            warning=warning,
        )

    def _upsert_vault_index(
        self,
        doc: VaultDocument,
        resolved_path: Path,
        frontmatter: dict[str, object],
        memory_id: int,
        scope: str,
        *,
        content_hash: str,
    ) -> None:
        metadata_json = json.dumps(frontmatter, sort_keys=True)
        self._conn.execute(
            """
            INSERT INTO vault_index (
                vault_path, note_type, scope, content_hash, last_scanned_at,
                metadata_json, memory_id
            ) VALUES (?, 'minx-memory', ?, ?, datetime('now'), ?, ?)
            ON CONFLICT(vault_path) DO UPDATE SET
                note_type = excluded.note_type,
                scope = excluded.scope,
                content_hash = excluded.content_hash,
                last_scanned_at = excluded.last_scanned_at,
                metadata_json = excluded.metadata_json,
                memory_id = excluded.memory_id
            """,
            (
                doc.relative_path,
                scope,
                content_hash,
                metadata_json,
                memory_id,
            ),
        )

    def _get_memory_row(self, memory_id: int) -> Row:
        row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"memory {memory_id} disappeared during reconciliation")
        return row

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


class _SkipNoteError(Exception):
    def __init__(self, warning: VaultReconcileWarning) -> None:
        super().__init__(warning.message)
        self.warning = warning


def _skip_with_warning(
    counts: _ReconcileCounts,
    warnings: list[VaultReconcileWarning],
    warning: VaultReconcileWarning,
) -> None:
    counts.skipped += 1
    warnings.append(warning)
    if warning.kind == "conflict":
        counts.conflicts += 1


def _count_outcome(counts: _ReconcileCounts, outcome: str) -> None:
    if outcome == "created":
        counts.created += 1
    elif outcome == "confirmed":
        counts.confirmed += 1
    elif outcome == "updated":
        counts.updated += 1
    elif outcome == "skipped":
        counts.skipped += 1
    else:
        raise RuntimeError(f"unknown reconcile outcome: {outcome}")


def _require_identity_match(row: Row, identity: MemoryIdentity, vault_path: str) -> None:
    if (
        str(row["scope"]) != identity.scope
        or str(row["memory_type"]) != identity.memory_type
        or str(row["subject"]) != identity.subject
    ):
        raise _SkipNoteError(
            VaultReconcileWarning(
                kind="identity_mismatch",
                vault_path=vault_path,
                message="memory_id does not match memory_key, scope, memory_type, or subject",
                memory_id=int(row["id"]),
                memory_key=identity.memory_key,
            )
        )


def _conflict_warning(row: Row, identity: MemoryIdentity, vault_path: str) -> VaultReconcileWarning:
    return VaultReconcileWarning(
        kind="conflict",
        vault_path=vault_path,
        message="sync_base_updated_at does not match current memory updated_at",
        memory_id=int(row["id"]),
        memory_key=identity.memory_key,
        db_updated_at=str(row["updated_at"]),
        sync_base_updated_at=identity.sync_base_updated_at,
    )


def _parse_payload_json(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidInputError("stored payload_json is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise InvalidInputError("stored payload_json must be a JSON object")
    return parsed


def _canonical_payload_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True)


_CANONICAL_KEYS = {
    "type",
    "scope",
    "memory_key",
    "memory_type",
    "subject",
    "memory_id",
    "sync_base_updated_at",
    "payload_json",
}


def _frontmatter_equals_expected(
    frontmatter: dict[str, object],
    expected: dict[str, object],
) -> bool:
    """Cheap structural equality test for canonical frontmatter shape."""
    if set(frontmatter) != _CANONICAL_KEYS:
        return False
    for key in _CANONICAL_KEYS:
        if key == "payload_json":
            lhs = frontmatter.get(key)
            if not isinstance(lhs, dict):
                return False
            rhs = expected.get(key)
            rhs_dict: dict[str, object] = rhs if isinstance(rhs, dict) else {}
            if _canonical_payload_json(lhs) != _canonical_payload_json(rhs_dict):
                return False
        else:
            if str(frontmatter.get(key)) != str(expected.get(key)):
                return False
    return True


def _frontmatter_refresh_not_needed(
    frontmatter: dict[str, object],
    identity: MemoryIdentity,
    result: _ApplyResult,
) -> bool:
    if result.outcome != "skipped":
        return False
    if identity.sync_base_updated_at != result.updated_at:
        return False
    if set(frontmatter) != _CANONICAL_KEYS:
        return False
    if optional_str(frontmatter.get("type")) != "minx-memory":
        return False
    if optional_str(frontmatter.get("scope")) != identity.scope:
        return False
    if optional_str(frontmatter.get("memory_key")) != identity.memory_key:
        return False
    if optional_str(frontmatter.get("memory_type")) != identity.memory_type:
        return False
    if optional_str(frontmatter.get("subject")) != identity.subject:
        return False
    try:
        memory_id = parse_optional_int(frontmatter.get("memory_id"), "memory_id")
        payload = parse_memory_payload(frontmatter, allow_implicit=False)
    except InvalidInputError:
        return False
    return (
        memory_id == result.memory_id
        and optional_str(frontmatter.get("sync_base_updated_at")) == result.updated_at
        and _canonical_payload_json(payload) == _canonical_payload_json(result.payload)
    )


def _canonical_frontmatter(
    identity: MemoryIdentity,
    *,
    memory_id: int,
    payload: dict[str, object],
    updated_at: str,
) -> dict[str, object]:
    return {
        "type": "minx-memory",
        "scope": identity.scope,
        "memory_key": identity.memory_key,
        "memory_type": identity.memory_type,
        "subject": identity.subject,
        "memory_id": memory_id,
        "sync_base_updated_at": updated_at,
        "payload_json": payload,
    }


def _vault_event_payload(doc: VaultDocument, change: str) -> dict[str, object]:
    return {
        "vault_path": doc.relative_path,
        "content_hash": doc.content_hash,
        "change": change,
    }


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row_updated_after_note_mtime(row: Row, path: Path) -> bool:
    try:
        db_updated = datetime.fromisoformat(str(row["updated_at"])).replace(tzinfo=UTC)
    except ValueError:
        logger.warning(
            "memory row has malformed updated_at during vault reconcile stale-candidate check",
            extra={"memory_id": row["id"], "updated_at": row["updated_at"]},
        )
        return False
    note_mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return db_updated > note_mtime
