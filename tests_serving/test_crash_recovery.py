"""Subprocess crash/restart tests. Ground truth is the provider's own effect ledger."""
import concurrent.futures
import json
import signal
import sqlite3
import subprocess
import sys
import time
import unittest

import httpx

from tests_serving.harness import ADDRESS, CLI, ROOT, Stack
from tests_serving.sse import SSEParser


def event_types(stack, run_id, actor="alice"):
    """Replay the full committed history (the run may still be open, so read with a short deadline)."""
    parser, events = SSEParser(), []
    try:
        with httpx.stream("GET", stack.url(f"/api/v1/runs/{run_id}/events"), headers=stack.headers(actor),
                          timeout=httpx.Timeout(10, read=1.5)) as response:
            assert response.status_code == 200, response.read()
            for chunk in response.iter_raw():
                events.extend(parser.feed(chunk))
    except httpx.ReadTimeout:
        pass
    return [event["event"] for event in events]


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = Stack(env={"RRA_RECONCILE_BACKOFF_MAX_SECONDS": "0.5"})
        self.stack.start_provider()

    def tearDown(self):
        self.stack.close()

    def db_operation(self, run_id):
        conn = self.stack.app_db()
        try:
            return dict(conn.execute("SELECT * FROM operations WHERE run_id=?", (run_id,)).fetchone())
        finally:
            conn.close()

    def test_success_survives_restart_and_command_replay_never_redispatches(self):
        s = self.stack
        s.start_app()
        run = s.new_run()
        first = s.approve(run, key="approve-key-0001")
        self.assertEqual(first.status_code, 202)
        s.wait_state(run["run_id"], {"succeeded"})
        s.stop_app()
        s.start_app()
        self.assertEqual(s.snapshot(run["run_id"])["state"], "succeeded")
        replay = s.approve(run, key="approve-key-0001")
        self.assertEqual((replay.status_code, replay.json()), (first.status_code, first.json()))
        fresh = s.approve(run, key="approve-key-0002")
        self.assertEqual((fresh.status_code, fresh.json()["decision"]), (409, "consent_not_pending"))
        time.sleep(0.6)
        self.assertEqual(len(s.provider_effects()), 1)
        self.assertEqual(event_types(s, run["run_id"]).count("dispatch_started"), 1)

    def test_app_dies_after_claim_before_send_then_fenced_reconciliation_proves_no_effect(self):
        s = self.stack
        s.start_app({"RRA_TEST_FAILPOINTS": "crash_after_claim"})
        run = s.new_run()
        s.approve(run)
        self.assertEqual(s.wait_app_exit(), 70)
        self.assertEqual(self.db_operation(run["run_id"])["state"], "dispatching")
        self.assertEqual(s.provider_effects(), [])
        s.start_app()
        final = s.wait_state(run["run_id"], {"failed"})
        self.assertEqual(final["operation"]["state"], "failed_no_effect")
        self.assertEqual(final["operation"]["reconciliation"]["code"], "status_not_found_fenced")
        self.assertFalse(final["resource_hold"]["held"])
        # A delayed copy of the original request can never apply after the fence.
        late = httpx.post(f"http://127.0.0.1:{s.provider_port}/v1/mutations",
                          headers={"Authorization": "Bearer " + (s.root / "app" / "provider.key").read_text().strip()},
                          json={"idempotency_key": "rra-" + run["proposal"]["operation_id"], "tenant": "acme",
                                "tool_name": "change_order_address",
                                "arguments": {"order_id": "SYN-ORDER-1042", "address": ADDRESS}, "expected_revision": 1})
        self.assertEqual(late.json()["code"], "key_fenced")
        self.assertEqual(s.provider_effects(), [])
        self.assertEqual(event_types(s, run["run_id"])[-4:],  # Then the run's completeness marker.
                         ["dispatch_started", "dispatch_uncertain", "serving.reconciled", "serving.run_finished"])

    def test_provider_applies_then_dies_before_reply_holds_order_until_reconciled(self):
        s = self.stack
        s.start_app()
        s.arm("crash_after_commit")
        run = s.new_run()
        s.approve(run)
        unknown = s.wait_state(run["run_id"], {"outcome_unknown"})
        self.assertTrue(unknown["resource_hold"]["held"])
        self.assertEqual(len(s.provider_effects()), 1)  # Applied, though the app never saw the reply.
        s.kill_provider()
        s.start_provider()
        s.arm(*["status_unavailable"] * 8)  # Keep the outcome unresolved while the hold is probed.
        # The hold blocks a new run through the same session, a brand-new session, and a new operation.
        session = s.post("/api/v1/sessions", "alice", "hold-session-0001", {"resource_id": "SYN-ORDER-1042"})
        self.assertEqual(session.status_code, 201)
        for key, session_id in (("hold-run-0001", run["session_id"]), ("hold-run-0002", session.json()["session_id"])):
            blocked = s.post(f"/api/v1/sessions/{session_id}/runs", "alice", key,
                             {"request": {"type": "change_order_address", "address": ADDRESS}})
            self.assertEqual((blocked.status_code, blocked.json()["code"]), (409, "resource_on_hold"))
        self.assertEqual(s.snapshot(run["run_id"])["state"], "outcome_unknown")  # Still unresolved while probed.
        other = s.new_run("bob", "SYN-ORDER-2077", tag="unrelated")
        s.approve(other, "bob")
        s.wait_state(other["run_id"], {"succeeded"}, "bob")  # Other customers are not blocked.
        final = s.wait_state(run["run_id"], {"succeeded"}, timeout=20)
        self.assertEqual(final["operation"]["reconciliation"]["code"], "status_applied")
        self.assertFalse(final["resource_hold"]["held"])
        self.assertEqual(len(s.provider_effects("SYN-ORDER-1042")), 1)

    def test_app_dies_after_provider_reply_before_recording(self):
        s = self.stack
        s.start_app({"RRA_TEST_FAILPOINTS": "crash_after_provider_response"})
        run = s.new_run()
        s.approve(run)
        self.assertEqual(s.wait_app_exit(), 71)
        self.assertEqual(len(s.provider_effects()), 1)
        s.start_app()
        final = s.wait_state(run["run_id"], {"succeeded"})
        self.assertEqual(final["operation"]["reconciliation"]["code"], "status_applied")
        self.assertEqual(len(s.provider_effects()), 1)
        types = event_types(s, run["run_id"])
        self.assertEqual(types.count("dispatch_started"), 1)
        self.assertLess(types.index("dispatch_uncertain"), types.index("serving.reconciled"))

    def test_provider_dies_before_accepting(self):
        s = self.stack
        s.start_app()
        s.arm("crash_before_accept")
        run = s.new_run()
        s.approve(run)
        s.wait_state(run["run_id"], {"outcome_unknown"})
        s.kill_provider()
        s.start_provider()
        final = s.wait_state(run["run_id"], {"failed"})
        self.assertEqual(final["operation"]["state"], "failed_no_effect")
        self.assertEqual(s.provider_effects(), [])

    def test_basic_provider_without_status_stays_unknown_and_blocks_only_that_order(self):
        s = self.stack
        s.kill_provider()
        s.start_provider("basic")
        s.start_app()
        s.arm("crash_after_commit")
        run = s.new_run()
        s.approve(run)
        s.wait_state(run["run_id"], {"outcome_unknown"})
        s.kill_provider()
        s.start_provider("basic")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            snapshot = s.snapshot(run["run_id"])
            if (snapshot["operation"]["reconciliation"] or {}).get("status") == "unsupported":
                break
            time.sleep(0.1)
        self.assertEqual(snapshot["operation"]["reconciliation"]["status"], "unsupported")
        s.stop_app()
        s.start_app()
        time.sleep(1)
        snapshot = s.snapshot(run["run_id"])
        self.assertEqual((snapshot["state"], snapshot["operation"]["state"]), ("outcome_unknown", "unknown"))
        self.assertTrue(snapshot["resource_hold"]["held"])
        session = s.post("/api/v1/sessions", "alice", "basic-session-01", {"resource_id": "SYN-ORDER-1042"})
        blocked = s.post(f"/api/v1/sessions/{session.json()['session_id']}/runs", "alice", "basic-run-0001",
                         {"request": {"type": "change_order_address", "address": ADDRESS}})
        self.assertEqual((blocked.status_code, blocked.json()["code"]), (409, "resource_on_hold"))
        other = s.new_run("bob", "SYN-ORDER-2077")
        s.approve(other, "bob")
        s.wait_state(other["run_id"], {"succeeded"}, "bob")
        self.assertEqual(len(s.provider_effects("SYN-ORDER-1042")), 1)  # No automatic retry.
        self.assertIn("serving.reconciliation_unavailable", event_types(s, run["run_id"]))

    def test_definite_no_effect_replies_release_the_hold_without_retry(self):
        s = self.stack
        s.start_app()
        for action, code in (("reject_invalid", "invalid_request"), ("rate_limited", "rate_limited")):
            s.arm(action)
            run = s.new_run(tag=action)
            s.approve(run)
            final = s.wait_state(run["run_id"], {"failed"})
            self.assertEqual((final["operation"]["state"], final["operation"]["error_code"]), ("failed_no_effect", code))
            self.assertFalse(final["resource_hold"]["held"])
        # Revision drift at the provider: the precondition rejects the stale proposal with no effect.
        run = s.new_run(tag="drift")
        key = (s.root / "app" / "provider.key").read_text().strip()
        external = httpx.post(f"http://127.0.0.1:{s.provider_port}/v1/mutations", headers={"Authorization": f"Bearer {key}"},
                              json={"idempotency_key": "external-change-1", "tenant": "acme", "tool_name": "change_order_address",
                                    "arguments": {"order_id": "SYN-ORDER-1042", "address": dict(ADDRESS, apartment="9Z")},
                                    "expected_revision": run["proposal"]["resource"]["revision"]})
        self.assertEqual(external.status_code, 200)
        s.approve(run)
        final = s.wait_state(run["run_id"], {"failed"})
        self.assertEqual(final["operation"]["error_code"], "revision_conflict")
        self.assertEqual([effect["idempotency_key"] for effect in s.provider_effects()], ["external-change-1"])

    def test_timeout_and_malformed_replies_are_unknown_not_failed(self):
        s = self.stack
        s.start_app()
        s.arm("malformed_after_commit")
        run = s.new_run(tag="malformed")
        s.approve(run)
        final = s.wait_state(run["run_id"], {"succeeded"})
        self.assertEqual(final["operation"]["reconciliation"]["code"], "status_applied")
        self.assertIn("dispatch_uncertain", event_types(s, run["run_id"]))
        s.arm("hang")
        run = s.new_run(tag="hang")
        s.approve(run)
        final = s.wait_state(run["run_id"], {"failed"}, timeout=15)
        # Timed out client-side, then the fenced lookup proved the hung request had not applied.
        self.assertEqual(final["operation"]["error_code"], "provider_timeout")
        self.assertEqual(final["operation"]["reconciliation"]["code"], "status_not_found_fenced")
        self.assertEqual(len(s.provider_effects()), 1)

    def test_unavailable_status_lookup_retries_read_only_then_resolves(self):
        s = self.stack
        s.start_app()
        # Armed together: the mutation applies but replies garbage, then the first two lookups get 503.
        s.arm("malformed_after_commit", "status_unavailable", "status_unavailable")
        run = s.new_run()
        s.approve(run)
        final = s.wait_state(run["run_id"], {"succeeded"})
        self.assertEqual(final["operation"]["reconciliation"]["attempts"], 3)
        self.assertEqual(final["operation"]["reconciliation"]["code"], "status_applied")
        self.assertEqual(len(s.provider_effects()), 1)

    def test_graceful_shutdown_mid_dispatch_records_unknown(self):
        s = self.stack
        # The provider timeout must outlast the provider's delay, so shutdown is what ends the dispatch
        # (under load a 2 s timeout could otherwise fire first: also "unknown", but a different code).
        s.extra_env["RRA_PROVIDER_TIMEOUT_SECONDS"] = "10"
        s.start_app()
        s.arm("slow:3")
        run = s.new_run()
        s.approve(run)
        s.wait_state(run["run_id"], {"dispatching"})
        # Uvicorn drains, then re-raises the captured SIGTERM, so the exit status is -SIGTERM.
        self.assertIn(s.stop_app(signal.SIGTERM), {0, -signal.SIGTERM})
        self.assertIn('"event":"service.stopped"', s.logs())
        self.assertEqual(self.db_operation(run["run_id"])["error_code"], "shutdown_during_dispatch")
        time.sleep(3)  # The provider still finishes the request the app stopped waiting for.
        self.assertEqual(len(s.provider_effects()), 1)
        s.start_app()
        final = s.wait_state(run["run_id"], {"succeeded"})
        self.assertEqual(final["operation"]["reconciliation"]["code"], "status_applied")
        self.assertEqual(len(s.provider_effects()), 1)

    def test_concurrent_approvals_produce_one_authorization_and_one_effect(self):
        s = self.stack
        s.start_app()
        run = s.new_run()
        with concurrent.futures.ThreadPoolExecutor(10) as pool:
            responses = list(pool.map(lambda i: s.approve(run, key=f"race-consent-{i:04d}"), range(10)))
        decisions = sorted(response.json()["decision"] for response in responses)
        self.assertEqual(decisions.count("authorized"), 1)
        self.assertEqual(decisions.count("consent_not_pending"), 9)
        s.wait_state(run["run_id"], {"succeeded"})
        time.sleep(0.6)
        self.assertEqual(len(s.provider_effects()), 1)

    def test_provider_idempotency_contract(self):
        key = (self.stack.root / "app" / "provider.key").read_text().strip()
        url = f"http://127.0.0.1:{self.stack.provider_port}/v1/mutations"
        body = {"idempotency_key": "direct-key-0001", "tenant": "acme", "tool_name": "change_order_address",
                "arguments": {"order_id": "SYN-ORDER-1042", "address": ADDRESS}, "expected_revision": 1}
        first = httpx.post(url, headers={"Authorization": f"Bearer {key}"}, json=body).json()
        second = httpx.post(url, headers={"Authorization": f"Bearer {key}"}, json=body).json()
        self.assertEqual((first["effect_id"], second["effect_id"], second["duplicate"]), (1, 1, True))
        conflict = httpx.post(url, headers={"Authorization": f"Bearer {key}"},
                              json=dict(body, arguments={"order_id": "SYN-ORDER-1042", "address": dict(ADDRESS, apartment="7")}))
        self.assertEqual((conflict.status_code, conflict.json()["code"]), (409, "key_conflict"))
        self.assertEqual(len(self.stack.provider_effects()), 1)

    def test_cancellation_before_and_after_the_claim(self):
        s = self.stack
        s.start_app()
        early = s.new_run(tag="early")
        cancelled = s.post(f"/api/v1/runs/{early['run_id']}/cancel", "alice", "cancel-early-01")
        self.assertEqual(cancelled.json()["cancellation"], "prevented_dispatch")
        self.assertEqual(s.approve(early).json()["decision"], "consent_not_pending")
        s.arm("slow:2")
        first, queued = s.new_run(tag="first"), s.new_run(tag="queued")
        s.approve(first)
        s.wait_state(first["run_id"], {"dispatching"})
        self.assertEqual(s.approve(queued).status_code, 409)  # Order is held by the in-flight operation.
        late = s.post(f"/api/v1/runs/{first['run_id']}/cancel", "alice", "cancel-late-001")
        self.assertEqual((late.json()["cancellation"], late.json()["dispatch_started"]), ("requested_after_dispatch", True))
        final = s.wait_state(first["run_id"], {"succeeded"})
        self.assertTrue(final["cancel_requested"])  # Cancel never claims the effect was undone.
        again = s.post(f"/api/v1/runs/{first['run_id']}/cancel", "alice", "cancel-late-001")
        self.assertEqual(again.json(), late.json())
        self.assertEqual(len(s.provider_effects()), 1)

    def test_cancel_of_authorized_operation_waiting_for_a_claim_prevents_dispatch(self):
        s = self.stack
        s.start_app({"RRA_WORKER_CONCURRENCY": "1", "RRA_PROVIDER_TIMEOUT_SECONDS": "10"})
        s.arm("slow:3")
        busy = s.new_run("bob", "SYN-ORDER-2077", tag="busy")
        s.approve(busy, "bob")
        s.wait_state(busy["run_id"], {"dispatching"}, "bob")  # The only dispatch slot is occupied.
        waiting = s.new_run(tag="waiting")
        self.assertEqual(s.approve(waiting).json()["decision"], "authorized")
        time.sleep(0.6)  # Several worker scans: no capacity, so the operation stays pending.
        conn = s.app_db()
        try:
            pending = conn.execute("SELECT state, attempt_count FROM operations WHERE run_id=?",
                                   (waiting["run_id"],)).fetchone()
        finally:
            conn.close()
        self.assertEqual(tuple(pending), ("pending", 0))
        cancel = s.post(f"/api/v1/runs/{waiting['run_id']}/cancel", "alice", "cancel-pending-1").json()
        self.assertEqual((cancel["cancellation"], cancel["dispatch_started"], cancel["state"]),
                         ("prevented_dispatch", False, "cancelled"))
        s.wait_state(busy["run_id"], {"succeeded"}, "bob")
        time.sleep(0.6)  # The freed slot must not pick up the cancelled operation.
        self.assertEqual(s.snapshot(waiting["run_id"])["operation"]["state"], "cancelled")
        self.assertEqual(s.provider_effects("SYN-ORDER-1042"), [])

    def test_basic_provider_cannot_verify_a_restore(self):
        s = self.stack
        s.start_app()
        s.run_cli("backup", str(s.root / "snapshot.db"))
        s.stop_app()
        s.kill_provider()
        s.start_provider("basic")
        s.run_cli("restore", str(s.root / "snapshot.db"))
        result = subprocess.run([sys.executable, str(CLI), "verify-restore"], cwd=ROOT, env=s.env(),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 1)
        self.assertIn("stays read-only", result.stdout)
        s.start_app(ready=False)
        self.assertEqual(httpx.get(s.url("/health/ready")).status_code, 503)

    def test_stale_backup_restore_is_read_only_until_provider_verification(self):
        s = self.stack
        s.extra_env["RRA_PROVIDER_TIMEOUT_SECONDS"] = "10"
        s.start_app()
        s.arm("slow:4")
        run = s.new_run()
        s.approve(run)
        s.wait_state(run["run_id"], {"dispatching"})
        s.run_cli("backup", str(s.root / "snapshot.db"))
        snapshot = sqlite3.connect(str(s.root / "snapshot.db"))
        captured = snapshot.execute("SELECT state FROM operations WHERE run_id=?", (run["run_id"],)).fetchone()[0]
        snapshot.close()
        self.assertEqual(captured, "dispatching")  # The backup is stale relative to the provider.
        s.wait_state(run["run_id"], {"succeeded"})
        s.stop_app()
        s.run_cli("restore", str(s.root / "snapshot.db"))
        s.start_app(ready=False)
        self.assertEqual(httpx.get(s.url("/health/ready")).status_code, 503)  # Restore unverified.
        session = s.post("/api/v1/sessions", "alice", "restored-sess-01", {"resource_id": "SYN-ORDER-1042"})
        blocked = s.post(f"/api/v1/sessions/{session.json()['session_id']}/runs", "alice", "restored-run-001",
                         {"request": {"type": "change_order_address", "address": ADDRESS}})
        self.assertEqual(blocked.json()["code"], "restore_unverified")
        s.stop_app()
        verify = s.run_cli("verify-restore")
        self.assertIn("writes re-enabled", verify.stdout)
        s.start_app()
        self.assertEqual(s.snapshot(run["run_id"])["state"], "succeeded")
        fresh = s.new_run(address=dict(ADDRESS, apartment="5C"), tag="after-restore")
        s.approve(fresh)
        s.wait_state(fresh["run_id"], {"succeeded"})
        effects = s.provider_effects()
        self.assertEqual(len(effects), 2)
        self.assertEqual(len({effect["idempotency_key"] for effect in effects}), 2)

    def test_verify_restore_settles_prompts_that_were_awaiting_consent_in_the_backup(self):
        s = self.stack
        s.start_app()
        applied = s.new_run(tag="later-applied")
        never_sent = s.new_run("carol", "SYN-ORDER-1043", tag="never-sent")
        s.run_cli("backup", str(s.root / "awaiting.db"))  # Both prompts await consent in the backup.
        s.approve(applied)
        s.wait_state(applied["run_id"], {"succeeded"})  # Applied after the backup was taken.
        s.stop_app()
        s.run_cli("restore", str(s.root / "awaiting.db"))
        s.start_app(ready=False)
        cancel = s.post(f"/api/v1/runs/{applied['run_id']}/cancel", "alice", "restored-cancel-01")
        self.assertEqual((cancel.status_code, cancel.json()["code"]), (503, "restore_unverified"))  # Not "prevented".
        s.stop_app()
        self.assertIn("writes re-enabled", s.run_cli("verify-restore").stdout)
        s.start_app()
        settled = s.snapshot(applied["run_id"])
        self.assertEqual((settled["state"], settled["operation"]["reconciliation"]["code"]), ("succeeded", "status_applied"))
        self.assertTrue(settled["operation"]["dispatch_started"])
        self.assertEqual(s.snapshot(never_sent["run_id"], "carol")["state"], "invalidated")
        late = httpx.post(f"http://127.0.0.1:{s.provider_port}/v1/mutations",  # A delayed copy of that key.
                          headers={"Authorization": "Bearer " + (s.root / "app" / "provider.key").read_text().strip()},
                          json={"idempotency_key": "rra-" + never_sent["proposal"]["operation_id"], "tenant": "acme",
                                "tool_name": "change_order_address",
                                "arguments": {"order_id": "SYN-ORDER-1043", "address": ADDRESS}, "expected_revision": 1})
        self.assertEqual(late.json()["code"], "key_fenced")
        self.assertEqual((len(s.provider_effects("SYN-ORDER-1042")), len(s.provider_effects("SYN-ORDER-1043"))), (1, 0))

    def test_unverified_restore_does_not_expire_prompts_before_verification(self):
        s = self.stack
        s.extra_env.update({"RRA_PROPOSAL_TTL_SECONDS": "2", "RRA_MAINTENANCE_SECONDS": "0.2"})
        s.start_app()
        run = s.new_run(tag="old-backup")
        s.run_cli("backup", str(s.root / "old.db"))
        s.approve(run)
        s.wait_state(run["run_id"], {"succeeded"})  # Applied after the backup.
        s.stop_app()
        s.run_cli("restore", str(s.root / "old.db"))
        time.sleep(2.5)  # The restored prompt is now past its lifetime.
        s.start_app(ready=False)
        time.sleep(1)  # Several maintenance ticks while unverified.
        s.stop_app()
        conn = s.app_db()
        try:
            state = conn.execute("SELECT state FROM proposals WHERE run_id=?", (run["run_id"],)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(state, "awaiting_consent")  # Not expired: verify-restore must still see it.
        self.assertIn("writes re-enabled", s.run_cli("verify-restore").stdout)
        s.start_app()
        final = s.snapshot(run["run_id"])
        self.assertEqual((final["state"], final["operation"]["reconciliation"]["code"]), ("succeeded", "status_applied"))
        self.assertEqual(len(s.provider_effects()), 1)

    def test_restore_keeps_credential_changes_made_after_the_backup(self):
        s = self.stack
        s.start_app()
        s.run_cli("backup", str(s.root / "creds.db"))
        old_bob = s.tokens["bob"]
        s.run_cli("revoke", "alice")  # E.g. a leaked token, revoked after the backup was taken.
        s.run_cli("rotate", "bob")
        new_bob = json.loads((s.root / "app" / "credentials.json").read_text())["bob"]
        s.stop_app()
        self.assertIn("current credentials kept", s.run_cli("restore", str(s.root / "creds.db")).stdout)
        self.assertIn("writes re-enabled", s.run_cli("verify-restore").stdout)
        s.start_app()
        probe = s.url("/api/v1/runs/run_" + "0" * 32)  # 401 = credential refused, 404 = authenticated.
        status = lambda token: httpx.get(probe, headers={"Authorization": f"Bearer {token}"}, timeout=5).status_code
        self.assertEqual((status(s.tokens["alice"]), status(old_bob), status(new_bob)), (401, 401, 404))

    def test_one_service_process_per_data_directory(self):
        s = self.stack
        s.start_app()
        second = subprocess.run([sys.executable, str(CLI), "serve-app", "--port", str(s.provider_port + 1)], cwd=ROOT,
                                env=s.env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(second.returncode, 3)
        self.assertIn("another serving process holds", second.stderr)
        restore = subprocess.run([sys.executable, str(CLI), "restore", str(s.root / "nothing.db")], cwd=ROOT,
                                 env=s.env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(restore.returncode, 3)  # Refused before it reads the snapshot or touches the database.
        run = s.new_run(tag="still-single")
        s.approve(run)
        s.wait_state(run["run_id"], {"succeeded"})
        self.assertEqual(len(s.provider_effects()), 1)
        s.stop_app()
        s.start_app()  # The lock is released when the process exits.


class StorageFailureTests(unittest.TestCase):
    """Real SQLite faults while recording a provider outcome: a held write lock (transient) and a
    database file made read-only (permanent). Ground truth stays the provider's ledger."""

    def setUp(self):
        self.stack = Stack(env={"RRA_RECONCILE_BACKOFF_MAX_SECONDS": "0.5", "RRA_RECOVERY_PROBE_SECONDS": "1",
                                "RRA_COMPLETION_RETRY_SECONDS": "2"})
        self.stack.start_provider()
        self.db = self.stack.root / "app" / "serving.db"

    def tearDown(self):
        self.repair()
        self.stack.close()

    def repair(self):
        """Operator fix. SQLite recreates -wal/-shm with the database file's mode, so while the file was
        read-only those side files may have been recreated read-only too: all three need restoring."""
        for suffix in ("", "-wal", "-shm"):
            path = self.db.with_name(self.db.name + suffix)
            if path.exists():
                path.chmod(0o644)

    def metrics(self):
        return self.stack.get("/api/v1/ops/metrics", "operator").json()

    def wait_ready(self, status, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = httpx.get(self.stack.url("/health/ready"), timeout=5).json()
            if body["status"] == status:
                return body
            time.sleep(0.1)
        raise AssertionError(f"readiness never became {status}: {body}")

    def dispatching_run(self):
        s = self.stack
        s.arm("slow:1.5")
        run = s.new_run()
        s.approve(run)
        s.wait_state(run["run_id"], {"dispatching"})
        return run

    def test_transient_lock_contention_recovers_with_one_provider_call(self):
        s = self.stack
        s.start_app({"RRA_COMPLETION_RETRY_SECONDS": "30"})
        run = self.dispatching_run()
        blocker = sqlite3.connect(str(self.db), isolation_level=None, timeout=10)
        blocker.execute("BEGIN IMMEDIATE")  # Every completion write now waits 5 s, then fails "locked".
        time.sleep(8)
        self.assertEqual(s.snapshot(run["run_id"])["state"], "dispatching")
        blocker.execute("ROLLBACK")
        blocker.close()
        final = s.wait_state(run["run_id"], {"succeeded"}, timeout=15)
        self.assertIsNone(final["operation"]["reconciliation"])  # Recorded directly, not re-derived.
        counters = self.metrics()["counters"]
        self.assertGreaterEqual(counters["completion_write_retries"], 1)
        self.assertEqual(counters["provider_attempts"], 1)
        self.assertNotIn("outcomes_parked", counters)
        self.assertEqual(len(s.provider_effects()), 1)

    def test_permanent_failure_is_visible_pauses_admission_and_recovers_without_redispatch(self):
        s = self.stack
        s.start_app()
        other = s.new_run("bob", "SYN-ORDER-2077", tag="admission")
        run = self.dispatching_run()
        self.db.chmod(0o444)  # New connections open read-only: "attempt to write a readonly database".
        body = self.wait_ready("degraded")
        self.assertEqual(body["outcomes_unrecorded"], 1)
        gauges = self.metrics()["gauges"]
        self.assertEqual((gauges["worker_degraded"], gauges["outcomes_unrecorded"]), (1, 1))
        refused = s.approve(other, "bob")
        self.assertEqual((refused.status_code, refused.json()["code"]), (503, "service_degraded"))
        self.assertIn("Retry-After", refused.headers)
        self.assertEqual(s.snapshot(other["run_id"], "bob")["state"], "awaiting_consent")
        self.assertEqual(s.snapshot(run["run_id"])["state"], "dispatching")  # Durable claim retained, not guessed.
        parked = [json.loads(line) for line in s.logs().splitlines() if '"worker.outcome_parked"' in line]
        self.assertEqual([(e["operation_id"], e["state"], e["effect_id"]) for e in parked],
                         [(run["proposal"]["operation_id"], "succeeded", 1)])  # Provider evidence for an operator.
        self.repair()
        self.wait_ready("ready")
        final = s.wait_state(run["run_id"], {"succeeded"})
        self.assertIsNone(final["operation"]["reconciliation"])  # The parked outcome itself was recorded.
        self.assertEqual(s.approve(other, "bob").json()["decision"], "authorized")
        s.wait_state(other["run_id"], {"succeeded"}, "bob")
        self.assertEqual(self.metrics()["counters"]["provider_attempts"], 2)
        self.assertEqual(len(s.provider_effects("SYN-ORDER-1042")), 1)

    def test_shutdown_during_permanent_failure_meets_its_deadline_and_restart_reconciles(self):
        s = self.stack
        s.start_app()
        run = self.dispatching_run()
        self.db.chmod(0o444)
        self.wait_ready("degraded")
        started = time.monotonic()
        self.assertIn(s.stop_app(signal.SIGTERM), {0, -signal.SIGTERM})
        # Documented deadline: about 2 x RRA_DRAIN_SECONDS (1 s here) plus process teardown.
        self.assertLess(time.monotonic() - started, 2 * 1 + 3)
        stopped = [json.loads(line) for line in s.logs().splitlines() if '"worker.stopped_with_unrecorded_outcomes"' in line]
        self.assertEqual(stopped[0]["operation_ids"], [run["proposal"]["operation_id"]])
        conn = s.app_db()
        try:
            row = conn.execute("SELECT state, attempt_count FROM operations WHERE run_id=?", (run["run_id"],)).fetchone()
            hold = conn.execute("SELECT COUNT(*) FROM resource_holds").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual((row["state"], row["attempt_count"], hold), ("dispatching", 1, 1))
        self.repair()
        s.start_app()
        final = s.wait_state(run["run_id"], {"succeeded"})
        self.assertEqual(final["operation"]["reconciliation"]["code"], "status_applied")
        self.assertEqual(self.metrics()["counters"].get("provider_attempts", 0), 0)  # Restart never re-dispatches.
        self.assertEqual(len(s.provider_effects()), 1)
        self.assertEqual(event_types(s, run["run_id"]).count("dispatch_started"), 1)

    def test_shutdown_with_a_held_lock_and_several_dispatches_is_bounded(self):
        s = self.stack
        s.start_app()
        s.arm("slow:1", "slow:1", "slow:1")
        runs = [s.new_run(actor, order, tag="lock-shutdown") for actor, order in
                (("alice", "SYN-ORDER-1042"), ("carol", "SYN-ORDER-1043"), ("bob", "SYN-ORDER-2077"))]
        for run, actor in zip(runs, ("alice", "carol", "bob")):
            s.approve(run, actor)
        for run, actor in zip(runs, ("alice", "carol", "bob")):
            s.wait_state(run["run_id"], {"dispatching"}, actor)
        blocker = sqlite3.connect(str(self.db), isolation_level=None, timeout=10)
        blocker.execute("BEGIN IMMEDIATE")  # Every write (completions, abandon records) now waits 5 s and fails.
        try:
            time.sleep(1.5)  # The provider has answered all three; their completions are blocked.
            started = time.monotonic()
            self.assertIn(s.stop_app(signal.SIGTERM), {0, -signal.SIGTERM})
            # HTTP drain (RRA_DRAIN_SECONDS + 1) + Worker.stop (2 x drain) + one SQLite busy timeout (5 s) + slack.
            # Abandoning the three claims one after another, as before, takes 15 s or more.
            self.assertLess(time.monotonic() - started, (1 + 1) + 2 * 1 + 5 + 2)
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        conn = s.app_db()
        try:
            rows = conn.execute("SELECT state, attempt_count FROM operations").fetchall()
        finally:
            conn.close()
        self.assertEqual(sorted(tuple(row) for row in rows), [("dispatching", 1)] * 3)  # Durable ambiguity kept.
        s.start_app()
        for run, actor in zip(runs, ("alice", "carol", "bob")):
            final = s.wait_state(run["run_id"], {"succeeded"}, actor)
            self.assertEqual(final["operation"]["reconciliation"]["code"], "status_applied")
        self.assertEqual(self.metrics()["counters"].get("provider_attempts", 0), 0)  # Restart never re-dispatches.
        self.assertEqual(len(s.provider_effects()), 3)


if __name__ == "__main__":
    unittest.main()
