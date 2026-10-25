"""Exposed recovery regressions: actual HTTP and independent provider effect ledger."""
import json
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import subprocess
import sqlite3
import sys
import threading
import unittest

import httpx

from tests_serving.harness import ADDRESS, CLI, ROOT, Stack
from provider_sim.server import Handler, ProviderServer, init_db
from evalgate.recovery import check_recovery
from evalgate.trace import validate_trace


class ObservedHandler(Handler):
    """Test observer only; the provider's real SQLite effect transaction is unchanged."""
    def do_POST(self):
        if self.path == "/v1/mutations":
            with self.server.observer_lock:
                self.server.mutation_requests += 1
        return super().do_POST()

    def _status(self, key):
        if key.startswith("rra-restore-probe-"):
            return super()._status(key)
        with self.server.observer_lock:
            self.server.status_requests += 1
            malformed = self.server.status_requests == self.server.malformed_status_at
        if malformed:
            return self._send(200, {"status": "applied", "idempotency_key": key, "effect_id": 1})
        return super()._status(key)


class RestoreEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.s = Stack()
        db = self.s.root / "provider" / "provider.db"
        init_db(db)
        self.provider = ProviderServer(("127.0.0.1", self.s.provider_port), db_path=db,
                                      key=(self.s.root / "app" / "provider.key").read_text().strip(),
                                      mode="full", test_mode=True)
        self.provider.RequestHandlerClass = ObservedHandler
        self.provider.observer_lock = threading.Lock()
        self.provider.mutation_requests = self.provider.status_requests = 0
        self.provider.malformed_status_at = -1
        self.provider_thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        self.provider_thread.start()
        self.s.start_app()

    def tearDown(self):
        self.s.close()
        self.provider.shutdown()
        self.provider.server_close()
        self.provider_thread.join(timeout=5)

    def restore_applied(self):
        s = self.s
        run = s.new_run(tag="restore-evidence")
        self.before = s.snapshot(run["run_id"])
        original_trace = s.get(f"/api/v1/runs/{run['run_id']}/trace?contract=trace-v2", "alice")
        self.original_identity = {k: json.loads(original_trace.text.splitlines()[0])[k] for k in ("run_id", "session_id", "trace_id")}
        s.run_cli("backup", str(s.root / "before-consent.db"))
        # Independent SQLite backup and hash, before restore writes its own metadata.
        with closing(sqlite3.connect(str(s.root / "before-consent.db"))) as source:
            with closing(sqlite3.connect(str(s.root / "independent-snapshot.db"))) as destination:
                source.backup(destination)
        expected_snapshot_hash = sha256((s.root / "independent-snapshot.db").read_bytes()).hexdigest()
        s.approve(run)
        s.wait_state(run["run_id"], {"succeeded"})
        self.after = s.snapshot(run["run_id"])
        s.stop_app()
        s.run_cli("restore", str(s.root / "before-consent.db"))
        with closing(s.app_db()) as db:
            meta = dict(db.execute("SELECT key, value FROM meta"))
        self.assertEqual(meta["restore_snapshot_sha256"], expected_snapshot_hash)
        self.expected = {**self.original_identity, "operation_id": run["proposal"]["operation_id"], "tool_name": "change_order_address",
                         "arguments": {"order_id": "SYN-ORDER-1042", "address": ADDRESS}, "tenant": "acme",
                         "expected_revision": 1, "retained_authorizations": 0, "retained_dispatches": 0,
                         "restore_id": meta["restore_generation"], "snapshot_sha256": expected_snapshot_hash}
        s.run_cli("verify-restore")
        s.start_app()
        return run

    def test_v3_explicit_recovery_without_invented_attempt(self):
        run = self.restore_applied()
        r = self.s.get(f"/api/v1/runs/{run['run_id']}/trace?contract=trace-v3", "alice")
        self.assertEqual(r.status_code, 200, r.text)
        events = [json.loads(line) for line in r.text.splitlines()]
        recovery = [e for e in events if e["event_type"] == "serving.restore_observed"]
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0]["payload"]["history"], "missing_after_restore")
        self.assertFalse(any(e["event_type"] in ("authorized", "dispatch_started") for e in events))
        self.assertEqual(len(self.s.provider_effects()), 1)
        self.assertEqual(self.provider.mutation_requests, 1)
        self.assertEqual(validate_trace(events), [])
        self.assertEqual(check_recovery(events, self.s.provider_effects(), self.expected)["verdict"], "PASS")
        self.assertFalse(check_recovery(events, self.s.provider_effects(), self.expected)["local_authorization_attested"])
        for version in ("trace-v1", "trace-v2"):
            legacy = self.s.get(f"/api/v1/runs/{run['run_id']}/trace?contract={version}", "alice")
            self.assertTrue(validate_trace([json.loads(x) for x in legacy.text.splitlines()]))
        with closing(self.s.app_db()) as db:
            row = db.execute("SELECT attempt_count, consumed_at FROM operations JOIN proposals USING(operation_id) WHERE operations.run_id=?", (run["run_id"],)).fetchone()
        self.assertEqual(tuple(row), (0, None))

    def test_old_sse_cursor_rejected_even_when_sequence_exists(self):
        run = self.restore_applied()
        r = httpx.get(self.s.url(f"/api/v1/runs/{run['run_id']}/events"),
                      headers=self.s.headers("alice", **{"Last-Event-ID": self.before["last_event_id"]}), timeout=5)
        self.assertEqual(r.status_code, 410, r.text)
        self.assertEqual(r.json()["code"], "replay_expired")

    def test_dashboard_discloses_recovered_effect_and_missing_history(self):
        run = self.restore_applied()
        listing = self.s.get("/api/v1/observability/requests", "alice").json()
        key = next(item["key"] for item in listing["items"] if item["run_id"] == run["run_id"])
        detail = self.s.get("/api/v1/observability/requests/" + key, "alice").json()
        op = next(iter(detail["operations"].values()))
        self.assertEqual(op["outcome"], "effect_observed_after_restore")
        self.assertTrue(op["history_incomplete"])
        self.assertEqual(op["mutation_attempts"], 0)
        self.assertFalse(op["authorized"])
        self.assertIn("missing_after_restore", detail["evidence"]["completeness"])

    def events(self, run):
        r = self.s.get(f"/api/v1/runs/{run['run_id']}/trace?contract=trace-v3", "alice")
        self.assertEqual(r.status_code, 200)
        return [json.loads(x) for x in r.text.splitlines()]

    def test_tampered_recovery_evidence_is_rejected(self):
        run = self.restore_applied()
        events, effects = self.events(run), self.s.provider_effects()
        mutants = {}
        for field, value in (("effect_id", 999), ("provider_revision", 9), ("restore_id", "0"*32),
                             ("snapshot_sha256", "0"*64), ("provider_key_sha256", "0"*64),
                             ("history", "complete"), ("outcome", "no_effect_confirmed"), ("effect_id", True),
                             ("provider_revision", {"bad": "shape"})):
            clone = deepcopy(events)
            next(e for e in clone if e["event_type"] == "serving.restore_observed")["payload"][field] = value
            mutants[field + ":" + str(value)] = clone
        clone = deepcopy(events)
        next(e for e in clone if e["event_type"] == "serving.restore_observed")["payload_hash"] = "0"*64
        mutants["payload_binding"] = clone
        clone = deepcopy(events)
        next(e for e in clone if e["event_type"] == "serving.restore_observed")["operation_id"] = "op_" + "0"*32
        mutants["operation_binding"] = clone
        clone = deepcopy(events)
        restored = next(e for e in clone if e["event_type"] == "serving.restore_observed")
        clone.insert(-1, deepcopy(restored))
        for i, e in enumerate(clone, 1): e.update(sequence=i, event_id="mutation-"+str(i))
        mutants["duplicate_observation"] = clone
        clone = deepcopy(events)
        for event in clone:
            for field in ("run_id", "trace_id", "session_id"): event[field] = "WRONG-" + field
        mutants["transplanted_stream"] = clone
        clone = deepcopy(events)
        prompt = next(e for e in clone if e["event_type"] == "prompt")
        auth = {**deepcopy(prompt), "event_type": "authorized", "payload": {"classifier_version": "fabricated"}}
        clone.insert(2, auth)
        for i, e in enumerate(clone, 1): e.update(sequence=i, event_id="fabricated-"+str(i))
        mutants["fabricated_authorization"] = clone
        clone = deepcopy(events)
        template = next(e for e in clone if e["event_type"] == "prompt")
        follow = [deepcopy(template),
                  {**deepcopy(template), "event_type": "authorized", "payload": {"classifier_version": "fabricated"}},
                  {**deepcopy(template), "event_type": "dispatch_started", "payload": {"tool_name": "change_order_address"}},
                  {**deepcopy(template), "event_type": "dispatch_succeeded", "payload": {"tool_name": "change_order_address"}}]
        clone[-1:-1] = follow
        for i, e in enumerate(clone, 1): e.update(sequence=i, event_id="redispatch-"+str(i))
        self.assertTrue(any("attempt after recovered effect" in p for p in validate_trace(clone)))
        mutants["restore_cannot_grant_later_dispatch"] = clone
        for name, clone in mutants.items():
            with self.subTest(mutant=name):
                self.assertEqual(check_recovery(clone, effects, self.expected)["verdict"], "INVALID")
        for field, value in (("tenant", "wrong"), ("request_hash", "0"*64), ("address", "{}"), ("revision_after", 99)):
            clone = deepcopy(effects); clone[0][field] = value
            with self.subTest(ledger_mutant=field):
                self.assertEqual(check_recovery(events, clone, self.expected)["verdict"], "INVALID")
        print(f"RESTORE_MUTANTS: {len(mutants)+4}/{len(mutants)+4} rejected; exposed regression evidence")

    def test_verification_repeat_and_restore_generation_repeat(self):
        run = self.restore_applied()
        first = self.events(run)
        cursor = self.s.snapshot(run["run_id"])["last_event_id"]
        self.s.stop_app(); self.s.run_cli("verify-restore"); self.s.start_app()
        self.assertEqual(self.events(run), first)
        r = httpx.get(self.s.url(f"/api/v1/runs/{run['run_id']}/events"),
                      headers=self.s.headers("alice", **{"Last-Event-ID": cursor}), timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("\nid: ", r.text)
        self.s.stop_app(); self.s.run_cli("restore", str(self.s.root / "before-consent.db"))
        self.s.run_cli("verify-restore"); self.s.start_app()
        newer = self.s.snapshot(run["run_id"])["last_event_id"]
        self.assertNotEqual(newer, cursor)
        old = httpx.get(self.s.url(f"/api/v1/runs/{run['run_id']}/events"),
                        headers=self.s.headers("alice", **{"Last-Event-ID": cursor}), timeout=5)
        self.assertEqual(old.status_code, 410)
        self.assertEqual(self.provider.mutation_requests, 1)
        self.assertEqual(len(self.s.provider_effects()), 1)

    def test_partial_malformed_verification_stays_read_only_then_retries(self):
        s = self.s
        a = s.new_run(tag="partial-a")
        b = s.new_run("carol", "SYN-ORDER-1043", tag="partial-b")
        s.run_cli("backup", str(s.root / "partial.db"))
        s.approve(a); s.approve(b, "carol")
        s.wait_state(a["run_id"], {"succeeded"}); s.wait_state(b["run_id"], {"succeeded"}, "carol")
        s.stop_app(); s.run_cli("restore", str(s.root / "partial.db"))
        self.provider.malformed_status_at = 2
        failed = subprocess.run([sys.executable, str(CLI), "verify-restore"], cwd=ROOT, env=s.env(),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(failed.returncode, 1)
        s.start_app(ready=False)
        self.assertEqual(httpx.get(s.url("/health/ready")).status_code, 503)
        with closing(s.app_db()) as db:
            settled = db.execute("SELECT COUNT(*) FROM operations WHERE state='succeeded'").fetchone()[0]
        self.assertEqual(settled, 1)
        s.stop_app(); s.run_cli("verify-restore"); s.start_app()
        self.assertEqual(httpx.get(s.url("/health/ready")).status_code, 200)
        for run, actor in ((a, "alice"), (b, "carol")):
            r = s.get(f"/api/v1/runs/{run['run_id']}/trace?contract=trace-v3", actor)
            self.assertEqual(sum(json.loads(x)["event_type"] == "serving.restore_observed" for x in r.text.splitlines()), 1)
        self.assertEqual((self.provider.mutation_requests, len(s.provider_effects())), (2, 2))

    def test_repeated_restore_preserves_both_backups_with_same_clock(self):
        s = self.s
        s.run_cli("backup", str(s.root / "same-clock.db"))
        s.stop_app()
        code = ("import sys,runpy,time; time.time=lambda:1700000000; "
                "sys.argv=[sys.argv[1], 'restore', sys.argv[2]]; "
                "runpy.run_path(sys.argv[0],run_name='__main__')")
        for _ in range(2):
            result = subprocess.run([sys.executable, "-B", "-c", code, str(CLI),
                                     str(s.root / "same-clock.db")], cwd=ROOT, env=s.env(),
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
        backups = list((s.root / "app").glob("serving.pre-restore-1700000000*.db"))
        self.assertEqual(len(backups), 2)
        for path in backups:
            with closing(sqlite3.connect(str(path))) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual((self.provider.mutation_requests, len(s.provider_effects())), (0, 0))

    def test_basic_provider_restore_remains_unverified(self):
        run = self.restore_applied()
        self.s.stop_app(); self.s.run_cli("restore", str(self.s.root / "before-consent.db"))
        self.provider.mode = "basic"
        r = subprocess.run([sys.executable, str(CLI), "verify-restore"], cwd=ROOT, env=self.s.env(),
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 1)
        self.s.start_app(ready=False)
        self.assertEqual(httpx.get(self.s.url("/health/ready")).status_code, 503)
        self.assertEqual((self.provider.mutation_requests, len(self.s.provider_effects())), (1, 1))


if __name__ == "__main__":
    unittest.main()
