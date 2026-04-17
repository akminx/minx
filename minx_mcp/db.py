"""SQLite access and migration application.

SQL migrations live in a single source of truth: the packaged tree
``minx_mcp/schema/migrations`` (next to this module). That directory ships in
the wheel and is the only path ``apply_migrations`` loads. An earlier version
of this codebase also kept a ``schema/migrations`` mirror at the repo root for
human browsing; it was removed because the parity test normalized whitespace
away and the duplication was an ongoing footgun. When adding a new migration,
drop the ``.sql`` file into ``minx_mcp/schema/migrations/`` only.

Migration contract:
- Migrations run inside a single transaction and must be idempotent.
- Non-additive schema changes must guard repeat execution (for example, use
  ``add_column_if_missing`` before ``ALTER TABLE ... ADD COLUMN``).
- If data backfill is required, keep schema creation and data updates as separate
  migrations so backfill retries do not block schema bootstrap.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from sqlite3 import Connection

_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# ``ALTER TABLE ... ADD COLUMN`` trailing fragment only (no identifiers here).
_COLUMN_SQL_FRAGMENT_RE = re.compile(
    r"(?is)^(?:TEXT|INTEGER|REAL|BLOB|NUMERIC)"
    r"(?:\s+NOT\s+NULL)?"
    r"(?:\s+DEFAULT\s+(?:"
    r"NULL|"
    r"TRUE|FALSE|"
    r"CURRENT_TIMESTAMP|CURRENT_TIME|CURRENT_DATE|"
    r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|"
    r"'(?:''|[^'])*'"
    r"))?"
    r"\s*$"
)


def _validate_column_sql_fragment(column_sql: str) -> None:
    stripped = column_sql.strip()
    if not stripped:
        raise ValueError("column_sql must not be empty")
    if ";" in stripped or "--" in stripped:
        raise ValueError("column_sql must not contain SQL comments or statement separators")
    if _COLUMN_SQL_FRAGMENT_RE.fullmatch(stripped) is None:
        raise ValueError(
            "column_sql must be an allowed SQLite type fragment "
            "(TEXT/INTEGER/REAL/BLOB/NUMERIC, optional NOT NULL, optional DEFAULT <literal>)"
        )


def migration_dir() -> Path:
    """Directory of ``*.sql`` files used at runtime by ``apply_migrations``.

    Resolves to ``minx_mcp/schema/migrations`` relative to this package (editable
    install, wheel extract, or test import). This is the sole migration source for
    ``get_connection`` / ``apply_migrations``.
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


@contextmanager
def scoped_connection(db_path: Path) -> Generator[Connection, None, None]:
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


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
        add_column_if_missing(
            conn,
            table_name="_migrations",
            column_name="checksum",
            column_sql="TEXT",
        )
        applied = {
            row["name"]: row["checksum"]
            for row in conn.execute("SELECT name, checksum FROM _migrations").fetchall()
        }
        for path in paths:
            content = path.read_text(encoding="utf-8")
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
    except (
        Exception
    ):  # Broad except intentional: roll back migration transaction on any failure before re-raising
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.row_factory = original_row_factory


def add_column_if_missing(
    conn: Connection,
    *,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> bool:
    """Add a column once and safely no-op on reruns.

    Returns ``True`` when the column was added and ``False`` when it already
    exists.
    """
    table_identifier = _quote_sql_identifier(table_name)
    column_identifier = _quote_sql_identifier(column_name)
    columns = conn.execute(f"PRAGMA table_info({table_identifier})").fetchall()
    if not columns:
        raise sqlite3.OperationalError(f"no such table: {table_name}")
    names = {_column_name_from_pragma_row(row) for row in columns}
    if column_name in names:
        return False
    _validate_column_sql_fragment(column_sql)
    conn.execute(f"ALTER TABLE {table_identifier} ADD COLUMN {column_identifier} {column_sql}")
    return True


def _quote_sql_identifier(value: str) -> str:
    if not _SQL_IDENTIFIER_RE.match(value):
        raise ValueError(f"Invalid SQLite identifier: {value}")
    return f'"{value}"'


def _column_name_from_pragma_row(row: sqlite3.Row | tuple[object, ...]) -> str:
    if isinstance(row, sqlite3.Row):
        return str(row["name"])
    return str(row[1])


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
