# Minx Core Platform + Finance Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first `minx-mcp` implementation slice: shared platform foundations plus a complete finance MCP domain with imports, categorization, anomaly detection, sensitive querying, vault reports, and `stdio` plus HTTP-capable transport.

**Architecture:** `minx-mcp` starts as a Python monorepo with a shared core package and one domain package, `finance`. Shared modules own configuration, SQLite access, migrations, jobs, preferences, audit logging, vault writing, document text extraction, and transport bootstrapping. The finance domain owns parser normalization, importer adapters, import dedupe, categorization, reporting, anomaly logic, and MCP tool registration on top of the shared core.

**Tech Stack:** Python 3.12, SQLite, MCP Python SDK (`FastMCP`), pytest, python-dotenv, LiteParse via subprocess adapter, setuptools packaging

---

### Task 1: Scaffold The Repo And Shared Settings

**Files:**
- Create: `/Users/akmini/Documents/minx-mcp/pyproject.toml`
- Create: `/Users/akmini/Documents/minx-mcp/README.md`
- Create: `/Users/akmini/Documents/minx-mcp/.env.example`
- Create: `/Users/akmini/Documents/minx-mcp/.gitignore`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/__init__.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/config.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/__init__.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/__main__.py`
- Create: `/Users/akmini/Documents/minx-mcp/tests/__init__.py`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_smoke.py`

- [ ] **Step 1: Write the failing smoke tests**

Create `/Users/akmini/Documents/minx-mcp/tests/test_smoke.py`:

```python
from minx_mcp.config import get_settings


def test_settings_defaults_are_portable():
    settings = get_settings()
    assert settings.db_path.name == "minx.db"
    assert settings.default_transport == "stdio"


def test_settings_honor_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("MINX_DB_PATH", str(tmp_path / "custom.db"))
    monkeypatch.setenv("MINX_HTTP_PORT", "9001")

    settings = get_settings()

    assert settings.db_path == tmp_path / "custom.db"
    assert settings.http_port == 9001


def test_package_version_exists():
    import minx_mcp

    assert minx_mcp.__version__ == "0.1.0"
```

- [ ] **Step 2: Run the smoke tests to verify they fail**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_smoke.py -v
```

Expected:

```text
FAIL with ModuleNotFoundError for minx_mcp
```

- [ ] **Step 3: Add packaging and repo metadata**

Create `/Users/akmini/Documents/minx-mcp/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "minx-mcp"
version = "0.1.0"
description = "Shared Minx MCP services and finance domain"
requires-python = ">=3.12"
dependencies = [
  "mcp[cli]>=1.13.0",
  "python-dotenv>=1.0.1",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3.0",
  "pytest-asyncio>=0.24.0",
]

[project.scripts]
minx-finance = "minx_mcp.finance.__main__:main"

[tool.setuptools.packages.find]
include = ["minx_mcp*"]
```

Create `/Users/akmini/Documents/minx-mcp/.gitignore`:

```gitignore
.venv/
__pycache__/
.pytest_cache/
*.pyc
dist/
build/
*.egg-info/
```

Create `/Users/akmini/Documents/minx-mcp/README.md`:

```markdown
# minx-mcp

Scaffold for the Minx MCP platform and finance domain.

This repository currently includes:

- package metadata and environment examples
- shared settings scaffolding
- a safe placeholder finance entry point
- smoke tests for the initial core package

Later tasks in the plan add the database layer, finance domain behavior, reporting, and MCP transport.
```

Create `/Users/akmini/Documents/minx-mcp/.env.example`:

```dotenv
MINX_DATA_DIR=${HOME}/.minx/data
MINX_DB_PATH=${HOME}/.minx/data/minx.db
MINX_VAULT_PATH=${HOME}/Documents/minx-vault
MINX_STAGING_PATH=${HOME}/.minx/staging
MINX_LITEPARSE_BIN=lit
MINX_HTTP_HOST=127.0.0.1
MINX_HTTP_PORT=8000
MINX_DEFAULT_TRANSPORT=stdio
```

- [ ] **Step 4: Add the shared package and settings module**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/__init__.py`:

```python
__all__ = ["__version__"]

__version__ = "0.1.0"
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    vault_path: Path
    staging_path: Path
    liteparse_bin: str
    http_host: str
    http_port: int
    default_transport: str


def get_settings() -> Settings:
    home = Path.home()
    data_dir = Path(os.environ.get("MINX_DATA_DIR", home / ".minx" / "data"))
    return Settings(
        data_dir=data_dir,
        db_path=Path(os.environ.get("MINX_DB_PATH", data_dir / "minx.db")),
        vault_path=Path(os.environ.get("MINX_VAULT_PATH", home / "Documents" / "minx-vault")),
        staging_path=Path(os.environ.get("MINX_STAGING_PATH", home / ".minx" / "staging")),
        liteparse_bin=os.environ.get("MINX_LITEPARSE_BIN", "lit"),
        http_host=os.environ.get("MINX_HTTP_HOST", "127.0.0.1"),
        http_port=int(os.environ.get("MINX_HTTP_PORT", "8000")),
        default_transport=os.environ.get("MINX_DEFAULT_TRANSPORT", "stdio"),
    )
```

Create `/Users/akmini/Documents/minx-mcp/tests/__init__.py`:

```python
# Tests package for minx-mcp.
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/__init__.py`:

```python
"""Finance domain package for minx-mcp."""
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/__main__.py`:

```python
from __future__ import annotations


def main() -> None:
    print("minx-finance is not implemented yet. Complete later finance tasks to enable this command.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Re-run the smoke tests**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_smoke.py -v
```

Expected:

```text
3 passed
```

- [ ] **Step 6: Commit the scaffold**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && git add pyproject.toml README.md .env.example .gitignore minx_mcp tests && git commit -m "chore: scaffold minx core package"
```

### Task 2: Build SQLite Foundations And Finance Schema

**Files:**
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/db.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/schema/__init__.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/schema/migrations/__init__.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/schema/migrations/001_platform.sql`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/schema/migrations/002_finance.sql`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/schema/migrations/003_finance_views.sql`
- Create: `/Users/akmini/Documents/minx-mcp/schema/migrations/001_platform.sql`
- Create: `/Users/akmini/Documents/minx-mcp/schema/migrations/002_finance.sql`
- Create: `/Users/akmini/Documents/minx-mcp/schema/migrations/003_finance_views.sql`
- Modify: `/Users/akmini/Documents/minx-mcp/pyproject.toml`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_db.py`

- [ ] **Step 1: Write the failing database tests**

Create `/Users/akmini/Documents/minx-mcp/tests/test_db.py`:

```python
import importlib.metadata
import sqlite3
import threading
from pathlib import Path

import pytest

from minx_mcp.db import apply_migrations, get_connection


def test_database_bootstrap_creates_platform_and_finance_tables(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    }
    assert "_migrations" in names
    assert "jobs" in names
    assert "preferences" in names
    assert "audit_log" in names
    assert "finance_accounts" in names
    assert "finance_categories" in names
    assert "finance_import_batches" in names
    assert "finance_transactions" in names
    assert "finance_transaction_dedupe" in names
    assert "finance_report_runs" in names
    assert "v_finance_monthly_spend" in names


def test_migrations_are_idempotent(tmp_path):
    db_path = tmp_path / "minx.db"
    first = get_connection(db_path)
    first.close()
    second = get_connection(db_path)
    count = second.execute("SELECT COUNT(*) AS c FROM _migrations").fetchone()["c"]
    assert count == 3


def test_finance_seed_rows_exist(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    accounts = {
        row["name"]
        for row in conn.execute("SELECT name FROM finance_accounts ORDER BY name")
    }
    categories = {
        row["name"]
        for row in conn.execute("SELECT name FROM finance_categories ORDER BY name")
    }
    assert {"DCU", "Discover", "Robinhood Gold"} <= accounts
    assert {"Groceries", "Dining Out", "Income", "Uncategorized"} <= categories


def test_connection_enables_required_pragmas(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert foreign_keys == 1
    assert journal_mode == "wal"


def test_apply_migrations_handles_plain_sqlite_connections(tmp_path):
    db_path = tmp_path / "plain.db"
    conn = sqlite3.connect(str(db_path))
    original_row_factory = conn.row_factory

    apply_migrations(conn)
    apply_migrations(conn)

    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    assert count == 3
    assert conn.row_factory is original_row_factory


def test_failed_migration_rolls_back_partial_changes(tmp_path, monkeypatch):
    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    (migration_root / "001_good.sql").write_text(
        "CREATE TABLE seeded_table (id INTEGER PRIMARY KEY);"
    )
    (migration_root / "002_bad.sql").write_text(
        "CREATE TABLE half_done (id INTEGER PRIMARY KEY);\n"
        "THIS IS NOT VALID SQL;"
    )

    monkeypatch.setattr("minx_mcp.db.migration_dir", lambda: migration_root)
    conn = sqlite3.connect(str(tmp_path / "broken.db"))

    with pytest.raises(sqlite3.DatabaseError):
        apply_migrations(conn)

    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "half_done" not in names
    assert "seeded_table" not in names
    assert "_migrations" not in names


def test_concurrent_bootstrap_succeeds_for_same_db_file(tmp_path):
    db_path = tmp_path / "shared.db"
    errors = []

    def bootstrap():
        try:
            conn = get_connection(db_path)
            conn.close()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=bootstrap) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []


def test_built_wheel_includes_packaged_migrations(tmp_path):
    del tmp_path

    names = {
        str(path)
        for path in importlib.metadata.files("minx-mcp") or []
    }

    assert "minx_mcp/schema/migrations/001_platform.sql" in names
    assert "minx_mcp/schema/migrations/002_finance.sql" in names
    assert "minx_mcp/schema/migrations/003_finance_views.sql" in names


def test_source_and_packaged_migrations_match():
    project_root = Path(__file__).resolve().parent.parent
    source_root = project_root / "schema" / "migrations"
    packaged_root = project_root / "minx_mcp" / "schema" / "migrations"

    for filename in [
        "001_platform.sql",
        "002_finance.sql",
        "003_finance_views.sql",
    ]:
        assert (source_root / filename).read_text().strip() == (packaged_root / filename).read_text().strip()
```

- [ ] **Step 2: Run the database tests to verify they fail**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_db.py -v
```

Expected:

```text
FAIL with ModuleNotFoundError for minx_mcp.db
```

- [ ] **Step 3: Implement the SQLite bootstrapper**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/db.py`:

```python
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
            conn.execute("INSERT INTO _migrations (name) VALUES (?)", (path.name,))
            applied.add(path.name)
        conn.commit()
    except sqlite3.DatabaseError:
        conn.rollback()
        raise
    finally:
        conn.row_factory = original_row_factory


def _split_sql_script(script: str) -> list[str]:
    statements = []
    current = []

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
```

Modify `/Users/akmini/Documents/minx-mcp/pyproject.toml` to package migration SQL from the package directory:

```toml
[tool.setuptools.package-data]
"minx_mcp.schema.migrations" = ["*.sql"]
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/schema/__init__.py`:

```python
"""Packaged schema resources for minx-mcp."""
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/schema/migrations/__init__.py`:

```python
"""SQL migration resources for minx-mcp."""
```

- [ ] **Step 4: Add the platform and finance migrations**

Create `/Users/akmini/Documents/minx-mcp/schema/migrations/001_platform.sql`:

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT,
    source_ref TEXT,
    idempotency_key TEXT UNIQUE,
    result_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS preferences (
    domain TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (domain, key)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    session_ref TEXT,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Create `/Users/akmini/Documents/minx-mcp/schema/migrations/002_finance.sql`:

```sql
CREATE TABLE IF NOT EXISTS finance_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    account_type TEXT NOT NULL,
    import_profile TEXT,
    last_imported_at TEXT
);

CREATE TABLE IF NOT EXISTS finance_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES finance_categories(id)
);

CREATE TABLE IF NOT EXISTS finance_category_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES finance_categories(id),
    match_kind TEXT NOT NULL,
    pattern TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS finance_import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES finance_accounts(id),
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    raw_fingerprint TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    inserted_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS finance_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES finance_accounts(id),
    batch_id INTEGER NOT NULL REFERENCES finance_import_batches(id),
    posted_at TEXT NOT NULL,
    description TEXT NOT NULL,
    merchant TEXT,
    amount REAL NOT NULL,
    category_id INTEGER REFERENCES finance_categories(id),
    category_source TEXT NOT NULL DEFAULT 'uncategorized',
    external_id TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS finance_transaction_dedupe (
    fingerprint TEXT PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES finance_transactions(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS finance_report_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_kind TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    vault_path TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_finance_transactions_posted_at
ON finance_transactions(posted_at);

CREATE INDEX IF NOT EXISTS idx_finance_transactions_category
ON finance_transactions(category_id);

INSERT OR IGNORE INTO finance_accounts (name, account_type, import_profile) VALUES
    ('DCU', 'bank', 'dcu'),
    ('Discover', 'credit', 'discover'),
    ('Robinhood Gold', 'credit', 'robinhood_gold');

INSERT OR IGNORE INTO finance_categories (name) VALUES
    ('Uncategorized'),
    ('Groceries'),
    ('Dining Out'),
    ('Income'),
    ('Subscriptions'),
    ('Shopping'),
    ('Transportation');
```

Create `/Users/akmini/Documents/minx-mcp/schema/migrations/003_finance_views.sql`:

```sql
CREATE VIEW IF NOT EXISTS v_finance_monthly_spend AS
SELECT
    substr(posted_at, 1, 7) AS month,
    COALESCE(c.name, 'Uncategorized') AS category_name,
    SUM(t.amount) AS total_amount
FROM finance_transactions t
LEFT JOIN finance_categories c ON c.id = t.category_id
GROUP BY substr(posted_at, 1, 7), COALESCE(c.name, 'Uncategorized');
```

- [ ] **Step 5: Run the database tests**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_db.py -v
```

Expected:

```text
9 passed
```

- [ ] **Step 6: Commit the schema layer**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && git add minx_mcp/db.py schema tests/test_db.py && git commit -m "feat: add shared sqlite schema"
```

### Task 3: Add Shared Core Helpers For Jobs, Preferences, Audit, Vault Output, Document Text, And Transport

**Files:**
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/jobs.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/preferences.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/audit.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/vault_writer.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/document_text.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/transport.py`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_jobs.py`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_preferences.py`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_audit.py`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_vault_writer.py`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_transport.py`

- [ ] **Step 1: Write the failing tests for the shared helpers**

Create `/Users/akmini/Documents/minx-mcp/tests/test_jobs.py`:

```python
from minx_mcp.db import get_connection
from minx_mcp.jobs import get_job, mark_completed, mark_running, submit_job


def test_submit_job_reuses_idempotency_key(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    first = submit_job(conn, "finance_import", "test", "/tmp/a.csv", "same-file")
    second = submit_job(conn, "finance_import", "test", "/tmp/a.csv", "same-file")
    assert first["id"] == second["id"]


def test_job_status_transitions_are_persisted(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    job = submit_job(conn, "finance_import", "test", "/tmp/a.csv", "key-1")
    mark_running(conn, job["id"])
    mark_completed(conn, job["id"], {"inserted": 3})
    stored = get_job(conn, job["id"])
    assert stored["status"] == "completed"
    assert stored["result"]["inserted"] == 3
```

Create `/Users/akmini/Documents/minx-mcp/tests/test_preferences.py`:

```python
from minx_mcp.db import get_connection
from minx_mcp.preferences import get_csv_mapping, save_csv_mapping


def test_csv_mapping_round_trip(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    save_csv_mapping(
        conn,
        "generic-checking",
        {
            "account_name": "DCU",
            "date_column": "Date",
            "amount_column": "Amount",
            "description_column": "Memo",
            "date_format": "%Y-%m-%d",
        },
    )
    loaded = get_csv_mapping(conn, "generic-checking")
    assert loaded["account_name"] == "DCU"
    assert loaded["description_column"] == "Memo"
```

Create `/Users/akmini/Documents/minx-mcp/tests/test_audit.py`:

```python
from minx_mcp.audit import log_sensitive_access
from minx_mcp.db import get_connection


def test_sensitive_access_is_logged(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    log_sensitive_access(conn, "sensitive_finance_query", "session-1", "Queried March transactions")
    row = conn.execute("SELECT tool_name, session_ref, summary FROM audit_log").fetchone()
    assert dict(row) == {
        "tool_name": "sensitive_finance_query",
        "session_ref": "session-1",
        "summary": "Queried March transactions",
    }
```

Create `/Users/akmini/Documents/minx-mcp/tests/test_vault_writer.py`:

```python
from minx_mcp.vault_writer import VaultWriter


def test_vault_writer_rejects_paths_outside_allowed_dirs(tmp_path):
    writer = VaultWriter(tmp_path, ("Finance",))
    try:
        writer.write_markdown("../bad.md", "nope")
    except ValueError as exc:
        assert "outside allowed vault roots" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_replace_section_updates_named_heading(tmp_path):
    writer = VaultWriter(tmp_path, ("Finance",))
    writer.write_markdown(
        "Finance/weekly.md",
        "# Weekly\n\n## Summary\n\nOld value\n\n## Notes\n\nKeep me\n",
    )
    path = writer.replace_section("Finance/weekly.md", "Summary", "New value")
    text = path.read_text()
    assert "## Summary\n\nNew value" in text
    assert "## Notes\n\nKeep me" in text
```

Create `/Users/akmini/Documents/minx-mcp/tests/test_transport.py`:

```python
from minx_mcp.transport import build_transport_config


def test_transport_config_supports_stdio_and_http():
    stdio = build_transport_config("stdio", "127.0.0.1", 8000)
    http = build_transport_config("http", "127.0.0.1", 8000)
    assert stdio["transport"] == "stdio"
    assert http["transport"] == "streamable-http"
```

- [ ] **Step 2: Implement jobs and job events**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/jobs.py`:

```python
from __future__ import annotations

import json
import uuid
from sqlite3 import Connection


def submit_job(
    conn: Connection,
    job_type: str,
    requested_by: str | None,
    source_ref: str | None,
    idempotency_key: str | None,
) -> dict:
    if idempotency_key:
        existing = conn.execute(
            "SELECT * FROM jobs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            return _row_to_job(existing)
    job_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO jobs (id, job_type, status, requested_by, source_ref, idempotency_key)
        VALUES (?, ?, 'queued', ?, ?, ?)
        """,
        (job_id, job_type, requested_by, source_ref, idempotency_key),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, status, message) VALUES (?, 'queued', 'Job created')",
        (job_id,),
    )
    conn.commit()
    return get_job(conn, job_id)


def mark_running(conn: Connection, job_id: str) -> None:
    _set_status(conn, job_id, "running", None)


def mark_completed(conn: Connection, job_id: str, result: dict) -> None:
    _set_status(conn, job_id, "completed", json.dumps(result))


def mark_failed(conn: Connection, job_id: str, message: str) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET status = 'failed', error_message = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (message, job_id),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, status, message) VALUES (?, 'failed', ?)",
        (job_id, message),
    )
    conn.commit()


def get_job(conn: Connection, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def _set_status(conn: Connection, job_id: str, status: str, result_json: str | None) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET status = ?, result_json = COALESCE(?, result_json), updated_at = datetime('now')
        WHERE id = ?
        """,
        (status, result_json, job_id),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, status, message) VALUES (?, ?, ?)",
        (job_id, status, f"Job moved to {status}"),
    )
    conn.commit()


def _row_to_job(row) -> dict:
    result = json.loads(row["result_json"]) if row["result_json"] else None
    return {
        "id": row["id"],
        "job_type": row["job_type"],
        "status": row["status"],
        "requested_by": row["requested_by"],
        "source_ref": row["source_ref"],
        "idempotency_key": row["idempotency_key"],
        "result": result,
        "error_message": row["error_message"],
    }
```

- [ ] **Step 3: Implement preferences and reusable CSV mappings**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/preferences.py`:

```python
from __future__ import annotations

import json
from sqlite3 import Connection


def set_preference(conn: Connection, domain: str, key: str, value: object) -> None:
    conn.execute(
        """
        INSERT INTO preferences (domain, key, value_json, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(domain, key)
        DO UPDATE SET value_json = excluded.value_json, updated_at = datetime('now')
        """,
        (domain, key, json.dumps(value)),
    )
    conn.commit()


def get_preference(conn: Connection, domain: str, key: str, default: object | None = None) -> object:
    row = conn.execute(
        "SELECT value_json FROM preferences WHERE domain = ? AND key = ?",
        (domain, key),
    ).fetchone()
    return json.loads(row["value_json"]) if row else default


def save_csv_mapping(conn: Connection, profile_name: str, mapping: dict) -> None:
    set_preference(conn, "finance.csv_mapping", profile_name, mapping)


def get_csv_mapping(conn: Connection, profile_name: str) -> dict | None:
    return get_preference(conn, "finance.csv_mapping", profile_name, None)
```

- [ ] **Step 4: Implement audit logging and vault-safe markdown writes**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/audit.py`:

```python
from __future__ import annotations

from sqlite3 import Connection


def log_sensitive_access(
    conn: Connection,
    tool_name: str,
    session_ref: str | None,
    summary: str,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (tool_name, session_ref, summary) VALUES (?, ?, ?)",
        (tool_name, session_ref, summary),
    )
    conn.commit()
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/vault_writer.py`:

```python
from __future__ import annotations

from pathlib import Path


class VaultWriter:
    def __init__(self, vault_root: Path, allowed_roots: tuple[str, ...]) -> None:
        self.vault_root = vault_root
        self.allowed_roots = allowed_roots

    def write_markdown(self, relative_path: str, content: str) -> Path:
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def replace_section(self, relative_path: str, heading: str, body: str) -> Path:
        path = self._resolve(relative_path)
        text = path.read_text() if path.exists() else ""
        marker = f"## {heading}"
        blocks = text.split(marker)
        replacement = f"{marker}\n\n{body.strip()}\n"
        if len(blocks) == 1:
            new_text = f"{text.rstrip()}\n\n{replacement}\n".strip() + "\n"
        else:
            before = blocks[0].rstrip()
            remainder = blocks[1]
            next_heading = remainder.find("\n## ")
            tail = remainder[next_heading:] if next_heading != -1 else ""
            new_text = f"{before}\n\n{replacement}{tail.lstrip()}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text)
        return path

    def _resolve(self, relative_path: str) -> Path:
        normalized = Path(relative_path)
        if normalized.is_absolute():
            raise ValueError("vault paths must be relative")
        if not normalized.parts or normalized.parts[0] not in self.allowed_roots:
            raise ValueError("outside allowed vault roots")
        return self.vault_root / normalized
```

- [ ] **Step 5: Implement the LiteParse adapter and shared transport helpers**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/document_text.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path

from minx_mcp.config import get_settings


def extract_text(path: Path) -> str:
    settings = get_settings()
    proc = subprocess.run(
        [settings.liteparse_bin, str(path)],
        capture_output=True,
        check=True,
        text=True,
    )
    return proc.stdout
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/transport.py`:

```python
from __future__ import annotations


def build_transport_config(transport: str, host: str, port: int) -> dict[str, object]:
    if transport == "stdio":
        return {"transport": "stdio", "host": host, "port": port}
    if transport == "http":
        return {"transport": "streamable-http", "host": host, "port": port}
    raise ValueError(f"Unsupported transport: {transport}")


def run_server(mcp, transport: str, host: str, port: int) -> None:
    config = build_transport_config(transport, host, port)
    mcp.settings.host = config["host"]
    mcp.settings.port = config["port"]
    mcp.run(transport=config["transport"])
```

- [ ] **Step 6: Run the shared helper tests**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_jobs.py tests/test_preferences.py tests/test_audit.py tests/test_vault_writer.py tests/test_transport.py -v
```

Expected:

```text
7 passed
```

- [ ] **Step 7: Commit the shared helpers**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && git add minx_mcp tests && git commit -m "feat: add core jobs prefs audit vault and transport"
```

### Task 4: Build Finance Importers And Normalization

**Files:**
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/__init__.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/importers.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/__init__.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/dcu.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/discover.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/robinhood_gold.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/generic_csv.py`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_finance_parsers.py`

- [ ] **Step 1: Write the failing finance parser tests**

Create `/Users/akmini/Documents/minx-mcp/tests/test_finance_parsers.py`:

```python
from pathlib import Path

from minx_mcp.finance.importers import detect_source_kind, parse_source_file


def test_detect_robinhood_csv(tmp_path):
    path = tmp_path / "robinhood_transactions.csv"
    path.write_text("Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n")
    assert detect_source_kind(path) == "robinhood_csv"


def test_parse_dcu_csv(tmp_path):
    path = tmp_path / "free checking transactions.csv"
    path.write_text("Date,Description,Transaction Type,Amount\n2026-03-01,Payroll,Deposit,1200.00\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
    parsed = parse_source_file(path, account_name="DCU")
    assert parsed["account_name"] == "DCU"
    assert parsed["transactions"][1]["merchant"] == "H-E-B"


def test_parse_discover_pdf_via_liteparse_adapter(tmp_path, monkeypatch):
    path = tmp_path / "discover_statement.pdf"
    path.write_text("stub")
    sample = "Transactions\n03/01/26 03/01/26 H-E-B $ 42.16 Supermarkets\n"
    monkeypatch.setattr("minx_mcp.finance.parsers.discover.extract_text", lambda _: sample)
    parsed = parse_source_file(path, account_name="Discover", source_kind="discover_pdf")
    assert parsed["transactions"][0]["amount"] == -42.16


def test_parse_generic_csv_with_saved_mapping(tmp_path):
    path = tmp_path / "generic.csv"
    path.write_text("Booked,Debit,Merchant,Details\n2026-03-01,18.10,TARGET,Household\n")
    mapping = {
        "date_column": "Booked",
        "amount_column": "Debit",
        "merchant_column": "Merchant",
        "description_column": "Details",
        "date_format": "%Y-%m-%d",
    }
    parsed = parse_source_file(path, account_name="Discover", source_kind="generic_csv", mapping=mapping)
    assert parsed["transactions"][0]["description"] == "Household"
```

- [ ] **Step 2: Implement the importer registry and detection**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/__init__.py`:

```python
__all__ = ["__version__"]

__version__ = "0.1.0"
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/importers.py`:

```python
from __future__ import annotations

from pathlib import Path

from minx_mcp.document_text import extract_text
from minx_mcp.finance.parsers.dcu import parse_dcu_csv, parse_dcu_pdf
from minx_mcp.finance.parsers.discover import parse_discover_pdf
from minx_mcp.finance.parsers.generic_csv import parse_generic_csv
from minx_mcp.finance.parsers.robinhood_gold import parse_robinhood_csv


def detect_source_kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith("robinhood_transactions.csv"):
        return "robinhood_csv"
    if "free checking transactions.csv" in name:
        return "dcu_csv"
    if "discover" in name and path.suffix.lower() == ".pdf":
        return "discover_pdf"
    if name.startswith("stmt_") and path.suffix.lower() == ".pdf":
        return "dcu_pdf"
    raise ValueError(f"Could not detect finance source for {path}")


def parse_source_file(
    path: Path,
    account_name: str,
    source_kind: str | None = None,
    mapping: dict | None = None,
) -> dict:
    kind = source_kind or detect_source_kind(path)
    if kind == "robinhood_csv":
        return parse_robinhood_csv(path, account_name)
    if kind == "dcu_csv":
        return parse_dcu_csv(path, account_name)
    if kind == "dcu_pdf":
        return parse_dcu_pdf(path, account_name)
    if kind == "discover_pdf":
        return parse_discover_pdf(path, account_name)
    if kind == "generic_csv":
        if not mapping:
            raise ValueError("generic_csv requires a saved mapping")
        return parse_generic_csv(path, account_name, mapping)
    raise ValueError(f"Unsupported finance source kind: {kind}")
```

- [ ] **Step 3: Implement the DCU and Robinhood parsers**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/dcu.py`:

```python
from __future__ import annotations

import csv
import re
from pathlib import Path

from minx_mcp.document_text import extract_text


def parse_dcu_csv(path: Path, account_name: str) -> dict:
    transactions = []
    with path.open() as handle:
        for row in csv.DictReader(handle):
            transactions.append(
                {
                    "posted_at": row["Date"],
                    "description": row["Description"],
                    "amount": float(row["Amount"]),
                    "merchant": row["Description"],
                    "category_hint": None,
                    "external_id": None,
                }
            )
    return {
        "account_name": account_name,
        "source_type": "csv",
        "source_ref": str(path),
        "raw_fingerprint": f"path:{path.name}",
        "transactions": transactions,
    }


def parse_dcu_pdf(path: Path, account_name: str) -> dict:
    text = extract_text(path)
    transactions = []
    for line in text.splitlines():
        match = re.match(
            r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<desc>.+?)\s+(?P<amount>-?\d+\.\d{2})$",
            line.strip(),
        )
        if not match:
            continue
        transactions.append(
            {
                "posted_at": match.group("date"),
                "description": match.group("desc"),
                "amount": float(match.group("amount")),
                "merchant": match.group("desc"),
                "category_hint": None,
                "external_id": None,
            }
        )
    return {
        "account_name": account_name,
        "source_type": "pdf",
        "source_ref": str(path),
        "raw_fingerprint": f"path:{path.name}",
        "transactions": transactions,
    }
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/robinhood_gold.py`:

```python
from __future__ import annotations

import csv
from pathlib import Path


def parse_robinhood_csv(path: Path, account_name: str) -> dict:
    transactions = []
    with path.open() as handle:
        for row in csv.DictReader(handle):
            amount = float(row["Amount"])
            description = row["Description"]
            transactions.append(
                {
                    "posted_at": row["Date"],
                    "description": description,
                    "amount": amount,
                    "merchant": description,
                    "category_hint": None,
                    "external_id": None,
                }
            )
    return {
        "account_name": account_name,
        "source_type": "csv",
        "source_ref": str(path),
        "raw_fingerprint": f"path:{path.name}",
        "transactions": transactions,
    }
```

- [ ] **Step 4: Implement the Discover PDF and generic CSV parsers**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/discover.py`:

```python
from __future__ import annotations

import re
from pathlib import Path

from minx_mcp.document_text import extract_text


def parse_discover_pdf(path: Path, account_name: str) -> dict:
    text = extract_text(path)
    transactions = []
    for line in text.splitlines():
        match = re.match(
            r"^(?P<trans>\d{2}/\d{2}/\d{2})\s+\d{2}/\d{2}/\d{2}\s+(?P<desc>.+?)\s+\$\s*(?P<amount>\d+\.\d{2})\s+(?P<category>.+)$",
            line.strip(),
        )
        if not match:
            continue
        month, day, year = match.group("trans").split("/")
        iso_date = f"20{year}-{month}-{day}"
        description = match.group("desc")
        transactions.append(
            {
                "posted_at": iso_date,
                "description": description,
                "amount": -float(match.group("amount")),
                "merchant": description,
                "category_hint": match.group("category").lower(),
                "external_id": None,
            }
        )
    return {
        "account_name": account_name,
        "source_type": "pdf",
        "source_ref": str(path),
        "raw_fingerprint": f"path:{path.name}",
        "transactions": transactions,
    }
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/generic_csv.py`:

```python
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path


def parse_generic_csv(path: Path, account_name: str, mapping: dict) -> dict:
    transactions = []
    with path.open() as handle:
        for row in csv.DictReader(handle):
            posted_at = datetime.strptime(
                row[mapping["date_column"]],
                mapping["date_format"],
            ).strftime("%Y-%m-%d")
            description = row[mapping["description_column"]]
            merchant = row.get(mapping.get("merchant_column", ""), description)
            amount = float(row[mapping["amount_column"]])
            transactions.append(
                {
                    "posted_at": posted_at,
                    "description": description,
                    "amount": -abs(amount),
                    "merchant": merchant,
                    "category_hint": row.get(mapping.get("category_hint_column", ""), None),
                    "external_id": None,
                }
            )
    return {
        "account_name": account_name,
        "source_type": "csv",
        "source_ref": str(path),
        "raw_fingerprint": f"path:{path.name}",
        "transactions": transactions,
    }
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/__init__.py`:

```python
# Finance parser package.
```

- [ ] **Step 5: Run the parser tests**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_finance_parsers.py -v
```

Expected:

```text
4 passed
```

- [ ] **Step 6: Commit the parser layer**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && git add minx_mcp/finance tests/test_finance_parsers.py && git commit -m "feat: add finance importers and parser normalization"
```

### Task 5: Build Finance Service Logic, Categorization, Anomalies, And Reports

**Files:**
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/dedupe.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/analytics.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/reports.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py`
- Create: `/Users/akmini/Documents/minx-mcp/templates/finance-weekly-summary.md`
- Create: `/Users/akmini/Documents/minx-mcp/templates/finance-monthly-summary.md`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_finance_reports.py`

- [ ] **Step 1: Write the failing finance service and report tests**

Create `/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py`:

```python
from minx_mcp.db import get_connection
from minx_mcp.finance.service import FinanceService
from minx_mcp.preferences import save_csv_mapping


def test_import_job_is_idempotent_for_same_file(tmp_path):
    db_path = tmp_path / "minx.db"
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text("Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n")
    service = FinanceService(get_connection(db_path), tmp_path)
    first = service.finance_import(str(source), account_name="Robinhood Gold")
    second = service.finance_import(str(source), account_name="Robinhood Gold")
    assert first["job_id"] == second["job_id"]


def test_manual_and_rule_based_categorization_both_work(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
    service = FinanceService(get_connection(tmp_path / "minx.db"), tmp_path)
    service.finance_import(str(source), account_name="DCU")
    service.add_category_rule("Groceries", "merchant_contains", "H-E-B")
    service.apply_category_rules()
    tx = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert tx["category_name"] == "Groceries"
    service.finance_categorize([tx["id"]], "Dining Out")
    changed = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert changed["category_name"] == "Dining Out"


def test_safe_summary_and_sensitive_query_are_separate(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
    service = FinanceService(get_connection(tmp_path / "minx.db"), tmp_path)
    service.finance_import(str(source), account_name="DCU")
    safe = service.safe_finance_summary()
    sensitive = service.sensitive_finance_query(limit=10, session_ref="abc-123")
    assert "transactions" not in safe
    assert sensitive["transactions"][0]["description"] == "H-E-B"


def test_anomalies_flag_large_uncategorized_transactions(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,Unknown Merchant,Withdrawal,-500.00\n")
    service = FinanceService(get_connection(tmp_path / "minx.db"), tmp_path)
    service.finance_import(str(source), account_name="DCU")
    anomalies = service.finance_anomalies()
    assert anomalies["items"][0]["kind"] == "large_uncategorized"
```

Create `/Users/akmini/Documents/minx-mcp/tests/test_finance_reports.py`:

```python
from minx_mcp.db import get_connection
from minx_mcp.finance.service import FinanceService


def test_weekly_and_monthly_reports_write_to_vault(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n2026-03-10,Payroll,Deposit,1200.00\n")
    vault_root = tmp_path / "vault"
    service = FinanceService(get_connection(tmp_path / "minx.db"), vault_root)
    service.finance_import(str(source), account_name="DCU")
    weekly = service.generate_weekly_report("2026-03-02", "2026-03-08")
    monthly = service.generate_monthly_report("2026-03-01", "2026-03-31")
    assert weekly["vault_path"].endswith("Finance/weekly-2026-03-02.md")
    assert monthly["vault_path"].endswith("Finance/monthly-2026-03.md")
```

- [ ] **Step 2: Implement dedupe fingerprints and the import workflow**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/dedupe.py`:

```python
from __future__ import annotations

import hashlib


def fingerprint_transaction(account_name: str, transaction: dict) -> str:
    raw = "|".join(
        [
            account_name,
            transaction["posted_at"],
            transaction["description"],
            f"{transaction['amount']:.2f}",
            transaction.get("external_id") or "",
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py`:

```python
from __future__ import annotations

import hashlib
from pathlib import Path
from sqlite3 import Connection

from minx_mcp.audit import log_sensitive_access
from minx_mcp.finance.analytics import summarize_finances, find_anomalies
from minx_mcp.finance.dedupe import fingerprint_transaction
from minx_mcp.finance.importers import parse_source_file
from minx_mcp.finance.reports import build_monthly_report, build_weekly_report
from minx_mcp.jobs import get_job, mark_completed, mark_failed, mark_running, submit_job
from minx_mcp.vault_writer import VaultWriter


class FinanceService:
    def __init__(self, conn: Connection, vault_root: Path) -> None:
        self.conn = conn
        self.vault_writer = VaultWriter(vault_root, ("Finance",))

    def finance_import(
        self,
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
        mapping: dict | None = None,
    ) -> dict:
        idempotency_key = hashlib.sha256(f"{account_name}|{source_ref}".encode()).hexdigest()
        job = submit_job(self.conn, "finance_import", "system", source_ref, idempotency_key)
        if job["status"] == "completed":
            return {"job_id": job["id"], "status": job["status"], "result": job["result"]}
        try:
            mark_running(self.conn, job["id"])
            parsed = parse_source_file(Path(source_ref), account_name, source_kind, mapping)
            account_id = self._account_id(account_name)
            batch_id = self._insert_batch(account_id, parsed)
            inserted = 0
            skipped = 0
            for txn in parsed["transactions"]:
                fingerprint = fingerprint_transaction(account_name, txn)
                existing = self.conn.execute(
                    "SELECT transaction_id FROM finance_transaction_dedupe WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                if existing:
                    skipped += 1
                    continue
                tx_id = self._insert_transaction(account_id, batch_id, txn)
                self.conn.execute(
                    "INSERT INTO finance_transaction_dedupe (fingerprint, transaction_id) VALUES (?, ?)",
                    (fingerprint, tx_id),
                )
                inserted += 1
            self.conn.execute(
                "UPDATE finance_import_batches SET inserted_count = ?, skipped_count = ? WHERE id = ?",
                (inserted, skipped, batch_id),
            )
            self.conn.execute(
                "UPDATE finance_accounts SET last_imported_at = datetime('now') WHERE id = ?",
                (account_id,),
            )
            self.conn.commit()
            self.apply_category_rules()
            result = {"batch_id": batch_id, "inserted": inserted, "skipped": skipped}
            mark_completed(self.conn, job["id"], result)
            return {"job_id": job["id"], "status": "completed", "result": result}
        except Exception as exc:
            mark_failed(self.conn, job["id"], str(exc))
            raise

    def _account_id(self, account_name: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM finance_accounts WHERE name = ?",
            (account_name,),
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown finance account: {account_name}")
        return row["id"]

    def _insert_batch(self, account_id: int, parsed: dict) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO finance_import_batches (account_id, source_type, source_ref, raw_fingerprint)
            VALUES (?, ?, ?, ?)
            """,
            (
                account_id,
                parsed["source_type"],
                parsed["source_ref"],
                parsed["raw_fingerprint"],
            ),
        )
        return int(cursor.lastrowid)

    def _insert_transaction(self, account_id: int, batch_id: int, txn: dict) -> int:
        uncategorized_id = self.conn.execute(
            "SELECT id FROM finance_categories WHERE name = 'Uncategorized'"
        ).fetchone()["id"]
        cursor = self.conn.execute(
            """
            INSERT INTO finance_transactions (
                account_id, batch_id, posted_at, description, merchant, amount, category_id, external_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                batch_id,
                txn["posted_at"],
                txn["description"],
                txn["merchant"],
                txn["amount"],
                uncategorized_id,
                txn.get("external_id"),
            ),
        )
        return int(cursor.lastrowid)
```

- [ ] **Step 3: Implement categorization, summaries, anomalies, and sensitive queries**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/analytics.py`:

```python
from __future__ import annotations

from sqlite3 import Connection

from minx_mcp.audit import log_sensitive_access


def summarize_finances(conn: Connection) -> dict:
    total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM finance_transactions"
    ).fetchone()["total"]
    categories = [
        dict(row)
        for row in conn.execute(
            """
            SELECT c.name AS category_name, ROUND(SUM(t.amount), 2) AS total_amount
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            GROUP BY c.name
            ORDER BY total_amount ASC
            """
        ).fetchall()
    ]
    return {"net_total": total, "categories": categories}


def find_anomalies(conn: Connection) -> dict:
    items = []
    for row in conn.execute(
        """
        SELECT t.id, t.description, t.amount, c.name AS category_name
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        WHERE t.amount <= -250 AND COALESCE(c.name, 'Uncategorized') = 'Uncategorized'
        ORDER BY t.amount ASC
        """
    ).fetchall():
        items.append(
            {
                "kind": "large_uncategorized",
                "transaction_id": row["id"],
                "description": row["description"],
                "amount": row["amount"],
            }
        )
    return {"items": items}


def sensitive_query(
    conn: Connection,
    limit: int = 50,
    session_ref: str | None = None,
) -> dict:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                t.id,
                t.posted_at,
                t.description,
                t.amount,
                a.name AS account_name,
                c.name AS category_name
            FROM finance_transactions t
            JOIN finance_accounts a ON a.id = t.account_id
            LEFT JOIN finance_categories c ON c.id = t.category_id
            ORDER BY t.posted_at DESC, t.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    ]
    log_sensitive_access(conn, "sensitive_finance_query", session_ref, f"Returned {len(rows)} rows")
    return {"transactions": rows}
```

Extend `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py` with:

```python
from minx_mcp.finance.analytics import sensitive_query, summarize_finances, find_anomalies


    def add_category_rule(self, category_name: str, match_kind: str, pattern: str) -> None:
        category_id = self.conn.execute(
            "SELECT id FROM finance_categories WHERE name = ?",
            (category_name,),
        ).fetchone()["id"]
        self.conn.execute(
            """
            INSERT INTO finance_category_rules (category_id, match_kind, pattern)
            VALUES (?, ?, ?)
            """,
            (category_id, match_kind, pattern),
        )
        self.conn.commit()

    def apply_category_rules(self) -> None:
        rules = self.conn.execute(
            """
            SELECT r.pattern, r.match_kind, r.category_id, c.name AS category_name
            FROM finance_category_rules r
            JOIN finance_categories c ON c.id = r.category_id
            ORDER BY r.priority ASC, r.id ASC
            """
        ).fetchall()
        for rule in rules:
            if rule["match_kind"] != "merchant_contains":
                continue
            self.conn.execute(
                """
                UPDATE finance_transactions
                SET category_id = ?, category_source = 'rule'
                WHERE merchant LIKE ?
                """,
                (rule["category_id"], f"%{rule['pattern']}%"),
            )
        self.conn.commit()

    def finance_categorize(self, transaction_ids: list[int], category_name: str) -> None:
        category_id = self.conn.execute(
            "SELECT id FROM finance_categories WHERE name = ?",
            (category_name,),
        ).fetchone()["id"]
        placeholders = ",".join("?" for _ in transaction_ids)
        self.conn.execute(
            f"""
            UPDATE finance_transactions
            SET category_id = ?, category_source = 'manual'
            WHERE id IN ({placeholders})
            """,
            [category_id, *transaction_ids],
        )
        self.conn.commit()

    def safe_finance_summary(self) -> dict:
        return summarize_finances(self.conn)

    def finance_anomalies(self) -> dict:
        return find_anomalies(self.conn)

    def sensitive_finance_query(self, limit: int = 50, session_ref: str | None = None) -> dict:
        return sensitive_query(self.conn, limit=limit, session_ref=session_ref)
```

- [ ] **Step 4: Implement weekly and monthly report generation**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/reports.py`:

```python
from __future__ import annotations

import json
from sqlite3 import Connection


def build_weekly_report(conn: Connection, period_start: str, period_end: str) -> dict:
    rows = conn.execute(
        """
        SELECT COALESCE(c.name, 'Uncategorized') AS category_name, ROUND(SUM(t.amount), 2) AS total_amount
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        WHERE t.posted_at BETWEEN ? AND ?
        GROUP BY COALESCE(c.name, 'Uncategorized')
        ORDER BY total_amount ASC
        """,
        (period_start, period_end),
    ).fetchall()
    return {
        "period_start": period_start,
        "period_end": period_end,
        "categories": [dict(row) for row in rows],
    }


def build_monthly_report(conn: Connection, period_start: str, period_end: str) -> dict:
    rows = conn.execute(
        """
        SELECT a.name AS account_name, ROUND(SUM(t.amount), 2) AS total_amount
        FROM finance_transactions t
        JOIN finance_accounts a ON a.id = t.account_id
        WHERE t.posted_at BETWEEN ? AND ?
        GROUP BY a.name
        ORDER BY total_amount ASC
        """,
        (period_start, period_end),
    ).fetchall()
    return {
        "period_start": period_start,
        "period_end": period_end,
        "accounts": [dict(row) for row in rows],
    }


def persist_report_run(
    conn: Connection,
    report_kind: str,
    period_start: str,
    period_end: str,
    vault_path: str,
    summary: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO finance_report_runs (report_kind, period_start, period_end, vault_path, summary_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (report_kind, period_start, period_end, vault_path, json.dumps(summary)),
    )
    conn.commit()
```

Create `/Users/akmini/Documents/minx-mcp/templates/finance-weekly-summary.md`:

```markdown
# Weekly Finance Summary

Period: ${period_start} to ${period_end}

## Category Totals
${category_lines}
```

Create `/Users/akmini/Documents/minx-mcp/templates/finance-monthly-summary.md`:

```markdown
# Monthly Finance Summary

Period: ${period_start} to ${period_end}

## Account Totals
${account_lines}
```

Extend `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py` with:

```python
from pathlib import Path
from string import Template

from minx_mcp.finance.reports import build_monthly_report, build_weekly_report, persist_report_run


    def generate_weekly_report(self, period_start: str, period_end: str) -> dict:
        summary = build_weekly_report(self.conn, period_start, period_end)
        template = Template(Path("templates/finance-weekly-summary.md").read_text())
        category_lines = "\n".join(
            f"- {item['category_name']}: {item['total_amount']}"
            for item in summary["categories"]
        ) or "- No transactions"
        content = template.substitute(
            period_start=period_start,
            period_end=period_end,
            category_lines=category_lines,
        )
        relative_path = f"Finance/weekly-{period_start}.md"
        path = self.vault_writer.write_markdown(relative_path, content)
        persist_report_run(self.conn, "weekly", period_start, period_end, str(path), summary)
        return {"vault_path": str(path), "summary": summary}

    def generate_monthly_report(self, period_start: str, period_end: str) -> dict:
        summary = build_monthly_report(self.conn, period_start, period_end)
        template = Template(Path("templates/finance-monthly-summary.md").read_text())
        account_lines = "\n".join(
            f"- {item['account_name']}: {item['total_amount']}"
            for item in summary["accounts"]
        ) or "- No transactions"
        content = template.substitute(
            period_start=period_start,
            period_end=period_end,
            account_lines=account_lines,
        )
        relative_path = f"Finance/monthly-{period_start[:7]}.md"
        path = self.vault_writer.write_markdown(relative_path, content)
        persist_report_run(self.conn, "monthly", period_start, period_end, str(path), summary)
        return {"vault_path": str(path), "summary": summary}
```

- [ ] **Step 5: Run the finance service and reporting tests**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_finance_service.py tests/test_finance_reports.py -v
```

Expected:

```text
5 passed
```

- [ ] **Step 6: Commit the finance service layer**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && git add minx_mcp/finance templates tests/test_finance_service.py tests/test_finance_reports.py && git commit -m "feat: add finance service categorization and reports"
```

### Task 6: Add The Finance MCP Server, CLI Entry Point, And End-To-End Verification

**Files:**
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/server.py`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/__main__.py`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_finance_server.py`
- Create: `/Users/akmini/Documents/minx-mcp/tests/test_end_to_end.py`
- Modify: `/Users/akmini/Documents/minx-mcp/README.md`

- [ ] **Step 1: Write the failing MCP server and end-to-end tests**

Create `/Users/akmini/Documents/minx-mcp/tests/test_finance_server.py`:

```python
from mcp.server.fastmcp import FastMCP

from minx_mcp.db import get_connection
from minx_mcp.finance.server import SAFE_TOOLS, SENSITIVE_TOOLS, create_finance_server
from minx_mcp.finance.service import FinanceService


def test_finance_server_registers_expected_tool_names(tmp_path):
    service = FinanceService(get_connection(tmp_path / "minx.db"), tmp_path / "vault")
    server = create_finance_server(service)
    assert isinstance(server, FastMCP)
    assert SAFE_TOOLS == [
        "safe_finance_summary",
        "safe_finance_accounts",
        "finance_import",
        "finance_categorize",
        "finance_anomalies",
        "finance_job_status",
        "finance_generate_weekly_report",
        "finance_generate_monthly_report",
    ]
    assert SENSITIVE_TOOLS == ["sensitive_finance_query"]


def test_streamable_http_app_is_available(tmp_path):
    service = FinanceService(get_connection(tmp_path / "minx.db"), tmp_path / "vault")
    server = create_finance_server(service)
    app = server.streamable_http_app()
    assert callable(app)
```

Create `/Users/akmini/Documents/minx-mcp/tests/test_end_to_end.py`:

```python
from minx_mcp.db import get_connection
from minx_mcp.finance.service import FinanceService


def test_import_to_summary_to_report_flow(tmp_path):
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text("Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n")
    vault = tmp_path / "vault"
    service = FinanceService(get_connection(tmp_path / "minx.db"), vault)
    imported = service.finance_import(str(source), account_name="Robinhood Gold")
    summary = service.safe_finance_summary()
    report = service.generate_monthly_report("2026-03-01", "2026-03-31")
    assert imported["result"]["inserted"] == 1
    assert summary["categories"]
    assert report["vault_path"].endswith("Finance/monthly-2026-03.md")
```

- [ ] **Step 2: Implement the FastMCP server wrapper**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/server.py`:

```python
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


SAFE_TOOLS = [
    "safe_finance_summary",
    "safe_finance_accounts",
    "finance_import",
    "finance_categorize",
    "finance_anomalies",
    "finance_job_status",
    "finance_generate_weekly_report",
    "finance_generate_monthly_report",
]

SENSITIVE_TOOLS = ["sensitive_finance_query"]


def create_finance_server(service) -> FastMCP:
    mcp = FastMCP("minx-finance", stateless_http=True, json_response=True)

    @mcp.tool(name="safe_finance_summary")
    def safe_finance_summary() -> dict:
        return service.safe_finance_summary()

    @mcp.tool(name="safe_finance_accounts")
    def safe_finance_accounts() -> dict:
        rows = service.conn.execute(
            "SELECT name, account_type, last_imported_at FROM finance_accounts ORDER BY name"
        ).fetchall()
        return {"accounts": [dict(row) for row in rows]}

    @mcp.tool(name="finance_import")
    def finance_import(source_ref: str, account_name: str, source_kind: str | None = None) -> dict:
        return service.finance_import(source_ref, account_name, source_kind=source_kind)

    @mcp.tool(name="finance_categorize")
    def finance_categorize(transaction_ids: list[int], category_name: str) -> dict:
        service.finance_categorize(transaction_ids, category_name)
        return {"updated": len(transaction_ids)}

    @mcp.tool(name="finance_anomalies")
    def finance_anomalies() -> dict:
        return service.finance_anomalies()

    @mcp.tool(name="finance_job_status")
    def finance_job_status(job_id: str) -> dict | None:
        return service.get_job(job_id)

    @mcp.tool(name="finance_generate_weekly_report")
    def finance_generate_weekly_report(period_start: str, period_end: str) -> dict:
        return service.generate_weekly_report(period_start, period_end)

    @mcp.tool(name="finance_generate_monthly_report")
    def finance_generate_monthly_report(period_start: str, period_end: str) -> dict:
        return service.generate_monthly_report(period_start, period_end)

    @mcp.tool(name="sensitive_finance_query")
    def sensitive_finance_query(limit: int = 50, session_ref: str | None = None) -> dict:
        return service.sensitive_finance_query(limit=limit, session_ref=session_ref)

    return mcp
```

- [ ] **Step 3: Add the CLI entrypoint and expose job lookup**

Extend `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py` with:

```python
    def get_job(self, job_id: str) -> dict | None:
        return get_job(self.conn, job_id)
```

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/__main__.py`:

```python
from __future__ import annotations

import argparse

from minx_mcp.config import get_settings
from minx_mcp.db import get_connection
from minx_mcp.finance.server import create_finance_server
from minx_mcp.finance.service import FinanceService
from minx_mcp.transport import run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "http"], default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser


def main() -> None:
    settings = get_settings()
    args = build_parser().parse_args()
    conn = get_connection(settings.db_path)
    service = FinanceService(conn, settings.vault_path)
    server = create_finance_server(service)
    run_server(
        server,
        transport=args.transport or settings.default_transport,
        host=args.host or settings.http_host,
        port=args.port or settings.http_port,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Update the README with local setup and runtime commands**

Replace `/Users/akmini/Documents/minx-mcp/README.md` with:

````markdown
# minx-mcp

Shared Minx MCP platform and finance domain.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Run finance over stdio

```bash
.venv/bin/python -m minx_mcp.finance --transport stdio
```

## Run finance over HTTP

```bash
.venv/bin/python -m minx_mcp.finance --transport http --host 127.0.0.1 --port 8000
```

The HTTP transport uses FastMCP streamable HTTP and is intended as the runtime seam for later dashboard work.
````

- [ ] **Step 5: Run the full first-slice test suite**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest -v
```

Expected:

```text
All tests pass, including parser, service, report, server, and end-to-end coverage.
```

- [ ] **Step 6: Commit the finance MCP surface**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && git add README.md minx_mcp tests && git commit -m "feat: add finance mcp server and end-to-end coverage"
```

## Self-Review

### Spec coverage

- Shared core platform: covered in Tasks 1 through 3.
- Finance importers and normalization: covered in Task 4.
- Manual and rule-based categorization: covered in Task 5.
- Safe summaries, sensitive queries, and audit logging: covered in Tasks 3, 5, and 6.
- Weekly and monthly vault reports: covered in Task 5.
- `stdio` and HTTP-capable transport: covered in Tasks 3 and 6.

### Placeholder scan

- No placeholder markers remain.
- Each task includes exact file paths, commands, and concrete code snippets.

### Type consistency

- Finance parser output consistently uses `posted_at`, `description`, `amount`, `merchant`, `category_hint`, and `external_id`.
- Service methods consistently use `finance_*`, `safe_*`, and `sensitive_*` naming.
- Transport naming consistently maps `http` to FastMCP `streamable-http`.
