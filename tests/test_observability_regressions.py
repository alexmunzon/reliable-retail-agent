"""Regression cases from the independent dashboard review; frozen fixtures stay unchanged."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from observability import costs, ingest, project, server, sidecar
from observability.report import Dataset

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "observability" / "fixtures" / "v1"


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def projected(events):
    streams, _ = ingest.build_streams("review", "0" * 64, list(enumerate(events, 1)), {})
    return project.project(streams[0], [], "synthetic_fixture", ingest.Limits())


class CostConflictRegressions(unittest.TestCase):
    def setUp(self):
        records = lines(FIX / "sidecar" / "healthy.jsonl")
        self.usage = next(r for r in records if r["kind"] == "usage")
        self.receipt = next(r for r in records if r["kind"] == "billing_receipt")
        self.table = json.loads((FIX / "pricing" / "synthetic-2026-01-01.json").read_text())

    def calculate(self, usages, receipts=(), tables=None):
        span = self.usage["span_id"]
        return costs.request_costs(usages, list(receipts), tables or [self.table], {span: True}, {span: None})

    def conflict(self):
        other = copy.deepcopy(self.usage)
        other["record_id"] = "conflicting-usage"
        other["tokens"]["input"] += 1
        return other

    def test_conflicting_usage_does_not_leave_a_complete_partial_total(self):
        valid = {**self.usage, "record_id": "valid-other", "provider_request_id": "other-call"}
        for records in ([self.usage, self.conflict(), valid], [valid, self.conflict(), self.usage]):
            with self.subTest(order=[r["record_id"] for r in records]):
                result = self.calculate(records)
                estimate = result["estimated_charge"]
                self.assertEqual(result["conflicting_usage_identities"], 1)
                self.assertIsNone(estimate["total"])
                self.assertEqual(estimate["known_subtotal"], {"USD": "0.0075"})
                self.assertEqual(estimate["unknown_usage_records"], 1)
                self.assertEqual(estimate["reason"], "conflicting_usage_identity")

    def test_conflicting_usage_dates_cannot_choose_a_price_by_input_order(self):
        other = {**self.usage, "record_id": "another-date", "observed_at": "2026-11-03T00:00:00Z"}
        self.assertIsNone(self.calculate([self.usage, other])["estimated_charge"]["total"])

    def test_receipt_conflict_is_quarantined_in_both_orders(self):
        other = {**self.receipt, "record_id": "receipt-conflict", "amount": "999"}
        for receipts in ([self.receipt, other], [other, self.receipt]):
            result = self.calculate([self.usage], receipts)
            self.assertEqual(result["confirmed_charge"], {})
            self.assertIsNone(result["confirmed_charge_total"])
            self.assertFalse(result["confirmed_charge_complete"])
            self.assertEqual(result["conflicting_receipts"], 1)

    def test_identical_receipt_redelivery_is_counted_once(self):
        other = {**self.receipt, "record_id": "receipt-redelivery"}
        result = self.calculate([self.usage], [self.receipt, other])
        self.assertEqual(result["confirmed_charge"], {"USD": "0.0074"})
        self.assertEqual(result["confirmed_receipts"], 1)
        self.assertEqual(result["duplicate_receipts"], 1)
        self.assertTrue(result["confirmed_charge_complete"])

    def test_receipt_for_conflicted_usage_is_not_confirmed(self):
        result = self.calculate([self.usage, self.conflict()], [self.receipt])
        self.assertEqual(result["confirmed_charge"], {})
        self.assertEqual(result["unbound_receipts"], 1)

    def test_receipt_subtotal_is_not_complete_when_another_call_lacks_a_receipt(self):
        other = {**self.usage, "record_id": "other-usage", "provider_request_id": "other-call"}
        result = self.calculate([self.usage, other], [self.receipt])
        self.assertEqual(result["confirmed_charge"], {"USD": "0.0074"})
        self.assertIsNone(result["confirmed_charge_total"])
        self.assertEqual(result["unreceipted_usage_identities"], 1)

    def test_span_rollup_keeps_currencies_separate(self):
        euro = copy.deepcopy(self.table)
        euro.update(table_id="synthetic-eur", currency="EUR",
                    models={"synthetic-eur-model": self.table["models"]["synthetic-model-a"]})
        other = {**self.usage, "record_id": "eur-usage", "provider_request_id": "eur-call", "model_id": "synthetic-eur-model"}
        self.assertEqual(costs.validate_table(euro), [])
        result = self.calculate([self.usage, other], tables=[self.table, euro])
        rollup = result["span_rollup"][self.usage["span_id"]]
        self.assertEqual(rollup["known_by_currency"], {"EUR": "0.0075", "USD": "0.0075"})
        self.assertIsNone(rollup["known"])
        self.assertIsNone(rollup["currency"])

    def test_projected_usage_retains_measurement_provenance(self):
        row = self.calculate([self.usage])["usage"][0]
        for field in ("provider_request_id", "source", "usage_semantics", "emitter"):
            self.assertEqual(row[field], self.usage[field], field)


class TelemetryInputRegressions(unittest.TestCase):
    def test_partial_telemetry_loss_keeps_subtotals_but_invalidates_totals(self):
        events = lines(FIX / "traces" / "healthy.jsonl")
        original = lines(FIX / "sidecar" / "healthy.jsonl")
        usage = next(r for r in original if r["kind"] == "usage")
        damaged_usage = {**usage, "record_id": "unbound", "provider_request_id": "lost-call", "span_id": "missing-span"}
        dropped = {key: usage[key] for key in sidecar.COMMON}
        dropped.update(record_id="drop", kind="drop_report", reason="queue_full", dropped_records=1)
        for extra, rejected in (([damaged_usage], {}), ([dropped], {}), ([], {"unparseable_line": 1})):
            with self.subTest(extra=[r["kind"] for r in extra], rejected=rejected):
                streams, _ = ingest.build_streams("review", "0" * 64, list(enumerate(events, 1)), {})
                ingest.attach_sidecar(streams, list(enumerate(original + extra, 1)), rejected, ingest.Limits())
                table = json.loads((FIX / "pricing" / "synthetic-2026-01-01.json").read_text())
                result = project.project(streams[0], [table], "synthetic_fixture", ingest.Limits())
                self.assertEqual(result["status"], "completed")
                self.assertIsNone(result["costs"]["estimated_charge"]["total"])
                self.assertEqual(result["costs"]["estimated_charge"]["known_subtotal"], {"USD": "0.0075"})
                self.assertEqual(result["costs"]["confirmed_charge"], {"USD": "0.0074"})
                self.assertIsNone(result["costs"]["confirmed_charge_total"])
                self.assertTrue(result["costs"]["incomplete_evidence_reasons"])

    def test_invalid_field_types_reject_without_crashing(self):
        record = next(r for r in lines(FIX / "sidecar" / "healthy.jsonl") if r["kind"] == "usage")
        for field in ("kind", "run_id", "trace_id", "record_id"):
            for value in ([], {}):
                with self.subTest(field=field, value=value):
                    bad = {**record, field: value}
                    self.assertTrue(sidecar.validate_record(bad))
                    stream = ingest.Stream("x", "x", "0" * 64, record["run_id"], record["trace_id"])
                    result = ingest.attach_sidecar([stream], [(1, bad)], {}, ingest.Limits())
                    self.assertEqual(stream.sidecar, [])
                    self.assertEqual(sum(stream.sidecar_rejected.values()) + result["invalid_unattributable"], 1)

    def test_sidecar_file_failures_preserve_business_trace(self):
        baseline = Dataset().requests["healthy/run-healthy"]
        original = ingest.read_bounded
        for failure in ("artifact_unreadable", "artifact_too_large", "digest_mismatch"):
            def damaged(path, limit):
                if str(path).endswith("/sidecar/healthy.jsonl"):
                    return (b"{}\n", None) if failure == "digest_mismatch" else (None, failure)
                return original(path, limit)
            with self.subTest(failure=failure), mock.patch.object(ingest, "read_bounded", damaged):
                result = Dataset().requests["healthy/run-healthy"]
                self.assertEqual(result["status"], baseline["status"])
                self.assertEqual(result["operations"], baseline["operations"])
                self.assertTrue(result["evidence"]["sidecar_rejected"])
                self.assertEqual(result["timing"]["duration_kind"], "unknown")
                self.assertIsNone(result["costs"]["estimated_charge"]["total"])
                self.assertEqual(result["costs"]["confirmed_charge"], {})

    def test_missing_sidecar_path_preserves_business_trace(self):
        original = ingest.safe_path
        with mock.patch.object(ingest, "safe_path", lambda root, path, pattern:
                               None if path == "sidecar/healthy.jsonl" else original(root, path, pattern)):
            result = Dataset().requests["healthy/run-healthy"]
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["evidence"]["sidecar_rejected"])

    def test_calendar_invalid_query_is_a_bad_request(self):
        for timestamp in ("2026-99-01T00:00Z", "2026-02-30T00:00Z", "2026-01-01T25:00Z"):
            with self.subTest(timestamp=timestamp), self.assertRaises(server.BadRequest):
                server._query("from=" + timestamp)
        self.assertEqual(server._query("from=2028-02-29T00:00Z")["from"], "2028-02-29T00:00Z")


class TraceCompatibilityRegressions(unittest.TestCase):
    def test_trace_v2_refusal_is_a_confirmed_no_effect_failure(self):
        result = projected(lines(ROOT / "contracts" / "examples" / "serving-v2-rejected.jsonl"))
        self.assertEqual(result["evidence"]["contract_verdict"], "CONSISTENT")
        self.assertEqual(result["evidence"]["completeness"], [])
        self.assertEqual(result["status"], "failed")
        op = result["operations"]["op-addr-1"]
        self.assertEqual((op["outcome"], op["confirmed_by"], op["mutation_attempts"]),
                         ("no_effect_confirmed", "dispatch_rejected", 1))
        event = next(e for e in result["events"] if e["event_type"] == "dispatch_rejected")
        self.assertEqual((event["code"], event["effect"], event["schema_version"]), ("revision_conflict", "none", "trace-v2"))

    def test_v2_refusal_freeform_code_is_redacted(self):
        events = lines(ROOT / "contracts" / "examples" / "serving-v2-rejected.jsonl")
        next(e for e in events if e["event_type"] == "dispatch_rejected")["payload"]["code"] = "sam@example.invalid"
        result = projected(events)
        self.assertNotIn("sam@example.invalid", json.dumps(result))
        self.assertEqual(next(e for e in result["events"] if e["event_type"] == "dispatch_rejected")["code"], "redacted")

    def test_v1_namespaced_refusal_is_not_silently_upgraded(self):
        events = lines(ROOT / "contracts" / "examples" / "serving-v2-rejected.jsonl")
        for event in events:
            event["schema_version"] = "trace-v1"
            if event["event_type"] == "dispatch_rejected":
                event.update(event_type="serving.dispatch_failed_no_effect", payload={"code": "revision_conflict"})
        result = projected(events)
        self.assertEqual(result["evidence"]["contract_verdict"], "INVALID")
        self.assertIn("attempt_without_outcome", result["evidence"]["completeness"])

    def test_backend_cancellation_before_claim_is_visible(self):
        events = lines(FIX / "traces" / "cancel-before-claim.jsonl")
        for event in events:
            if event["event_type"] == "serving.run_cancelled":
                event["event_type"] = "serving.cancelled"
        result = projected(events)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(next(iter(result["operations"].values()))["cancellation"], "before_claim")

    def test_cancel_request_after_claim_does_not_undo_confirmed_effect(self):
        events = lines(FIX / "traces" / "healthy.jsonl")
        index = next(i for i, e in enumerate(events) if e["event_type"] == "dispatch_started")
        cancellation = {**events[index], "event_type": "serving.cancel_requested", "event_id": "cancel-request", "payload": {}}
        events.insert(index + 1, cancellation)
        for number, event in enumerate(events, 1):
            event["sequence"] = number
        result = projected(events)
        self.assertEqual(result["status"], "completed")
        op = next(iter(result["operations"].values()))
        self.assertEqual((op["cancellation"], op["outcome"]), ("after_claim", "effect_confirmed"))

    def test_distinct_run_and_trace_identity_has_a_detail_and_export_route(self):
        events = lines(ROOT / "contracts" / "examples" / "serving-v2-rejected.jsonl")
        for event in events:
            event.update(run_id="run_" + "1" * 32, trace_id="trc_" + "2" * 32)
        result = projected(events)
        data = Dataset()
        data.requests[result["key"]] = result
        data.owners[result["key"]] = "tenant-a"
        for route in ("requests", "export"):
            self.assertIsNotNone(server.KEY.fullmatch(f"/api/{route}/{result['key']}"))
            # Exercise the actual route/key construction without opening a socket.
            handler = object.__new__(server.Handler)
            handler.server = SimpleNamespace(dataset=data, principal="tenant-a", allowed_hosts={"localhost"}, static={})
            handler.headers = {"Host": "localhost"}
            handler.path = f"/api/{route}/{result['key']}"
            replies = []
            handler._json = lambda status, body, extra=None: replies.append((status, body))
            handler.do_GET()
            self.assertEqual(replies[0][0], 200)
            request = replies[0][1] if route == "requests" else replies[0][1]["request"]
            self.assertEqual(request["trace_id"], "trc_" + "2" * 32)
            handler.server.principal = "tenant-b"
            handler.do_GET()
            self.assertEqual(replies[-1], (404, {"error": "not_found"}))


if __name__ == "__main__":
    unittest.main()
