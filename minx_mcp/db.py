from __future__ import annotations

"""SQLite access and migration application.

SQL migrations are loaded only from the packaged tree ``minx_mcp/schema/migrations``
(next to this module). That directory is what ships in the wheel; the repository
also keeps ``schema/migrations`` as a mirror—tests enforce matching filenames and
normalized SQL contents.
"""

import hashlib
import re
import sqlite3
import time
from pathlib import Path


def migration_dir() -> Path:
    """Directory of ``*.sql`` files used at runtime by ``apply_migrations``.

    Resolves to ``minx_mcp/schema/migrations`` relative to this package (editable
    install, wheel extract, or test import). This is the sole migration source for
    ``get_connection`` / ``apply_migrations``; it is not the repo-root
    ``schema/migrations`` path.
    """
    return Path(__file__).resolve().parent / "schema" / "migrations"


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    for attempt in range(5):
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            apply_migrations(conn)
            break
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt == 4:
                conn.close()
                raise
            time.sleep(0.05 * (attempt + 1))
    return conn


def apply_migrations(conn: sqlite3.Connection) -> None:
    original_row_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        paths = sorted(migration_dir().glob("*.sql"))
        if not paths:
            raise FileNotFoundError(f"No migration files found in {migration_dir()}")
        _validate_migration_paths(paths)

        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now')),
                checksum TEXT
            )
            """
        )
        existing_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(_migrations)").fetchall()
        }
        if "checksum" not in existing_cols:
            conn.execute("ALTER TABLE _migrations ADD COLUMN checksum TEXT")
        applied = {
            row["name"]: row["checksum"]
            for row in conn.execute("SELECT name, checksum FROM _migrations").fetchall()
        }
        for path in paths:
            content = path.read_text()
            checksum = hashlib.sha256(content.encode()).hexdigest()
            if path.name in applied:
                stored = applied[path.name]
                if stored is not None and stored != checksum:
                    raise RuntimeError(
                        f"Migration {path.name} has been modified after application "
                        f"(expected {stored[:12]}…, got {checksum[:12]}…)"
                    )
                continue
            for statement in _split_sql_script(content):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO _migrations (name, checksum) VALUES (?, ?)",
                (path.name, checksum),
            )
            applied[path.name] = checksum
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.row_factory = original_row_factory


def _split_sql_script(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []

    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        candidate = "\n".join(current).strip()
        if sqlite3.complete_statement(candidate):
            statements.append(candidate)
            current = []

    if current:
        raise sqlite3.DatabaseError("Incomplete migration statement")

    return statements


def _validate_migration_paths(paths: list[Path]) -> None:
    numbers: list[int] = []

    for path in paths:
        match = re.match(r"(?P<number>\d{3})_.+\.sql$", path.name)
        if not match:
            raise ValueError(f"Unexpected migration filename: {path.name}")
        numbers.append(int(match.group("number")))

    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        raise FileNotFoundError(
            f"Incomplete migration set. Expected numbered migrations {expected}, found {numbers}."
        )
