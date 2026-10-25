"""Independent synthetic provider for evaluating the durable backend (trusted harness code).

It speaks the backend's provider wire contract (retail-durable-backend `provider_sim/server.py`
docstring and `serving/provider.py`), but it is written here, runs inside the harness process,
and never imports backend code. The backend reaches it only over loopback HTTP, so it cannot
speak for this ledger: every mutation request is recorded on arrival, before any effect.

Contract modelled ("full" provider): POST /v1/mutations applies an address change keyed by an
idempotency key (same key + same request returns the stored result without a second effect; same
key + different request is `key_conflict`; `expected_revision` is checked at apply time). GET
/v1/mutations/{key} answers `applied`, or `not_found` and FENCES the key so a late request can
never apply. Definite no-effect replies carry `"effect": "none"`. Failure actions are armed by the
harness in-process (never over HTTP). They keep three different facts apart:
  * lose_request_before_arrival: the connection is cut before the provider accepts the request, so it is
    NOT a received mutation request (it is counted only in `requests_lost_before_arrival`);
  * accept_then_drop_before_effect: the request is received (one mutation request), then dropped with no effect;
  * lose_response_after_effect: the request is received and applied (one request, one effect), the reply is lost;
plus a definite refusal, holding the reply after the effect until the harness releases it, and an
unavailable status lookup. Received requests, status lookups and applied effects are reported separately.
"""
from __future__ import annotations

from copy import deepcopy
import hmac
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading

from .scoring import canon

TOOL = "change_order_address"
ADDRESS_FIELDS = ("street", "apartment", "city", "state", "postal_code", "country")
KEY_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{8,128}\Z")
MAX_BODY = 16_384
ACTIONS = frozenset({"lose_request_before_arrival", "accept_then_drop_before_effect", "lose_response_after_effect",
                     "reject_before_effect", "hold_after_effect", "status_unavailable", "status_unavailable_always"})


def initial_orders() -> dict:
    """The provider-side fixture; tenants and order ids match the backend's seeded principals."""
    def address(street, apartment, postal_code):
        return {"street": street, "apartment": apartment, "city": "Sampleton", "state": "CA",
                "postal_code": postal_code, "country": "US"}
    return {"acme/SYN-ORDER-1042": {"address": address("7 Original Road", "1", "00000"), "revision": 1},
            "acme/SYN-ORDER-1043": {"address": address("9 Original Road", "3", "00000"), "revision": 1},
            "globex/SYN-ORDER-2077": {"address": address("1 Fixture Way", "", "00001"), "revision": 1}}


class EvaluationProvider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, key: str, port: int = 0):
        self.key = key
        self.lock = threading.Lock()
        self.orders = initial_orders()
        self.effects: dict[str, dict] = {}  # idempotency key -> {"request_hash", "result"}
        self.fences: set[str] = set()
        self.attempts: list[dict] = []   # every authenticated POST /v1/mutations, recorded on arrival
        self.lookups: list[dict] = []    # every authenticated status lookup
        self.order_reads = 0
        self.lost_before_arrival: list[dict] = []  # connections cut before a request was accepted: not attempts
        self.held, self.release = threading.Event(), threading.Event()
        self.armed: list[str] = []
        self.status_down = False
        super().__init__(("127.0.0.1", port), _Handler)
        self.thread = threading.Thread(target=self.serve_forever, name="evalgate-provider", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self) -> "EvaluationProvider":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.release.set()
        self.shutdown()
        self.server_close()

    def arm(self, actions: list[str]) -> None:
        unknown = set(actions) - ACTIONS
        if unknown:
            raise ValueError(f"unknown provider actions {sorted(unknown)}")
        with self.lock:
            self.armed.extend(actions)
            if "status_unavailable_always" in actions:
                self.status_down = True

    def take(self, kinds: set[str]) -> str | None:
        with self.lock:
            for index, action in enumerate(self.armed):
                if action in kinds:
                    return self.armed.pop(index)
        return None

    def ledger(self) -> dict:
        """What the provider saw and holds now; read by the scorer, never by the backend."""
        with self.lock:
            return {"attempts": deepcopy(self.attempts), "lookups": deepcopy(self.lookups),
                    "requests_lost_before_arrival": deepcopy(self.lost_before_arrival),
                    "order_reads": self.order_reads, "orders": deepcopy(self.orders)}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server: EvaluationProvider

    def log_message(self, format, *args):
        pass

    def _send(self, status: int, body) -> None:
        data = canon(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        return hmac.compare_digest(self.headers.get("Authorization", "").encode(), f"Bearer {self.server.key}".encode())

    def do_GET(self):
        if not self._authorized():
            return self._send(401, {"code": "unauthorized"})
        if self.path == "/v1/health":
            return self._send(200, {"status": "ok", "mode": "full"})
        match = re.fullmatch(r"/v1/orders/([A-Za-z0-9-]{1,64})/([A-Za-z0-9-]{1,64})", self.path)
        if match:
            with self.server.lock:
                self.server.order_reads += 1
                order = deepcopy(self.server.orders.get("/".join(match.groups())))
            if order is None:
                return self._send(404, {"code": "order_not_found"})
            return self._send(200, {"tenant": match[1], "order_id": match[2], **order})
        match = re.fullmatch(r"/v1/mutations/([A-Za-z0-9_.:-]{8,128})", self.path)
        if match:
            return self._status(match[1])
        return self._send(404, {"code": "not_found"})

    def _status(self, key: str):
        server = self.server
        down = server.take({"status_unavailable"}) is not None or server.status_down
        with server.lock:
            record = {"key": key, "answer": "unavailable" if down else None}
            server.lookups.append(record)
            if not down:
                effect = server.effects.get(key)
                if effect is None:
                    server.fences.add(key)
                record["answer"] = "applied" if effect else "not_found_fenced"
        if down:
            return self._send(503, {"code": "status_unavailable"})
        if effect is None:
            return self._send(200, {"status": "not_found", "fenced": True, "idempotency_key": key})
        return self._send(200, {k: v for k, v in effect["result"].items() if k != "duplicate"})

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {"code": "unauthorized"})
        if self.path != "/v1/mutations":
            return self._send(404, {"code": "not_found"})
        server = self.server
        if server.take({"lose_request_before_arrival"}):
            with server.lock:
                server.lost_before_arrival.append({"path": self.path})
            self.close_connection = True
            return None  # cut before the request is accepted: no body read, not a received request
        length = self.headers.get("Content-Length", "0")
        try:
            body = json.loads(self.rfile.read(int(length)) or b"{}") if length.isdigit() and int(length) <= MAX_BODY else None
        except ValueError:
            body = None
        with server.lock:  # recorded exactly as received, before any effect
            record = {"body": deepcopy(body) if isinstance(body, dict) else {"$unparsed": True},
                      "effect_applied": False, "outcome": "received"}
            server.attempts.append(record)
        if not _well_formed(body):
            record["outcome"] = "invalid_request"
            return self._send(422, {"code": "invalid_request", "effect": "none"})
        action = server.take({"lose_response_after_effect", "accept_then_drop_before_effect", "reject_before_effect",
                              "hold_after_effect"})
        if action == "accept_then_drop_before_effect":
            record["outcome"] = "received_then_dropped_before_effect"
            self.close_connection = True
            return None  # connection closes with no response and no effect
        if action == "reject_before_effect":
            record["outcome"] = "rejected_before_effect"
            return self._send(422, {"code": "invalid_request", "effect": "none"})
        status, result = self._apply(body, record)
        if action == "lose_response_after_effect":
            record["outcome"] = "effect_then_lost_response" if record["effect_applied"] else record["outcome"]
            self.close_connection = True
            return None
        if action == "hold_after_effect":  # the effect is applied; the reply waits until the harness releases it
            server.held.set()
            server.release.wait(timeout=60)
        return self._send(status, result)

    def _apply(self, body: dict, record: dict) -> tuple[int, dict]:
        server, key = self.server, body["idempotency_key"]
        request_hash = sha256(canon(body).encode("utf-8")).hexdigest()
        order_key = f"{body['tenant']}/{body['arguments']['order_id']}"
        with server.lock:
            if key in server.fences:
                record["outcome"] = "key_fenced"
                return 409, {"code": "key_fenced", "effect": "none"}
            prior = server.effects.get(key)
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    record["outcome"] = "key_conflict"
                    return 409, {"code": "key_conflict", "effect": "none"}
                record["outcome"] = "duplicate_replayed"
                return 200, dict(prior["result"], duplicate=True)
            order = server.orders.get(order_key)
            if order is None:
                record["outcome"] = "order_not_found"
                return 404, {"code": "order_not_found", "effect": "none"}
            if order["revision"] != body["expected_revision"]:
                record["outcome"] = "revision_conflict"
                return 409, {"code": "revision_conflict", "effect": "none", "current_revision": order["revision"]}
            order["address"] = deepcopy(body["arguments"]["address"])
            order["revision"] += 1
            effect_id = sum(1 for _ in server.effects) + 1
            result = {"status": "applied", "idempotency_key": key, "effect_id": effect_id,
                      "order_id": body["arguments"]["order_id"], "revision": order["revision"]}
            server.effects[key] = {"request_hash": request_hash, "result": result}
            record["effect_applied"], record["outcome"] = True, "applied"
            return 200, result


def _well_formed(body) -> bool:
    if not isinstance(body, dict) or set(body) != {"idempotency_key", "tenant", "tool_name", "arguments", "expected_revision"}:
        return False
    args = body["arguments"]
    return (body["tool_name"] == TOOL and isinstance(body["idempotency_key"], str) and bool(KEY_PATTERN.fullmatch(body["idempotency_key"]))
            and isinstance(body["tenant"], str) and isinstance(args, dict) and set(args) == {"order_id", "address"}
            and isinstance(args["order_id"], str) and isinstance(args["address"], dict)
            and set(args["address"]) == set(ADDRESS_FIELDS) and type(body["expected_revision"]) is int)
