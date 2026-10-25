"""Opaque bearer tokens for the local pilot: random secret, stored only as a SHA-256 verifier."""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

TOKEN_PATTERN = re.compile(r"rra_[A-Za-z0-9_-]{43}\Z")
SCOPES = frozenset({"runs:read", "runs:write", "consent:write", "ops:read"})
CUSTOMER_SCOPES = ("runs:read", "runs:write", "consent:write")

# Synthetic pilot identities. Tokens are generated per installation, never committed.
SEED_PRINCIPALS = [
    {"actor_id": "alice", "tenant_id": "acme", "scopes": CUSTOMER_SCOPES, "resources": ["SYN-ORDER-1042"]},
    {"actor_id": "carol", "tenant_id": "acme", "scopes": CUSTOMER_SCOPES, "resources": ["SYN-ORDER-1043"]},
    {"actor_id": "bob", "tenant_id": "globex", "scopes": CUSTOMER_SCOPES, "resources": ["SYN-ORDER-2077"]},
    {"actor_id": "dave", "tenant_id": "acme", "scopes": ("runs:read",), "resources": []},
    {"actor_id": "operator", "tenant_id": "ops", "scopes": ("ops:read",), "resources": []},
]


@dataclass(frozen=True)
class Principal:
    actor_id: str
    tenant_id: str
    scopes: frozenset[str]
    # Internal request identity: rotation changes this even when wall clocks repeat or move backward.
    # Never include the verifier in logs, public responses, or exported traces.
    credential_verifier: str = field(default="", repr=False)

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def new_token() -> str:
    return "rra_" + secrets.token_urlsafe(32)


def verifier(token: str) -> str:
    return sha256(token.encode("ascii")).hexdigest()


def parse_bearer(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not TOKEN_PATTERN.fullmatch(token):
        return None
    return token


def resolve(conn: sqlite3.Connection, token: str) -> Principal | None:
    digest = verifier(token)
    row = conn.execute("SELECT actor_id, tenant_id, scopes, status, token_hash FROM principals WHERE token_hash=?",
                       (digest,)).fetchone()
    if row is None or row["status"] != "active" or not hmac.compare_digest(row["token_hash"], digest):
        return None
    return Principal(row["actor_id"], row["tenant_id"], frozenset(json.loads(row["scopes"])), digest)


def refresh(conn: sqlite3.Connection, principal: Principal) -> Principal | None:
    """Revalidate a previously resolved caller within the transaction using it."""
    row = conn.execute("SELECT actor_id, tenant_id, scopes, status, token_hash FROM principals WHERE actor_id=?",
                       (principal.actor_id,)).fetchone()
    if (row is None or row["status"] != "active" or row["tenant_id"] != principal.tenant_id
            or not principal.credential_verifier
            or not hmac.compare_digest(row["token_hash"], principal.credential_verifier)):
        return None
    return Principal(row["actor_id"], row["tenant_id"], frozenset(json.loads(row["scopes"])), row["token_hash"])


def is_active(conn: sqlite3.Connection, principal: Principal, token: str) -> bool:
    current = resolve(conn, token)
    return (current is not None and current.actor_id == principal.actor_id
            and current.tenant_id == principal.tenant_id and current.scopes == principal.scopes
            and hmac.compare_digest(current.credential_verifier, principal.credential_verifier))


def write_credentials(path: Path, tokens: dict[str, str]) -> None:
    """Owner-only file; created with 0600 before any secret is written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    existing.update(tokens)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def seed(conn: sqlite3.Connection) -> dict[str, str]:
    """Insert missing synthetic principals and resources; return only newly issued tokens."""
    issued: dict[str, str] = {}
    now = time.time()
    for item in SEED_PRINCIPALS:
        if conn.execute("SELECT 1 FROM principals WHERE actor_id=?", (item["actor_id"],)).fetchone():
            continue
        token = new_token()
        conn.execute("INSERT INTO principals VALUES (?, ?, ?, ?, 'active', ?, ?)",
                     (item["actor_id"], item["tenant_id"], verifier(token), json.dumps(sorted(item["scopes"])), now, now))
        for resource_id in item["resources"]:
            conn.execute("INSERT INTO resources VALUES (?, ?, ?, NULL)", (item["tenant_id"], resource_id, item["actor_id"]))
        issued[item["actor_id"]] = token
    return issued


def rotate(conn: sqlite3.Connection, actor_id: str) -> str:
    token = new_token()
    updated = conn.execute("UPDATE principals SET token_hash=?, status='active', rotated_at=? WHERE actor_id=?",
                           (verifier(token), time.time(), actor_id)).rowcount
    if updated != 1:
        raise KeyError(actor_id)
    return token


def revoke(conn: sqlite3.Connection, actor_id: str) -> None:
    # Replace the verifier too, so the old secret can never be re-activated by rotation mistakes.
    updated = conn.execute("UPDATE principals SET status='revoked', token_hash=?, rotated_at=? WHERE actor_id=?",
                           (verifier(new_token()), time.time(), actor_id)).rowcount
    if updated != 1:
        raise KeyError(actor_id)
