"""Observability dashboard: ingress limits, path safety, redaction, owner isolation and the read-only server."""
from __future__ import annotations

import ast
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from observability import ingest
from observability.ingest import Limits, load_fixture_set, parse_lines
from observability.report import Dataset
from observability.server import DashboardServer

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "observability" / "fixtures" / "v1"
PKG = ROOT / "observability"
SENTINELS = ("sam@example.invalid", "Original Road", "SYNTHETIC full prompt", "copied into telemetry", "SYNTHETIC confirmation for")


class TempSet:
    """A writable copy of the frozen fixture set for tamper tests."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "set"
        shutil.copytree(FIX, self.root)
        self.cases = json.loads((self.root / "cases.json").read_text())
        return self

    def save(self):
        (self.root / "cases.json").write_text(json.dumps(self.cases))

    def __exit__(self, *exc):
        self.tmp.cleanup()


class IngressLimits(unittest.TestCase):
    def test_oversized_line_is_rejected_before_parsing(self):
        line = b'{"a": "' + b"x" * 100 + b'"}'
        with mock.patch.object(ingest.json, "loads", side_effect=AssertionError("parsed an oversized line")):
            parsed, rejected = parse_lines(line + b"\n", Limits(max_line_bytes=50))
        self.assertEqual((parsed, rejected), ([], {"line_too_large": 1}))

    def test_depth_string_count_and_constant_limits(self):
        deep = json.dumps({"a": [[[[[[[[[[1]]]]]]]]]]}).encode()
        long = json.dumps({"a": "y" * 600}).encode()
        nan = b'{"a": NaN}'
        dup = b'{"a": 1, "a": 2}'
        _, rejected = parse_lines(b"\n".join([deep, long, nan, dup, b"[1]"]) + b"\n", Limits())
        self.assertEqual(rejected, {"value_too_deep": 1, "string_too_long": 1, "unparseable_line": 2, "not_object": 1})
        _, rejected = parse_lines(b"{}\n" * 5, Limits(max_lines=3))
        self.assertEqual(rejected["too_many_lines"], 2)

    def test_oversized_artifact_is_refused_by_size_before_reading(self):
        with TempSet() as s:
            loaded = load_fixture_set(s.root, Limits(max_artifact_bytes=200))
            self.assertTrue(any(p["code"] == "trace_artifact_too_large" for p in loaded["problems"]))

    def test_span_count_per_request_is_bounded(self):
        data = Dataset(limits=Limits(max_spans_per_request=100))
        timing = data.requests["large/run-large"]["timing"]
        self.assertEqual(timing["spans_accepted"], 100)
        self.assertEqual(timing["rejection_codes"].get("span.over_span_limit"), 501)


class ManifestAndPaths(unittest.TestCase):
    def test_traversal_absolute_and_odd_names_are_never_opened(self):
        with TempSet() as s:
            for bad in ("../../etc/passwd", "/etc/passwd", "traces/../cases.json", "traces/HEALTHY.jsonl", "traces\\x.jsonl"):
                s.cases["cases"][0]["trace"] = bad
                s.save()
                loaded = load_fixture_set(s.root)
                self.assertIn({"case": "healthy", "code": "trace_path_rejected"}, loaded["problems"], bad)

    def test_symlinked_artifact_is_rejected(self):
        with TempSet() as s:
            target = s.root / "traces" / "healthy.jsonl"
            outside = Path(s.tmp.name) / "outside.jsonl"
            outside.write_bytes(target.read_bytes())
            target.unlink()
            os.symlink(outside, target)
            loaded = load_fixture_set(s.root)
            self.assertIn({"case": "healthy", "code": "trace_path_rejected"}, loaded["problems"])

    def test_tampered_artifact_fails_its_digest_binding(self):
        with TempSet() as s:
            path = s.root / "sidecar" / "healthy.jsonl"
            path.write_bytes(path.read_bytes().replace(b'"0.0074"', b'"0.0001"'))
            loaded = load_fixture_set(s.root)
            self.assertIn({"case": "healthy", "code": "sidecar_digest_mismatch"}, loaded["problems"])
            stream = next(st for st in loaded["streams"] if st.key == "healthy/run-healthy")
            self.assertEqual(stream.sidecar, [])  # unverified metrics are discarded, authoritative trace retained
            self.assertEqual(stream.sidecar_rejected, {"sidecar.digest_mismatch": 1})

    def test_unsupported_data_origin_is_refused(self):
        with TempSet() as s:
            s.cases["data_origin"] = "live_serving"
            s.save()
            with self.assertRaises(ValueError):
                Dataset(s.root)


class Redaction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Dataset()

    def test_no_sentinel_reaches_any_export_or_report_view(self):
        for principal in ("tenant-a", "tenant-b"):
            blob = json.dumps([self.data.export(principal, r["key"]) for r in self.data.visible(principal)])
            blob += json.dumps(self.data.meta(principal)) + json.dumps(self.data.aggregates(principal))
            for sentinel in SENTINELS:
                self.assertNotIn(sentinel, blob)

    def test_contract_diagnostics_become_codes_never_echoed_values(self):
        hostile = {"schema_version": "trace-v1", "event_id": "e1", "sequence": 1, "event_type": "LEAK sam@example.invalid",
                   "session_id": "s", "run_id": "r", "trace_id": "r", "source": {"component": "backend.service",
                   "mapping_version": "m"}, "evidence_class": "serving_observation", "occurred_at": "2026-01-01T00:00:00Z",
                   "payload": {}, "secret_field": "x"}
        streams, _ = ingest.build_streams("hostile", "0" * 64, [(1, hostile)], {})
        codes = json.dumps(streams[0].contract_codes)
        self.assertNotIn("sam@example.invalid", codes)
        self.assertNotIn("secret_field", codes)
        self.assertTrue(all(code.startswith(("contract.", "observability.")) for code in streams[0].contract_codes))

    def test_disallowed_sidecar_fields_reject_the_whole_record(self):
        r = self.data.requests["redaction/run-redaction"]
        self.assertNotIn("leaky", json.dumps(r["timing"]["lanes"]))
        self.assertEqual(r["costs"]["usage"], [])


class OwnershipAndEffects(unittest.TestCase):
    def test_observability_code_cannot_reach_business_mutation_paths(self):
        forbidden = {"rra", "adapters", "serving", "provider_sim", "server", "evalgate.driver", "evalgate.runner", "evalgate.simulator"}
        for path in PKG.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
                for name in names:
                    self.assertFalse(any(name == f or name.startswith(f + ".") for f in forbidden), f"{path.name} imports {name}")
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("write_text", "write_bytes", "unlink", "rename"):
                    self.assertEqual(path.name, "__main__.py", f"{path.name} writes files")

    def test_owner_scope_applies_to_detail_export_list_and_aggregates(self):
        data = Dataset()
        self.assertIsNone(data.get("tenant-a", "tenant-b/run-tenant-b-private"))
        self.assertIsNone(data.export("tenant-a", "tenant-b/run-tenant-b-private"))
        self.assertNotIn("tenant-b/run-tenant-b-private", [r["key"] for r in data.visible("tenant-a")])
        self.assertEqual(sum(a["requests"] for a in data.aggregates("tenant-b").values()), 1)
        self.assertEqual(data.visible("nobody"), [])


class TelemetryFailureIsolation(unittest.TestCase):
    def _business(self, data):
        return {k: (r["status"], {o: (v["outcome"], v["mutation_attempts"]) for o, v in r["operations"].items()})
                for k, r in data.requests.items()}

    def test_losing_or_corrupting_all_telemetry_never_changes_business_outcomes(self):
        baseline = self._business(Dataset())
        # Request-span "error" is the only telemetry input to the headline (handler failure); remove it from comparison.
        handler_failed = {k for k, r in Dataset().requests.items() if r["timing"]["request_span_status"] == "error"}
        for damage in (lambda b: b"", lambda b: b.replace(b'"kind": "span"', b'"kind": "spam"'), lambda b: b[: len(b) // 2]):
            with TempSet() as s:
                for relative in {entry["sidecar"] for entry in s.cases["cases"]}:
                    path = s.root / relative
                    path.write_bytes(damage(path.read_bytes()))
                for entry in s.cases["cases"]:
                    entry["sidecar_sha256"] = sha256((s.root / entry["sidecar"]).read_bytes()).hexdigest()
                s.save()
                damaged = self._business(Dataset(s.root))
            for key, (status, ops) in baseline.items():
                if key in handler_failed:
                    continue
                self.assertEqual(damaged[key][1], ops, key)
                self.assertEqual(damaged[key][0], status, key)


class ReadOnlyServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = DashboardServer(Dataset(), "tenant-a", port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{self.port}")
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status, dict(response.getheaders()), body

    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_static_and_api_carry_strict_headers(self):
        for path in ("/", "/app.js", "/styles.css", "/api/meta"):
            status, headers, _ = self.call("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_every_write_method_is_refused(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"):
            status, headers, _ = self.call(method, "/api/requests")
            self.assertEqual(status, 405, method)
            self.assertEqual(headers["Allow"], "GET, HEAD")

    def test_foreign_host_header_is_refused(self):
        self.assertEqual(self.call("GET", "/api/meta", host="evil.example:80")[0], 421)

    def test_traversal_and_unknown_paths(self):
        for path in ("/../server.py", "/%2e%2e/server.py", "/web/app.js", "/api/requests/healthy/..%2fx", "/%00", "//etc/passwd"):
            self.assertIn(self.call("GET", path)[0], (400, 404), path)

    def test_list_filter_and_detail_values_match_the_oracle(self):
        oracle = json.loads((FIX / "oracle.json").read_text())
        status, _, body = self.call("GET", "/api/requests?status=unknown_outcome&page_size=100")
        self.assertEqual(status, 200)
        keys = {i["key"] for i in json.loads(body)["items"]}
        expected = {k for k, v in oracle["requests"].items() if v["status"] == "unknown_outcome" and v.get("owner", "tenant-a") == "tenant-a"}
        self.assertEqual(keys, expected)
        status, _, body = self.call("GET", "/api/requests/healthy/run-healthy")
        detail = json.loads(body)
        self.assertEqual(detail["timing"]["request_duration_ns"], 1_450_000_000)
        self.assertEqual(detail["costs"]["confirmed_charge"], {"USD": "0.0074"})
        status, _, body = self.call("GET", "/api/requests?from=2026-10-03T10:00Z&to=2026-10-03T10:12Z&page_size=100")
        self.assertEqual(json.loads(body)["total"], 11)  # load runs 01..11 start inside [10:00, 10:12)

    def test_another_principals_request_is_indistinguishable_from_missing(self):
        hidden = self.call("GET", "/api/requests/tenant-b/run-tenant-b-private")
        missing = self.call("GET", "/api/requests/tenant-b/run-does-not-exist")
        self.assertEqual((hidden[0], hidden[2]), (404, missing[2]))
        self.assertEqual(self.call("GET", "/api/export/tenant-b/run-tenant-b-private")[0], 404)

    def test_principal_cannot_be_chosen_by_the_client(self):
        self.assertEqual(self.call("GET", "/api/requests?principal=tenant-b")[0], 400)
        self.assertEqual(json.loads(self.call("GET", "/api/meta")[2])["viewer"], "tenant-a")

    def test_bad_query_values_are_rejected(self):
        for query in ("status=pwned", "page_size=101", "page=-1", "from=yesterday", "route=Bad%20Route", "status=a&status=b"):
            self.assertEqual(self.call("GET", f"/api/requests?{query}")[0], 400, query)

    def test_export_is_an_attachment_and_redacted(self):
        status, headers, body = self.call("GET", "/api/export/redaction/run-redaction")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel.encode(), body)

    def test_page_text_has_no_html_injection_sinks(self):
        js = (PKG / "web" / "app.js").read_text()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertFalse(sink in js, sink)


if __name__ == "__main__":
    unittest.main()
