"""Application service: every state change is one short SQLite write transaction.

Each transition validates the expected state inside the transaction, writes its
outbox event with the next per-run sequence in that same transaction, and never
performs provider I/O while the transaction is open. Consent semantics are the
existing gate's: `rra.gate.MutationIntent` / `canonical_json` for the action
digest, the `ConfirmationGate` prompt text, and `study.consent_current.classify_response`.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import secrets
import sqlite3
import threading
import time
from typing import Any

from rra.gate import ConfirmationGate, MutationIntent, canonical_json
from rra.version import DEVELOPMENT_VERSION
from study.consent_current import CLASSIFIER_VERSION, classify_response

from . import auth, storage
from .auth import Principal
from .config import Settings

TOOL_NAME = "change_order_address"
STATE_SCHEMA_VERSION = "serving-state-v1"
CANONICAL_SCHEMA_VERSION = "rra-canonical-json-v1"
POLICY_VERSION = f"serving-consent-policy-v2/{DEVELOPMENT_VERSION}"
CONSENT_BINDING_VERSION = "consent-binding-v2"
# Provisional serving envelope. It carries every field of the milestone-1 trace
# envelope list; mapping onto contracts/trace-v1 waits for the evaluation owner.
EVENT_SCHEMA_VERSION = "rra-serving-event-v0-provisional"
TERMINAL_RUN_STATES = frozenset({"succeeded", "failed", "rejected", "cancelled", "expired", "invalidated"})
ACTIVE_RUN_STATES = ("awaiting_consent", "queued", "dispatching", "outcome_unknown")
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_.:-]{8,128}\Z")
EVENT_ID = re.compile(r"evt_(run_[0-9a-f]{32})_([0-9]{8})(?:_r([0-9a-f]{32}))?\Z")


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False,
                 retry_after: int | None = None, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retryable, self.retry_after, self.extra = retryable, retry_after, extra or {}


@dataclass(frozen=True)
class Claim:
    operation_id: str
    claim_id: str
    run_id: str
    tenant_id: str
    provider_key: str
    tool_name: str
    arguments: dict[str, Any]
    expected_revision: int


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _never_called(arguments: dict[str, Any]) -> None:
    raise AssertionError("prompt rendering never dispatches")


def render_prompt(intent: MutationIntent) -> str:
    """The exact complete prompt the existing gate presents for this intent."""
    return ConfirmationGate({intent.tool_name: _never_called}).propose(intent).prompt


def consent_binding_hash(row: sqlite3.Row | dict[str, Any], credential_verifier: str) -> str:
    """Versioned envelope binding server-known context to the unchanged payload_hash."""
    return digest({"binding_version": CONSENT_BINDING_VERSION, "credential_verifier": credential_verifier,
                   "actor_id": row["actor_id"],
                   "tenant_id": row["tenant_id"], "session_id": row["session_id"],
                   "operation_id": row["operation_id"], "resource_id": row["resource_id"],
                   "resource_revision": row["resource_revision"], "payload_hash": row["payload_hash"],
                   "prompt_id": row["prompt_id"], "proposal_version": row["proposal_version"],
                   "canonical_schema_version": row["canonical_schema_version"],
                   "policy_version": row["policy_version"], "classifier_version": row["classifier_version"]})


def event_id(run_id: str, sequence: int) -> str:
    return f"evt_{run_id}_{sequence:08d}"


def stream_cursor(run_id: str, sequence: int, generation: str | None = None) -> str:
    """Transport identity includes restore generation; business event IDs stay unchanged."""
    return event_id(run_id, sequence) + ("_r" + generation if generation else "")


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._local = threading.local()  # Runs that became terminal in this thread's open write transaction.

    # ------------------------------------------------------------------ plumbing
    def connect(self) -> sqlite3.Connection:
        return storage.connect(self.settings.db_path)

    def _write(self, fn, *args, **kwargs):
        conn = self.connect()
        self._local.finished, self._local.sources = [], {}
        try:
            with storage.write_tx(conn):
                result = fn(conn, *args, **kwargs)
                # Last event of a finished run, in the same transaction: a trace without it is incomplete.
                for run_id, state, now in self._local.finished:
                    self._event(conn, run_id, "serving.run_finished", {"title": "Run finished", "state": state}, now=now,
                                source=self._local.sources.get(run_id, "serving-api"))
                return result
        except sqlite3.OperationalError as error:
            if "locked" in str(error) or "busy" in str(error):
                raise ServiceError(503, "database_busy", "Storage is busy; retry with the same Idempotency-Key.",
                                   retryable=True, retry_after=1) from None
            raise
        finally:
            self._local.finished = self._local.sources = None
            conn.close()

    def _read(self, fn, *args, **kwargs):
        conn = self.connect()
        try:
            with storage.read_tx(conn):
                return fn(conn, *args, **kwargs)
        finally:
            conn.close()

    def _event(self, conn, run_id: str, event_type: str, payload: dict[str, Any], *, now: float,
               source: str = "serving-api", operation_id: str | None = None,
               payload_hash: str | None = None, prompt_id: str | None = None) -> str:
        row = conn.execute("UPDATE runs SET next_seq=next_seq+1, version=version+1, updated_at=? WHERE run_id=? "
                           "RETURNING next_seq, session_id, trace_id", (now, run_id)).fetchone()
        sequence = row["next_seq"]
        envelope = {"schema_version": EVENT_SCHEMA_VERSION, "event_id": event_id(run_id, sequence),
                    "sequence": sequence, "event_type": event_type, "session_id": row["session_id"],
                    "run_id": run_id, "trace_id": row["trace_id"], "operation_id": operation_id,
                    "payload_hash": payload_hash, "prompt_id": prompt_id,
                    "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z",
                    "source": source, "evidence_class": "serving_observation_synthetic_provider",
                    "payload": payload}
        encoded = canonical_json(envelope)
        if len(encoded.encode("utf-8")) > self.settings.max_event_bytes:
            raise ValueError("event exceeds configured size bound")
        conn.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)", (run_id, sequence, event_type, encoded, now))
        if getattr(self._local, "sources", None) is not None:
            self._local.sources[run_id] = source
        return envelope["event_id"]

    def _run_state(self, conn, run_id: str, new_state: str, expected: set[str], now: float) -> None:
        marks = ",".join("?" * len(expected))
        terminal_at = now if new_state in TERMINAL_RUN_STATES else None
        updated = conn.execute(f"UPDATE runs SET state=?, terminal_at=COALESCE(?, terminal_at) "
                               f"WHERE run_id=? AND state IN ({marks})",
                               (new_state, terminal_at, run_id, *sorted(expected))).rowcount
        if updated != 1:
            raise RuntimeError(f"run {run_id} not in expected state for {new_state}")
        if new_state in TERMINAL_RUN_STATES:
            if getattr(self._local, "finished", None) is None:
                raise RuntimeError("terminal run transitions must happen inside Service._write")
            self._local.finished.append((run_id, new_state, now))

    def _idempotent(self, conn, principal: Principal, scope: str, key: str, request: Any, now: float):
        if not isinstance(key, str) or not IDEMPOTENCY_KEY.fullmatch(key):
            raise ServiceError(400, "invalid_idempotency_key",
                               "Idempotency-Key header is required: 8-128 letters, digits, '.', ':', '_' or '-'.")
        request_hash = digest({"scope": scope, "request": request})
        row = conn.execute("SELECT request_hash, status_code, response FROM idempotency WHERE actor_id=? AND tenant_id=? "
                           "AND scope=? AND key=? AND expires_at>?",
                           (principal.actor_id, principal.tenant_id, scope, key, now)).fetchone()
        if row is None:
            return request_hash, None
        if row["request_hash"] != request_hash:
            raise ServiceError(409, "idempotency_conflict", "This Idempotency-Key was already used with a different request.")
        return request_hash, (row["status_code"], json.loads(row["response"]))

    def _remember(self, conn, principal: Principal, scope: str, key: str, request_hash: str,
                  status: int, body: dict[str, Any], now: float) -> tuple[int, dict[str, Any]]:
        conn.execute("INSERT OR REPLACE INTO idempotency VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (principal.actor_id, principal.tenant_id, scope, key, request_hash, status,
                      canonical_json(body), now, now + self.settings.idempotency_ttl_seconds))
        return status, body

    @staticmethod
    def _restore_guard(conn) -> None:
        if storage.get_meta(conn, "restore_state") == "unverified":
            raise ServiceError(503, "restore_unverified",
                               "Database was restored from a backup and is read-only until provider reconciliation.",
                               retryable=True, retry_after=30)

    @staticmethod
    def _hold(conn, tenant_id: str, resource_id: str) -> sqlite3.Row | None:
        return conn.execute("SELECT h.operation_id, o.state FROM resource_holds h JOIN operations o USING (operation_id) "
                            "WHERE h.tenant_id=? AND h.resource_id=?", (tenant_id, resource_id)).fetchone()

    @staticmethod
    def authorize(conn, principal: Principal, scope: str) -> Principal:
        """Check live identity and scope inside the transaction that reads or changes owned state."""
        current = auth.refresh(conn, principal)
        if current is None:
            raise ServiceError(401, "unauthenticated", "A valid current bearer token is required.")
        if not current.has(scope):
            raise ServiceError(403, "insufficient_scope", f"This credential lacks the {scope} scope.")
        return current

    def _owned_run(self, conn, principal: Principal, run_id: str, scope: str = "runs:read") -> sqlite3.Row:
        self.authorize(conn, principal, scope)
        row = conn.execute("SELECT r.* FROM runs r JOIN sessions s ON s.session_id=r.session_id "
                           "JOIN resources res ON res.tenant_id=s.tenant_id AND res.resource_id=s.resource_id "
                           "WHERE r.run_id=? AND r.tenant_id=? AND r.actor_id=? "
                           "AND s.tenant_id=r.tenant_id AND s.actor_id=r.actor_id AND res.owner_actor_id=r.actor_id",
                           (run_id, principal.tenant_id, principal.actor_id)).fetchone()
        if row is None:
            raise ServiceError(404, "not_found", "Run not found.")
        return row

    def can_observe(self, principal: Principal, run_id: str) -> bool:
        """Read-only stream revalidation, including current run/resource ownership."""
        try:
            self._read(self._owned_run, principal, run_id)
            return True
        except (ServiceError, sqlite3.Error):
            return False

    # ------------------------------------------------------------------ commands
    def create_session(self, principal: Principal, key: str, body: dict[str, Any], now: float | None = None):
        return self._write(self._create_session, principal, key, body, time.time() if now is None else now)

    def _create_session(self, conn, principal, key, body, now):
        self.authorize(conn, principal, "runs:write")
        request_hash, replay = self._idempotent(conn, principal, "POST /api/v1/sessions", key, body, now)
        resource = conn.execute("SELECT 1 FROM resources WHERE tenant_id=? AND resource_id=? AND owner_actor_id=?",
                                (principal.tenant_id, body["resource_id"], principal.actor_id)).fetchone()
        if resource is None:
            raise ServiceError(404, "not_found", "Resource not found.")
        if replay:
            return replay
        active = conn.execute("SELECT COUNT(*) FROM sessions WHERE actor_id=? AND expires_at>?",
                              (principal.actor_id, now)).fetchone()[0]
        if active >= self.settings.max_sessions_per_principal:
            raise ServiceError(429, "session_limit", "Too many active sessions for this principal.", retry_after=60)
        session_id = new_id("ses")
        conn.execute("INSERT INTO sessions (session_id, tenant_id, actor_id, resource_id, created_at, expires_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (session_id, principal.tenant_id, principal.actor_id,
                                                  body["resource_id"], now, now + self.settings.session_ttl_seconds))
        response = {"session_id": session_id, "resource_id": body["resource_id"], "expires_at": now + self.settings.session_ttl_seconds,
                    "links": {"runs": f"/api/v1/sessions/{session_id}/runs"}}
        return self._remember(conn, principal, "POST /api/v1/sessions", key, request_hash, 201, response, now)

    def prepare_run(self, principal: Principal, session_id: str, key: str, body: dict[str, Any], now: float | None = None):
        """Read-only pre-check. Returns a stored replay, or the resource to look up at the provider."""
        now = time.time() if now is None else now

        def check(conn):
            scope = f"POST /api/v1/sessions/{session_id}/runs"
            self._owned_session(conn, principal, session_id)
            _, replay = self._idempotent(conn, principal, scope, key, body, now)
            if replay:
                return replay, None
            self._restore_guard(conn)
            session = self._owned_session(conn, principal, session_id, now)
            return None, session["resource_id"]
        return self._read(check)

    def _owned_session(self, conn, principal, session_id, now=None):
        self.authorize(conn, principal, "runs:write")
        session = conn.execute("SELECT s.* FROM sessions s JOIN resources res "
                               "ON res.tenant_id=s.tenant_id AND res.resource_id=s.resource_id "
                               "WHERE s.session_id=? AND s.tenant_id=? AND s.actor_id=? AND res.owner_actor_id=s.actor_id",
                               (session_id, principal.tenant_id, principal.actor_id)).fetchone()
        if session is None:
            raise ServiceError(404, "not_found", "Session not found.")
        if now is not None and session["expires_at"] <= now:
            raise ServiceError(410, "session_expired", "Session expired; create a new session.")
        return session

    def create_run(self, principal: Principal, session_id: str, key: str, body: dict[str, Any],
                   order: dict[str, Any], now: float | None = None):
        return self._write(self._create_run, principal, session_id, key, body, order, time.time() if now is None else now)

    def _create_run(self, conn, principal, session_id, key, body, order, now):
        scope = f"POST /api/v1/sessions/{session_id}/runs"
        self._owned_session(conn, principal, session_id)
        request_hash, replay = self._idempotent(conn, principal, scope, key, body, now)
        if replay:
            return replay
        self._restore_guard(conn)
        session = self._owned_session(conn, principal, session_id, now)
        resource_id = session["resource_id"]
        owner = conn.execute("SELECT 1 FROM resources WHERE tenant_id=? AND resource_id=? AND owner_actor_id=?",
                             (principal.tenant_id, resource_id, principal.actor_id)).fetchone()
        if owner is None or order.get("order_id") != resource_id or order.get("tenant") != principal.tenant_id:
            raise ServiceError(404, "not_found", "Resource not found.")
        hold = self._hold(conn, principal.tenant_id, resource_id)
        if hold is not None:
            raise ServiceError(409, "resource_on_hold", "A previous change to this order is unresolved; no new change "
                               "can be proposed until it is reconciled.", extra={"hold_state": hold["state"]})
        marks = ",".join("?" * len(ACTIVE_RUN_STATES))
        active = conn.execute(f"SELECT COUNT(*) FROM runs WHERE actor_id=? AND state IN ({marks})",
                              (principal.actor_id, *ACTIVE_RUN_STATES)).fetchone()[0]
        if active >= self.settings.max_active_runs_per_principal:
            raise ServiceError(429, "active_run_limit", "Too many active runs for this principal.", retry_after=30)
        request = body["request"]
        # Scripted actor: a deterministic mapping from the structured request to one intent.
        run_id, operation_id, prompt_id = new_id("run"), new_id("op"), new_id("prm")
        intent = MutationIntent(TOOL_NAME, {"order_id": resource_id, "address": request["address"]}, operation_id)
        prompt = render_prompt(intent)
        message = body.get("message")
        stored_input = {"type": request["type"], "message_chars": len(message) if message else 0,
                        "message_sha256": sha256(message.encode()).hexdigest() if message else None,
                        "actor": "scripted-deterministic"}
        conn.execute("INSERT INTO runs (run_id, session_id, tenant_id, actor_id, trace_id, state, input, "
                     "state_schema_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'awaiting_consent', ?, ?, ?, ?)",
                     (run_id, session_id, principal.tenant_id, principal.actor_id, new_id("trc"),
                      canonical_json(stored_input), STATE_SCHEMA_VERSION, now, now))
        conn.execute("UPDATE resources SET known_revision=? WHERE tenant_id=? AND resource_id=?",
                     (order["revision"], principal.tenant_id, resource_id))
        conn.execute("INSERT INTO proposals (operation_id, run_id, session_id, tenant_id, actor_id, resource_id, "
                     "resource_revision, tool_name, canonical_payload, payload_hash, prompt_id, prompt_text, "
                     "proposal_version, canonical_schema_version, policy_version, classifier_version, state, "
                     "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'awaiting_consent', ?, ?)",
                     (operation_id, run_id, session_id, principal.tenant_id, principal.actor_id, resource_id,
                      order["revision"], TOOL_NAME, canonical_json({"tool_name": TOOL_NAME, "arguments": intent.arguments}),
                      intent.fingerprint, prompt_id, prompt, CANONICAL_SCHEMA_VERSION, POLICY_VERSION,
                      CLASSIFIER_VERSION, now, now + self.settings.proposal_ttl_seconds))
        self._event(conn, run_id, "serving.run_accepted", {"actor": "scripted-deterministic", "request_type": request["type"]}, now=now)
        # No `prompt` event yet: it is recorded when the owner is first shown the text (snapshot()).
        response = {"run_id": run_id, "state": "awaiting_consent", "operation_id": operation_id,
                    "links": {"self": f"/api/v1/runs/{run_id}", "events": f"/api/v1/runs/{run_id}/events",
                              "consents": f"/api/v1/runs/{run_id}/consents", "cancel": f"/api/v1/runs/{run_id}/cancel"}}
        return self._remember(conn, principal, scope, key, request_hash, 202, response, now)

    def consent(self, principal: Principal, run_id: str, key: str, body: dict[str, Any], now: float | None = None):
        return self._write(self._consent, principal, run_id, key, body, time.time() if now is None else now)

    def _consent(self, conn, principal, run_id, key, body, now):
        scope = f"POST /api/v1/runs/{run_id}/consents"
        run = self._owned_run(conn, principal, run_id, "consent:write")
        request_hash, replay = self._idempotent(conn, principal, scope, key, body, now)
        if replay:
            return replay
        self._restore_guard(conn)
        proposal = conn.execute("SELECT * FROM proposals WHERE run_id=?", (run_id,)).fetchone()

        def done(status, decision, detail, **extra):
            response = {"run_id": run_id, "decision": decision, "detail": detail,
                        "state": conn.execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()["state"], **extra}
            return self._remember(conn, principal, scope, key, request_hash, status, response, now)

        if proposal["state"] != "awaiting_consent":
            return done(409, "consent_not_pending", "No unattempted operation is awaiting consent.",
                        proposal_state=proposal["state"])
        common = dict(now=now, operation_id=proposal["operation_id"], payload_hash=proposal["payload_hash"],
                      prompt_id=proposal["prompt_id"])
        if now >= proposal["expires_at"]:
            self._expire_proposal(conn, proposal, now)
            return done(410, "proposal_expired", "The confirmation prompt expired; submit a new request.")
        if (proposal["policy_version"], proposal["classifier_version"], proposal["canonical_schema_version"]) != \
                (POLICY_VERSION, CLASSIFIER_VERSION, CANONICAL_SCHEMA_VERSION):
            conn.execute("UPDATE proposals SET state='invalidated', decided_at=? WHERE operation_id=?", (now, proposal["operation_id"]))
            self._run_state(conn, run_id, "invalidated", {"awaiting_consent"}, now)
            self._event(conn, run_id, "invalidated", {"title": "Consent policy changed",
                                                      "reason": "policy_or_schema_version_changed"}, **common)
            return done(409, "consent_policy_changed", "Consent policy changed since this prompt; submit a new request.")
        if not self._prompt_delivered(conn, run_id):
            return done(409, "prompt_not_delivered", "Fetch the run snapshot to see the exact prompt before answering.")
        if (body["prompt_id"], body["proposal_version"], body["payload_hash"]) != \
                (proposal["prompt_id"], proposal["proposal_version"], proposal["payload_hash"]):
            self._event(conn, run_id, "blocked", {"title": "Stale consent blocked",
                                                  "reason": "response_not_bound_to_active_prompt"}, **common)
            return done(409, "stale_consent", "A fresh answer to the current prompt, version and payload is required.")
        hold = self._hold(conn, proposal["tenant_id"], proposal["resource_id"])
        if hold is not None:
            self._event(conn, run_id, "blocked", {"title": "Unresolved change on this order",
                                                  "reason": "resource_on_hold", "hold_state": hold["state"]}, **common)
            return done(409, "resource_on_hold", "A previous change to this order is unresolved.")
        classification = classify_response(body["response"])
        response_hash = sha256(body["response"].encode("utf-8")).hexdigest()
        if classification == "REJECT":
            conn.execute("UPDATE proposals SET state='rejected', decided_at=?, response_sha256=?, "
                         "response_classification=? WHERE operation_id=? AND state='awaiting_consent'",
                         (now, response_hash, classification, proposal["operation_id"]))
            self._run_state(conn, run_id, "rejected", {"awaiting_consent"}, now)
            self._event(conn, run_id, "rejected", {"title": "Customer rejected mutation", "classification": classification}, **common)
            return done(200, "rejected", "Customer rejection blocks dispatch.")
        if classification != "AFFIRM":
            self._event(conn, run_id, "blocked", {"title": "Ambiguous response blocked", "reason": "ambiguous_response",
                                                  "classification": classification}, **common)
            hint = " To change the details, submit a new request." if classification == "CHANGE" else ""
            return done(200, "ambiguous", "An unambiguous affirmative to the exact prompt is required." + hint,
                        classification=classification)
        pending = conn.execute("SELECT COUNT(*) FROM operations WHERE state='pending'").fetchone()[0]
        if pending >= self.settings.max_pending_operations:
            raise ServiceError(503, "queue_full", "Dispatch queue is full; retry later with the same Idempotency-Key.",
                               retryable=True, retry_after=5)
        binding = consent_binding_hash(proposal, principal.credential_verifier)
        updated = conn.execute("UPDATE proposals SET state='authorized', decided_at=?, consent_binding_hash=?, "
                               "response_sha256=?, response_classification=?, authorization_expires_at=? "
                               "WHERE operation_id=? AND state='awaiting_consent'",
                               (now, binding, response_hash, classification,
                                now + self.settings.authorization_ttl_seconds, proposal["operation_id"])).rowcount
        if updated != 1:
            raise RuntimeError("proposal state changed inside transaction")
        conn.execute("INSERT INTO operations (operation_id, run_id, tenant_id, resource_id, provider_key, state, created_at) "
                     "VALUES (?, ?, ?, ?, ?, 'pending', ?)", (proposal["operation_id"], run_id, proposal["tenant_id"],
                                                             proposal["resource_id"], "rra-" + proposal["operation_id"], now))
        self._run_state(conn, run_id, "queued", {"awaiting_consent"}, now)
        self._event(conn, run_id, "authorized", {"title": "Fresh exact-payload consent", "consent_binding_hash": binding,
                                                 "consent_binding_version": CONSENT_BINDING_VERSION,
                                                 "classifier_version": proposal["classifier_version"],
                                                 "classification": classification}, **common)
        return done(202, "authorized", "The exact operation is authorized for one dispatch attempt.")

    def _expire_proposal(self, conn, proposal, now):
        conn.execute("UPDATE proposals SET state='expired', decided_at=? WHERE operation_id=? AND state='awaiting_consent'",
                     (now, proposal["operation_id"]))
        self._run_state(conn, proposal["run_id"], "expired", {"awaiting_consent"}, now)
        self._event(conn, proposal["run_id"], "serving.expired", {"title": "Confirmation prompt expired"}, now=now,
                    operation_id=proposal["operation_id"], payload_hash=proposal["payload_hash"], prompt_id=proposal["prompt_id"])

    def cancel(self, principal: Principal, run_id: str, key: str, now: float | None = None):
        return self._write(self._cancel, principal, run_id, key, time.time() if now is None else now)

    def _cancel(self, conn, principal, run_id, key, now):
        scope = f"POST /api/v1/runs/{run_id}/cancel"
        run = self._owned_run(conn, principal, run_id, "runs:write")
        request_hash, replay = self._idempotent(conn, principal, scope, key, {}, now)
        if replay:
            return replay
        # A restored row may be stale: "prevented dispatch" could be false until provider evidence settles it.
        self._restore_guard(conn)
        proposal = conn.execute("SELECT * FROM proposals WHERE run_id=?", (run_id,)).fetchone()
        operation = conn.execute("SELECT * FROM operations WHERE run_id=?", (run_id,)).fetchone()
        common = dict(now=now, operation_id=proposal["operation_id"], payload_hash=proposal["payload_hash"],
                      prompt_id=proposal["prompt_id"])
        state = run["state"]
        # The atomic dispatch claim is the linearization point: before it, cancellation wins.
        if state in {"awaiting_consent", "queued"}:
            conn.execute("UPDATE proposals SET state='cancelled', decided_at=? WHERE operation_id=? "
                         "AND state IN ('awaiting_consent', 'authorized')", (now, proposal["operation_id"]))
            if operation is not None:
                conn.execute("UPDATE operations SET state='cancelled', finished_at=? WHERE operation_id=? AND state='pending'",
                             (now, operation["operation_id"]))
            conn.execute("UPDATE runs SET cancel_requested=1 WHERE run_id=?", (run_id,))
            self._run_state(conn, run_id, "cancelled", {"awaiting_consent", "queued"}, now)
            self._event(conn, run_id, "serving.cancelled", {"title": "Cancelled before dispatch claim",
                                                            "dispatch_started": False}, **common)
            outcome = "prevented_dispatch"
        elif state not in {"dispatching", "outcome_unknown"}:
            outcome = "already_terminal"  # Includes succeeded/failed: the recorded effect state is reported as is.
        else:
            if not run["cancel_requested"]:
                conn.execute("UPDATE runs SET cancel_requested=1 WHERE run_id=?", (run_id,))
                self._event(conn, run_id, "serving.cancel_requested",
                            {"title": "Cancellation requested after dispatch claim", "dispatch_started": True,
                             "effect_state": operation["state"],
                             "note": "Cancellation does not undo a side effect that may already exist."}, **common)
            outcome = "requested_after_dispatch"
        final = conn.execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()["state"]
        operation = conn.execute("SELECT state, attempt_count FROM operations WHERE run_id=?", (run_id,)).fetchone()
        response = {"run_id": run_id, "state": final, "cancellation": outcome,
                    "dispatch_started": bool(operation and operation["attempt_count"]),
                    "effect_state": operation["state"] if operation else None}
        return self._remember(conn, principal, scope, key, request_hash, 200, response, now)

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _prompt_delivered(conn, run_id: str) -> bool:
        return conn.execute("SELECT 1 FROM events WHERE run_id=? AND event_type='prompt' LIMIT 1", (run_id,)).fetchone() is not None

    def _deliver_prompt(self, conn, principal, run_id, now):
        """Record, once, that this exact prompt is delivered to the authenticated owner's client (trace-v1 `prompt`)."""
        self._restore_guard(conn)
        self._owned_run(conn, principal, run_id)
        proposal = conn.execute("SELECT * FROM proposals WHERE run_id=?", (run_id,)).fetchone()
        if proposal["state"] != "awaiting_consent" or now >= proposal["expires_at"] or self._prompt_delivered(conn, run_id):
            return
        payload = json.loads(proposal["canonical_payload"])
        self._event(conn, run_id, "prompt", {"title": "Exact payload presented", "prompt": proposal["prompt_text"],
                                             "tool_name": payload["tool_name"], "arguments": payload["arguments"],
                                             "resource_revision": proposal["resource_revision"],
                                             "proposal_version": proposal["proposal_version"]},
                    now=now, operation_id=proposal["operation_id"], payload_hash=proposal["payload_hash"],
                    prompt_id=proposal["prompt_id"])

    def snapshot(self, principal: Principal, run_id: str) -> dict[str, Any]:
        now = time.time()
        result = self._read(self._snapshot_in, principal, run_id, now)
        if result["proposal"]["prompt"] is not None and not result["_prompt_delivered"]:
            def deliver(conn):  # Recording and reading in one transaction: no consent can land in between.
                locked_at = time.time()  # Fresh: waiting for the writer lock must not outlast the prompt's expiry.
                self._deliver_prompt(conn, principal, run_id, locked_at)
                return self._snapshot_in(conn, principal, run_id, locked_at)
            try:
                result = self._write(deliver)
            except Exception as error:  # A delivery that cannot be recorded is not made; consent stays refused.
                if isinstance(error, ServiceError) and error.status in {401, 403, 404}:
                    raise  # Identity/ownership changed while waiting: do not return the earlier snapshot.
                result["proposal"]["prompt"] = None
                result["proposal"]["prompt_unavailable"] = "delivery_not_recorded"
        result.pop("_prompt_delivered")
        return result

    def _snapshot_in(self, conn, principal: Principal, run_id: str, now: float) -> dict[str, Any]:
        run = self._owned_run(conn, principal, run_id)
        proposal = conn.execute("SELECT * FROM proposals WHERE run_id=?", (run_id,)).fetchone()
        operation = conn.execute("SELECT * FROM operations WHERE run_id=?", (run_id,)).fetchone()
        hold = self._hold(conn, proposal["tenant_id"], proposal["resource_id"])
        payload = json.loads(proposal["canonical_payload"])
        result = {"run_id": run_id, "session_id": run["session_id"], "state": run["state"],
                  "cancel_requested": bool(run["cancel_requested"]),
                  "labels": {"provider": "synthetic", "actor": "scripted-deterministic", "deployment": "local-pilot"},
                  "proposal": {"operation_id": proposal["operation_id"], "state": proposal["state"],
                               "tool_name": payload["tool_name"], "arguments": payload["arguments"],
                               "payload_hash": proposal["payload_hash"], "fingerprint": proposal["payload_hash"],
                               "prompt_id": proposal["prompt_id"], "proposal_version": proposal["proposal_version"],
                               "resource": {"resource_id": proposal["resource_id"], "revision": proposal["resource_revision"]},
                               "expires_at": proposal["expires_at"],
                               "prompt": proposal["prompt_text"] if proposal["state"] == "awaiting_consent"
                               and now < proposal["expires_at"] else None},
                  "operation": None,
                  "resource_hold": {"held": hold is not None, "by_operation_id": hold["operation_id"] if hold else None,
                                    "state": hold["state"] if hold else None},
                  "last_event_id": stream_cursor(run_id, run["next_seq"], storage.get_meta(conn, "restore_generation")) if run["next_seq"] else None,
                  "_prompt_delivered": self._prompt_delivered(conn, run_id),
                  "links": {"events": f"/api/v1/runs/{run_id}/events"}}
        if operation is not None:
            reconciliation = json.loads(operation["reconciliation"]) if operation["reconciliation"] else None
            missing_history = (operation["state"] == "succeeded" and not operation["attempt_count"]
                               and reconciliation and reconciliation.get("method") == "restore_provider_status_lookup")
            result["operation"] = {"state": operation["state"], "dispatch_started": bool(operation["attempt_count"]),
                                   "dispatch_history": "missing_after_restore" if missing_history else
                                   "recorded" if operation["attempt_count"] else "not_started",
                                   "error_code": operation["error_code"],
                                   "provider_result": json.loads(operation["provider_result"]) if operation["provider_result"] else None,
                                   "reconciliation": reconciliation,
                                   "recovery_observation": (reconciliation or {}).get("restore_observation")}
        return result

    def stream_start(self, principal: Principal, run_id: str, cursor: str | None) -> int:
        """Validate ownership and cursor before the stream opens; return the last delivered sequence."""
        def read(conn):
            run = self._owned_run(conn, principal, run_id)
            if cursor is None:
                after = 0
            else:
                match = EVENT_ID.fullmatch(cursor)
                if not match or match[1] != run_id:
                    raise ServiceError(400, "invalid_cursor", "Last-Event-ID does not identify an event of this run.")
                if match[3] != storage.get_meta(conn, "restore_generation"):
                    raise ServiceError(410, "replay_expired", "History changed after restore; recover from the current snapshot.",
                                       extra={"snapshot": f"/api/v1/runs/{run_id}"})
                if int(match[2]) > run["next_seq"]:
                    raise ServiceError(400, "invalid_cursor", "Last-Event-ID does not identify an event of this run.")
                after = int(match[2])
            if after < run["purged_through_seq"]:
                raise ServiceError(410, "replay_expired", "Events after this cursor are past retention; "
                                   "recover from the run snapshot.", extra={"snapshot": f"/api/v1/runs/{run_id}"})
            return after
        return self._read(read)

    def stream_generation(self) -> str | None:
        return self._read(lambda conn: storage.get_meta(conn, "restore_generation"))

    def trace_envelopes(self, principal: Principal, run_id: str) -> list[dict[str, Any]]:
        """Every stored envelope of an owned run, oldest first; refuses a run whose history was purged."""
        def read(conn):
            run = self._owned_run(conn, principal, run_id)
            if run["purged_through_seq"]:
                raise ServiceError(410, "trace_expired", "This run's events are past retention; no complete trace exists.")
            return [json.loads(row["envelope"]) for row in
                    conn.execute("SELECT envelope FROM events WHERE run_id=? ORDER BY sequence", (run_id,))]
        return self._read(read)

    def events_after(self, run_id: str, after: int, limit: int) -> tuple[list[tuple[int, str, str]], str, int]:
        """One read snapshot: a page of events, the run state, and the retention watermark."""
        def read(conn):
            rows = conn.execute("SELECT sequence, event_type, envelope FROM events WHERE run_id=? AND sequence>? "
                                "ORDER BY sequence LIMIT ?", (run_id, after, limit)).fetchall()
            run = conn.execute("SELECT state, purged_through_seq FROM runs WHERE run_id=?", (run_id,)).fetchone()
            return ([(row["sequence"], row["event_type"], row["envelope"]) for row in rows], run["state"],
                    run["purged_through_seq"])
        return self._read(read)

    # ------------------------------------------------------------------ worker side
    def claim(self, capacity: int, now: float | None = None) -> list[Claim]:
        return self._write(self._claim, capacity, time.time() if now is None else now)

    def _claim(self, conn, capacity, now):
        if capacity <= 0 or storage.get_meta(conn, "restore_state") == "unverified":
            return []
        rows = conn.execute(
            "SELECT o.operation_id, o.provider_key, r.state AS run_state, r.cancel_requested, pr.status AS principal_status, "
            "pr.tenant_id AS principal_tenant, pr.scopes AS principal_scopes, pr.token_hash AS credential_verifier, "
            "res.owner_actor_id, res.known_revision, p.* FROM operations o JOIN proposals p USING (operation_id) "
            "JOIN runs r ON r.run_id=o.run_id JOIN principals pr ON pr.actor_id=p.actor_id "
            "JOIN resources res ON res.tenant_id=p.tenant_id AND res.resource_id=p.resource_id "
            "WHERE o.state='pending' ORDER BY o.created_at LIMIT ?", (self.settings.max_pending_operations,)).fetchall()
        claims: list[Claim] = []
        for row in rows:
            if len(claims) >= capacity:
                break
            common = dict(now=now, source="serving-worker", operation_id=row["operation_id"],
                          payload_hash=row["payload_hash"], prompt_id=row["prompt_id"])
            reason = None
            if row["principal_status"] != "active":
                reason = "credential_revoked"
            elif row["principal_tenant"] != row["tenant_id"]:
                reason = "principal_tenant_changed"
            elif not {"runs:write", "consent:write"}.issubset(json.loads(row["principal_scopes"])):
                reason = "permission_revoked"
            elif row["owner_actor_id"] != row["actor_id"]:
                reason = "ownership_changed"
            elif row["state"] != "authorized" or row["run_state"] != "queued" or row["cancel_requested"]:
                reason = "not_authorized"
            elif (row["policy_version"], row["classifier_version"], row["canonical_schema_version"]) != \
                    (POLICY_VERSION, CLASSIFIER_VERSION, CANONICAL_SCHEMA_VERSION):
                reason = "policy_or_schema_version_changed"
            elif row["consent_binding_hash"] != consent_binding_hash(row, row["credential_verifier"]):
                reason = "consent_binding_mismatch"
            elif row["known_revision"] is not None and row["known_revision"] != row["resource_revision"]:
                reason = "resource_revision_changed"
            elif now >= row["authorization_expires_at"]:
                reason = "authorization_expired"
            if reason is None and self._hold(conn, row["tenant_id"], row["resource_id"]) is not None:
                continue  # Serialized per resource: wait while another operation holds it.
            if reason is not None:
                conn.execute("UPDATE operations SET state='invalidated', finished_at=?, error_code=? "
                             "WHERE operation_id=? AND state='pending'", (now, reason, row["operation_id"]))
                conn.execute("UPDATE proposals SET state='invalidated' WHERE operation_id=? AND state='authorized'",
                             (row["operation_id"],))
                if row["run_state"] == "queued":
                    self._run_state(conn, row["run_id"], "invalidated", {"queued"}, now)
                self._event(conn, row["run_id"], "invalidated", {"title": "Authorization no longer valid at claim time",
                                                                 "reason": reason}, **common)
                continue
            claim_id = new_id("clm")
            conn.execute("INSERT INTO resource_holds VALUES (?, ?, ?, ?)",
                         (row["tenant_id"], row["resource_id"], row["operation_id"], now))
            conn.execute("UPDATE proposals SET state='consumed', consumed_at=? WHERE operation_id=? AND state='authorized'",
                         (now, row["operation_id"]))
            updated = conn.execute("UPDATE operations SET state='dispatching', claim_id=?, attempt_count=1, claimed_at=? "
                                   "WHERE operation_id=? AND state='pending' AND attempt_count=0",
                                   (claim_id, now, row["operation_id"])).rowcount
            if updated != 1:
                raise RuntimeError("operation claim lost inside the writer transaction")
            self._run_state(conn, row["run_id"], "dispatching", {"queued"}, now)
            payload = json.loads(row["canonical_payload"])
            self._event(conn, row["run_id"], "dispatch_started",
                        {"title": "Tool invocation started", "detail": "One attempt recorded before calling the provider.",
                         "tool_name": payload["tool_name"], "provider_key": row["provider_key"],
                         "expected_revision": row["resource_revision"]}, **common)
            claims.append(Claim(row["operation_id"], claim_id, row["run_id"], row["tenant_id"], row["provider_key"],
                                payload["tool_name"], payload["arguments"], row["resource_revision"]))
        return claims

    def complete(self, claim: Claim, outcome, now: float | None = None) -> bool:
        return self._write(self._complete, claim, outcome, time.time() if now is None else now)

    def _complete(self, conn, claim, outcome, now):
        result = canonical_json(outcome.result) if outcome.result is not None else None
        updated = conn.execute("UPDATE operations SET state=?, finished_at=?, error_code=?, provider_result=?, "
                               "next_reconcile_at=? WHERE operation_id=? AND claim_id=? AND state='dispatching'",
                               (outcome.state, now, None if outcome.state == "succeeded" else outcome.code, result,
                                now if outcome.state == "unknown" else None, claim.operation_id, claim.claim_id)).rowcount
        if updated != 1:
            return False  # A stale claim never overwrites a newer authoritative state.
        proposal = conn.execute("SELECT payload_hash, prompt_id FROM proposals WHERE operation_id=?", (claim.operation_id,)).fetchone()
        common = dict(now=now, source="serving-worker", operation_id=claim.operation_id,
                      payload_hash=proposal["payload_hash"], prompt_id=proposal["prompt_id"])
        self._apply_outcome(conn, claim.operation_id, claim.run_id, outcome, {"dispatching"}, common, now)
        return True

    def _apply_outcome(self, conn, operation_id, run_id, outcome, from_states, common, now, *, reconciled=False):
        tenant_resource = conn.execute("SELECT tenant_id, resource_id FROM operations WHERE operation_id=?",
                                       (operation_id,)).fetchone()
        if outcome.state == "succeeded":
            conn.execute("DELETE FROM resource_holds WHERE operation_id=?", (operation_id,))
            conn.execute("UPDATE resources SET known_revision=? WHERE tenant_id=? AND resource_id=?",
                         (outcome.result["revision"], *tenant_resource))
            self._run_state(conn, run_id, "succeeded", from_states, now)
            if not reconciled:
                self._event(conn, run_id, "dispatch_succeeded", {"title": "Tool invocation succeeded", "tool_name": TOOL_NAME,
                                                                 "provider_result": outcome.result}, **common)
        elif outcome.state == "failed_no_effect":
            conn.execute("DELETE FROM resource_holds WHERE operation_id=?", (operation_id,))
            if outcome.code == "revision_conflict":
                conn.execute("UPDATE resources SET known_revision=NULL WHERE tenant_id=? AND resource_id=?", tenant_resource)
            self._run_state(conn, run_id, "failed", from_states, now)
            if not reconciled:
                self._event(conn, run_id, "serving.dispatch_failed_no_effect",
                            {"title": "Provider reported a definite no-effect failure", "code": outcome.code}, **common)
        else:
            self._run_state(conn, run_id, "outcome_unknown", from_states, now)
            self._event(conn, run_id, "dispatch_uncertain",
                        {"title": "Outcome uncertain; retry stopped", "code": outcome.code, "tool_name": TOOL_NAME,
                         "detail": "The attempt is consumed. No automatic retry; the order stays on hold until reconciled."},
                        **common)

    def recover_orphans(self, reason: str = "orphaned_dispatch_on_restart", now: float | None = None) -> int:
        """A `dispatching` row with no live owner is ambiguous: record unknown, never re-claim."""
        now = time.time() if now is None else now

        def recover(conn):
            rows = conn.execute("SELECT o.operation_id, o.run_id, p.payload_hash, p.prompt_id FROM operations o "
                                "JOIN proposals p USING (operation_id) WHERE o.state='dispatching'").fetchall()
            for row in rows:
                conn.execute("UPDATE operations SET state='unknown', error_code=?, finished_at=?, next_reconcile_at=? "
                             "WHERE operation_id=? AND state='dispatching'", (reason, now, now, row["operation_id"]))
                self._run_state(conn, row["run_id"], "outcome_unknown", {"dispatching"}, now)
                self._event(conn, row["run_id"], "dispatch_uncertain",
                            {"title": "Outcome uncertain; retry stopped", "code": reason, "tool_name": TOOL_NAME,
                             "detail": "The service stopped inside the dispatch window. No automatic retry."},
                            now=now, source="serving-worker", operation_id=row["operation_id"],
                            payload_hash=row["payload_hash"], prompt_id=row["prompt_id"])
            return len(rows)
        return self._write(recover)

    def abandon_claim(self, claim: Claim, reason: str, now: float | None = None) -> bool:
        """Shutdown/timeout path for one in-flight claim: ambiguity is recorded, not erased."""
        from .provider import Outcome
        return self.complete(claim, Outcome("unknown", reason), now)

    def due_reconciliations(self, limit: int, now: float | None = None) -> list[tuple[str, str]]:
        now = time.time() if now is None else now
        return self._read(lambda conn: [(row["operation_id"], row["provider_key"]) for row in conn.execute(
            "SELECT operation_id, provider_key FROM operations WHERE state='unknown' AND next_reconcile_at IS NOT NULL "
            "AND next_reconcile_at<=? ORDER BY next_reconcile_at LIMIT ?", (now, limit))])

    def record_reconciliation(self, operation_id: str, outcome, now: float | None = None) -> str:
        return self._write(self._record_reconciliation, operation_id, outcome, time.time() if now is None else now)

    def _record_reconciliation(self, conn, operation_id, outcome, now):
        row = conn.execute("SELECT o.*, p.payload_hash, p.prompt_id FROM operations o JOIN proposals p USING (operation_id) "
                           "WHERE operation_id=? AND o.state='unknown'", (operation_id,)).fetchone()
        if row is None:
            return "not_unknown"
        common = dict(now=now, source="serving-worker", operation_id=operation_id,
                      payload_hash=row["payload_hash"], prompt_id=row["prompt_id"])
        evidence = {"method": "provider_status_lookup", "code": outcome.code, "observed": outcome.result,
                    "checked_at": now, "attempts": row["reconcile_attempts"] + 1}
        if outcome.state in {"succeeded", "failed_no_effect"}:
            conn.execute("UPDATE operations SET state=?, reconciliation=?, reconcile_attempts=reconcile_attempts+1, "
                         "next_reconcile_at=NULL, provider_result=COALESCE(?, provider_result) WHERE operation_id=? AND state='unknown'",
                         (outcome.state, canonical_json(dict(evidence, status="resolved")),
                          canonical_json(outcome.result) if outcome.state == "succeeded" else None, operation_id))
            self._apply_outcome(conn, operation_id, row["run_id"], outcome, {"outcome_unknown"}, common, now, reconciled=True)
            self._event(conn, row["run_id"], "serving.reconciled",
                        {"title": "Outcome established from provider evidence", "resolved_state": outcome.state,
                         "method": evidence["method"], "evidence": evidence}, **common)
            return outcome.state
        attempts = row["reconcile_attempts"] + 1
        if outcome.code == "status_unsupported" or attempts >= self.settings.reconcile_max_attempts:
            status = "unsupported" if outcome.code == "status_unsupported" else "exhausted"
            conn.execute("UPDATE operations SET reconciliation=?, reconcile_attempts=?, next_reconcile_at=NULL "
                         "WHERE operation_id=?", (canonical_json(dict(evidence, status=status)), attempts, operation_id))
            self._event(conn, row["run_id"], "serving.reconciliation_unavailable",
                        {"title": "Outcome remains unknown", "status": status, "method": evidence["method"],
                         "detail": "Manual reconciliation with provider evidence is required; the order stays on hold."},
                        **common)
            return status
        delay = min(self.settings.reconcile_backoff_seconds * 2 ** (attempts - 1), self.settings.reconcile_backoff_max_seconds)
        conn.execute("UPDATE operations SET reconciliation=?, reconcile_attempts=?, next_reconcile_at=? WHERE operation_id=?",
                     (canonical_json(dict(evidence, status="retrying_lookup")), attempts, now + delay, operation_id))
        return "retrying_lookup"

    def maintenance(self, now: float | None = None) -> dict[str, int]:
        """Expire stale prompts, drop expired request keys, purge events of old terminal runs."""
        return self._write(self._maintenance, time.time() if now is None else now)

    def _maintenance(self, conn, now):
        keys = conn.execute("DELETE FROM idempotency WHERE expires_at<=?", (now,)).rowcount
        if storage.get_meta(conn, "restore_state") == "unverified":
            # Expiring a restored prompt would hide it from verify-restore, whose provider lookup is the only
            # evidence of whether it was approved and applied after the backup. Only key cleanup runs until then.
            return {"expired_prompts": 0, "expired_keys": keys, "purged_runs": 0}
        expired = conn.execute("SELECT * FROM proposals WHERE state='awaiting_consent' AND expires_at<=? LIMIT 100", (now,)).fetchall()
        for proposal in expired:
            self._expire_proposal(conn, proposal, now)
        cutoff = now - self.settings.event_retention_seconds
        runs = conn.execute("SELECT run_id, next_seq FROM runs WHERE terminal_at IS NOT NULL AND terminal_at<=? "
                            "AND purged_through_seq<next_seq LIMIT 100", (cutoff,)).fetchall()
        for run in runs:
            conn.execute("DELETE FROM events WHERE run_id=? AND sequence<=?", (run["run_id"], run["next_seq"]))
            conn.execute("UPDATE runs SET purged_through_seq=? WHERE run_id=?", (run["next_seq"], run["run_id"]))
        return {"expired_prompts": len(expired), "expired_keys": keys, "purged_runs": len(runs)}

    # ------------------------------------------------------------------ restore
    def restore_open_operations(self) -> list[tuple[str, str, str]]:
        return self._read(lambda conn: [(row["operation_id"], row["provider_key"], row["state"]) for row in conn.execute(
            "SELECT operation_id, provider_key, state FROM operations WHERE state IN ('pending', 'dispatching', 'unknown')")])

    def restore_resources(self) -> list[tuple[str, str]]:
        return self._read(lambda conn: [(row["tenant_id"], row["resource_id"]) for row in conn.execute(
            "SELECT tenant_id, resource_id FROM resources")])

    def restore_awaiting_proposals(self) -> list[tuple[str, str]]:
        """Prompts still awaiting consent in the snapshot. Their key may have been consented to and sent
        after the snapshot was taken, so verification must look each one up (which also fences it)."""
        return self._read(lambda conn: [(row["operation_id"], "rra-" + row["operation_id"]) for row in conn.execute(
            "SELECT operation_id FROM proposals WHERE state='awaiting_consent'")])

    def restore_resolve_proposal(self, operation_id: str, outcome, now: float | None = None) -> str:
        now = time.time() if now is None else now

        def resolve(conn):
            row = conn.execute("SELECT * FROM proposals WHERE operation_id=? AND state='awaiting_consent'",
                               (operation_id,)).fetchone()
            if row is None or outcome.state == "unknown":
                return "unresolved"
            if outcome.state == "failed_no_effect":
                # Never sent, and the lookup has now fenced the key. The restored prompt may be stale against
                # the live order, so it is discarded and the customer submits a new request.
                conn.execute("UPDATE proposals SET state='invalidated', decided_at=? WHERE operation_id=?", (now, operation_id))
                self._run_state(conn, row["run_id"], "invalidated", {"awaiting_consent"}, now)
                self._event(conn, row["run_id"], "invalidated", {"title": "Prompt discarded after restore",
                                                                 "reason": "restored_from_backup"},
                            now=now, source="serving-restore", operation_id=operation_id,
                            payload_hash=row["payload_hash"], prompt_id=row["prompt_id"])
                return "invalidated"
            # Consented to and applied after the snapshot. The provider's record is the evidence; the missing
            # local history of that consent and attempt is not reconstructed.
            conn.execute("INSERT INTO operations (operation_id, run_id, tenant_id, resource_id, provider_key, state, created_at) "
                         "VALUES (?, ?, ?, ?, ?, 'pending', ?)", (operation_id, row["run_id"], row["tenant_id"],
                                                                 row["resource_id"], "rra-" + operation_id, now))
            return self._restore_settle(conn, operation_id, outcome, now)
        return self._write(resolve)

    def restore_resolve(self, operation_id: str, outcome, now: float | None = None) -> str:
        """Settle one operation from a restored snapshot using fenced provider status evidence."""
        return self._write(self._restore_settle, operation_id, outcome, time.time() if now is None else now)

    def _restore_settle(self, conn, operation_id: str, outcome, now: float) -> str:
        row = conn.execute("SELECT o.*, p.payload_hash, p.prompt_id, r.state AS run_state FROM operations o "
                           "JOIN proposals p USING (operation_id) JOIN runs r ON r.run_id=o.run_id "
                           "WHERE operation_id=? AND o.state IN ('pending', 'dispatching', 'unknown')",
                           (operation_id,)).fetchone()
        if row is None or outcome.state == "unknown":
            return "unresolved"
        common = dict(now=now, source="serving-restore", operation_id=operation_id,
                      payload_hash=row["payload_hash"], prompt_id=row["prompt_id"])
        recovery = None
        generation = storage.get_meta(conn, "restore_generation")
        snapshot_hash = storage.get_meta(conn, "restore_snapshot_sha256")
        if outcome.state == "succeeded" and not row["attempt_count"] and generation and snapshot_hash:
            receipt = {"effect_id": outcome.result["effect_id"], "revision": outcome.result["revision"]}
            recovery = {"outcome": "effect_confirmed", "by": "restore_provider_status_lookup",
                        "history": "missing_after_restore", "restore_id": generation,
                        "snapshot_sha256": snapshot_hash,
                        "provider_key_sha256": sha256(row["provider_key"].encode()).hexdigest(),
                        "provider_receipt_sha256": sha256(canonical_json(receipt).encode()).hexdigest(),
                        "effect_id": receipt["effect_id"], "provider_revision": receipt["revision"]}
        evidence_data = {"method": "restore_provider_status_lookup", "code": outcome.code,
                         "observed": outcome.result, "checked_at": now}
        if recovery is not None:
            evidence_data["restore_observation"] = recovery
        evidence = canonical_json(evidence_data)
        if row["state"] == "pending" and outcome.state == "failed_no_effect":
            # Never dispatched (the key is now fenced): discard the pre-backup authorization.
            conn.execute("UPDATE operations SET state='invalidated', error_code='restored_authorization_discarded', "
                         "finished_at=?, reconciliation=? WHERE operation_id=?", (now, evidence, operation_id))
            conn.execute("UPDATE proposals SET state='invalidated' WHERE operation_id=?", (operation_id,))
            self._run_state(conn, row["run_id"], "invalidated", {"queued"}, now)
            self._event(conn, row["run_id"], "invalidated", {"title": "Authorization discarded after restore",
                                                             "reason": "restored_from_backup"}, **common)
            return "invalidated"
        conn.execute("UPDATE operations SET state=?, finished_at=?, reconciliation=?, "
                     "provider_result=COALESCE(?, provider_result), next_reconcile_at=NULL WHERE operation_id=?",
                     (outcome.state, now, evidence, canonical_json(outcome.result) if outcome.state == "succeeded" else None,
                      operation_id))
        if outcome.state == "succeeded":
            # Mark the proposal unusable, preserving any actual claim-time consumption timestamp.
            # A provider receipt cannot reconstruct a consumption missing from the backup.
            conn.execute("UPDATE proposals SET state='consumed' WHERE operation_id=?", (operation_id,))
        run_state = row["run_state"]
        if row["state"] == "dispatching":  # The snapshot caught it mid-dispatch: record that ambiguity first.
            self._run_state(conn, row["run_id"], "outcome_unknown", {"dispatching"}, now)
            self._event(conn, row["run_id"], "dispatch_uncertain",
                        {"title": "Outcome uncertain; retry stopped", "code": "restored_inside_dispatch_window",
                         "tool_name": TOOL_NAME}, **common)
            run_state = "outcome_unknown"
        self._apply_outcome(conn, operation_id, row["run_id"], outcome, {run_state}, common, now, reconciled=True)
        self._event(conn, row["run_id"], "serving.reconciled", {"title": "Outcome established after restore",
                                                                "resolved_state": outcome.state,
                                                                "method": "restore_provider_status_lookup",
                                                                **({"restore_observation": recovery} if recovery else {})}, **common)
        return outcome.state

    def restore_complete(self, revisions: dict[tuple[str, str], int]) -> None:
        def complete(conn):
            for (tenant_id, resource_id), revision in revisions.items():
                conn.execute("UPDATE resources SET known_revision=? WHERE tenant_id=? AND resource_id=?",
                             (revision, tenant_id, resource_id))
            storage.set_meta(conn, "restore_state", None)
        self._write(complete)

    def counts(self) -> dict[str, int]:
        def read(conn):
            result = {f"operations_{row[0]}": row[1] for row in conn.execute("SELECT state, COUNT(*) FROM operations GROUP BY state")}
            result["resource_holds"] = conn.execute("SELECT COUNT(*) FROM resource_holds").fetchone()[0]
            result["events"] = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            return result
        return self._read(read)

    def probe_write(self) -> None:
        """A real write: succeeds only when recording an outcome would too."""
        self._write(lambda conn: storage.set_meta(conn, "last_write_probe", str(time.time())))

    def ready(self) -> bool:
        storage.check_schema(self.settings.db_path)
        return self._read(lambda conn: storage.get_meta(conn, "restore_state") != "unverified")
