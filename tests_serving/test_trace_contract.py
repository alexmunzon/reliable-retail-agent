"""Service traces, exported over HTTP, judged by the evaluation owner's own pinned checker.

The checker and schema are read from the evaluation-gate commit pinned in serving.trace_export
(git archive into a temp dir; that worktree is never touched). At that commit the checker validates
envelopes, enums, required fields and sequence order (`evalgate.trace.validate_trace`); ordering
semantics are not part of the committed handoff yet. Each scenario runs through the real HTTP
service, and the trace's story is cross-checked against the provider's independent effect ledger:
a trace may never claim fewer or more effects than the provider recorded.
"""
import concurrent.futures
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest

import httpx

from serving.service import TERMINAL_RUN_STATES
from serving.trace_export import TRACE_V1_COMMIT, TRACE_V1_SCHEMA_BLOB
from tests_serving.harness import ADDRESS, ROOT, Stack



def load_owner_checker() -> Path | None:
    """Extract evalgate/ and contracts/ at the pinned commit; None if this clone lacks that commit."""
    blob = subprocess.run(["git", "-C", str(ROOT), "rev-parse", f"{TRACE_V1_COMMIT}:contracts/trace-v1.schema.json"],
                          capture_output=True, text=True)
    if blob.returncode != 0:
        return None
    assert blob.stdout.strip() == TRACE_V1_SCHEMA_BLOB, "pinned schema blob changed"
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", TRACE_V1_COMMIT, "evalgate", "contracts"],
                             capture_output=True, check=True)
    target = Path(tempfile.mkdtemp(prefix="rra-trace-v1-"))
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        members = [m for m in tar.getmembers() if m.isfile() or m.isdir()]
        assert all(not m.name.startswith("/") and ".." not in Path(m.name).parts for m in members)
        tar.extractall(target, members=members)
    return target


class ContractCase(unittest.TestCase):
    """Shared stack, export, owner check and ledger comparison; subclasses hold the scenarios."""
    env = {"RRA_RECONCILE_BACKOFF_MAX_SECONDS": "0.5"}
    @classmethod
    def setUpClass(cls):
        cls.checker = load_owner_checker()
        if cls.checker is None:
            raise unittest.SkipTest(f"evaluation-gate commit {TRACE_V1_COMMIT[:7]} is not in this clone: "
                                    "trace-v1 compatibility is NOT established here")
        cls.stack = Stack(env=cls.env)
        cls.stack.start_provider()
        cls.stack.start_app()

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    # ------------------------------------------------------------------ helpers
    def trace(self, run_id, actor="alice"):
        response = self.stack.get(f"/api/v1/runs/{run_id}/trace", actor)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("application/x-ndjson"))
        return response.text

    def owner_check(self, text):
        path = self.checker / f"trace-{time.monotonic_ns()}.jsonl"
        path.write_text(text, encoding="utf-8")
        code = ("import json, pathlib, sys; sys.path.insert(0, sys.argv[1]); from evalgate.trace import validate_trace; "
                "events = [json.loads(line) for line in pathlib.Path(sys.argv[2]).read_text().splitlines()]; "
                "print(json.dumps(validate_trace(events)))")
        result = subprocess.run([sys.executable, "-I", "-c", code, str(self.checker), str(path)],
                                capture_output=True, text=True, timeout=60, check=True)
        return json.loads(result.stdout)

    def assert_accepted_and_consistent(self, run, actor="alice", *, expect_unknown=False):
        """Owner's checker accepts the trace, and the trace tells the same story as the provider's ledger,
        in both directions: no claimed effect without a ledger row, no ledger row the trace hides."""
        text = self.trace(run["run_id"], actor)
        self.assertEqual(self.owner_check(text), [])
        events = [json.loads(line) for line in text.splitlines()]
        for event in events:  # Privacy: hashes and codes only.
            self.assertNotIn("Example Lane", json.dumps(event))
            self.assertNotIn("rra-op_", json.dumps(event))
        proposal = run["proposal"]
        for event in events:  # Every exported binding is this run's own operation and payload.
            self.assertEqual(event.get("operation_id", proposal["operation_id"]), proposal["operation_id"])
            self.assertEqual(event.get("payload_hash", proposal["payload_hash"]), proposal["payload_hash"])
        kinds = [event["event_type"] for event in events]
        self.assertEqual([e["sequence"] for e in events], list(range(1, len(events) + 1)))  # No gaps.
        # A finished run's trace ends with exactly one completeness marker; an open run's has none.
        finished = self.stack.snapshot(run["run_id"], actor)["state"] in TERMINAL_RUN_STATES
        self.assertEqual(kinds.count("serving.run_finished"), int(finished))
        if finished:
            self.assertEqual(kinds.pop(), "serving.run_finished")
        outcomes = {event["payload"].get("outcome") for event in events if event["event_type"] == "serving.reconciled"}
        effects = [e for e in self.stack.provider_effects()
                   if e["idempotency_key"] == "rra-" + run["proposal"]["operation_id"]]
        self.assertLessEqual(kinds.count("dispatch_started"), 1)
        self.assertLessEqual(len(effects), 1)
        confirmed = "dispatch_succeeded" in kinds or "effect_confirmed" in outcomes
        denied = "no_effect_confirmed" in outcomes or "serving.dispatch_failed_no_effect" in kinds
        if confirmed:
            self.assertEqual(len(effects), 1)
        if denied or "dispatch_started" not in kinds:
            self.assertEqual(effects, [])
        if effects and not confirmed:  # The ledger has an effect: only an explicit, expected unknown may stand.
            self.assertTrue(expect_unknown and outcomes == {"still_unknown"}, kinds)
        if expect_unknown:
            self.assertFalse(confirmed or denied)
        return kinds



class TraceContractTests(ContractCase):
    def test_success_and_duplicate_concurrent_approvals(self):
        s = self.stack
        run = s.new_run(tag="dup")
        same_key = [s.approve(run, key="trace-dup-same-0001") for _ in range(2)]
        self.assertEqual(same_key[0].json(), same_key[1].json())
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            racing = list(pool.map(lambda i: s.approve(run, key=f"trace-dup-race-{i:04d}"), range(8)))
        self.assertTrue(all(r.json()["decision"] == "consent_not_pending" for r in racing))
        s.wait_state(run["run_id"], {"succeeded"})
        kinds = self.assert_accepted_and_consistent(run)
        self.assertEqual((kinds.count("authorized"), kinds.count("dispatch_succeeded")), (1, 1))

    def test_stale_and_ambiguous_consent_then_rejection(self):
        s = self.stack
        run = s.new_run(tag="stale")
        stale = dict(s.consent_body(run), payload_hash="0" * 64)
        self.assertEqual(s.post(f"/api/v1/runs/{run['run_id']}/consents", "alice", "trace-stale-0001", stale).status_code, 409)
        maybe = s.consent_body(run, "maybe later?")
        self.assertEqual(s.post(f"/api/v1/runs/{run['run_id']}/consents", "alice", "trace-ambig-0001", maybe).json()["decision"],
                         "ambiguous")
        no = s.consent_body(run, "no")
        self.assertEqual(s.post(f"/api/v1/runs/{run['run_id']}/consents", "alice", "trace-no-00001", no).json()["decision"],
                         "rejected")
        kinds = self.assert_accepted_and_consistent(run)
        self.assertEqual(kinds.count("blocked"), 2)
        self.assertNotIn("dispatch_started", kinds)

    def test_cancellation_before_and_after_the_claim(self):
        s = self.stack
        early = s.new_run(tag="cancel-early")
        s.post(f"/api/v1/runs/{early['run_id']}/cancel", "alice", "trace-cancel-0001")
        self.assertIn("serving.cancelled", self.assert_accepted_and_consistent(early))
        s.arm("slow:1.5")
        late = s.new_run(tag="cancel-late")
        s.approve(late)
        s.wait_state(late["run_id"], {"dispatching"})
        s.post(f"/api/v1/runs/{late['run_id']}/cancel", "alice", "trace-cancel-0002")
        s.wait_state(late["run_id"], {"succeeded"})
        kinds = self.assert_accepted_and_consistent(late)
        self.assertLess(kinds.index("serving.cancel_requested"), kinds.index("dispatch_succeeded"))

    def test_unknown_outcome_reconciled_and_hold_blocks_a_second_consent(self):
        s = self.stack
        first, second = s.new_run(tag="hold-a"), s.new_run(tag="hold-b")  # Same order, both awaiting consent.
        s.arm("malformed_after_commit", *["status_unavailable"] * 3)
        s.approve(first)
        s.wait_state(first["run_id"], {"outcome_unknown"})
        blocked = s.approve(second)
        self.assertEqual((blocked.status_code, blocked.json()["decision"]), (409, "resource_on_hold"))
        s.wait_state(first["run_id"], {"succeeded"}, timeout=20)
        kinds = self.assert_accepted_and_consistent(first)
        self.assertEqual(kinds[-2:], ["dispatch_uncertain", "serving.reconciled"])
        self.assertIn("blocked", self.assert_accepted_and_consistent(second))

    def test_crash_after_claim_recovers_without_redispatch(self):
        s = self.stack
        s.stop_app()
        s.start_app({"RRA_TEST_FAILPOINTS": "crash_after_claim"})
        run = s.new_run(tag="crash")
        s.approve(run)
        self.assertEqual(s.wait_app_exit(), 70)
        s.start_app()
        s.wait_state(run["run_id"], {"failed"})
        kinds = self.assert_accepted_and_consistent(run)
        self.assertEqual(kinds[-3:], ["dispatch_started", "dispatch_uncertain", "serving.reconciled"])

    def test_definite_no_effect_failure_exports_a_namespaced_outcome(self):
        # trace-v1 has no core outcome for a definite no-effect reply. The pinned envelope check accepts
        # the namespaced event; the owner's draft ordering rules would not (see docs/SERVING.md, trace-v1).
        s = self.stack
        s.arm("reject_invalid")
        run = s.new_run(tag="no-effect")
        s.approve(run)
        s.wait_state(run["run_id"], {"failed"})
        kinds = self.assert_accepted_and_consistent(run)
        self.assertEqual(kinds[-1], "serving.dispatch_failed_no_effect")

    def test_restore_of_a_mid_dispatch_backup_records_the_uncertainty_first(self):
        s = self.stack
        s.stop_app()
        s.start_app({"RRA_TEST_FAILPOINTS": "crash_after_provider_response"})
        run = s.new_run(tag="restore")
        s.approve(run)
        self.assertEqual(s.wait_app_exit(), 71)  # Provider applied; the app died before recording it.
        s.run_cli("backup", str(s.root / "mid-dispatch.db"))
        s.run_cli("restore", str(s.root / "mid-dispatch.db"))
        self.assertIn("writes re-enabled", s.run_cli("verify-restore").stdout)  # App still stopped.
        s.start_app()
        self.assertEqual(s.snapshot(run["run_id"])["state"], "succeeded")
        kinds = self.assert_accepted_and_consistent(run)
        self.assertEqual(kinds[-3:], ["dispatch_started", "dispatch_uncertain", "serving.reconciled"])

    def test_trace_requires_ownership(self):
        run = self.stack.new_run(tag="owner")
        self.assertEqual(self.stack.get(f"/api/v1/runs/{run['run_id']}/trace", "bob").status_code, 404)



class ExpiryAndExhaustionTraceTests(ContractCase):
    """Short prompt lifetime and few status lookups: expiry, and a manual hold after exhaustion."""
    env = {"RRA_RECONCILE_BACKOFF_MAX_SECONDS": "0.5", "RRA_PROPOSAL_TTL_SECONDS": "2", "RRA_RECONCILE_MAX_ATTEMPTS": "2"}

    def test_expired_prompt(self):
        s = self.stack
        run = s.new_run(tag="expire")
        time.sleep(2.2)
        self.assertEqual(s.approve(run).status_code, 410)
        kinds = self.assert_accepted_and_consistent(run)
        self.assertEqual(kinds, ["serving.run_accepted", "prompt", "serving.expired"])

    def test_exhausted_reconciliation_leaves_an_explicit_manual_hold(self):
        s = self.stack
        run = s.new_run("carol", "SYN-ORDER-1043", tag="exhaust")
        s.arm("malformed_after_commit", *["status_unavailable"] * 4)
        s.approve(run, "carol")
        deadline = time.monotonic() + 15
        while ((s.snapshot(run["run_id"], "carol")["operation"] or {}).get("reconciliation") or {}) \
                .get("status") != "exhausted":
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.2)
        snapshot = s.snapshot(run["run_id"], "carol")
        self.assertEqual((snapshot["state"], snapshot["resource_hold"]["held"]), ("outcome_unknown", True))
        kinds = self.assert_accepted_and_consistent(run, "carol", expect_unknown=True)
        self.assertEqual(kinds[-2:], ["dispatch_uncertain", "serving.reconciled"])  # still_unknown; no run_finished.


if __name__ == "__main__":
    unittest.main()
