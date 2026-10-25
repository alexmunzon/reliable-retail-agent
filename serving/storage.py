"""SQLite storage: explicit SQL, checksummed forward migrations, short transactions.

Supported topology: exactly one service process writes this file on a local
filesystem. Correctness comes from transactions, compare-and-set updates, and
unique constraints, never from Python locks.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import sqlite3
import time
from pathlib import Path
from typing import Iterator

BUSY_TIMEOUT_MS = 5_000

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "core-ledger", """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE principals (
  actor_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  scopes TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  created_at REAL NOT NULL,
  rotated_at REAL NOT NULL
);
CREATE TABLE resources (
  tenant_id TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  owner_actor_id TEXT NOT NULL REFERENCES principals(actor_id),
  known_revision INTEGER,
  PRIMARY KEY (tenant_id, resource_id)
);
CREATE TABLE idempotency (
  actor_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  response TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  PRIMARY KEY (actor_id, tenant_id, scope, key)
);
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL REFERENCES principals(actor_id),
  resource_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (tenant_id, resource_id) REFERENCES resources(tenant_id, resource_id)
);
CREATE INDEX sessions_by_actor ON sessions(actor_id, expires_at);
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(session_id),
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  state TEXT NOT NULL,
  input TEXT NOT NULL,
  state_schema_version TEXT NOT NULL,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  next_seq INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX runs_by_actor_state ON runs(actor_id, state);
CREATE TABLE proposals (
  operation_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
  session_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  resource_revision INTEGER NOT NULL,
  tool_name TEXT NOT NULL,
  canonical_payload TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  prompt_id TEXT NOT NULL UNIQUE,
  prompt_text TEXT NOT NULL,
  proposal_version INTEGER NOT NULL,
  canonical_schema_version TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  classifier_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('awaiting_consent', 'authorized', 'consumed', 'rejected',
                                       'invalidated', 'expired', 'cancelled')),
  consent_binding_hash TEXT,
  response_sha256 TEXT,
  response_classification TEXT,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  decided_at REAL,
  authorization_expires_at REAL,
  consumed_at REAL
);
CREATE TABLE operations (
  operation_id TEXT PRIMARY KEY REFERENCES proposals(operation_id),
  run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
  tenant_id TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  provider_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK (state IN ('pending', 'dispatching', 'succeeded', 'failed_no_effect',
                                       'unknown', 'cancelled', 'invalidated')),
  claim_id TEXT UNIQUE,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count IN (0, 1)),
  created_at REAL NOT NULL,
  claimed_at REAL,
  finished_at REAL,
  error_code TEXT,
  provider_result TEXT,
  reconciliation TEXT,
  reconcile_attempts INTEGER NOT NULL DEFAULT 0,
  next_reconcile_at REAL
);
CREATE INDEX operations_by_state ON operations(state, created_at);
CREATE TABLE resource_holds (
  tenant_id TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  operation_id TEXT NOT NULL UNIQUE REFERENCES operations(operation_id),
  created_at REAL NOT NULL,
  PRIMARY KEY (tenant_id, resource_id)
);
CREATE TABLE events (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  envelope TEXT NOT NULL,
  occurred_at REAL NOT NULL,
  PRIMARY KEY (run_id, sequence)
) WITHOUT ROWID;
"""),
    (2, "event-retention", """
ALTER TABLE runs ADD COLUMN purged_through_seq INTEGER NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN terminal_at REAL;
CREATE INDEX runs_by_terminal ON runs(terminal_at) WHERE terminal_at IS NOT NULL;
CREATE INDEX idempotency_by_expiry ON idempotency(expires_at);
"""),
]
SCHEMA_VERSION = MIGRATIONS[-1][0]


class SchemaError(RuntimeError):
    """The database schema is unknown, newer, or altered; fail closed."""


def checksum(sql: str) -> str:
    return sha256(sql.encode("utf-8")).hexdigest()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000,
                           check_same_thread=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE takes the single writer lock up front: no upgrade deadlocks."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextmanager
def read_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.execute("COMMIT")


def applied_versions(conn: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    if not exists:
        return {}
    return {row["version"]: (row["name"], row["checksum"])
            for row in conn.execute("SELECT version, name, checksum FROM schema_migrations")}


def migrate(path: Path, *, target: int = SCHEMA_VERSION) -> int:
    """Apply missing migrations in order; refuse altered, unknown, or newer schemas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode.lower() != "wal":
            raise SchemaError(f"WAL journal mode unavailable: {mode}")
        with write_tx(conn):
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
                         "name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at REAL NOT NULL)")
        known = {version: (name, checksum(sql)) for version, name, sql in MIGRATIONS}
        applied = applied_versions(conn)
        for version, record in applied.items():
            if version not in known:
                raise SchemaError(f"Database schema version {version} is newer than this code supports")
            if record != known[version]:
                raise SchemaError(f"Migration {version} checksum mismatch; refusing to run")
        for version, name, sql in MIGRATIONS:
            if version in applied or version > target:
                continue
            # executescript would COMMIT implicitly; run statements inside one explicit transaction.
            with write_tx(conn):
                for statement in (part.strip() for part in sql.split(";")):
                    if statement:
                        conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                             (version, name, checksum(sql), time.time()))
        return max(applied_versions(conn), default=0)
    finally:
        conn.close()


def check_schema(path: Path) -> None:
    """Open-time guard for the service: current, unaltered schema or refuse to start."""
    if not path.exists():
        raise SchemaError("Database does not exist; run `scripts/serving.py init`")
    conn = connect(path)
    try:
        applied = applied_versions(conn)
    finally:
        conn.close()
    expected = {version: (name, checksum(sql)) for version, name, sql in MIGRATIONS}
    if applied != expected:
        raise SchemaError("Database schema is not the current supported version; run `scripts/serving.py migrate`")


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM meta WHERE key=?", (key,))
    else:
        conn.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def backup(source: Path, destination: Path) -> None:
    """Use SQLite's online backup API so committed WAL content is included."""
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = connect(source)
    dst = sqlite3.connect(str(destination))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
