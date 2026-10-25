"""Observability dashboard: frozen fixtures, hand-written oracle, independent effects, planted mutants.

The oracle (observability/fixtures/v1/oracle.json) was written by hand from fixture facts before the projection
existed; these tests compare the projection with it and never the other way round.
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from observability import costs, ingest, project, report
from observability.report import Dataset, nearest_rank

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "observability" / "fixtures" / "v1"
ORACLE = json.loads((FIX / "oracle.json").read_text())
EFFECTS = json.loads((FIX / "effects.json").read_text())["operations"]
NS = 1_000_000


def _money(value):
    return None if value is None else Decimal(value)


def oracle_mismatches(data: Dataset) -> list[str]:
    """Every difference between the projection and the hand-written oracle (empty list = agreement)."""
    bad = []
    for key, exp in ORACLE["requests"].items():
        r = data.requests.get(key)
        if r is None:
            bad.append(f"{key}: missing")
            continue
        t, c, ev = r["timing"], r["costs"], r["evidence"]
        checks = {
            "status": r["status"], "contract": ev["contract_verdict"], "completeness": sorted(ev["completeness"]),
            "duration_ms": None if t["request_duration_ns"] is None else t["request_duration_ns"] / NS,
            "duration_kind": t["duration_kind"],
            "lower_bound_ms": None if t["lower_bound_ns"] is None else t["lower_bound_ns"] / NS,
            "command_response_ms": None if t["command_response_ns"] is None else t["command_response_ns"] / NS,
            "first_event_ms": None if t["first_event_ns"] is None else t["first_event_ns"] / NS,
            "by_class_ms": {k: v["total_ns"] / NS for k, v in t["by_class"].items()},
            "children_overlap": t["children_overlap"], "clock_domains": t["clock_domains"],
            "reconciliation_lookups": t["reconciliation_lookups"], "reconciliation_lookup_errors": t["reconciliation_lookup_errors"],
            "spans_accepted": t["spans_accepted"], "spans_rejected": t["spans_rejected"], "telemetry_dropped": t["telemetry_dropped"],
            "caveats": ev["caveats"], "flags": ev["flags"], "duplicate_lines_in_artifact": ev["duplicate_lines_in_artifact"],
            "reordered_lines": ev["reordered_lines"], "redacted_payload_fields": ev["redacted_payload_fields"],
            "redacted_error_class": ev["redacted_error_class"], "sidecar_rejected": ev["sidecar_rejected"],
            "estimated_charge": _money((c["estimated_charge"]["total"] or {}).get("USD")) if c["estimated_charge"]["total"] is not None else None,
            "confirmed_charge": _money(c["confirmed_charge"].get("USD")),
            "cost_reason": c["estimated_charge"]["reason"], "cost_uncertain_outcome": c["uncertain_outcome"],
            "known_subtotal": _money(c["estimated_charge"]["known_subtotal"].get("USD")),
            "unknown_usage_records": c["estimated_charge"]["unknown_usage_records"],
            "pricing_tables": c["estimated_charge"]["pricing_tables"],
            "estimated_charge_from_estimated_tokens": _money((c["estimated_charge_from_estimated_tokens"]["total"] or {}).get("USD")),
            "duplicate_usage_identities": c["duplicate_usage_identities"],
            "span_rollup_charge": {k.split(":")[-1]: _money(v["known"]) for k, v in c["span_rollup"].items()},
            "currency": next(iter((c["estimated_charge"]["total"] or {"?": 0})), None),
        }
        for field, expected in exp.items():
            if field in ("operations", "owner", "export_must_not_contain", "flags_absent", "latency_excluded_reason"):
                continue
            got = checks[field]
            if field in ("estimated_charge", "confirmed_charge", "known_subtotal", "estimated_charge_from_estimated_tokens"):
                expected = _money(expected)
            if field == "caveats":
                got = [c for c in expected if c in got]  # the oracle lists caveats that must be shown
            if field == "by_class_ms":
                got = {k: got.get(k) for k in expected}
            if field == "span_rollup_charge":
                got = {k: got.get(k) for k in expected}
                expected = {k: _money(v) for k, v in expected.items()}
            if field == "completeness":
                expected = sorted(expected)
            if got != expected:
                bad.append(f"{key}: {field} = {got!r}, oracle {expected!r}")
        for flag in exp.get("flags_absent", []):
            if flag in ev["flags"]:
                bad.append(f"{key}: unexpected flag {flag}")
        for op_id, op_exp in exp.get("operations", {}).items():
            op = r["operations"].get(op_id)
            if op is None:
                bad.append(f"{key}: operation {op_id} missing")
                continue
            for field, expected in op_exp.items():
                got = op.get(field)
                if got != expected:
                    bad.append(f"{key}: {op_id}.{field} = {got!r}, oracle {expected!r}")
    for name, exp in ORACLE["aggregates"].items():
        agg = data.aggregates("tenant-a").get(name)
        if agg is None:
            bad.append(f"aggregate {name} missing")
            continue
        lat = agg["latency"]
        got = {"requests": agg["requests"], "status_counts": agg["status_counts"], "eligible": lat["eligible"],
               "excluded": lat["excluded"], "method": lat["method"], "p50_ms": None if lat["p50_ns"] is None else lat["p50_ns"] / NS,
               "p95_ms": None if lat["p95_ns"] is None else lat["p95_ns"] / NS, "p95_rank": lat["p95_rank"],
               "estimated_charge": {k: Decimal(v) for k, v in agg["costs"]["estimated_charge_complete_requests"].items()},
               "confirmed_charge": agg["costs"]["confirmed_charge"], "usage_records_unknown": agg["costs"]["requests_with_unknown_estimate"]}
        want = {"requests": exp["requests"], "status_counts": exp["status_counts"], **{k: exp["latency"][k] for k in ("eligible", "excluded", "method", "p50_ms", "p95_ms", "p95_rank")},
                "estimated_charge": {k: Decimal(v) for k, v in exp["estimated_charge"].items()},
                "confirmed_charge": exp["confirmed_charge"], "usage_records_unknown": exp["usage_records_unknown"]}
        for field in want:
            if got[field] != want[field]:
                bad.append(f"aggregate {name}: {field} = {got[field]!r}, oracle {want[field]!r}")
    return bad


def latency_excluded(data: Dataset) -> list[str]:
    """Requests the oracle says must be excluded from latency samples, with the reason."""
    bad = []
    for key, exp in ORACLE["requests"].items():
        reason = exp.get("latency_excluded_reason")
        if not reason:
            continue
        r = data.requests[key]
        group = data.aggregates("tenant-a")[f"{r['data_origin']}/{r['evidence_class']}/{r['route_class']}"]
        if group["latency"]["excluded"].get(reason, 0) < 1:
            bad.append(f"{key}: not excluded as {reason}")
    return bad


class FrozenFixtures(unittest.TestCase):
    def test_manifest_hashes_match_every_file_including_the_oracle(self):
        manifest = json.loads((FIX / "MANIFEST.json").read_text())
        self.assertIn("oracle.json", manifest["files"])
        for path, digest in manifest["files"].items():
            self.assertEqual(sha256((FIX / path).read_bytes()).hexdigest(), digest, path)

    def test_generator_reproduces_the_frozen_set_byte_for_byte(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "observability_fixtures.py"), "--check"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fixture_traces_get_the_unchanged_contract_verdicts(self):
        from evalgate.trace import check_file
        invalid = {"duplicate-line", "out-of-order", "sequence-gap", "truncated", "malformed-line", "oversize-line"}
        for path in sorted((FIX / "traces").glob("*.jsonl")):
            problems, _ = check_file(path)
            self.assertEqual(bool(problems), path.stem in invalid, path.stem)


class OracleAgreement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Dataset()

    def test_projection_matches_hand_oracle(self):
        self.assertEqual(oracle_mismatches(self.data), [])

    def test_invalid_and_incomplete_requests_leave_the_latency_sample(self):
        self.assertEqual(latency_excluded(self.data), [])

    def test_visibility_pagination_and_set_level_problems(self):
        vis = ORACLE["visible_to"]
        self.assertEqual(len(self.data.visible("tenant-a")), vis["tenant-a"]["requests"])
        self.assertEqual(len(self.data.visible("tenant-b")), vis["tenant-b"]["requests"])
        sizes = [len(self.data.page("tenant-a", page=n, page_size=20)["items"]) for n in (1, 2, 3)]
        self.assertEqual(sizes, vis["tenant-a"]["pages_at_20"])
        keys = [i["key"] for n in (1, 2, 3) for i in self.data.page("tenant-a", page=n, page_size=20)["items"]]
        self.assertEqual(len(keys), len(set(keys)), "page boundaries must not repeat or drop requests")
        self.assertEqual(self.data.page("tenant-a", page=99)["page"], 3)
        self.assertEqual(self.data.page("tenant-a", page_size=10_000)["page_size"], 100)
        self.assertEqual(self.data.redeliveries, ORACLE["fixture_set"]["artifact_redeliveries"])
        codes = {p["case"]: p["code"] for p in self.data.problems}
        for case, code in ORACLE["fixture_set"]["file_problems"].items():
            self.assertEqual(codes.get(case), code)

    def test_no_projected_effect_contradicts_the_independent_simulator(self):
        """effects.json comes from evalgate.simulator; the dashboard never reads it."""
        for request in self.data.requests.values():
            for op_id, op in request["operations"].items():
                truth = EFFECTS.get(op_id)
                if truth is None or not op["mutation_attempts"]:
                    continue
                if op["outcome"] == "effect_confirmed" and request["status"] not in ("incomplete", "invalid_evidence"):
                    self.assertTrue(truth["effect_applied"], f"{request['key']} claims an effect the simulator never applied")
                if truth["effect_applied"]:
                    self.assertNotEqual(op["outcome"], "no_effect_confirmed", request["key"])
                    self.assertNotEqual(request["status"], "failed", request["key"])

    def test_authorization_alone_is_never_counted_as_execution(self):
        for request in self.data.requests.values():
            for op in request["operations"].values():
                if op["authorized"] and not op["mutation_attempts"]:
                    self.assertEqual(op["outcome"], "not_attempted")

    def test_contract_examples_are_artifact_scoped_requests(self):
        """serving-success and serving-illustrative reuse every id; offline-gate-trace holds three requests in one run."""
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "traces").mkdir()
            (root / "sidecar").mkdir()
            entries = []
            for name in ("serving-success", "serving-illustrative", "offline-gate-trace"):
                shutil.copy(ROOT / "contracts" / "examples" / f"{name}.jsonl", root / "traces" / f"{name}.jsonl")
                (root / "sidecar" / f"{name}.jsonl").write_bytes(b"")
                entries.append({"case_id": name, "trace": f"traces/{name}.jsonl", "sidecar": f"sidecar/{name}.jsonl",
                                "trace_sha256": sha256((root / "traces" / f"{name}.jsonl").read_bytes()).hexdigest(),
                                "sidecar_sha256": sha256(b"").hexdigest()})
            (root / "cases.json").write_text(json.dumps({"fixture_set": "obs-fixtures-v1", "data_origin": "synthetic_fixture",
                                                         "cases": entries, "pricing": [], "access": "access.json"}))
            (root / "access.json").write_text(json.dumps({"owners": {}}))
            loaded = ingest.load_fixture_set(root)
            keys = sorted(s.key for s in loaded["streams"])
            self.assertIn("serving-success/run-01J9EXAMPLE", keys)
            self.assertIn("serving-illustrative/run-01J9EXAMPLE", keys)
            self.assertEqual(sum(k.startswith("offline-gate-trace/") for k in keys), 3)
            projected = {s.key: project.project(s, [], "synthetic_fixture", ingest.Limits()) for s in loaded["streams"]}
            self.assertEqual(projected["serving-success/run-01J9EXAMPLE"]["status"], "completed")
            self.assertEqual(projected["serving-illustrative/run-01J9EXAMPLE"]["status"], "completed")
            self.assertEqual(projected["serving-illustrative/run-01J9EXAMPLE"]["operations"]["op-addr-1"]["confirmed_by"], "reconciled")
            for key, r in projected.items():
                if key.startswith("offline-gate-trace/"):
                    self.assertEqual(r["timing"]["duration_kind"], "unknown")  # legacy traces carry no time: never invented
                    self.assertIsNone(r["started_at"])


class Arithmetic(unittest.TestCase):
    def test_nearest_rank_small_samples(self):
        self.assertEqual(nearest_rank([], 95), (None, None))
        self.assertEqual(nearest_rank([7], 50), (7, 1))
        self.assertEqual(nearest_rank([7], 95), (7, 1))
        self.assertEqual(nearest_rank([1, 2], 50), (1, 1))
        self.assertEqual(nearest_rank(list(range(1, 20)), 95), (19, 19))
        self.assertEqual(nearest_rank(list(range(1, 21)), 95), (19, 19))
        self.assertEqual(nearest_rank(list(range(1, 21)), 50), (10, 10))

    def test_exact_decimal_estimate_and_zero_versus_unknown(self):
        table = json.loads((FIX / "pricing" / "synthetic-2026-01-01.json").read_text())
        usage = {"model_id": "synthetic-model-a", "provider": "synthetic-provider", "observed_at": "2026-10-02T00:00:00Z",
                 "tokens": {"input": 1, "output": 1, "cache_read": 1, "cache_write": 1, "reasoning": 1}}
        self.assertEqual(costs.estimate(usage, [table])["amount"], Decimal("0.00003705"))
        self.assertEqual(costs.money(Decimal("5.500")), "5.5")
        free = {**usage, "model_id": "synthetic-free-tier-model"}
        self.assertEqual(costs.estimate(free, [table])["amount"], Decimal(0))
        self.assertIsNone(costs.estimate({**usage, "model_id": "synthetic-unlisted-model"}, [table])["amount"])
        self.assertEqual(costs.estimate({**usage, "observed_at": None}, [table])["reason"], "no_usage_time")
        self.assertEqual(costs.estimate({**usage, "observed_at": "2025-12-31T23:59:59Z"}, [table])["reason"], "model_not_priced")
        missing = {**usage, "tokens": {**usage["tokens"], "reasoning": None}}
        self.assertEqual(costs.estimate(missing, [table])["reason"], "token_category_not_supplied")

    def test_conflicting_tables_for_one_date_are_ambiguous(self):
        table = json.loads((FIX / "pricing" / "synthetic-2026-01-01.json").read_text())
        other = json.loads(json.dumps(table))
        other["models"]["synthetic-model-a"]["input"] = "9.99"
        usage = {"model_id": "synthetic-model-a", "provider": "synthetic-provider", "observed_at": "2026-10-02T00:00:00Z",
                 "tokens": dict.fromkeys(("input", "output", "cache_read", "cache_write", "reasoning"), 1)}
        self.assertEqual(costs.estimate(usage, [table, other])["reason"], "ambiguous_pricing")

    def test_raw_monotonic_values_survive_as_exact_strings(self):
        r = Dataset().requests["healthy/run-healthy"]
        span = r["timing"]["lanes"]["proc-a:boot-1"][0]
        self.assertIsInstance(span["start_mono_ns"], str)
        self.assertEqual(int(span["end_mono_ns"]) - int(span["start_mono_ns"]), span["duration_ns"])


class PlantedMutants(unittest.TestCase):
    """Each bug the spec names is planted for one dataset build; a meaningful assertion must then fail."""

    def build(self):
        return Dataset()

    def assertCaught(self, failures, needle):
        self.assertTrue(failures, "mutant survived: no oracle assertion failed")
        self.assertTrue(any(needle in f for f in failures), f"mutant caught only by unrelated checks: {failures[:4]}")

    def test_mutant_authorization_counted_as_execution(self):
        real = project.operations

        def mutant(events):
            ops = real(events)
            for op in ops.values():
                if op["authorized"] and not op["mutation_attempts"]:
                    op["mutation_attempts"], op["outcome"] = 1, "effect_confirmed"
            return ops
        with mock.patch.object(project, "operations", mutant):
            self.assertCaught(oracle_mismatches(self.build()), "cancel-before-claim/run-cancel-before-claim: op-cancel-early-1.mutation_attempts")

    def test_mutant_unknown_reported_as_failed_with_zero_cost(self):
        real = project.headline

        def mutant(stream, ops, request_span_status, cancelled):
            status, reasons = real(stream, ops, request_span_status, cancelled)
            return ("failed", reasons) if status == "unknown_outcome" else (status, reasons)
        real_estimate = costs.estimate

        def zero(usage, tables):
            result = real_estimate(usage, tables)
            return {**result, "amount": result["amount"] if result["amount"] is not None else Decimal(0), "currency": result["currency"] or "USD", "table_id": result["table_id"] or "mutant"}
        with mock.patch.object(project, "headline", mutant), mock.patch.object(costs, "estimate", zero):
            failures = oracle_mismatches(self.build())
        self.assertCaught(failures, "lost-response/run-lost-response: status = 'failed'")
        self.assertCaught(failures, "zero-vs-unknown-price/run-zero-vs-unknown-price: estimated_charge")

    def test_mutant_parent_and_child_cost_both_counted(self):
        real = costs.request_costs

        def mutant(*args):
            out = real(*args)
            total = sum((Decimal(v["known"]) for v in out["span_rollup"].values()), Decimal(0))
            if out["estimated_charge"]["total"] is not None and out["span_rollup"]:
                out["estimated_charge"]["total"] = {"USD": costs.money(total)}
            return out
        with mock.patch.object(project, "request_costs", mutant):
            self.assertCaught(oracle_mismatches(self.build()), "overlap/run-overlap: estimated_charge")

    def test_mutant_wall_clock_subtracted_across_processes(self):
        def mutant(span):
            if span["start_mono_ns"] is None or span["end_mono_ns"] is None:
                return None
            return span["end_mono_ns"] - span["start_mono_ns"]  # ignores end_clock_domain
        with mock.patch.object(project, "_duration", mutant):
            self.assertCaught(oracle_mismatches(self.build()), "process-restart/run-restart: duration")

    def test_mutant_missing_records_suppressed(self):
        real = ingest.build_streams

        def mutant(*args):
            streams, summary = real(*args)
            for s in streams:
                s.completeness = []
                s.contract_codes = {}
            return streams, summary
        with mock.patch.object(ingest, "build_streams", mutant):
            self.assertCaught(oracle_mismatches(self.build()), "truncated/run-truncated: status")

    def test_mutant_failed_requests_hidden_from_p95(self):
        real = report.aggregate

        def mutant(rows, **kw):
            return real([r for r in rows if r["status"] != "failed"], **kw)
        with mock.patch.object(report, "aggregate", mutant):
            failures = oracle_mismatches(self.build())
        self.assertCaught(failures, "p95_ms = 300.0")

    def test_mutant_redaction_bypassed(self):
        def mutant(event):
            return {**event, **event["payload"], "label": event["event_type"]}, 0
        with mock.patch.object(project, "redact_event", mutant):
            data = self.build()
            exported = json.dumps(data.export("tenant-a", "redaction/run-redaction"))
        self.assertTrue(any(s in exported for s in ORACLE["requests"]["redaction/run-redaction"]["export_must_not_contain"]))
        self.assertCaught(oracle_mismatches(data), "redaction/run-redaction: redacted_payload_fields")

    def test_mutant_serves_another_principals_trace(self):
        with mock.patch.object(Dataset, "get", lambda self, principal, key: self.requests.get(key)):
            data = self.build()
            self.assertIsNotNone(data.get("tenant-a", "tenant-b/run-tenant-b-private"))  # the planted leak
        self.assertIsNone(Dataset().get("tenant-a", "tenant-b/run-tenant-b-private"))   # the real code refuses

    def test_real_code_passes_every_check_the_mutants_fail(self):
        data = self.build()
        self.assertEqual(oracle_mismatches(data), [])
        exported = json.dumps(data.export("tenant-a", "redaction/run-redaction"))
        for sentinel in ORACLE["requests"]["redaction/run-redaction"]["export_must_not_contain"]:
            self.assertNotIn(sentinel, exported)


if __name__ == "__main__":
    unittest.main()
