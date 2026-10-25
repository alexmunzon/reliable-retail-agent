"""Synthetic retail provider used as the external side-effect boundary in tests and demos.

Contract (mode "full"):
  * POST /v1/mutations applies an order-address change and records the effect in the
    same SQLite transaction, keyed by a caller-supplied idempotency key. The same key
    with the same request returns the stored result without a second effect; the same
    key with a different request is rejected with no effect. `expected_revision` is a
    precondition checked at apply time.
  * GET /v1/mutations/{key} is linearizable with mutations (one SQLite writer). If the
    key is absent it is FENCED in the same transaction, so a delayed request carrying
    that key can never apply later. "not_found" is therefore authoritative no-effect.
Mode "basic" models a provider without these guarantees: no key deduplication, no
revision precondition, and no status lookup (501). Callers must leave ambiguity unresolved.

Failure injection is accepted only when started with --test-mode, through an
authenticated test endpoint. It is not reachable through the serving API.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import sqlite3
import threading
import time
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 16_384
KEY_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{8,128}\Z")
ADDRESS_FIELDS = ("street", "apartment", "city", "state", "postal_code", "country")
SEED_ORDERS = [
    ("acme", "SYN-ORDER-1042", {"street": "7 Original Road", "apartment": "1", "city": "Sampleton",
                                "state": "CA", "postal_code": "00000", "country": "US"}),
    ("acme", "SYN-ORDER-1043", {"street": "9 Original Road", "apartment": "3", "city": "Sampleton",
                                "state": "CA", "postal_code": "00000", "country": "US"}),
    ("globex", "SYN-ORDER-2077", {"street": "1 Fixture Way", "apartment": "", "city": "Testville",
                                  "state": "NY", "postal_code": "00000", "country": "US"}),
]
SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (tenant TEXT NOT NULL, order_id TEXT NOT NULL, address TEXT NOT NULL,
  revision INTEGER NOT NULL, PRIMARY KEY (tenant, order_id));
CREATE TABLE IF NOT EXISTS effects (effect_id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL, tenant TEXT NOT NULL, order_id TEXT NOT NULL, address TEXT NOT NULL,
  revision_after INTEGER NOT NULL, applied_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS effects_by_key ON effects(idempotency_key);
CREATE TABLE IF NOT EXISTS fences (idempotency_key TEXT PRIMARY KEY, fenced_at REAL NOT NULL);
"""


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_db(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        for statement in filter(None, (part.strip() for part in SCHEMA.split(";"))):
            conn.execute(statement)
        for tenant, order_id, address in SEED_ORDERS:
            conn.execute("INSERT OR IGNORE INTO orders VALUES (?, ?, ?, 1)", (tenant, order_id, canonical(address)))
        conn.execute("COMMIT")
    finally:
        conn.close()


class ProviderServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, *, db_path: Path, key: str, mode: str, test_mode: bool):
        self.db_path, self.key, self.mode, self.test_mode = db_path, key, mode, test_mode
        self.failpoints: list[str] = []
        self.failpoints_lock = threading.Lock()
        super().__init__(address, Handler)

    def take_failpoint(self, kinds: set[str]) -> str | None:
        with self.failpoints_lock:
            for index, action in enumerate(self.failpoints):
                if action.split(":", 1)[0] in kinds:
                    return self.failpoints.pop(index)
        return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server: ProviderServer

    def log_message(self, format, *args):
        pass  # No request payloads (addresses) in provider logs.

    def _send(self, status: int, body, *, raw: bytes | None = None) -> None:
        data = raw if raw is not None else canonical(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.key
        return hmac.compare_digest(header.encode(), expected.encode())

    def _body(self):
        length = self.headers.get("Content-Length", "0")
        if not length.isdigit() or int(length) > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(int(length)) or b"{}")
        except ValueError:
            return None

    def _crash(self) -> None:
        # Deterministic process death: no response, no cleanup, durable state only.
        os._exit(75)

    def do_GET(self):
        if not self._authorized():
            return self._send(401, {"code": "unauthorized"})
        if self.path == "/v1/health":
            return self._send(200, {"status": "ok", "mode": self.server.mode})
        match = re.fullmatch(r"/v1/orders/([A-Za-z0-9-]{1,64})/([A-Za-z0-9-]{1,64})", self.path)
        if match:
            conn = open_db(self.server.db_path)
            try:
                row = conn.execute("SELECT * FROM orders WHERE tenant=? AND order_id=?", match.groups()).fetchone()
            finally:
                conn.close()
            if row is None:
                return self._send(404, {"code": "order_not_found"})
            return self._send(200, {"tenant": row["tenant"], "order_id": row["order_id"],
                                    "address": json.loads(row["address"]), "revision": row["revision"]})
        match = re.fullmatch(r"/v1/mutations/([A-Za-z0-9_.:-]{8,128})", self.path)
        if match:
            return self._status(match[1])
        return self._send(404, {"code": "not_found"})

    def _status(self, key: str):
        if self.server.mode != "full":
            return self._send(501, {"code": "status_unsupported"})
        action = self.server.take_failpoint({"status_unavailable", "status_crash"})
        if action == "status_crash":
            self._crash()
        if action == "status_unavailable":
            return self._send(503, {"code": "status_unavailable"})
        conn = open_db(self.server.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM effects WHERE idempotency_key=? ORDER BY effect_id LIMIT 1", (key,)).fetchone()
            if row is None:
                conn.execute("INSERT OR IGNORE INTO fences VALUES (?, ?)", (key, time.time()))
            conn.execute("COMMIT")
        finally:
            conn.close()
        if row is None:
            return self._send(200, {"status": "not_found", "fenced": True, "idempotency_key": key})
        return self._send(200, self._applied(row))

    @staticmethod
    def _applied(row) -> dict:
        return {"status": "applied", "idempotency_key": row["idempotency_key"], "effect_id": row["effect_id"],
                "order_id": row["order_id"], "revision": row["revision_after"]}

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {"code": "unauthorized"})
        body = self._body()
        if self.path == "/v1/test/failpoints":
            if not self.server.test_mode or not isinstance(body, dict) or not isinstance(body.get("actions"), list):
                return self._send(404, {"code": "not_found"})
            with self.server.failpoints_lock:
                self.server.failpoints = [str(item) for item in body["actions"]]
            return self._send(200, {"armed": self.server.failpoints})
        if self.path != "/v1/mutations":
            return self._send(404, {"code": "not_found"})
        if not isinstance(body, dict) or set(body) != {"idempotency_key", "tenant", "tool_name", "arguments", "expected_revision"}:
            return self._send(422, {"code": "invalid_request", "effect": "none"})
        args = body["arguments"]
        if (body["tool_name"] != "change_order_address" or not isinstance(body["idempotency_key"], str)
                or not KEY_PATTERN.fullmatch(body["idempotency_key"]) or not isinstance(args, dict)
                or set(args) != {"order_id", "address"} or not isinstance(args["address"], dict)
                or set(args["address"]) != set(ADDRESS_FIELDS) or type(body["expected_revision"]) is not int):
            return self._send(422, {"code": "invalid_request", "effect": "none"})
        action = self.server.take_failpoint({"crash_before_accept", "crash_after_commit", "reject_invalid",
                                             "rate_limited", "slow", "hang", "malformed_after_commit"})
        if action == "crash_before_accept":
            self._crash()
        if action == "reject_invalid":
            return self._send(422, {"code": "invalid_request", "effect": "none"})
        if action == "rate_limited":
            return self._send(429, {"code": "rate_limited", "effect": "none"})
        if action and action.startswith("slow:"):
            time.sleep(float(action.split(":", 1)[1]))
        if action == "hang":
            time.sleep(120)
        status, result = self._apply(body)
        if action == "crash_after_commit":
            self._crash()
        if action == "malformed_after_commit":
            return self._send(200, None, raw=b"{not json")
        return self._send(status, result)

    def _apply(self, body: dict) -> tuple[int, dict]:
        key, tenant, args = body["idempotency_key"], body["tenant"], body["arguments"]
        request_hash = sha256(canonical(body).encode()).hexdigest()
        full = self.server.mode == "full"
        conn = open_db(self.server.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if full:
                    if conn.execute("SELECT 1 FROM fences WHERE idempotency_key=?", (key,)).fetchone():
                        conn.execute("ROLLBACK")
                        return 409, {"code": "key_fenced", "effect": "none"}
                    prior = conn.execute("SELECT * FROM effects WHERE idempotency_key=?", (key,)).fetchone()
                    if prior is not None:
                        conn.execute("ROLLBACK")
                        if prior["request_hash"] != request_hash:
                            return 409, {"code": "key_conflict", "effect": "none"}
                        return 200, dict(self._applied(prior), duplicate=True)
                order = conn.execute("SELECT revision FROM orders WHERE tenant=? AND order_id=?",
                                     (tenant, args["order_id"])).fetchone()
                if order is None:
                    conn.execute("ROLLBACK")
                    return 404, {"code": "order_not_found", "effect": "none"}
                if full and order["revision"] != body["expected_revision"]:
                    conn.execute("ROLLBACK")
                    return 409, {"code": "revision_conflict", "effect": "none", "current_revision": order["revision"]}
                revision = order["revision"] + 1
                conn.execute("UPDATE orders SET address=?, revision=? WHERE tenant=? AND order_id=?",
                             (canonical(args["address"]), revision, tenant, args["order_id"]))
                cursor = conn.execute("INSERT INTO effects (idempotency_key, request_hash, tenant, order_id, address, "
                                      "revision_after, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                      (key, request_hash, tenant, args["order_id"], canonical(args["address"]),
                                       revision, time.time()))
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        return 200, {"status": "applied", "idempotency_key": key, "effect_id": cursor.lastrowid,
                     "order_id": args["order_id"], "revision": revision}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synthetic retail provider (local pilot only)")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--mode", choices=("full", "basic"), default="full")
    parser.add_argument("--test-mode", action="store_true")
    args = parser.parse_args(argv)
    db_path = args.data_dir / "provider.db"
    init_db(db_path)
    key = args.key_file.read_text(encoding="utf-8").strip()
    with ProviderServer(("127.0.0.1", args.port), db_path=db_path, key=key, mode=args.mode,
                        test_mode=args.test_mode) as server:
        print(f"PROVIDER_READY {server.server_port} {args.mode}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
