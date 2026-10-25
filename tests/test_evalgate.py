"""Negative controls for the regression gate's own oracle, evidence and contract.

A gate that can be fooled by a friendly trace, a stored verdict or a replayed
result file is not a gate. Every test here must make the harness say no.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from evalgate import compare, runner, scoring, trace

ROOT = Path(__file__).resolve().parents[1]
LABEL_ONLY_KEYS = ("expected_invocations", "state_patch", "rationale", "adjudication", "normative", "family_id",
                   "legit_opportunities", "forbidden_opportunities", "allowed_step_errors", "integrity_stratum")


def rehash(folder: Path, name: str) -> None:
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["outputs"][name] = runner.file_hash(folder / name)
    (folder / "manifest.json").write_text(json.dumps(manifest))


class HarnessFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="evalgate-test-"))
        cls.policy = runner.load_policy()
        cls.cases, cls.labels = runner.load_suite(cls.policy)
        approved = {"git_sha": cls.policy["approved_baseline_sha"], "dirty": False}
        cls.baseline_root = cls.tmp / "approved-baseline"
        cls.baseline_root.mkdir()
        runner.export_revision(ROOT, cls.policy["approved_baseline_sha"], cls.baseline_root)  # the real approved content
        cls.baseline, _ = runner.run(cls.baseline_root, role="baseline", out_root=cls.tmp, candidate_kind="approved-baseline", sut_git=approved)
        cls.candidate, cls.decision = runner.run(ROOT, role="candidate", out_root=cls.tmp, candidate_kind="working-tree")
        output = json.loads((cls.candidate / "driver-output.json").read_text())
        cls.observations = {r["case_id"]: r for r in output["results"]}
        cls.label_by_id = {l["case_id"]: l for l in cls.labels["labels"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def score(self, case_id, observation):
        case = next(c for c in self.cases["cases"] if c["case_id"] == case_id)
        return scoring.score_case(self.label_by_id[case_id], case, observation, self.cases["intents"], self.labels["patches"], "test-run")

    def copy_run(self, source):
        target = Path(tempfile.mkdtemp(dir=self.tmp)) / source.name  # same name keeps run identities consistent
        shutil.copytree(source, target)
        return target


class OracleTests(HarnessFixture):
    def test_unmodified_candidate_passes_with_exact_denominators(self):
        self.assertEqual("PASS", self.decision)
        metrics = {(m["metric_id"], m["stratum"]): m for m in json.loads((self.candidate / "metrics.json").read_text())}
        self.assertEqual(0, metrics["dangerous_dispatch_attempts", "normative"]["numerator"])
        self.assertGreater(metrics["dangerous_dispatch_attempts", "normative"]["denominator"], 30)
        legit = metrics["correct_legitimate_execution", "normative"]
        self.assertEqual(legit["numerator"], legit["denominator"])

    def test_friendly_status_without_effect_fails(self):
        observation = deepcopy(self.observations["legit-grammar-01"])
        observation["invocations"], observation["state_after"] = [], deepcopy(observation["state_before"])
        observation["raw"]["gate_events"] = [e for e in observation["raw"]["gate_events"] if e["type"] != "dispatch_started"]
        self.assertIn("succeeded", [s.get("status") for s in observation["steps"]])
        record = self.score("legit-grammar-01", observation)
        self.assertEqual("fail", record["verdict"])
        self.assertIn("missed_legitimate_execution", record["failure_classes"])

    def test_state_mutated_despite_blocked_trace_fails(self):
        observation = deepcopy(self.observations["danger-missing-consent"])
        allowed = self.observations["legit-grammar-01"]
        observation["invocations"], observation["state_after"] = deepcopy(allowed["invocations"]), deepcopy(allowed["state_after"])
        record = self.score("danger-missing-consent", observation)
        self.assertIn("unauthorized_dispatch", record["failure_classes"])
        self.assertIn("unsafe_effect", record["failure_classes"])
        self.assertIn("invalid_evidence", record["failure_classes"])  # the trace says nothing was dispatched

    def test_trace_that_drops_a_dispatch_is_invalid(self):
        observation = deepcopy(self.observations["legit-grammar-01"])
        observation["raw"]["gate_events"] = [e for e in observation["raw"]["gate_events"] if e["type"] != "dispatch_started"]
        self.assertIn("invalid_evidence", self.score("legit-grammar-01", observation)["failure_classes"])

    def test_wrong_payload_and_duplicate_effects_are_classified(self):
        observation = deepcopy(self.observations["legit-grammar-01"])
        observation["invocations"][0]["arguments"]["zip"] = "11111"
        self.assertIn("wrong_payload", self.score("legit-grammar-01", observation)["failure_classes"])
        observation = deepcopy(self.observations["legit-grammar-01"])
        observation["invocations"].append(deepcopy(observation["invocations"][0]))
        self.assertIn("duplicate_effect", self.score("legit-grammar-01", observation)["failure_classes"])

    def blocked(self, case_id):
        observation = deepcopy(self.observations[case_id])
        observation["invocations"], observation["state_after"] = [], deepcopy(observation["state_before"])
        observation["raw"]["gate_events"] = [e for e in observation["raw"]["gate_events"] if not e["type"].startswith(("dispatch_", "authorized"))]
        for step in observation["steps"]:
            step["invocations_before"] = step["invocations_after"] = 0
        return observation

    def test_block_everything_fails_usefulness(self):
        record = self.score("legit-grammar-01", self.blocked("legit-grammar-01"))
        self.assertEqual(["missed_legitimate_execution"], record["failure_classes"])
        self.assertEqual(1, record["observed"]["gate_false_blocks"])

    def test_missing_and_crashed_cases_are_invalid_not_passing(self):
        self.assertEqual("invalid", self.score("danger-maybe", None)["verdict"])
        self.assertEqual("invalid", self.score("danger-maybe", {"case_id": "danger-maybe", "crash": "ImportError: x"})["verdict"])
        records = [self.score(l["case_id"], self.observations[l["case_id"]]) for l in self.labels["labels"]]
        self.assertEqual("INVALID", scoring.decide(records[1:], self.labels["labels"])[0])
        self.assertEqual("INVALID", scoring.decide(records + records[:1], self.labels["labels"])[0])

    def test_prompt_hiding_an_argument_fails_even_when_effects_match(self):
        observation = deepcopy(self.observations["legit-grammar-01"])
        for event in observation["raw"]["gate_events"]:
            if event["type"] == "prompt":
                event["detail"] = event["detail"].replace('"zip": "00002"', "")
        self.assertEqual(["incomplete_consent_prompt"], self.score("legit-grammar-01", observation)["failure_classes"])

    def test_tau_approval_of_a_prompt_the_user_never_saw_fails(self):
        observation = deepcopy(self.observations["legit-tau-confirm"])
        observation["shown_prompts"] = [{"step": 0, "intent": "A1", "text": "Please confirm."}]
        record = self.score("legit-tau-confirm", observation)
        self.assertIn("incomplete_consent_prompt", record["failure_classes"])
        self.assertTrue(any("never shown verbatim" in d for d in record["details"]))

    # Regression tests for the 2026-10-24 gpt-6-astra review findings.
    def test_invocation_before_consent_fails_even_if_final_effects_match(self):
        observation = deepcopy(self.observations["legit-cancel"])
        steps = observation["steps"]  # propose, respond, dispatch
        steps[0]["invocations_after"] = 1
        steps[1]["invocations_before"] = steps[1]["invocations_after"] = 1
        steps[2]["invocations_before"] = 1
        record = self.score("legit-cancel", observation)
        self.assertIn("unauthorized_dispatch", record["failure_classes"])
        self.assertIn("unsafe_effect", record["failure_classes"])

    def test_prompt_with_values_but_no_field_names_or_operation_fails(self):
        observation = deepcopy(self.observations["legit-grammar-01"])
        for entry in observation["shown_prompts"]:
            entry["text"] = entry["text"].replace('"zip": ', "").replace("op-addr-1", "")
        self.assertIn("incomplete_consent_prompt", self.score("legit-grammar-01", observation)["failure_classes"])

    def test_missing_lifecycle_or_late_dispatch_start_is_invalid(self):
        for mutate in (lambda seen: [e for e in seen if e["type"] != "authorized"],
                       lambda seen: seen[:-1]):
            observation = deepcopy(self.observations["legit-grammar-01"])
            observation["invocations"][0]["trace_at_invoke"] = mutate(observation["invocations"][0]["trace_at_invoke"])
            self.assertIn("invalid_evidence", self.score("legit-grammar-01", observation)["failure_classes"])
        observation = deepcopy(self.observations["legit-grammar-01"])
        observation["raw"]["gate_events"] = [e for e in observation["raw"]["gate_events"] if e["type"] != "dispatch_succeeded"]
        self.assertIn("invalid_evidence", self.score("legit-grammar-01", observation)["failure_classes"])

    def test_missing_read_evidence_cannot_pass_vacuously(self):
        observation = deepcopy(self.observations["read-passthrough-tau"])
        observation["steps"], observation["reads"] = [], []
        record = self.score("read-passthrough-tau", observation)
        self.assertEqual("fail", record["verdict"])
        self.assertIn("missed_legitimate_execution", record["failure_classes"])

    def test_allowed_exception_on_the_wrong_step_is_an_interface_error(self):
        observation = deepcopy(self.observations["danger-nan-argument"])
        self.assertEqual("pass", self.score("danger-nan-argument", observation)["verdict"])
        observation["steps"][1] = {**observation["steps"][1], "error": "ValueError"}
        observation["steps"][1].pop("status", None)
        self.assertIn("interface_error", self.score("danger-nan-argument", observation)["failure_classes"])

    # Regression tests for the second gpt-6-astra review (round 2, findings N2-N5).
    def test_numeric_prefix_or_extended_identifier_in_prompt_fails(self):
        for old, new in (('"amount_usd": 25', '"amount_usd": 250'), ('"amount_usd": 25', '"amount_usd": 25.5'),
                         ("op-credit-1", "op-credit-10"), ("issue_store_credit", "issue_store_credit_v2"),
                ('"amount_usd": 25,', '"amount_usd": 25,000,'), ('"amount_usd": 25,', '"amount_usd": 25\u202f000,'),
                ('"amount_usd": 25,', '"amount_usd": 25 000,'),
                ("Complete arguments:\n{", 'Complete arguments:\n{"New": {"amount_usd": 250, "order_id": "SYN-ORDER-1042"}, "Old":'),
                ("Complete arguments:\n", "Amount to issue: $250\nComplete arguments:\n"),  # round 5: contradictory prose
                ('"amount_usd": 25,', '"amount_usd": 250, "amount_usd": 25,')):  # round 5: duplicate key, last one wins
            with self.subTest(new=new):
                observation = deepcopy(self.observations["legit-store-credit"])
                for entry in observation["shown_prompts"]:
                    entry["text"] = entry["text"].replace(old, new)
                for event in observation["raw"]["gate_events"]:
                    if event["type"] == "prompt":
                        event["detail"] = event["detail"].replace(old, new)
                self.assertIn("incomplete_consent_prompt", self.score("legit-store-credit", observation)["failure_classes"])
        self.assertEqual("pass", self.score("legit-store-credit", deepcopy(self.observations["legit-store-credit"]))["verdict"])

    def test_two_calls_in_one_dispatch_cannot_stand_in_for_a_later_operation(self):
        observation = deepcopy(self.observations["legit-tau-new-operation-same-payload"])
        steps = observation["steps"]  # confirm A1, dispatch A1, confirm A1_op2, dispatch A1_op2
        self.assertEqual([0, 0, 1, 1], [s["invocations_before"] for s in steps])
        steps[1]["invocations_after"] = 2
        for step in steps[2:]:
            step["invocations_before"] = step["invocations_after"] = 2
        record = self.score("legit-tau-new-operation-same-payload", observation)
        self.assertIn("duplicate_effect", record["failure_classes"])
        self.assertIn("missed_legitimate_execution", record["failure_classes"])

    def test_one_approval_cannot_cover_a_second_operation_with_the_same_payload(self):
        observation = deepcopy(self.observations["legit-tau-new-operation-same-payload"])
        events = observation["raw"]["gate_events"]
        second = [next(e for e in events[events.index(next(x for x in events if x["type"] == "dispatch_started")):] if e["type"] == kind)
                  for kind in ("prompt", "authorized")]  # the fresh prompt and approval given for A1_op2
        for event in second:
            events.remove(event)
        observation["invocations"][1]["trace_at_invoke"] = [{"type": e["type"], "fingerprint": e["fingerprint"]} for e in events
                                                            ][:len(observation["invocations"][1]["trace_at_invoke"]) - 2]
        observation["shown_prompts"] = observation["shown_prompts"][:1]
        self.assertIn("invalid_evidence", self.score("legit-tau-new-operation-same-payload", observation)["failure_classes"])

    def test_reordered_step_ranges_are_invalid(self):
        observation = deepcopy(self.observations["legit-two-operations"])
        steps = observation["steps"]  # propose, respond, dispatch A1, propose, respond, dispatch A2
        steps[2]["invocations_before"], steps[2]["invocations_after"] = 1, 2
        steps[5]["invocations_before"], steps[5]["invocations_after"] = 0, 1
        self.assertIn("invalid_evidence", self.score("legit-two-operations", observation)["failure_classes"])

    def test_snapshot_at_invoke_must_match_the_final_trace(self):
        observation = deepcopy(self.observations["legit-grammar-01"])
        events = observation["raw"]["gate_events"]
        authorized = next(e for e in events if e["type"] == "authorized")
        events.remove(authorized)
        outcome = next(i for i, e in enumerate(events) if e["type"] == "dispatch_succeeded")
        events.insert(outcome, {**authorized, "type": "blocked", "title": "Unconfirmed dispatch blocked"})
        self.assertIn("invalid_evidence", self.score("legit-grammar-01", observation)["failure_classes"])

    def test_forged_or_misdirected_read_record_is_invalid(self):
        other = deepcopy(self.observations["read-other-order-tau"]["reads"][0])
        for forged in ({"tool": "bogus", "arguments": {}, "observation": "forged"},
                       {**other, "arguments": {"order_id": "SYN-ORDER-1042"}}):
            with self.subTest(forged=forged["tool"]):
                observation = deepcopy(self.observations["read-other-order-tau"])
                observation["reads"] = [forged]
                record = self.score("read-other-order-tau", observation)
                self.assertEqual("fail", record["verdict"])
                self.assertIn("invalid_evidence", record["failure_classes"])

    def test_numeric_type_changes_are_distinct_payloads(self):
        self.assertNotEqual(scoring.payload_hash("t", {"x": 1}), scoring.payload_hash("t", {"x": 1.0}))
        self.assertNotEqual(scoring.payload_hash("t", {"x": 1}), scoring.payload_hash("t", {"x": True}))


class LeakageTests(HarnessFixture):
    def test_actor_request_contains_no_scorer_labels(self):
        sent = (self.candidate / "request.json").read_text()
        request = json.loads(sent)
        self.assertEqual(request, runner.actor_request(self.cases, ROOT.resolve()))
        for case in request["cases"]:
            self.assertLessEqual(set(case), set(runner.ACTOR_CASE_KEYS))
        for key in LABEL_ONLY_KEYS:
            self.assertNotIn(f'"{key}"', sent)
        for label in self.labels["labels"]:
            self.assertNotIn(label["rationale"], sent)


class EvidenceTests(HarnessFixture):
    def assertInvalid(self, folder, fragment):
        problems, _, _ = runner.validate_run(folder)
        self.assertTrue(any(fragment in p for p in problems), problems)

    def test_clean_runs_validate(self):
        self.assertEqual([], runner.validate_run(self.candidate)[0])
        self.assertEqual([], runner.validate_run(self.baseline)[0])

    def test_truncated_records_fail_hash_check(self):
        folder = self.copy_run(self.candidate)
        text = (folder / "records.jsonl").read_text()
        (folder / "records.jsonl").write_text(text[: len(text) // 2])
        self.assertInvalid(folder, "does not match its manifest hash")

    def test_forged_verdict_with_recomputed_hash_fails_rescore(self):
        folder = self.copy_run(self.candidate)
        lines = (folder / "records.jsonl").read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if json.loads(line)["verdict"] == "fail")
        record = json.loads(lines[index])
        record["verdict"], record["failure_classes"], record["details"] = "pass", [], []
        lines[index] = json.dumps(record)
        (folder / "records.jsonl").write_text("\n".join(lines) + "\n")
        rehash(folder, "records.jsonl")
        self.assertInvalid(folder, "does not match a re-score of raw evidence")

    def test_dropped_duplicate_and_renamed_records_are_invalid(self):
        for mutate, fragment in ((lambda ls: ls[1:], "do not match the scheduled cases"),
                                 (lambda ls: ls + ls[:1], "duplicate case records"),
                                 (lambda ls: [ls[0].replace('"legit-grammar-01"', '"legit-grammar-99"')] + ls[1:], "scheduled cases")):
            with self.subTest(fragment=fragment):
                folder = self.copy_run(self.candidate)
                lines = (folder / "records.jsonl").read_text().splitlines()
                (folder / "records.jsonl").write_text("\n".join(mutate(lines)) + "\n")
                rehash(folder, "records.jsonl")
                self.assertInvalid(folder, fragment)

    def test_nonfinite_unknown_version_and_private_fields_are_rejected(self):
        folder = self.copy_run(self.candidate)
        (folder / "metrics.json").write_text((folder / "metrics.json").read_text().replace('"value": 0.0', '"value": NaN', 1))
        rehash(folder, "metrics.json")
        self.assertInvalid(folder, "not strict JSON")
        folder = self.copy_run(self.candidate)
        manifest = json.loads((folder / "manifest.json").read_text())
        manifest["harness"]["version"] = "evalgate-harness-v0"
        (folder / "manifest.json").write_text(json.dumps(manifest))
        self.assertInvalid(folder, "different harness version")
        folder = self.copy_run(self.candidate)
        output = json.loads((folder / "driver-output.json").read_text())
        output["results"][0]["api_key"] = "synthetic-not-a-secret"
        (folder / "driver-output.json").write_text(json.dumps(output))
        rehash(folder, "driver-output.json")
        self.assertInvalid(folder, "private fields")

    def test_imported_gate_must_be_the_hashed_gate(self):
        folder = self.copy_run(self.candidate)
        manifest = json.loads((folder / "manifest.json").read_text())
        manifest["sut"]["components"]["rra/gate.py"] = "0" * 64
        (folder / "manifest.json").write_text(json.dumps(manifest))
        self.assertInvalid(folder, "rra.gate: imported module is missing or is not the file hashed")

    def edit_json(self, folder, name, mutate, rehash_output=True):
        value = json.loads((folder / name).read_text())
        value = mutate(value) or value
        (folder / name).write_text(json.dumps(value))
        if rehash_output:
            rehash(folder, name)

    def test_request_traces_raw_results_and_protocol_are_validated(self):
        folder = self.copy_run(self.candidate)
        (folder / "request.json").write_text("not JSON"); rehash(folder, "request.json")
        self.assertInvalid(folder, "not strict JSON")
        folder = self.copy_run(self.candidate)
        self.edit_json(folder, "request.json", lambda r: r["cases"][0].update(steps=[]))
        self.assertInvalid(folder, "does not match the frozen actor-visible suite")
        folder = self.copy_run(self.candidate)
        self.edit_json(folder, "driver-output.json", lambda o: o.update(results=[dict(o["results"][0], crash="x")] + o["results"]))
        self.assertInvalid(folder, "exactly once")
        folder = self.copy_run(self.candidate)
        self.edit_json(folder, "driver-output.json", lambda o: o.update(protocol="evalgate-driver-v0"))
        self.assertInvalid(folder, "unknown driver protocol")
        folder = self.copy_run(self.candidate)
        lines = (folder / "traces.jsonl").read_text().splitlines()
        (folder / "traces.jsonl").write_text("\n".join(lines[1:]) + "\n"); rehash(folder, "traces.jsonl")
        self.assertInvalid(folder, "do not regenerate")

    def test_provenance_harness_termination_and_decision_are_checked(self):
        folder = self.copy_run(self.candidate)  # both sides emptied, so they still agree with each other
        self.edit_json(folder, "driver-output.json", lambda o: o.update(loaded={}))
        self.edit_json(folder, "manifest.json", lambda m: m["sut"].update(loaded_modules={}, components={}), rehash_output=False)
        self.assertInvalid(folder, "imported module is missing")
        for mutate, fragment in ((lambda m: m["harness"].update(files_sha256="0" * 64), "different harness files"),
                                 (lambda m: m.update(termination="incomplete"), "did not complete"),
                                 (lambda m: m.update(decision="FAIL"), "does not match the re-derived decision")):
            with self.subTest(fragment=fragment):
                folder = self.copy_run(self.candidate)
                self.edit_json(folder, "manifest.json", mutate, rehash_output=False)
                self.assertInvalid(folder, fragment)

    def test_comparison_requires_valid_baseline_and_expected_clean_candidate(self):
        folder, decision = compare.compare(self.baseline, self.candidate, self.tmp, expect_candidate_sha="f" * 40)
        self.assertEqual("INVALID", decision)
        self.assertIn("not the expected", (folder / "comparison.md").read_text())
        crashed = self.copy_run(self.baseline)
        def crash(o):
            o["results"][0] = {"case_id": o["results"][0]["case_id"], "crash": "ImportError: synthetic"}
        self.edit_json(crashed, "driver-output.json", crash)
        problems, _, _ = runner.validate_run(crashed)
        self.assertTrue(problems)  # its stored records/decision no longer match the raw evidence
        self.assertEqual("INVALID", compare.compare(crashed, self.candidate, self.tmp)[1])

    def trace_invalid_copy(self, source):
        """A run whose raw gate trace breaks trace-v1, with every derived file honestly regenerated."""
        folder = self.copy_run(source)
        output = json.loads((folder / "driver-output.json").read_text())
        result = next(r for r in output["results"] if r.get("raw", {}).get("gate_events"))
        result["raw"]["gate_events"][0]["seq"] = "not a sequence"
        (folder / "driver-output.json").write_text(json.dumps(output)); rehash(folder, "driver-output.json")
        events = runner.map_traces(folder.name, self.labels, {r["case_id"]: r for r in output["results"]}, output["classifier_version"])
        self.assertTrue(trace.validate_trace(events))
        (folder / "traces.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events)); rehash(folder, "traces.jsonl")
        self.edit_json(folder, "manifest.json", lambda m: m.update(decision="INVALID"), rehash_output=False)  # the re-derived decision
        return folder

    def test_trace_invalid_runs_cannot_compare_pass(self):
        self.assertEqual("INVALID", compare.compare(self.baseline, self.trace_invalid_copy(self.candidate), self.tmp)[1])
        self.assertEqual("INVALID", compare.compare(self.trace_invalid_copy(self.baseline), self.candidate, self.tmp)[1])
        from evalgate.__main__ import main
        self.assertEqual(2, main(["validate", str(self.trace_invalid_copy(self.candidate))]))  # standalone validation too

    def test_claimed_commit_must_match_the_evaluated_content(self):
        approved = {"git_sha": self.policy["approved_baseline_sha"], "dirty": False}
        relabelled, _ = runner.run(ROOT, role="baseline", out_root=self.tmp, candidate_kind="approved-baseline", sut_git=approved)
        manifest = json.loads((relabelled / "manifest.json").read_text())
        self.assertNotEqual(manifest["sut"]["components"], json.loads((self.baseline / "manifest.json").read_text())["sut"]["components"])
        folder, decision = compare.compare(relabelled, self.candidate, self.tmp)
        self.assertEqual("INVALID", decision)
        self.assertIn("not the content of", (folder / "comparison.md").read_text())
        claimed = self.policy["approved_baseline_sha"]  # this tree's rra/gate.py differs from that commit's
        folder, decision = compare.compare(self.baseline, self.candidate, self.tmp, expect_candidate_sha=claimed)
        self.assertEqual("INVALID", decision)
        self.assertIn("not the content of", (folder / "comparison.md").read_text())

    def test_mismatched_suite_hash_is_rejected(self):
        folder = self.copy_run(self.candidate)
        manifest = json.loads((folder / "manifest.json").read_text())
        manifest["suite"]["labels_sha256"] = "0" * 64
        (folder / "manifest.json").write_text(json.dumps(manifest))
        self.assertInvalid(folder, "different suite")

    def test_comparison_rejects_unapproved_baseline_and_swapped_roles(self):
        other, _ = runner.run(ROOT, role="baseline", out_root=self.tmp, candidate_kind="approved-baseline",
                              sut_git={"git_sha": "1" * 40, "dirty": False})
        folder, decision = compare.compare(other, self.candidate, self.tmp)
        self.assertEqual("INVALID", decision)
        self.assertIn("not the approved", (folder / "comparison.md").read_text())
        self.assertEqual("INVALID", compare.compare(self.candidate, self.baseline, self.tmp)[1])
        self.assertEqual("PASS", compare.compare(self.baseline, self.candidate, self.tmp)[1])

    def test_evidence_directories_are_never_reused(self):
        folder = runner.new_run_dir(self.tmp, "candidate")
        with self.assertRaises(FileExistsError):
            folder.mkdir()
        with self.assertRaises(FileExistsError):
            (folder / "x").write_text("a"); runner._write(folder, "x", "b")

    def test_suite_edits_require_a_policy_update(self):
        policy = dict(self.policy, suite=dict(self.policy["suite"], labels_sha256="0" * 64))
        with self.assertRaises(runner.EvaluationError):
            runner.load_suite(policy)


class ContractTests(HarnessFixture):
    def test_schema_enums_match_the_checker(self):
        schema = json.loads((ROOT / "contracts/trace-v1.schema.json").read_text())
        defs = schema["$defs"]
        self.assertEqual(set(trace.CORE_EVENT_TYPES), set(defs["core_event_type"]["enum"]))
        self.assertEqual(set(trace.BLOCKED_REASONS), set(defs["payload_blocked"]["properties"]["reason"]["enum"]))
        self.assertEqual(set(trace.EVIDENCE_CLASSES), set(schema["properties"]["evidence_class"]["enum"]))
        self.assertEqual(trace.REQUIRED_KEYS, set(schema["required"]))
        self.assertEqual(trace.ENVELOPE_KEYS, set(schema["properties"]))
        self.assertEqual(set(trace.RECONCILED_OUTCOMES), set(defs["payload_reconciled"]["properties"]["outcome"]["enum"]))

    def test_real_gate_traces_conform_and_cover_every_core_event(self):
        events = [json.loads(line) for line in (self.candidate / "traces.jsonl").read_text().splitlines()]
        self.assertEqual([], trace.validate_trace(events))
        self.assertEqual(set(trace.CORE_EVENT_TYPES), {e["event_type"] for e in events} & set(trace.CORE_EVENT_TYPES))
        reasons = {e["payload"]["reason"] for e in events if e["event_type"] == "blocked"}
        self.assertNotIn("unmapped_legacy_reason", reasons)

    def test_invalid_events_are_rejected(self):
        good = json.loads((self.candidate / "traces.jsonl").read_text().splitlines()[0])
        for change, fragment in (({"event_type": "dispatch_maybe"}, "unknown event_type"), ({"payload_hash": "abc"}, "payload_hash"),
                                 ({"evidence_class": "vibes"}, "evidence_class"), ({"debug": True}, "disallowed fields"),
                                 ({"sequence": 0}, "sequence")):
            with self.subTest(change=change):
                self.assertTrue(any(fragment in p for p in trace.validate_event({**good, **change})))
        serving = json.loads((ROOT / "contracts/examples/serving-illustrative.jsonl").read_text().splitlines()[2])
        self.assertEqual([], trace.validate_event(serving))
        for change, fragment in ((lambda e: [e.pop("operation_id"), e.pop("prompt_id")], "require operation_id and prompt_id"),
                                 (lambda e: e.update(occurred_at="garbage"), "UTC timestamp"),
                                 (lambda e: e["payload"].update(classifier_version=False), "string classifier_version"),
                                 (lambda e: e["payload"].update(debug=True), "disallowed fields"),
                                 (lambda e: e["source"].update(component="somewhere"), "invalid source")):
            with self.subTest(fragment=fragment):
                event = deepcopy(serving); change(event)
                self.assertTrue(any(fragment in p for p in trace.validate_event(event)), trace.validate_event(event))
        events = [json.loads(line) for line in (self.candidate / "traces.jsonl").read_text().splitlines()]
        sample = {kind: next(e for e in events if e["event_type"] == kind) for kind in ("authorized", "dispatch_uncertain")}
        for kind, change, fragment in (
                ("authorized", lambda e: e["source"].update(raw_sequence="nonsense"), "invalid source"),
                ("authorized", lambda e: e["source"].update(raw_sequence=0), "invalid source"),
                ("authorized", lambda e: e["source"].update(raw_title="x" * 129), "invalid source"),
                ("authorized", lambda e: e["source"].update(raw_type=7), "invalid source"),
                ("authorized", lambda e: e["payload"].update(consent_binding_version=3), "consent_binding_version"),
                ("authorized", lambda e: e["payload"].update(classifier_version="x" * 97), "classifier_version"),
                ("dispatch_uncertain", lambda e: e["payload"].update(error_class=7), "error_class")):
            with self.subTest(kind=kind, fragment=fragment):
                event = deepcopy(sample[kind]); change(event)
                self.assertTrue(any(fragment in p for p in trace.validate_event(event)), trace.validate_event(event))
        missing_hash = {k: v for k, v in good.items() if k != "payload_hash"}
        self.assertTrue(trace.validate_event(missing_hash))
        self.assertTrue(any("duplicate" in p for p in trace.validate_trace([good, good])))

    def test_evaluation_schema_matches_emitted_records(self):
        schema = json.loads((ROOT / "contracts/evaluation-v1.schema.json").read_text())["$defs"]
        self.assertEqual(set(scoring.FAILURE_CLASSES), set(schema["failure_class"]["enum"]))
        records = [json.loads(line) for line in (self.candidate / "records.jsonl").read_text().splitlines()]
        case = schema["case_record"]
        for record in records:
            self.assertLessEqual(set(case["required"]), set(record))
            self.assertLessEqual(set(record), set(case["properties"]))
            self.assertEqual(set(case["properties"]["observed"]["required"]), set(record["observed"]))
            for item in record["observed"]["invocations"]:
                self.assertIn(item["outcome"], case["properties"]["observed"]["properties"]["invocations"]["items"]["properties"]["outcome"]["enum"])
        for metric in json.loads((self.candidate / "metrics.json").read_text()):
            self.assertEqual(set(schema["metric"]["required"]), set(metric))
            self.assertIn(metric["metric_id"], schema["metric"]["properties"]["metric_id"]["enum"])
        manifest = json.loads((self.candidate / "manifest.json").read_text())
        self.assertLessEqual(set(schema["run_manifest"]["required"]), set(manifest))
        folder, _ = compare.compare(self.baseline, self.candidate, self.tmp)
        result = json.loads((folder / "comparison.json").read_text())
        self.assertEqual(set(schema["comparison"]["required"]), set(result))

    def test_invalid_examples_fail_for_their_stated_reason(self):
        folder = ROOT / "contracts/examples/invalid"
        expected = json.loads((folder / "expected.json").read_text())
        self.assertEqual(set(expected), {p.name for p in folder.glob("*.jsonl")})
        for name, spec in expected.items():
            with self.subTest(name=name):
                problems, _ = trace.check_file(folder / name)
                self.assertTrue(any(spec["expected_problem"] in p for p in problems), problems)

    def test_trace_check_reports_consistent_or_invalid_never_pass(self):
        from evalgate.__main__ import main
        for path in sorted((ROOT / "contracts/examples").glob("*.jsonl")):
            with self.subTest(path=path.name):
                self.assertEqual(0, main(["trace-check", str(path)]))
        self.assertEqual(2, main(["trace-check", str(ROOT / "contracts/examples/invalid/empty.jsonl")]))
        self.assertEqual(2, main(["trace-check", str(self.tmp / "no-such-trace.jsonl")]))
        result = subprocess.run([sys.executable, "-m", "evalgate", "trace-check", str(ROOT / "contracts/examples/serving-success.jsonl")],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertIn("CONSISTENT", result.stdout)
        self.assertIn("This is not a PASS", result.stdout)

    def test_ordering_rules_apply_to_offline_gate_traces_too(self):
        events = [json.loads(line) for line in (self.candidate / "traces.jsonl").read_text().splitlines()]
        stream = [e for e in events if e["trace_id"] == "legit-grammar-01"]
        self.assertEqual([], trace.validate_trace(stream))
        without_consent = [e for e in stream if e["event_type"] != "authorized"]
        self.assertTrue(any("without a fresh authorization" in p for p in trace.validate_trace(without_consent)))
        without_outcome = [e for e in stream if e["event_type"] != "dispatch_succeeded"]
        self.assertTrue(any("no recorded outcome" in p for p in trace.validate_trace(without_outcome)))

    def test_examples_validate(self):
        for path in sorted((ROOT / "contracts/examples").glob("*.jsonl")):
            with self.subTest(path=path.name):
                events = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual([], trace.validate_trace(events))


class ServiceTraceTests(unittest.TestCase):
    """Regression tests for the gpt-6-astra review of the service-trace checker (findings 1-3, 5-10)."""
    H, H2 = "9" * 64, "a" * 64

    def event(self, kind, op="op-addr-1", hashed=None, prompt_id=None, **payload):
        event = {"schema_version": "trace-v1", "event_type": kind, "evidence_class": "serving_observation", "run_id": "run-1",
                 "trace_id": "run-1", "session_id": "sess-1", "occurred_at": "2026-11-02T18:04:05Z", "payload": payload,
                 "source": {"component": "backend.service", "mapping_version": "backend-outbox-map-v1"}}
        if op:
            event["operation_id"] = op
        if hashed or kind in ("prompt", "authorized", "rejected", "invalidated", "dispatch_started", "dispatch_succeeded",
                              "dispatch_uncertain", "serving.reconciled"):
            event["payload_hash"] = hashed or self.H
        if prompt_id:
            event["prompt_id"] = prompt_id
        return event

    def stream(self, *events, finish=True):
        events = [self.event("serving.run_accepted", op=None)] + list(events)
        if finish:
            events.append(self.event("serving.run_finished", op=None))
        for index, event in enumerate(events, 1):
            event["sequence"], event["event_id"] = index, f"evt-{index:04d}"
        return events

    def consented(self, op="op-addr-1", hashed=None, prompt_id="prm-1", outcome="dispatch_succeeded"):
        tool = {"tool_name": "modify_pending_order_address"}
        return [self.event("prompt", op, hashed, prompt_id, prompt_sha256="8" * 64, **tool),
                self.event("authorized", op, hashed, prompt_id, classifier_version="v5"),
                self.event("dispatch_started", op, hashed, **tool), self.event(outcome, op, hashed, **tool)]

    def assertProblem(self, events, fragment=""):
        problems = trace.validate_trace(events)
        self.assertTrue(problems and any(fragment in p for p in problems), problems)

    def test_control_streams_are_consistent(self):
        self.assertEqual([], trace.validate_trace(self.stream(*self.consented())))
        unknown = self.consented(outcome="dispatch_uncertain") + [self.event("serving.reconciled", outcome="effect_confirmed")]
        self.assertEqual([], trace.validate_trace(self.stream(*unknown, *self.consented(op="op-addr-2", prompt_id="prm-2"))))

    def test_reconciling_another_payload_does_not_resolve_an_unknown_outcome(self):
        events = self.consented(outcome="dispatch_uncertain") + [self.event("serving.reconciled", hashed=self.H2, outcome="effect_confirmed")]
        self.assertProblem(self.stream(*events, *self.consented(op="op-addr-2", prompt_id="prm-2")), "reconcil")

    def test_refusal_or_block_without_operation_id_revokes_the_approval(self):
        for kind, payload in (("invalidated", {"reason": "revoked"}), ("rejected", {}), ("blocked", {"reason": "stale_consent"})):
            with self.subTest(kind=kind):
                prompt, authorized, started, outcome = self.consented()
                self.assertProblem(self.stream(prompt, authorized, self.event(kind, op=None, hashed=self.H, **payload), started, outcome))

    def test_only_the_current_prompt_can_be_approved(self):
        tool = {"tool_name": "modify_pending_order_address"}
        old = self.event("prompt", prompt_id="prm-1", prompt_sha256="8" * 64, **tool)
        new = self.event("prompt", hashed=self.H2, prompt_id="prm-2", prompt_sha256="7" * 64, **tool)
        _, authorized, started, outcome = self.consented()
        self.assertProblem(self.stream(old, new, authorized, started, outcome), "prompt")
        self.assertProblem(self.stream(old, self.event("rejected"), authorized, started, outcome), "prompt")

    def test_tool_names_must_agree_within_an_operation(self):
        prompt, authorized, started, outcome = self.consented()
        started["payload"]["tool_name"] = "cancel_pending_order"
        self.assertProblem(self.stream(prompt, authorized, started, outcome), "tool")

    def test_serving_prompts_must_carry_the_shown_text_hash(self):
        prompt, *rest = self.consented()
        del prompt["payload"]["prompt_sha256"]
        self.assertProblem(self.stream(prompt, *rest), "prompt_sha256")

    def test_dropped_lines_are_detected_and_empty_input_is_never_consistent(self):
        events = self.stream(*self.consented())
        self.assertProblem(events[:3])  # tail cut between complete lines, before any attempt
        self.assertProblem(events[:2] + events[3:])  # a middle line dropped
        self.assertProblem([])

    def test_extension_events_may_use_any_payload(self):
        self.assertEqual([], trace.validate_event(dict(self.stream()[0], payload={"prompt_sha256": None})))

    def write(self, text):
        folder = Path(tempfile.mkdtemp(prefix="evalgate-trace-"))
        self.addCleanup(shutil.rmtree, folder)
        (folder / "trace.jsonl").write_text(text, encoding="utf-8")
        return folder / "trace.jsonl"

    def test_malformed_values_duplicate_keys_and_framing_fail_closed_without_crashing(self):
        from evalgate.__main__ import main
        lines = [json.dumps(e) for e in self.stream(*self.consented())]
        duplicate = lines[-2].replace('"event_type": "dispatch_succeeded"', '"event_type": "dispatch_uncertain", "event_type": "dispatch_succeeded"')
        bad_key = json.loads(lines[1]); bad_key["operation_id"] = ["op"]
        for text in ("null\n", "[]\n", "\n".join(lines[:-2] + [duplicate, lines[-1]]) + "\n",
                     "\u2028".join(lines) + "\n", "\n".join(lines[:1] + [json.dumps(bad_key)] + lines[2:]) + "\n"):
            with self.subTest(text=text[:40]):
                problems, _ = trace.check_file(self.write(text))
                self.assertTrue(problems)
                self.assertEqual(2, main(["trace-check", str(self.write(text))]))
        titled = [json.loads(line) for line in lines]
        titled[1]["source"]["raw_title"] = "line\u2028separator"
        self.assertEqual([], trace.check_file(self.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in titled)))[0])
        self.assertEqual([], trace.check_file(self.write("\r\n".join(lines) + "\r\n"))[0])


class ReproducibilityTests(HarnessFixture):
    def test_fixed_inputs_give_identical_verdicts_records_and_traces(self):
        again, decision = runner.run(ROOT, role="candidate", out_root=self.tmp, candidate_kind="working-tree")
        self.assertEqual(self.decision, decision)
        def stable(folder, name):
            text = (folder / name).read_text().replace(folder.name, "<run>")
            return [json.loads(line) for line in text.splitlines()] if name.endswith(".jsonl") else json.loads(text)
        for name in ("records.jsonl", "traces.jsonl", "metrics.json", "driver-output.json"):
            with self.subTest(name=name):
                self.assertEqual(stable(self.candidate, name), stable(again, name))
        first, second = (compare.compare(self.baseline, run, self.tmp) for run in (self.candidate, again))
        self.assertEqual(first[1], second[1])
        self.assertEqual(*(json.loads((folder / "comparison.json").read_text())["changed_cases"] for folder, _ in (first, second)))


class TerminationTests(unittest.TestCase):
    """A hung or crashed system under test must end in an explicit INVALID result, never a hang or a silent omission."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="evalgate-termination-"))
        policy = json.loads(runner.POLICY_PATH.read_text())
        policy["driver_timeout_seconds"] = 3
        cls.policy_path = cls.tmp / "policy-short-timeout.json"
        cls.policy_path.write_text(json.dumps(policy))
        cls.labels = runner.load_suite(runner.load_policy(cls.policy_path))[1]["labels"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def sut(self, name, appended_to_gate):
        root = self.tmp / name
        for package in ("rra", "study", "adapters"):
            shutil.copytree(ROOT / package, root / package, ignore=shutil.ignore_patterns("__pycache__"))
        with open(root / "rra/gate.py", "a", encoding="utf-8") as handle:
            handle.write("\n" + appended_to_gate + "\n")
        return root

    def evaluate(self, root):
        started = time.monotonic()
        folder, decision = runner.run(root, role="candidate", out_root=self.tmp / "runs", candidate_kind="mutant", policy_path=self.policy_path)
        elapsed = time.monotonic() - started
        records = [json.loads(line) for line in (folder / "records.jsonl").read_text().splitlines()]
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual("INVALID", decision)
        self.assertEqual(sorted(l["case_id"] for l in self.labels), sorted(r["case_id"] for r in records))  # one explicit record per case
        self.assertNotIn("pass", {r["verdict"] for r in records if r["status"] != "completed"})
        return elapsed, records, manifest, folder

    def test_hung_driver_times_out_as_invalid(self):
        elapsed, records, manifest, folder = self.evaluate(self.sut("hang", "import time as _t\n_t.sleep(120)"))
        self.assertLess(elapsed, 30)
        self.assertEqual("incomplete", manifest["termination"])
        self.assertEqual({"missing"}, {r["status"] for r in records})
        self.assertIn("timed out", " ".join(manifest["reasons"]))
        problems, _, _ = runner.validate_run(folder, self.policy_path)
        self.assertTrue(any("did not complete" in p for p in problems))

    def test_crashed_driver_process_is_invalid(self):
        _, records, manifest, _ = self.evaluate(self.sut("exit", "import os as _o\n_o._exit(3)"))
        self.assertEqual("incomplete", manifest["termination"])
        self.assertEqual({"infrastructure_interruption"}, {c for r in records for c in r["failure_classes"]})

    def test_malformed_driver_output_is_invalid_not_a_harness_crash(self):
        for name, output in (("list", "[]"), ("object", "{}"), ("results", '{"protocol": "evalgate-driver-v1", "loaded": {}, "results": [1]}')):
            with self.subTest(output=output):
                code = f"import os as _o, sys as _s\n_s.stdout.write({output!r}); _s.stdout.flush(); _o._exit(0)"
                _, records, manifest, _ = self.evaluate(self.sut(f"malformed-{name}", code))
                self.assertEqual("incomplete", manifest["termination"])
                self.assertEqual({"missing"}, {r["status"] for r in records})

    def test_per_case_crash_is_an_explicit_invalid_record(self):
        _, records, manifest, _ = self.evaluate(self.sut("ctor", "class ConfirmationGate(ConfirmationGate):\n"
                                                                  "    def __init__(self, *args, **kwargs):\n"
                                                                  "        raise RuntimeError('synthetic constructor crash')"))
        self.assertEqual("complete", manifest["termination"])
        crashed = [r for r in records if r["status"] == "crashed"]
        self.assertTrue(crashed)
        self.assertTrue(all(r["verdict"] == "invalid" and r["failure_classes"] == ["interface_error"] for r in crashed))


class IsolationTests(unittest.TestCase):
    def test_driver_and_runner_agree_on_sut_modules(self):
        from evalgate import driver
        self.assertEqual(driver.SUT_MODULES, runner.SUT_MODULES)
        self.assertEqual(driver.PROTOCOL, runner.DRIVER_PROTOCOL)

    def test_harness_import_loads_no_provider_runtime(self):
        script = ("import sys, evalgate.__main__; "
                  "assert not any(n == 'openai' or n.startswith(('openai.', 'tau_bench', 'rra', 'study', 'adapters')) for n in sys.modules), "
                  "sorted(n for n in sys.modules if n.startswith(('rra', 'study', 'adapters')))")
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
