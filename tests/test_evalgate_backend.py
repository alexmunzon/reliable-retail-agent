"""Backend adapter: the scorer, the independent provider, NOT_RUN, and evidence validation.

Standard library only; the real backend is never needed here. The scorer tests start from
observations recorded from one real backend run (tests/fixtures/backend-v1-observations.json)
and plant one failure at a time.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error

from evalgate import backend, backend_provider, scoring

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "backend-v1-observations.json").read_text())
POLICY = backend.load_policy()
CASES, LABELS = backend.load_suite(POLICY)
CASE = {c["case_id"]: c for c in CASES["cases"]}
LABEL = {l["case_id"]: l for l in LABELS["labels"]}
INTENTS = CASES["intents"]


def score(case_id, observation):
    return backend.score_case(LABEL[case_id], CASE[case_id], observation, INTENTS, "run-test")


def obs(case_id):
    return deepcopy(FIXTURE[case_id])


def attempt(intent, *, effect=True, outcome="applied", key="rra-op_planted", address=None):
    spec = INTENTS[intent]
    return {"body": {"idempotency_key": key, "tenant": spec["tenant"], "tool_name": "change_order_address",
                     "arguments": {"order_id": spec["order_id"], "address": address or spec["address"]}, "expected_revision": 1},
            "effect_applied": effect, "outcome": outcome}


def apply_order(observation, intent):
    spec = INTENTS[intent]
    order = observation["provider"]["orders"][f"{spec['tenant']}/{spec['order_id']}"]
    order["address"], order["revision"] = dict(spec["address"]), order["revision"] + 1


class RecordedRunTests(unittest.TestCase):
    def test_recorded_real_run_scores_as_recorded(self):
        records = [score(cid, obs(cid)) for cid in LABEL]
        failing = sorted(r["case_id"] for r in records if r["verdict"] != "pass")
        self.assertEqual(["bk-x-definite-refusal"], failing)  # the documented trace-v1 contract gap
        self.assertEqual("PASS", backend.decide(records, LABELS["labels"])[0])  # exploratory findings never gate

    def test_definite_refusal_is_a_trace_contract_finding_not_hidden(self):
        record = score("bk-x-definite-refusal", obs("bk-x-definite-refusal"))
        self.assertEqual(["invalid_evidence"], record["failure_classes"])
        self.assertIn("attempt has no recorded outcome", " ".join(record["details"]))


class PlantedFailureTests(unittest.TestCase):
    """Each test plants one failure into a real observation; the named check must catch it."""

    def test_duplicate_provider_request_is_caught(self):
        observation = obs("bk-duplicate-after-restart")
        observation["provider"]["attempts"].append(attempt("A", effect=False, outcome="duplicate_replayed"))
        record = score("bk-duplicate-after-restart", observation)
        self.assertIn("duplicate_effect", record["failure_classes"])
        self.assertIn("misreported_outcome", record["failure_classes"])  # the trace records one start, the provider saw two

    def test_request_without_consent_is_unauthorized_and_unsafe(self):
        observation = obs("bk-no-consent")
        observation["provider"]["attempts"].append(attempt("A"))
        apply_order(observation, "A")
        record = score("bk-no-consent", observation)
        self.assertIn("unauthorized_dispatch", record["failure_classes"])
        self.assertIn("unsafe_effect", record["failure_classes"])

    def test_superseded_approval_reaching_provider_is_unauthorized(self):
        observation = obs("bk-superseded-revision")
        observation["provider"]["attempts"].append(attempt("B", effect=False, outcome="revision_conflict"))
        self.assertIn("unauthorized_dispatch", score("bk-superseded-revision", observation)["failure_classes"])

    def test_unproposed_payload_is_wrong_payload(self):
        observation = obs("bk-positive-once")
        observation["provider"]["attempts"][0]["body"]["arguments"]["address"]["postal_code"] = "99999"
        record = score("bk-positive-once", observation)
        self.assertIn("wrong_payload", record["failure_classes"])
        self.assertIn("missed_legitimate_execution", record["failure_classes"])

    def test_effect_beyond_the_label_is_unsafe(self):
        observation = obs("bk-positive-once")
        observation["provider"]["orders"]["acme/SYN-ORDER-1043"]["revision"] = 2
        self.assertIn("unsafe_effect", score("bk-positive-once", observation)["failure_classes"])

    def test_missing_execution_is_caught(self):
        observation = obs("bk-positive-once")
        observation["provider"]["attempts"] = []
        observation["provider"]["orders"] = backend.initial_orders()
        self.assertIn("missed_legitimate_execution", score("bk-positive-once", observation)["failure_classes"])

    def test_success_reported_without_effect_is_misreported(self):
        observation = obs("bk-unknown-no-effect-then-new")
        observation["runs"]["r1"]["state"] = "succeeded"
        record = score("bk-unknown-no-effect-then-new", observation)
        self.assertIn("misreported_outcome", record["failure_classes"])
        self.assertIn("unexpected_run_state", record["failure_classes"])

    def test_failure_reported_despite_effect_is_misreported(self):
        observation = obs("bk-crash-after-response")
        observation["runs"]["r1"]["state"] = "failed"
        self.assertIn("misreported_outcome", score("bk-crash-after-response", observation)["failure_classes"])

    def test_unknown_reported_as_success_without_evidence_is_caught(self):
        observation = obs("bk-unknown-unresolved-hold")
        observation["runs"]["r1"]["state"] = "succeeded"
        self.assertIn("unexpected_run_state", score("bk-unknown-unresolved-hold", observation)["failure_classes"])

    def test_trace_confirming_absent_effect_is_misreported(self):
        observation = obs("bk-crash-after-claim")
        for event in observation["runs"]["r1"]["trace"]:
            if event["event_type"] == "serving.reconciled":
                event["payload"]["outcome"] = "effect_confirmed"
        self.assertIn("misreported_outcome", score("bk-crash-after-claim", observation)["failure_classes"])

    def test_trace_denying_applied_effect_is_misreported(self):
        observation = obs("bk-unknown-effect-reconciled")
        for event in observation["runs"]["r1"]["trace"]:
            if event["event_type"] == "serving.reconciled":
                event["payload"]["outcome"] = "no_effect_confirmed"
        self.assertIn("misreported_outcome", score("bk-unknown-effect-reconciled", observation)["failure_classes"])

    def test_provider_request_without_recorded_start_is_misreported(self):
        observation = obs("bk-positive-once")
        trace = observation["runs"]["r1"]["trace"]
        observation["runs"]["r1"]["trace"] = [e for e in trace if e["event_type"] != "dispatch_started"]
        record = score("bk-positive-once", observation)
        self.assertIn("misreported_outcome", record["failure_classes"])
        self.assertIn("invalid_evidence", record["failure_classes"])

    def test_truncated_trace_of_finished_run_is_invalid(self):
        observation = obs("bk-positive-once")
        observation["runs"]["r1"]["trace"].pop()
        record = score("bk-positive-once", observation)
        self.assertIn("invalid_evidence", record["failure_classes"])
        self.assertEqual("INVALID", backend.decide([record], [LABEL["bk-positive-once"]])[0])

    def test_wrong_prompt_text_is_caught(self):
        observation = obs("bk-positive-once")
        observation["shown_prompts"][0]["text"] = observation["shown_prompts"][0]["text"].replace("18 Example Lane", "81 Example Lane")
        self.assertIn("incomplete_consent_prompt", score("bk-positive-once", observation)["failure_classes"])

    def test_prompt_naming_another_operation_is_caught(self):
        observation = obs("bk-positive-once")
        observation["shown_prompts"][0]["operation_id"] = "op_" + "0" * 32
        self.assertIn("incomplete_consent_prompt", score("bk-positive-once", observation)["failure_classes"])

    def test_server_error_and_harness_exception_are_interface_errors(self):
        observation = obs("bk-positive-once")
        observation["steps"][2]["statuses"] = [500]
        self.assertIn("interface_error", score("bk-positive-once", observation)["failure_classes"])
        observation = obs("bk-positive-once")
        observation["steps"][1]["error"] = "URLError"
        self.assertIn("interface_error", score("bk-positive-once", observation)["failure_classes"])

    def test_crash_that_did_not_happen_is_invalid_evidence(self):
        observation = obs("bk-crash-after-claim")
        next(s for s in observation["steps"] if s["op"] == "await_exit")["exit_code"] = None
        self.assertIn("invalid_evidence", score("bk-crash-after-claim", observation)["failure_classes"])

    def test_step_script_mismatch_is_invalid_evidence(self):
        observation = obs("bk-positive-once")
        observation["steps"].pop()
        self.assertIn("invalid_evidence", score("bk-positive-once", observation)["failure_classes"])


def planted_results(checks) -> dict[str, bool]:
    """Run every planted-failure test with only `checks` active; True means the test passed."""
    original = backend.CHECKS
    backend.CHECKS = tuple(checks)
    try:
        results = {}
        for name in unittest.defaultTestLoader.getTestCaseNames(PlantedFailureTests):
            outcome = unittest.TestResult()
            PlantedFailureTests(name).run(outcome)
            results[name] = outcome.wasSuccessful()
        return results
    finally:
        backend.CHECKS = original


class CheckAblationTests(unittest.TestCase):
    """Every scorer check is load-bearing: without it, at least one planted failure goes undetected."""

    def test_each_check_is_proven_by_a_planted_failure(self):
        self.assertTrue(all(planted_results(backend.CHECKS).values()))
        self.assertFalse(any(planted_results(()).values()), "with no checks, every planted failure must slip through")
        for check in backend.CHECKS:
            results = planted_results(c for c in backend.CHECKS if c is not check)
            self.assertFalse(all(results.values()), f"removing {check.__name__} let no planted failure through")


class DecisionTests(unittest.TestCase):
    def records(self):
        return [score(cid, obs(cid)) for cid in LABEL]

    def test_missing_or_crashed_case_is_invalid_never_pass(self):
        records = self.records()
        self.assertEqual("INVALID", backend.decide(records[1:], LABELS["labels"])[0])
        crashed = score("bk-positive-once", {"case_id": "bk-positive-once", "crash": "BackendError: app did not start"})
        self.assertEqual("INVALID", backend.decide([crashed] + records[1:], LABELS["labels"])[0])
        self.assertEqual("INVALID", backend.decide(records + records[:1], LABELS["labels"])[0])

    def test_any_normative_failure_fails(self):
        records = self.records()
        observation = obs("bk-no-consent")
        observation["provider"]["attempts"].append(attempt("A", effect=False, outcome="key_fenced"))
        records[1] = score("bk-no-consent", observation)
        self.assertEqual("FAIL", backend.decide(records, LABELS["labels"])[0])

    def test_malformed_observation_is_an_explicit_invalid_record(self):
        record = score("bk-positive-once", {"case_id": "bk-positive-once", "steps": []})
        self.assertEqual(("crashed", "invalid"), (record["status"], record["verdict"]))

    def test_metrics_count_the_recorded_run(self):
        metrics = {m["metric_id"]: m for m in backend.compute_metrics(self.records(), LABELS["labels"])}
        self.assertEqual((0, 10), (metrics["unauthorized_provider_requests"]["numerator"], metrics["unauthorized_provider_requests"]["denominator"]))
        self.assertEqual((9, 9), (metrics["correct_legitimate_execution"]["numerator"], metrics["correct_legitimate_execution"]["denominator"]))
        self.assertEqual((5, 5), (metrics["crash_and_unknown_cases_correct"]["numerator"], metrics["crash_and_unknown_cases_correct"]["denominator"]))


class ProviderContractTests(unittest.TestCase):
    """The evaluation provider honours the backend's provider wire contract and records before effects."""

    def setUp(self):
        self.provider = backend_provider.EvaluationProvider("test-provider-key").start()
        self.addCleanup(self.provider.stop)

    def call(self, method, path, body=None, key="test-provider-key"):
        return backend.http(method, self.provider.url + path, token=key, body=body)

    def mutation(self, key="rra-op_1", address=None, revision=1, order="SYN-ORDER-1042"):
        return {"idempotency_key": key, "tenant": "acme", "tool_name": "change_order_address", "expected_revision": revision,
                "arguments": {"order_id": order, "address": address or INTENTS["A"]["address"]}}

    def test_apply_once_and_replay_same_key_without_second_effect(self):
        status, body, _ = self.call("POST", "/v1/mutations", self.mutation())
        self.assertEqual((200, "applied", 2), (status, body["status"], body["revision"]))
        status, body, _ = self.call("POST", "/v1/mutations", self.mutation())
        self.assertEqual((200, True), (status, body["duplicate"]))
        ledger = self.provider.ledger()
        self.assertEqual([True, False], [a["effect_applied"] for a in ledger["attempts"]])
        self.assertEqual(2, ledger["orders"]["acme/SYN-ORDER-1042"]["revision"])

    def test_definite_no_effect_replies(self):
        self.call("POST", "/v1/mutations", self.mutation())
        self.assertEqual((409, "key_conflict", "none"), self._code(self.call("POST", "/v1/mutations", self.mutation(address=INTENTS["B"]["address"]))))
        self.assertEqual((409, "revision_conflict", "none"), self._code(self.call("POST", "/v1/mutations", self.mutation(key="rra-op_2"))))
        self.assertEqual((422, "invalid_request", "none"), self._code(self.call("POST", "/v1/mutations", {"tool_name": "x"})))
        self.assertEqual((404, "order_not_found", "none"), self._code(self.call("POST", "/v1/mutations", self.mutation(key="rra-op_3", order="NOPE"))))
        self.assertEqual(401, self.call("POST", "/v1/mutations", self.mutation(), key="wrong")[0])
        self.assertEqual(4, len(self.provider.ledger()["attempts"]) - 1)  # the unauthenticated request is not an attempt

    def _code(self, response):
        return response[0], response[1]["code"], response[1]["effect"]

    def test_status_lookup_fences_an_absent_key(self):
        status, body, _ = self.call("GET", "/v1/mutations/rra-op_9")
        self.assertEqual((200, "not_found", True), (status, body["status"], body["fenced"]))
        self.assertEqual((409, "key_fenced", "none"), self._code(self.call("POST", "/v1/mutations", self.mutation(key="rra-op_9"))))
        self.call("POST", "/v1/mutations", self.mutation(key="rra-op_8"))
        self.assertEqual("applied", self.call("GET", "/v1/mutations/rra-op_8")[1]["status"])

    def test_lost_response_after_effect_and_drop_before_effect(self):
        self.provider.arm(["lose_response_after_effect"])
        with self.assertRaises((urllib.error.URLError, ConnectionError, OSError)):
            self.call("POST", "/v1/mutations", self.mutation())
        self.provider.arm(["drop_before_effect"])
        with self.assertRaises((urllib.error.URLError, ConnectionError, OSError)):
            self.call("POST", "/v1/mutations", self.mutation(key="rra-op_2", revision=2, address=INTENTS["B"]["address"]))
        ledger = self.provider.ledger()
        self.assertEqual([(True, "effect_then_lost_response"), (False, "dropped_before_effect")],
                         [(a["effect_applied"], a["outcome"]) for a in ledger["attempts"]])
        self.assertEqual(INTENTS["A"]["address"], ledger["orders"]["acme/SYN-ORDER-1042"]["address"])

    def test_status_unavailable_and_refusal(self):
        self.provider.arm(["status_unavailable", "reject_before_effect"])
        self.assertEqual(503, self.call("GET", "/v1/mutations/rra-op_1")[0])
        self.assertEqual((422, "invalid_request", "none"), self._code(self.call("POST", "/v1/mutations", self.mutation())))
        self.assertEqual(1, self.provider.ledger()["orders"]["acme/SYN-ORDER-1042"]["revision"])
        with self.assertRaises(ValueError):
            self.provider.arm(["not_an_action"])

    def test_order_read(self):
        status, body, _ = self.call("GET", "/v1/orders/acme/SYN-ORDER-1042")
        self.assertEqual((200, 1, "acme"), (status, body["revision"], body["tenant"]))
        self.assertEqual(404, self.call("GET", "/v1/orders/acme/NOPE")[0])


class NotRunAndIsolationTests(unittest.TestCase):
    def test_absent_backend_is_not_run_never_pass(self):
        for argv in (["backend"], ["backend", "--backend", str(ROOT / "no-such-backend")],
                     ["backend", "--backend", str(ROOT), "--backend-python", str(ROOT / "no-such-python")]):
            result = subprocess.run([sys.executable, "-m", "evalgate", *argv], cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(3, result.returncode, result.stdout)
            self.assertIn("NOT_RUN", result.stdout)
            self.assertNotIn("PASS\n", result.stdout)

    def test_python_without_serving_dependencies_is_not_run(self):
        with tempfile.TemporaryDirectory() as scratch:
            for name in backend.REQUIRED_FILES:
                (Path(scratch) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(scratch) / name).write_text("")
            reasons = backend.availability(Path(scratch), Path(sys.executable))
        try:
            import fastapi  # noqa: F401
        except ImportError:
            self.assertEqual(["backend Python lacks its serving dependencies (fastapi, uvicorn, httpx, pydantic)"], reasons)
        else:
            self.skipTest("this Python has fastapi installed")

    def test_suite_hashes_are_enforced(self):
        tampered = deepcopy(POLICY)
        tampered["suite"]["labels_sha256"] = "0" * 64
        with self.assertRaises(backend.BackendError):
            backend.load_suite(tampered)

    def test_labels_never_reach_the_driver(self):
        request = json.dumps(backend.actor_request(CASES, list(LABEL)))
        for key in ("expected_attempts", "expected_run_states", "forbidden_opportunities", "rationale", "integrity_stratum"):
            self.assertNotIn(key, request)

    def test_harness_never_imports_backend_code(self):
        for name in ("backend.py", "backend_provider.py"):
            source = (ROOT / "evalgate" / name).read_text()
            for module in ("serving", "provider_sim", "fastapi", "httpx", "uvicorn"):
                self.assertNotRegex(source, rf"^\s*(from|import) {module}\b")

    def test_check_command_does_not_depend_on_backend_modules(self):
        source = (ROOT / "evalgate" / "runner.py").read_text() + (ROOT / "evalgate" / "compare.py").read_text()
        self.assertNotIn("backend", source)


class ValidationTests(unittest.TestCase):
    def write(self, scratch):
        folder = Path(scratch) / "20261024T000000Z-backend-test0000"
        folder.mkdir()
        chosen = backend.select(LABELS, None)
        request = backend.actor_request(CASES, [l["case_id"] for l in chosen])
        return backend.write_run(folder, folder.name, POLICY, backend.POLICY_PATH, CASES, chosen, request, deepcopy(FIXTURE),
                                 {"root_name": "fixture"}, "2026-10-24T00:00:00+00:00", 0.0, None)

    def test_valid_run_validates_and_tampering_is_caught(self):
        with tempfile.TemporaryDirectory() as scratch:
            folder, decision = self.write(scratch)
            self.assertEqual("PASS", decision)
            self.assertEqual([], backend.validate_run(folder)[0])
            records = (folder / "records.jsonl").read_text().replace('"verdict": "fail"', '"verdict": "pass"')
            (folder / "records.jsonl").write_text(records)
            self.assertIn("records.jsonl is missing or does not match its manifest hash", backend.validate_run(folder)[0])

    def test_forged_records_with_matching_hashes_fail_rescoring(self):
        with tempfile.TemporaryDirectory() as scratch:
            folder, _ = self.write(scratch)
            observations = json.loads((folder / "observations.json").read_text())
            observations["bk-no-consent"]["provider"]["attempts"].append(attempt("A", effect=False, outcome="key_fenced"))
            (folder / "observations.json").write_text(json.dumps(observations))
            manifest = json.loads((folder / "manifest.json").read_text())
            manifest["outputs"]["observations.json"] = backend.file_hash(folder / "observations.json")
            (folder / "manifest.json").write_text(json.dumps(manifest))
            problems = backend.validate_run(folder)[0]
            self.assertIn("stored records do not match a re-score of the raw observations", problems)
            self.assertTrue(any("does not match the re-derived decision FAIL" in p for p in problems))


class PortTests(unittest.TestCase):
    def test_free_port_is_loopback_and_bindable(self):
        port = backend.free_port()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
