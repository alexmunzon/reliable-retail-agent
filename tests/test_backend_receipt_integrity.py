"""Exposed artifact-integrity regressions; these do not attest a fresh execution."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from evalgate import backend
from tests import test_evalgate_backend as recorded


class ReceiptIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "20261025T000000Z-backend-integrity"
        self.folder.mkdir()
        chosen = backend.select(recorded.LABELS, None)
        _, decision = backend.write_run(
            self.folder, self.folder.name, recorded.POLICY, backend.POLICY_PATH,
            recorded.CASES, chosen, backend.actor_request(recorded.CASES, list(recorded.LABEL)),
            recorded.v2_fixture(), {"root_name": "fixture"}, "2026-10-25T00:00:00+00:00", 0.0, None)
        self.assertEqual("PASS", decision)

    def manifest(self):
        return json.loads((self.folder / "manifest.json").read_text())

    def write_manifest(self, value):
        (self.folder / "manifest.json").write_text(json.dumps(value))

    def replace_output(self, name, content):
        (self.folder / name).write_text(content)
        manifest = self.manifest()
        manifest["outputs"][name] = backend.file_hash(self.folder / name)
        self.write_manifest(manifest)

    def assert_invalid(self, fragment=None):
        problems, _ = backend.validate_run(self.folder)
        self.assertTrue(problems)
        if fragment:
            self.assertIn(fragment, " ".join(problems))

    def test_genuine_control_and_unverified_execution_disclosure(self):
        self.assertEqual([], backend.validate_run(self.folder)[0])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, backend.main_validate(SimpleNamespace(run_dir=str(self.folder))))
        self.assertIn("unverified backend provenance", output.getvalue())
        self.assertIn("execution is not independently attested", output.getvalue())

    def test_hashed_but_empty_truncated_changed_reordered_and_non_json_exports_fail(self):
        original = (self.folder / "traces.jsonl").read_text()
        lines = original.splitlines(keepends=True)
        changed = json.loads(lines[0])
        changed["event_id"] = "invented"
        variants = {"empty": "", "truncated": "".join(lines[:-1]),
                    "changed": json.dumps(changed) + "\n" + "".join(lines[1:]),
                    "reordered": "".join(reversed(lines)), "non-json": "NOT JSON\n"}
        for name, content in variants.items():
            with self.subTest(name=name):
                self.replace_output("traces.jsonl", content)
                self.assert_invalid()

    def test_wrong_declared_policy_suite_schema_and_version_fail(self):
        original = self.manifest()
        for section, key in (("policy", "id"), ("policy", "sha256"), ("suite", "id"),
                             ("suite", "cases_sha256"), ("suite", "labels_sha256"),
                             ("harness", "backend_harness_version")):
            with self.subTest(section=section, key=key):
                changed = deepcopy(original)
                changed[section][key] = "incorrect"
                self.write_manifest(changed)
                self.assert_invalid()
        changed = deepcopy(original)
        changed["schema_version"] = "unknown"
        self.write_manifest(changed)
        self.assert_invalid()

    def test_forged_user_facing_summary_and_reasons_fail(self):
        self.replace_output("summary.md", "# PASS: all 25 cases were independently verified\n")
        self.assert_invalid("summary does not regenerate")
        changed = self.manifest()
        changed["reasons"] = ["invented reason"]
        self.write_manifest(changed)
        self.assert_invalid("manifest reasons")

    def test_extra_missing_or_wrong_shape_observations_fail(self):
        original = json.loads((self.folder / "observations.json").read_text())
        extra = deepcopy(original)
        extra["unscheduled-effect"] = {"provider": {"attempts": [{"effect_applied": True}]}}
        missing = deepcopy(original)
        del missing[next(iter(missing))]
        for value in (extra, missing, [], None):
            with self.subTest(shape=type(value).__name__, count=len(value) if value is not None else None):
                self.replace_output("observations.json", json.dumps(value))
                self.assert_invalid("exactly the scheduled cases")

    def test_malformed_nested_containers_fail_cleanly(self):
        original = self.manifest()
        for field in ("harness", "outputs", "policy", "suite", "backend"):
            for value in (None, [], "not an object"):
                with self.subTest(field=field, value=value):
                    changed = deepcopy(original)
                    changed[field] = value
                    self.write_manifest(changed)
                    self.assert_invalid()

    def test_null_manifest_is_invalid_cli_exit_two_without_traceback(self):
        self.write_manifest(None)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(2, backend.main_validate(SimpleNamespace(run_dir=str(self.folder))))
        self.assertIn("manifest must be an object", output.getvalue())

    def test_excessive_json_nesting_is_invalid_without_traceback(self):
        nested = "[" * 2000 + "0" + "]" * 2000
        self.replace_output("traces.jsonl", nested + "\n")
        self.assert_invalid()
        (self.folder / "manifest.json").write_text(nested)
        # Parser recursion thresholds vary by Python build. Either rejecting the JSON
        # or parsing its invalid top-level shape must preserve the same CLI contract.
        self.assert_invalid()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(2, backend.main_validate(SimpleNamespace(run_dir=str(self.folder))))
        self.assertIn("INVALID", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())
        # Exercise the parser-error path explicitly even where this input parses.
        with mock.patch.object(backend, "strict_json", side_effect=RecursionError("synthetic parser depth")):
            self.assert_invalid("manifest unreadable")

    def test_unexpected_output_path_is_not_opened(self):
        changed = self.manifest()
        changed["outputs"]["../outside"] = "0" * 64
        self.write_manifest(changed)
        with mock.patch.object(backend, "file_hash", side_effect=AssertionError("must not read any output")):
            self.assert_invalid("exactly the expected outputs")

    def test_trace_order_is_stable_across_sorted_json_serialization(self):
        chosen = [{"case_id": "case"}]
        observations = {"case": {"runs": {"z": {"trace": [{"run": "z"}]}, "a": {"trace": [{"run": "a"}]}}}}
        decoded = json.loads(json.dumps(observations, sort_keys=True))
        self.assertEqual(backend.observation_traces(chosen, observations), backend.observation_traces(chosen, decoded))


class PinnedReceiptProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        (self.repo / "serving").mkdir()
        (self.repo / "serving" / "app.py").write_text("# immutable synthetic source\n")
        (self.repo / "requirements-serving.lock").write_text("foo==1.2.3\n")
        def git(*args):
            return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-q")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        self.pin = {"pin_version": "evalgate-backend-pin-v1", "repository": "fixture/repo", "commit": git("rev-parse", "HEAD"),
                    "lock_file": "requirements-serving.lock", "lock_sha256": backend.file_hash(self.repo / "requirements-serving.lock"),
                    "python": "3.11"}
        pin_path = root / "pin.json"
        pin_path.write_text(json.dumps(self.pin))
        self.info = {"mode": "pinned-commit", "repository": self.pin["repository"], "commit": self.pin["commit"],
                     "pin_sha256": backend.file_hash(pin_path), "lock_sha256": self.pin["lock_sha256"],
                     "lock_mismatches": [], "python": "3.11.1", "packages": [["foo", "1.2.3"]], **backend.backend_identity(self.repo)}
        patch = mock.patch.object(backend, "PIN_PATH", pin_path)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(backend, "load_pin", return_value=self.pin)
        patch.start()
        self.addCleanup(patch.stop)

    def test_genuine_pin_source_and_inventory_match(self):
        self.assertEqual([], backend.validate_backend_provenance(self.info, self.repo))

    def test_forged_pin_source_and_environment_claims_fail(self):
        for field, value in (("commit", "0" * 40), ("repository", "other/repo"), ("pin_sha256", "0" * 64),
                             ("lock_sha256", "0" * 64), ("files", {}), ("files_sha256", "0" * 64),
                             ("packages", [["foo", "9.9"]]), ("python", "3.12.1"),
                             ("packages", [["foo", "1.2.3"], ["foo", "9.9"]])):
            with self.subTest(field=field, value=value):
                self.assertTrue(backend.validate_backend_provenance({**self.info, field: value}, self.repo))

    def test_missing_local_git_commit_is_not_silently_verified(self):
        self.assertTrue(backend.validate_backend_provenance(self.info, Path(self.temp.name)))

    def test_commit_claim_without_pinned_mode_fails(self):
        self.assertTrue(backend.validate_backend_provenance({"commit": self.pin["commit"]}, self.repo))


if __name__ == "__main__":
    unittest.main()
