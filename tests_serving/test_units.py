"""In-process tests against real SQLite files: migrations, gate parity, transactions, stream bounds."""
import asyncio
import importlib.util
import os
import re
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from rra.gate import ConfirmationGate, MutationIntent
from serving import auth, storage
from serving.auth import CUSTOMER_SCOPES, Principal
from serving.config import Settings
from serving.provider import Outcome
from serving.service import Service, ServiceError, consent_binding_hash, render_prompt
from serving.streaming import Notifier, SSEResponse, event_stream
from serving.telemetry import Telemetry
from tests_serving.harness import ADDRESS, ROOT

ALICE = Principal("alice", "acme", frozenset(CUSTOMER_SCOPES))
ORDER = {"order_id": "SYN-ORDER-1042", "tenant": "acme", "revision": 1}
RUN_BODY = {"request": {"type": "change_order_address", "address": ADDRESS}, "message": None}


class ServiceCase(unittest.TestCase):
    settings_overrides: dict = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_dir=Path(self.tmp.name), **self.settings_overrides)
        storage.migrate(self.settings.db_path)
        conn = storage.connect(self.settings.db_path)
        with storage.write_tx(conn):
            self.tokens = auth.seed(conn)
        conn.close()
        self.service = Service(self.settings)
        self.counter = 0

    def tearDown(self):
        self.tmp.cleanup()

    def key(self, prefix="key"):
        self.counter += 1
        return f"{prefix}-{self.counter:06d}"

    def new_run(self, body=RUN_BODY, revision=1):
        _, session = self.service.create_session(ALICE, self.key("session"), {"resource_id": "SYN-ORDER-1042"})
        _, run = self.service.create_run(ALICE, session["session_id"], self.key("run"), body, dict(ORDER, revision=revision))
        return self.service.snapshot(ALICE, run["run_id"])

    def consent(self, snapshot, response="yes", **overrides):
        proposal = snapshot["proposal"]
        body = {"response": response, "prompt_id": proposal["prompt_id"],
                "proposal_version": proposal["proposal_version"], "payload_hash": proposal["payload_hash"], **overrides}
        return self.service.consent(ALICE, snapshot["run_id"], self.key("consent"), body)

    def sql(self, statement, params=()):
        conn = sqlite3.connect(str(self.settings.db_path))
        try:
            with conn:
                return conn.execute(statement, params).fetchall()
        finally:
            conn.close()


class MigrationTests(unittest.TestCase):
    def test_previous_schema_upgrades_and_altered_or_newer_schemas_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "serving.db"
            self.assertEqual(storage.migrate(db, target=1), 1)
            with self.assertRaises(storage.SchemaError):
                storage.check_schema(db)  # The service refuses to start on the previous version.
            conn = storage.connect(db)
            with storage.write_tx(conn):
                auth.seed(conn)
            conn.close()
            self.assertEqual(storage.migrate(db), storage.SCHEMA_VERSION)
            storage.check_schema(db)
            conn = storage.connect(db)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM principals").fetchone()[0], 5)  # Data kept.
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            conn.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version=1")
            conn.close()
            with self.assertRaises(storage.SchemaError):
                storage.migrate(db)
            conn = storage.connect(db)
            conn.execute("UPDATE schema_migrations SET checksum=? WHERE version=1", (storage.checksum(storage.MIGRATIONS[0][2]),))
            conn.execute("INSERT INTO schema_migrations VALUES (99, 'future', 'x', 0)")
            conn.close()
            with self.assertRaises(storage.SchemaError):
                storage.migrate(db)


class GateParityTests(ServiceCase):
    def test_prompt_text_is_the_existing_gate_prompt(self):
        snapshot = self.new_run()
        intent = MutationIntent("change_order_address", snapshot["proposal"]["arguments"], snapshot["proposal"]["operation_id"])
        gate = ConfirmationGate({"change_order_address": lambda arguments: None})
        self.assertEqual(snapshot["proposal"]["prompt"], gate.propose(intent).prompt)
        self.assertEqual(snapshot["proposal"]["payload_hash"], intent.fingerprint)
        self.assertEqual(render_prompt(intent), gate.propose(intent).prompt)

    def test_response_decisions_match_the_existing_gate(self):
        responses = ["yes", "Yes.", "I confirm.", "approved", "no", "cancel", "maybe", "yes please",
                     "Yes, but change the apartment to 4B.", "sure", "", "Yes, I approve these exact details as well."]
        for text in filter(None, responses):
            gate = ConfirmationGate({"change_order_address": lambda arguments: None})
            proposed = gate.propose(MutationIntent("change_order_address", {"order_id": "SYN-ORDER-1042", "address": ADDRESS}, "op"))
            expected = gate.respond(text, prompt_id=proposed.prompt_id).status
            status, body = self.consent(self.new_run(), text)
            self.assertEqual(body["decision"], expected, text)
            if expected != "authorized":
                self.sql("UPDATE runs SET state='cancelled' WHERE run_id=?", (body["run_id"],))

    def test_prompt_is_recorded_once_when_shown_and_consent_requires_it(self):
        _, session = self.service.create_session(ALICE, self.key("session"), {"resource_id": "SYN-ORDER-1042"})
        _, run = self.service.create_run(ALICE, session["session_id"], self.key("run"), RUN_BODY, ORDER)
        prompts = lambda: self.sql("SELECT COUNT(*) FROM events WHERE run_id=? AND event_type='prompt'", (run["run_id"],))[0][0]
        self.assertEqual(prompts(), 0)  # Generated, not yet shown to anyone.
        proposal = self.sql("SELECT prompt_id, payload_hash FROM proposals WHERE run_id=?", (run["run_id"],))[0]
        blind = {"run_id": run["run_id"], "proposal": {"prompt_id": proposal[0], "proposal_version": 1, "payload_hash": proposal[1]}}
        status, refused = self.consent(blind)
        self.assertEqual((status, refused["decision"]), (409, "prompt_not_delivered"))
        for _ in range(3):
            shown = self.service.snapshot(ALICE, run["run_id"])
        self.assertEqual(prompts(), 1)
        self.assertNotIn("_prompt_delivered", shown)
        self.assertEqual(self.consent(shown)[1]["decision"], "authorized")

    def test_binding_hash_covers_every_bound_field(self):
        base = {"actor_id": "alice", "tenant_id": "acme", "session_id": "s", "operation_id": "o", "resource_id": "r",
                "resource_revision": 1, "payload_hash": "p", "prompt_id": "q", "proposal_version": 1,
                "canonical_schema_version": "c", "policy_version": "v", "classifier_version": "k"}
        hashes = {consent_binding_hash(base)}
        for field in base:
            hashes.add(consent_binding_hash(dict(base, **{field: base[field] + 1 if isinstance(base[field], int) else "x"})))
        self.assertEqual(len(hashes), len(base) + 1)


class TransactionTests(ServiceCase):
    def test_competing_claimers_produce_one_claim(self):
        snapshot = self.new_run()
        self.consent(snapshot)
        results, barrier = [], threading.Barrier(8)

        def claim():
            barrier.wait()
            results.extend(Service(self.settings).claim(4))
        threads = [threading.Thread(target=claim) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 1)
        self.assertEqual(self.sql("SELECT attempt_count, state FROM operations"), [(1, "dispatching")])

    def test_competing_writers_allocate_contiguous_event_sequences(self):
        snapshot = self.new_run()
        barrier = threading.Barrier(8)

        def respond(index):
            barrier.wait()
            for repeat in range(5):
                Service(self.settings).consent(ALICE, snapshot["run_id"], f"ambiguous-{index}-{repeat}", {
                    "response": "maybe", "prompt_id": snapshot["proposal"]["prompt_id"],
                    "proposal_version": 1, "payload_hash": snapshot["proposal"]["payload_hash"]})
        threads = [threading.Thread(target=respond, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        sequences = [row[0] for row in self.sql("SELECT sequence FROM events WHERE run_id=? ORDER BY sequence",
                                                (snapshot["run_id"],))]
        self.assertEqual(sequences, list(range(1, 2 + 40 + 1)))

    def test_claim_revalidates_and_invalidates_without_dispatch(self):
        cases = {
            "credential_revoked": lambda run: self.sql("UPDATE principals SET status='revoked' WHERE actor_id='alice'"),
            "resource_revision_changed": lambda run: self.sql("UPDATE resources SET known_revision=7"),
            "policy_or_schema_version_changed": lambda run: self.sql(
                "UPDATE proposals SET policy_version='old' WHERE run_id=?", (run,)),
            "consent_binding_mismatch": lambda run: self.sql(
                "UPDATE proposals SET consent_binding_hash='forged' WHERE run_id=?", (run,)),
            "authorization_expired": lambda run: self.sql(
                "UPDATE proposals SET authorization_expires_at=0 WHERE run_id=?", (run,)),
        }
        for reason, tamper in cases.items():
            snapshot = self.new_run()
            self.consent(snapshot)
            tamper(snapshot["run_id"])
            self.assertEqual(self.service.claim(4), [])
            row = self.sql("SELECT state, error_code, attempt_count FROM operations WHERE run_id=?", (snapshot["run_id"],))
            self.assertEqual(row, [("invalidated", reason, 0)], reason)
            self.sql("UPDATE principals SET status='active'")
            self.sql("UPDATE resources SET known_revision=1")

    def test_stale_claim_cannot_overwrite_recovered_unknown(self):
        snapshot = self.new_run()
        self.consent(snapshot)
        claim, = self.service.claim(1)
        self.assertEqual(self.service.recover_orphans(), 1)
        self.assertFalse(self.service.complete(claim, Outcome("succeeded", "applied", {"revision": 2, "effect_id": 1})))
        self.assertEqual(self.service.snapshot(ALICE, snapshot["run_id"])["operation"]["state"], "unknown")

    def test_idempotency_conflict_and_cross_principal_isolation(self):
        snapshot = self.new_run()
        self.service.consent(ALICE, snapshot["run_id"], "consent-same-key", {
            "response": "maybe", "prompt_id": snapshot["proposal"]["prompt_id"], "proposal_version": 1,
            "payload_hash": snapshot["proposal"]["payload_hash"]})
        with self.assertRaises(ServiceError) as raised:
            self.service.consent(ALICE, snapshot["run_id"], "consent-same-key", {
                "response": "yes", "prompt_id": snapshot["proposal"]["prompt_id"], "proposal_version": 1,
                "payload_hash": snapshot["proposal"]["payload_hash"]})
        self.assertEqual(raised.exception.code, "idempotency_conflict")
        carol = Principal("carol", "acme", frozenset(CUSTOMER_SCOPES))
        with self.assertRaises(ServiceError) as raised:
            self.service.snapshot(carol, snapshot["run_id"])
        self.assertEqual(raised.exception.status, 404)


class WorkerRecordTests(unittest.TestCase):
    def test_completion_write_retries_until_it_lands(self):
        from serving.worker import Worker
        calls = []

        class FlakyService:
            def complete(self, claim, outcome):
                calls.append(outcome.state)
                if len(calls) <= 7:
                    raise ServiceError(503, "database_busy", "busy", retryable=True)
                return True
        worker = Worker(FlakyService(), None, Settings(), Telemetry(open(os.devnull, "w")), lambda: None)

        async def no_wait(_):
            return None
        with mock.patch("serving.worker.asyncio.sleep", no_wait):
            asyncio.run(worker._record(mock.Mock(operation_id="op_test"), Outcome("succeeded", "applied", {"revision": 2})))
        self.assertEqual(len(calls), 8)  # Past the old 5-attempt cutoff; the real outcome is recorded.

    def test_permanent_failure_parks_the_outcome_frees_the_slot_and_degrades(self):
        from serving.worker import Worker
        calls = []

        class BrokenService:
            def complete(self, claim, outcome):
                calls.append(outcome.state)
                raise sqlite3.OperationalError("attempt to write a readonly database")
        worker = Worker(BrokenService(), None, Settings(), Telemetry(open(os.devnull, "w")), lambda: None)

        async def scenario():
            with mock.patch("serving.worker.asyncio.sleep", mock.AsyncMock()):
                await asyncio.wait_for(worker._record(mock.Mock(operation_id="op_test"),
                                                      Outcome("succeeded", "applied", {"revision": 2})), timeout=5)
        asyncio.run(scenario())  # Returns: the dispatch slot is not held forever.
        self.assertEqual(len(calls), 1)  # Permanent: no tight retry loop.
        self.assertTrue(worker.degraded)
        self.assertEqual(list(worker.parked), ["op_test"])  # Kept, not dropped.


class StreamTests(ServiceCase):
    settings_overrides = {"sse_batch_size": 2, "sse_heartbeat_seconds": 0.001}

    def stream(self, run_id, after=0, authorized=lambda: True):
        return event_stream(service=self.service, settings=self.settings, notifier=Notifier(),
                            telemetry=Telemetry(open(os.devnull, "w")), run_id=run_id, after=after,
                            still_authorized=authorized)

    def pad_events(self, run_id, count):
        conn = self.service.connect()
        with storage.write_tx(conn):
            for index in range(count):
                self.service._event(conn, run_id, "test.padding", {"index": index}, now=time.time())
        conn.close()

    def test_purge_during_replay_ends_with_explicit_expiry_not_success(self):
        snapshot = self.new_run()
        self.pad_events(snapshot["run_id"], 6)
        self.service.cancel(ALICE, snapshot["run_id"], self.key("cancel"))

        async def scenario():
            frames, generator = [], self.stream(snapshot["run_id"])
            frames.append(await generator.__anext__())  # retry:
            frames.append(await generator.__anext__())  # sequence 1
            self.service.maintenance(now=time.time() + self.settings.event_retention_seconds + 10)
            async for frame in generator:
                frames.append(frame)
            return frames
        frames = asyncio.run(scenario())
        self.assertTrue(frames[-1].startswith(b"event: serving.replay_expired"), frames[-1])

    def test_revocation_is_enforced_during_a_long_backlog(self):
        snapshot = self.new_run()
        self.pad_events(snapshot["run_id"], 40)
        checks = []

        def authorized():
            checks.append(1)
            return len(checks) < 2

        async def scenario():
            return [frame async for frame in self.stream(snapshot["run_id"], authorized=authorized)]
        frames = asyncio.run(scenario())
        delivered = sum(frame.startswith(b"id: ") for frame in frames)
        self.assertLess(delivered, 42)  # Stopped mid-backlog, not after all pages.
        self.assertEqual(len(checks), 2)

    def test_wakeup_just_before_the_check_does_not_delay_revocation(self):
        snapshot = self.new_run()
        settings = Settings(**{**self.settings.__dict__, "sse_heartbeat_seconds": 1.0})
        notifier, revoked = Notifier(), threading.Event()

        async def scenario():
            generator = event_stream(service=self.service, settings=settings, notifier=notifier,
                                     telemetry=Telemetry(open(os.devnull, "w")), run_id=snapshot["run_id"], after=0,
                                     still_authorized=lambda: not revoked.is_set())
            revoked.set()
            loop = asyncio.get_running_loop()
            loop.call_later(0.9, notifier.notify)  # A commit wakes the stream just before the check is due.
            started = time.monotonic()
            frames = [frame async for frame in generator]
            return time.monotonic() - started, frames
        elapsed, frames = asyncio.run(scenario())
        self.assertLess(elapsed, 1.5, frames)  # Previously ~1.9 s: the wakeup restarted a full heartbeat.

    def test_stream_slot_released_when_headers_fail_or_client_leaves_first(self):
        for failing_send in (True, False):
            released = []

            async def body():
                yield b"never started"

            response = SSEResponse(body(), send_timeout=1, telemetry=Telemetry(open(os.devnull, "w")),
                                   release=lambda: released.append(1))

            async def send(message):
                if failing_send:
                    raise OSError("connection reset while sending headers")
                await asyncio.sleep(10)

            async def receive():
                return {"type": "http.disconnect"}

            async def scenario():
                try:
                    await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
                except Exception:  # The header failure surfaces (possibly as an ExceptionGroup).
                    pass
            asyncio.run(scenario())
            self.assertEqual(released, [1], failing_send)


class RestoreCrashTests(ServiceCase):
    def test_crash_before_atomic_rename_leaves_live_db_and_marked_staging(self):
        spec = importlib.util.spec_from_file_location("serving_cli", ROOT / "scripts" / "serving.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        snapshot = Path(self.tmp.name) / "snapshot.db"
        storage.backup(self.settings.db_path, snapshot)
        with mock.patch.object(cli.os, "replace", side_effect=OSError("simulated crash")):
            with self.assertRaises(OSError):
                cli.cmd_restore(self.settings, mock.Mock(path=str(snapshot)))
        live = storage.connect(self.settings.db_path)
        staged = storage.connect(self.settings.db_path.with_name("serving.restore-staging.db"))
        try:
            self.assertIsNone(storage.get_meta(live, "restore_state"))
            self.assertEqual(storage.get_meta(staged, "restore_state"), "unverified")
        finally:
            live.close()
            staged.close()


    def test_unverified_restore_refuses_cancel_and_new_prompt_deliveries(self):
        _, session = self.service.create_session(ALICE, self.key("session"), {"resource_id": "SYN-ORDER-1042"})
        _, run = self.service.create_run(ALICE, session["session_id"], self.key("run"), RUN_BODY, ORDER)
        self.sql("INSERT INTO meta VALUES ('restore_state', 'unverified')")
        with self.assertRaises(ServiceError) as refused:
            self.service.cancel(ALICE, run["run_id"], self.key("cancel"))
        self.assertEqual(refused.exception.code, "restore_unverified")
        shown = self.service.snapshot(ALICE, run["run_id"])
        self.assertEqual((shown["proposal"]["prompt"], shown["proposal"]["prompt_unavailable"]), (None, "delivery_not_recorded"))
        self.assertEqual(self.sql("SELECT state FROM runs WHERE run_id=?", (run["run_id"],)), [("awaiting_consent",)])


class DemoClientPolicyTests(unittest.TestCase):
    """Static guard on web/serving: the token stays in memory and server text is never parsed as HTML."""

    def test_client_never_persists_the_token_or_renders_html(self):
        client = ROOT / "web" / "serving"
        script = re.sub(r"//[^\n]*", "", (client / "client.js").read_text(encoding="utf-8"))  # Code, not comments.
        page = (client / "index.html").read_text(encoding="utf-8")
        for banned in ("localStorage", "sessionStorage", "indexedDB", "document.cookie", "innerHTML", "outerHTML",
                       "insertAdjacentHTML", "document.write", "eval(", "new Function", "EventSource",
                       "location", "history.", "URLSearchParams", "console."):
            self.assertFalse(banned in script, f"client.js uses {banned}")
        # CSP is script-src 'self': every script is an external same-origin file, no inline handlers.
        self.assertEqual(page.count("<script"), page.count('<script src="client.js"'))
        self.assertNotRegex(page, r"\son[a-z]+\s*=")
        self.assertRegex(page, r'id="token" type="password" autocomplete="off"')


if __name__ == "__main__":
    unittest.main()
