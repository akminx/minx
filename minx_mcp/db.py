from __future__ import annotations

import sqlite3
import time
from pathlib import Path


def migration_dir() -> Path:
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
    conn.row_factory = sqlite3.Row
    paths = sorted(migration_dir().glob("*.sql"))
    if not paths:
        raise FileNotFoundError(f"No migration files found in {migration_dir()}")

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        applied = {
            row[0]
            for row in conn.execute("SELECT name FROM _migrations").fetchall()
        }
        for path in paths:
            if path.name in applied:
                continue
            for statement in _split_sql_script(path.read_text()):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO _migrations (name) VALUES (?)",
                (path.name,),
            )
            applied.add(path.name)
        conn.commit()
    except sqlite3.DatabaseError:
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
