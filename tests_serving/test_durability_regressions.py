"""Credential, restore-evidence, and optional telemetry regressions; temporary SQLite only."""
import io
import asyncio
from dataclasses import replace
import json
import unittest
from unittest import mock

from serving import auth, storage
from serving.provider import Outcome
from serving.service import ServiceError
from serving.streaming import Notifier, event_stream
from serving.telemetry import MAX_DROPPED_LOGS, Telemetry
from tests_serving.test_units import ORDER, RUN_BODY, ServiceCase


class CredentialRegressions(ServiceCase):
    def assert_claim_refused(self, statement):
        run = self.new_run()
        self.consent(run)
        self.sql(statement)
        self.assertEqual(self.service.claim(1), [])
        self.assertEqual(self.sql("SELECT state, attempt_count FROM operations WHERE run_id=?", (run["run_id"],)),
                         [("invalidated", 0)])

    def test_claim_rechecks_current_scope(self):
        self.assert_claim_refused("UPDATE principals SET scopes='[\"runs:read\"]' WHERE actor_id='alice'")

    def test_claim_rechecks_current_tenant(self):
        self.assert_claim_refused("UPDATE principals SET tenant_id='other' WHERE actor_id='alice'")

    def test_revoke_then_rotate_cannot_revive_old_consent(self):
        run = self.new_run()
        self.consent(run)
        conn = self.service.connect()
        try:
            with storage.write_tx(conn):
                auth.revoke(conn, "alice")
                auth.rotate(conn, "alice")
                # The credential epoch must not depend on wall-clock ordering.
                conn.execute("UPDATE principals SET rotated_at=0 WHERE actor_id='alice'")
        finally:
            conn.close()
        self.assertEqual(self.service.claim(1), [])

    def test_rotation_invalidates_queued_consent_but_fresh_consent_can_execute(self):
        run = self.new_run()
        self.consent(run)
        conn = self.service.connect()
        try:
            with storage.write_tx(conn):
                token = auth.rotate(conn, "alice")
            current = auth.resolve(conn, token)
        finally:
            conn.close()
        self.assertEqual(self.service.claim(1), [])
        self.alice = current
        fresh = self.new_run()
        self.consent(fresh)
        self.assertEqual(len(self.service.claim(1)), 1)

    def test_old_policy_approval_is_invalidated(self):
        self.assert_claim_refused("UPDATE proposals SET policy_version='serving-consent-policy-v1/old'")

    def test_stale_resolved_caller_cannot_read_replay_or_write(self):
        run = self.new_run()
        self.consent(run, "maybe")
        consent_key = f"consent-{self.counter:06d}"
        conn = self.service.connect()
        try:
            auth.rotate(conn, "alice")
        finally:
            conn.close()
        proposal = run["proposal"]
        consent = {"response": "maybe", "prompt_id": proposal["prompt_id"], "proposal_version": 1,
                   "payload_hash": proposal["payload_hash"]}
        calls = (
            lambda: self.service.snapshot(self.alice, run["run_id"]),
            lambda: self.service.trace_envelopes(self.alice, run["run_id"]),
            lambda: self.service.stream_start(self.alice, run["run_id"], None),
            lambda: self.service.consent(self.alice, run["run_id"], consent_key, consent),
            lambda: self.service.cancel(self.alice, run["run_id"], self.key("cancel")),
            lambda: self.service.prepare_run(self.alice, run["session_id"], self.key("run"), RUN_BODY),
            lambda: self.service.create_run(self.alice, run["session_id"], self.key("run"), RUN_BODY, ORDER),
            lambda: self.service.create_session(self.alice, self.key("session"), {"resource_id": "SYN-ORDER-1042"}),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(ServiceError) as refused:
                    call()
                self.assertEqual(refused.exception.status, 401)
        self.assertFalse(self.service.can_observe(self.alice, run["run_id"]))

    def test_current_read_scope_and_tenant_revalidated_for_snapshot_trace_and_stream(self):
        run = self.new_run()
        for statement, expected in (
            ("UPDATE principals SET scopes='[\"runs:write\",\"consent:write\"]' WHERE actor_id='alice'", 403),
            ("UPDATE principals SET tenant_id='other' WHERE actor_id='alice'", 401),
        ):
            with self.subTest(statement=statement):
                self.sql(statement)
                for read in (self.service.snapshot, self.service.trace_envelopes):
                    with self.assertRaises(ServiceError) as refused:
                        read(self.alice, run["run_id"])
                    self.assertEqual(refused.exception.status, expected)
                self.assertFalse(self.service.can_observe(self.alice, run["run_id"]))
                self.sql("UPDATE principals SET tenant_id='acme', scopes='[\"runs:read\",\"runs:write\",\"consent:write\"]' WHERE actor_id='alice'")

    def test_current_resource_ownership_revalidated_even_for_replayed_consent(self):
        run = self.new_run()
        self.consent(run, "maybe")
        consent_key = f"consent-{self.counter:06d}"
        proposal = run["proposal"]
        body = {"response": "maybe", "prompt_id": proposal["prompt_id"], "proposal_version": 1,
                "payload_hash": proposal["payload_hash"]}
        self.assertTrue(self.service.can_observe(self.alice, run["run_id"]))
        self.sql("UPDATE resources SET owner_actor_id='carol' WHERE resource_id='SYN-ORDER-1042'")
        for call in (lambda: self.service.snapshot(self.alice, run["run_id"]),
                     lambda: self.service.trace_envelopes(self.alice, run["run_id"]),
                     lambda: self.service.consent(self.alice, run["run_id"], consent_key, body)):
            with self.assertRaises(ServiceError) as refused:
                call()
            self.assertEqual(refused.exception.status, 404)
        self.assertFalse(self.service.can_observe(self.alice, run["run_id"]))

    def test_existing_stream_stops_after_read_scope_revocation(self):
        run = self.new_run()

        async def scenario():
            stream = event_stream(service=self.service, settings=replace(self.settings, sse_heartbeat_seconds=0.001),
                                  notifier=Notifier(), telemetry=Telemetry(io.StringIO()), run_id=run["run_id"],
                                  after=0, still_authorized=lambda: self.service.can_observe(self.alice, run["run_id"]))
            self.assertEqual(await anext(stream), b"retry: 2000\n\n")
            self.sql("UPDATE principals SET scopes='[]' WHERE actor_id='alice'")
            await asyncio.sleep(0.005)
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)
            await stream.aclose()

        asyncio.run(scenario())

    def test_prompt_delivery_race_does_not_return_stale_authorized_snapshot(self):
        _, session = self.service.create_session(self.alice, self.key("session"), {"resource_id": "SYN-ORDER-1042"})
        _, run = self.service.create_run(self.alice, session["session_id"], self.key("run"), RUN_BODY, ORDER)
        original_write = self.service._write

        def rotate_before_delivery(fn, *args, **kwargs):
            conn = self.service.connect()
            try:
                auth.rotate(conn, "alice")
            finally:
                conn.close()
            return original_write(fn, *args, **kwargs)

        with mock.patch.object(self.service, "_write", side_effect=rotate_before_delivery):
            with self.assertRaises(ServiceError) as refused:
                self.service.snapshot(self.alice, run["run_id"])
        self.assertEqual(refused.exception.status, 401)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM events WHERE event_type='prompt'"), [(0,)])

    def test_verifier_is_not_in_repr_or_trace(self):
        run = self.new_run()
        self.consent(run)
        exported = json.dumps(self.service.trace_envelopes(self.alice, run["run_id"]))
        self.assertNotIn(self.alice.credential_verifier, repr(self.alice))
        self.assertNotIn(self.alice.credential_verifier, exported)
        self.assertNotIn(self.tokens["alice"], exported)


class RestoreHistoryRegressions(ServiceCase):
    def test_restore_keeps_missing_local_claim_history(self):
        run = self.new_run()
        outcome = Outcome("succeeded", "status_applied", {"effect_id": 1, "revision": 2})
        self.service.restore_resolve_proposal(run["proposal"]["operation_id"], outcome)
        self.assertEqual(self.sql("SELECT attempt_count, claim_id, claimed_at FROM operations"), [(0, None, None)])
        self.assertEqual(self.sql("SELECT decided_at, consumed_at FROM proposals"), [(None, None)])
        snapshot = self.service.snapshot(self.alice, run["run_id"])
        self.assertFalse(snapshot["operation"]["dispatch_started"])
        self.assertEqual(snapshot["operation"]["dispatch_history"], "missing_after_restore")
        self.assertEqual(snapshot["operation"]["provider_result"], {"effect_id": 1, "revision": 2})
        kinds = [e["event_type"] for e in self.service.trace_envelopes(self.alice, run["run_id"])]
        self.assertNotIn("authorized", kinds)
        self.assertNotIn("dispatch_started", kinds)
        self.assertIn("serving.reconciled", kinds)

    def test_restore_preserves_an_actual_local_claim(self):
        run = self.new_run()
        self.consent(run)
        claim, = self.service.claim(1)
        before = self.sql("SELECT attempt_count, claim_id, claimed_at FROM operations")
        consumed_before = self.sql("SELECT consumed_at FROM proposals")
        self.service.restore_resolve(claim.operation_id, Outcome("succeeded", "status_applied", {"effect_id": 1, "revision": 2}))
        self.assertEqual(self.sql("SELECT attempt_count, claim_id, claimed_at FROM operations"), before)
        self.assertEqual(self.sql("SELECT consumed_at FROM proposals"), consumed_before)
        self.assertEqual(self.service.snapshot(self.alice, run["run_id"])["operation"]["dispatch_history"], "recorded")


class TelemetryRegressions(unittest.TestCase):
    def test_optional_write_or_flush_failure_does_not_escape(self):
        for method in ("write", "flush"):
            with self.subTest(method=method):
                stream = mock.Mock()
                getattr(stream, method).side_effect = OSError("synthetic log failure")
                telemetry = Telemetry(stream)
                telemetry.log("worker.synthetic")
                self.assertEqual(telemetry.snapshot()["counters"]["telemetry_log_dropped"], 1)

    def test_drop_counter_saturates_and_logging_can_recover(self):
        stream = mock.Mock()
        stream.write.side_effect = OSError("failure")
        telemetry = Telemetry(stream)
        telemetry.counters["telemetry_log_dropped"] = MAX_DROPPED_LOGS
        telemetry.log("worker.synthetic")
        self.assertEqual(telemetry.snapshot()["counters"], {"telemetry_log_dropped": MAX_DROPPED_LOGS})
        stream.write.side_effect = None
        telemetry.log("worker.recovered")
        self.assertEqual(stream.flush.call_count, 1)

    def test_unserializable_optional_field_is_dropped_and_private_fields_stay_excluded(self):
        stream = io.StringIO()
        telemetry = Telemetry(stream)
        telemetry.log("worker.synthetic", effect_id=object())
        telemetry.log("worker.next", token="synthetic-secret", prompt="synthetic-private")
        self.assertEqual(telemetry.snapshot()["counters"]["telemetry_log_dropped"], 1)
        self.assertNotIn("synthetic-secret", stream.getvalue())
        self.assertNotIn("synthetic-private", stream.getvalue())
