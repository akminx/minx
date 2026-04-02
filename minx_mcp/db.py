from __future__ import annotations

import sqlite3
from pathlib import Path


def migration_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "schema" / "migrations"


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    apply_migrations(conn)
    return conn


def apply_migrations(conn: sqlite3.Connection) -> None:
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
    for path in sorted(migration_dir().glob("*.sql")):
        if path.name in applied:
            continue
        script = path.read_text().strip()
        escaped_name = path.name.replace("'", "''")
        wrapped_script = (
            "BEGIN IMMEDIATE;\n"
            f"{script}\n"
            f"INSERT INTO _migrations (name) VALUES ('{escaped_name}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(wrapped_script)
        except sqlite3.DatabaseError:
            conn.rollback()
            raise
    conn.commit()
