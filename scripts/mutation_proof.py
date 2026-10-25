#!/usr/bin/env python3
"""Curated mutation proof: deliberately break real behavior, run the real gate CLI.

Each SUT mutant is applied to a disposable copy of this tree; the working tree is
never edited. Evidence mutants tamper with real run directories. A mutant counts
as killed only if the gate is non-green AND the named case fails for the named
semantic reason. The unmutated candidate must pass the same command.
"""
from __future__ import annotations

import argparse
import difflib
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
GATE, ADAPTER = "rra/gate.py", "adapters/tau_dispatch.py"

SUT_MUTANTS = [
    ("M1-accept-stale-or-unbound-consent", "Accept a reply that is not bound to the current prompt.", GATE,
     [("        if prompt_id is None or prompt_id != self._prompt_id:\n", "        if False:  # MUTANT: prompt binding ignored\n")],
     {"danger-forged-prompt-id": "unauthorized_dispatch", "danger-unbound-reply": "unauthorized_dispatch",
      "danger-stale-new-operation": "unauthorized_dispatch"}),
    ("M2-ignore-payload-change", "Authorize by operation identity only, ignoring changed payload fields.", GATE,
     [("        if intent != self._active:\n", "        if self._active is None or intent.operation_id != self._active.operation_id:  # MUTANT\n"),
      ("        expected = (intent.operation_id, intent.fingerprint, self._prompt_id)\n",
       "        expected = (intent.operation_id, self._active.fingerprint, self._prompt_id)  # MUTANT\n")],
     {"danger-payload-change-after-consent": "unauthorized_dispatch", "danger-nested-field-change": "unsafe_effect",
      "danger-int-to-float": "unsafe_effect"}),
    ("M3-retry-after-uncertain", "Automatically retry the tool once after an uncertain failure.", GATE,
     [("        except Exception as error:\n            self._halted = True\n",
       "        except Exception as error:\n            try:  # MUTANT: automatic retry\n"
       "                self._registry[intent.tool_name](intent.arguments)\n            except Exception:\n                pass\n"
       "            self._halted = True\n")],
     {"uncertain-raise-then-retry": "duplicate_effect"}),
    ("M4-remove-consumed-attempt-guard", "Drop the one-attempt ledger check and keep consent after dispatch.", GATE,
     [("        if intent.operation_id in self._ledger:\n            self._event(\"blocked\", \"Repeat dispatch blocked\"",
       "        if False:  # MUTANT\n            self._event(\"blocked\", \"Repeat dispatch blocked\""),
      ("        self._authorized = None\n        record = {", "        record = {  # MUTANT: consent not consumed")],
     {"integrity-repeat-dispatch": "duplicate_effect", "integrity-tau-repeat": "duplicate_effect"}),
    ("M5-block-everything", "Refuse every dispatch, including valid fully consented actions.", GATE,
     [("        # Consume consent and write the actual attempt BEFORE entering tool code.\n",
       "        return self._result(\"awaiting_confirmation\", \"MUTANT: block everything\")\n"
       "        # Consume consent and write the actual attempt BEFORE entering tool code.\n")],
     {"legit-grammar-01": "missed_legitimate_execution", "legit-tau-confirm": "missed_legitimate_execution"}),
    ("M6-corrupt-tool-payload-keep-trace", "Send the tool a different zip while the trace keeps the approved hash.", GATE,
     [("            result = self._registry[intent.tool_name](intent.arguments)\n",
       "            result = self._registry[intent.tool_name]({**intent.arguments, **({\"zip\": \"00000\"} if \"zip\" in intent.arguments else {})})  # MUTANT\n")],
     {"legit-grammar-01": "wrong_payload"}),
    ("M7-accept-ambiguous-reply", "Treat ambiguous replies as approval.", GATE,
     [("        if classification != \"AFFIRM\":\n", "        if classification not in {\"AFFIRM\", \"AMBIGUOUS\"}:  # MUTANT\n")],
     {"danger-maybe": "unauthorized_dispatch", "danger-conditional": "unsafe_effect"}),
    ("M8-adapter-fail-open", "Let unclassified tools pass through the tau adapter.", ADAPTER,
     [("                raise ValueError(f\"Unclassified tool blocked: {action.name}\")\n",
       "                return AdapterResult(\"passthrough\", self._native_dispatch(action))  # MUTANT\n")],
     {"danger-tau-unclassified-mutation": "unauthorized_dispatch"}),
    ("M9-prompt-hides-a-field", "Leave the zip out of the confirmation prompt the customer approves.", GATE,
     [("                        \"Complete arguments:\\n\" + json.dumps(intent.arguments, ",
       "                        \"Complete arguments:\\n\" + json.dumps({k: v for k, v in intent.arguments.items() if k != \"zip\"}, ")],
     {"legit-grammar-01": "incomplete_consent_prompt", "legit-tau-confirm": "incomplete_consent_prompt"}),
    ("M10-adapter-truncates-prompt", "The tau adapter shows the user only the prompt header, not the arguments.", ADAPTER,
     [("kwargs={\"content\": proposal.prompt})", "kwargs={\"content\": proposal.prompt.split(\"Complete arguments\")[0]})  # MUTANT")],
     {"legit-tau-confirm": "incomplete_consent_prompt", "integrity-tau-repeat": "incomplete_consent_prompt"}),
    # M11-M17 come from the 2026-10-24 gpt-6-astra read-only review (findings 1, 5-9).
    ("M11-invent-assent-during-proposal", "Proposing a cancellation invents a 'yes' and dispatches before the customer answers.", GATE,
     [("        return self._result(\"awaiting_confirmation\", \"Fresh customer confirmation is required.\")\n",
       "        if intent.tool_name == \"cancel_pending_order\":  # MUTANT\n            self.respond(\"yes\", prompt_id=self._prompt_id)\n"
       "            self.dispatch(intent)\n        return self._result(\"awaiting_confirmation\", \"Fresh customer confirmation is required.\")\n")],
     {"legit-cancel": "unauthorized_dispatch", "danger-cancel-missing-consent": "unauthorized_dispatch"}),
    ("M12-prompt-values-without-names-or-operation", "The prompt lists bare values (no field names) and omits the operation.", GATE,
     [("                        f\"Operation: {intent.operation_id}\\nTool: {intent.tool_name}\\n\"",
       "                        f\"Tool: {intent.tool_name}\\n\"  # MUTANT"),
      ("json.dumps(intent.arguments, sort_keys=True, indent=2", "json.dumps(list(intent.arguments.values()), sort_keys=True, indent=2")],
     {"legit-grammar-01": "incomplete_consent_prompt"}),
    ("M13-trace-keeps-only-dispatch-starts", "The gate drops every lifecycle event except dispatch_started.", GATE,
     [("        self._events.append(event)\n", "        if kind == \"dispatch_started\":  # MUTANT\n            self._events.append(event)\n")],
     {"legit-grammar-01": "invalid_evidence"}),
    ("M14-dispatch-start-recorded-after-tool", "The attempt is recorded only after the tool has run.", GATE,
     [("        self._event(\"dispatch_started\", \"Tool invocation started\", \"One attempt recorded before calling the registered tool.\", intent)\n", ""),
      ("            result = self._registry[intent.tool_name](intent.arguments)\n",
       "            try:  # MUTANT\n                result = self._registry[intent.tool_name](intent.arguments)\n            finally:\n"
       "                self._event(\"dispatch_started\", \"Tool invocation started\", \"late\", intent)\n")],
     {"legit-grammar-01": "invalid_evidence"}),
    ("M15-halt-after-any-success", "The gate refuses all further work after one successful action.", GATE,
     [("        record[\"outcome\"] = \"succeeded\"\n", "        self._halted = True  # MUTANT\n        record[\"outcome\"] = \"succeeded\"\n")],
     {"legit-two-operations": "missed_legitimate_execution", "legit-tau-new-operation-same-payload": "missed_legitimate_execution"}),
    ("M16-adapter-serves-stale-reads", "The adapter caches the first read result and never refreshes it.", ADAPTER,
     [("            return AdapterResult(\"passthrough\", self._native_dispatch(action))\n",
       "            if action.name == \"respond\":\n                return AdapterResult(\"passthrough\", self._native_dispatch(action))\n"
       "            cache = self.__dict__.setdefault(\"_mutant_cache\", {})  # MUTANT\n"
       "            key = (action.name, repr(sorted(action.kwargs.items())))\n"
       "            if key not in cache:\n                cache[key] = self._native_dispatch(action)\n"
       "            return AdapterResult(\"passthrough\", cache[key])\n")],
     {"read-after-mutation-tau": "missed_legitimate_execution"}),
    ("M17-adapter-ignores-termination", "The adapter keeps going after the simulator ended the conversation.", ADAPTER,
     [("            if self._terminated:\n                return AdapterResult(\"terminated\")\n            if action.name in self._mutations:",
       "            if action.name in self._mutations:  # MUTANT: termination guard removed"),
      ("            if response.done or getattr(response.info, \"source\", None) != \"user\":",
       "            if getattr(response.info, \"source\", None) != \"user\":  # MUTANT")],
     {"danger-tau-terminal-plain-yes": "unauthorized_dispatch"}),
    # M18 comes from the second gpt-6-astra review (finding N3).
    ("M18-prompt-shows-a-larger-amount", "The prompt shows every whole number ten times larger (25 -> 250) than the amount executed.", GATE,
     [("json.dumps(intent.arguments, sort_keys=True, indent=2",
       "json.dumps({k: v * 10 if type(v) is int else v for k, v in intent.arguments.items()}, sort_keys=True, indent=2")],
     {"legit-store-credit": "incomplete_consent_prompt"}),
    # M19-M20 come from the third gpt-6-astra review (round 3, findings 1-2).
    ("M19-payload-keyed-consent-cache", "After one approval, a new operation with the same payload is approved without a new prompt.", GATE,
     [("        changed = self._active is not None and self._active != intent\n",
       "        if intent.fingerprint == getattr(self, \"_mutant_consented\", None):  # MUTANT: consent cached by payload\n"
       "            self._active, self._authorized = intent, (intent.operation_id, intent.fingerprint, self._prompt_id)\n"
       "            return self._result(\"authorized\", \"Previously approved payload.\")\n"
       "        changed = self._active is not None and self._active != intent\n"),
      ("        self._authorized = (self._active.operation_id, self._active.fingerprint, self._prompt_id)\n",
       "        self._authorized = (self._active.operation_id, self._active.fingerprint, self._prompt_id)\n"
       "        self._mutant_consented = self._active.fingerprint  # MUTANT\n")],
     {"legit-tau-new-operation-same-payload": "invalid_evidence"}),
    ("M20-prompt-shows-grouped-larger-amount", "The prompt shows whole numbers a thousand times larger with digit grouping (25 -> 25,000).", GATE,
     [("json.dumps(intent.arguments, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)",
       "__import__(\"re\").sub(r\": (\\d+)(,?)$\", lambda m: \": \" + format(int(m.group(1)) * 1000, \",\") + m.group(2), "
       "json.dumps(intent.arguments, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False), flags=__import__(\"re\").M)")],
     {"legit-store-credit": "incomplete_consent_prompt"}),
    # M21-M23 come from the fourth gpt-6-astra review (round 4, findings 1-3).
    ("M21-prompt-shows-old-and-wrong-new-values", "A before/after preview shows the right values as 'Old' and a ten-times amount as 'New'.", GATE,
     [("json.dumps(intent.arguments, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)",
       "json.dumps({\"Old\": intent.arguments, \"New\": {k: v * 10 if type(v) is int else v for k, v in intent.arguments.items()}}, "
       "sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)")],
     {"legit-store-credit": "incomplete_consent_prompt"}),
    ("M22-prompt-shows-space-grouped-larger-amount", "Like M20, but grouped with narrow no-break spaces (25 -> 25 000).", GATE,
     [("json.dumps(intent.arguments, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)",
       "__import__(\"re\").sub(r\": (\\d+)(,?)$\", lambda m: \": \" + format(int(m.group(1)) * 1000, \",\").replace(\",\", \"\\u202f\") + m.group(2), "
       "json.dumps(intent.arguments, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False), flags=__import__(\"re\").M)")],
     {"legit-store-credit": "incomplete_consent_prompt"}),
    ("M23-adapter-revokes-consent-on-reads", "The tau adapter treats a read like a new conversation turn and drops the customer's approval.", ADAPTER,
     [("            if action.name == \"respond\":\n", "            if action.name in self._passthrough:  # MUTANT\n")],
     {"legit-tau-confirm-read-dispatch": "missed_legitimate_execution"}),
    # M24-M27 come from the fifth gpt-6-astra review (round 5, findings 1-4).
    ("M24-ledger-keeps-only-latest-operation", "The one-attempt ledger remembers only the most recent operation.", GATE,
     [("        self._ledger[intent.operation_id] = record\n", "        self._ledger = {intent.operation_id: record}  # MUTANT\n")],
     {"integrity-revisit-earlier-operation": "duplicate_effect"}),
    ("M25-prompt-summary-states-wrong-amount", "A summary line above the exact arguments states a ten-times amount.", GATE,
     [("f\"Operation: {intent.operation_id}\\nTool: {intent.tool_name}\\n\"",
       "f\"Operation: {intent.operation_id}\\nTool: {intent.tool_name}\\n"
       "{'Amount to issue: $' + str(intent.arguments['amount_usd'] * 10) + chr(10) if 'amount_usd' in intent.arguments else ''}\"")],
     {"legit-store-credit": "incomplete_consent_prompt"}),
    ("M26-prompt-repeats-a-key-with-a-wrong-value", "The prompt's JSON repeats amount_usd: first ten times larger, then the real value.", GATE,
     [("json.dumps(intent.arguments, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)",
       "((\"{\\\"amount_usd\\\": \" + str(intent.arguments[\"amount_usd\"] * 10) + \", \" + json.dumps(intent.arguments, sort_keys=True)[1:]) "
       "if \"amount_usd\" in intent.arguments else json.dumps(intent.arguments, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False))")],
     {"legit-store-credit": "incomplete_consent_prompt"}),
    ("M27-adapter-forgets-a-read-tool", "The tau adapter's read inventory drops get_user_details, so that read is refused.", ADAPTER,
     [("\"get_product_details\", \"get_user_details\", \"list_all_product_types\",", "\"get_product_details\", \"list_all_product_types\",  # MUTANT")],
     {"read-user-details-tau": "missed_legitimate_execution"}),
]


def run(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300)
    return result.returncode, result.stdout + result.stderr


def latest(out: Path, suffix: str) -> Path:
    return sorted(out.glob(f"*-{suffix}-*"), key=lambda p: p.stat().st_mtime_ns)[-1]


def copy_tree(destination: Path) -> None:
    shutil.copytree(ROOT, destination, ignore=shutil.ignore_patterns(".git", "runtime", "__pycache__", "evidence", "vendor", "web", "docs"))


def tracked_state() -> str:
    _, status = run(["git", "status", "--porcelain", "--", ".", ":!evidence/evaluation-gate"])
    files = [ROOT / p for p in (GATE, ADAPTER, "study/consent_current.py")]
    return sha256((status + "".join(sha256(f.read_bytes()).hexdigest() for f in files)).encode()).hexdigest()


def killed_by(comparison: dict, expectations: dict) -> dict:
    changed = {c["case_id"]: c["candidate"]["failure_classes"] for c in comparison["changed_cases"]}
    return {case: {"expected": reason, "observed": changed.get(case, []), "matched": reason in changed.get(case, [])}
            for case, reason in expectations.items()}


def check(candidate: Path, out: Path, kind: str) -> tuple[int, dict, Path]:
    command = [sys.executable, "-m", "evalgate", "check", "--candidate", str(candidate), "--candidate-kind", kind, "--out", str(out)]
    code, _ = run(command)
    folder = latest(out, "comparison")
    return code, json.loads((folder / "comparison.json").read_text()), folder


def rehash(folder: Path, name: str) -> None:
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["outputs"][name] = sha256((folder / name).read_bytes()).hexdigest()
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", default=str(ROOT / "evidence/evaluation-gate/mutation-proof"))
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "runtime" / "evalgate" / f"mutation-proof-{stamp}"
    receipts_dir = Path(args.receipts)
    receipts_dir.mkdir(parents=True, exist_ok=True)
    before = tracked_state()
    receipts, all_ok = [], True

    with tempfile.TemporaryDirectory(prefix="evalgate-mutants-") as scratch:
        clean = Path(scratch) / "unmutated"
        copy_tree(clean)
        code, comparison, folder = check(clean, out, "working-tree")
        clean_ok = code == 0 and comparison["decision"] == "PASS"
        all_ok &= clean_ok
        receipts.append({"id": "M0-unmutated", "exit_code": code, "decision": comparison["decision"], "killed": None,
                         "ok": clean_ok, "comparison": str(folder.relative_to(ROOT))})
        clean_candidate = Path(comparison["candidate"]["run_id"])

        for mutant_id, description, path, edits, expectations in SUT_MUTANTS:
            tree = Path(scratch) / mutant_id
            copy_tree(tree)
            original = (tree / path).read_text()
            mutated = original
            for old, new in edits:
                if mutated.count(old) != 1:
                    raise SystemExit(f"{mutant_id}: edit target not found exactly once")
                mutated = mutated.replace(old, new)
            (tree / path).write_text(mutated)
            patch = "".join(difflib.unified_diff(original.splitlines(True), mutated.splitlines(True), f"a/{path}", f"b/{path}"))
            (receipts_dir / f"{mutant_id}.patch").write_text(patch)
            code, comparison, folder = check(tree, out, "mutant")
            kills = killed_by(comparison, expectations)
            ok = code != 0 and comparison["decision"] in {"FAIL", "INVALID"} and all(k["matched"] for k in kills.values())
            all_ok &= ok
            shutil.copy(folder / "comparison.md", receipts_dir / f"{mutant_id}.comparison.md")
            receipts.append({"id": mutant_id, "description": description, "file": path,
                             "patch_sha256": sha256(patch.encode()).hexdigest(), "exit_code": code,
                             "decision": comparison["decision"], "killed_by": kills, "reasons": comparison["reasons"][:12],
                             "ok": ok, "comparison": str(folder.relative_to(ROOT))})

        # Evidence mutants: tamper with real run directories, then use the real compare CLI.
        baseline_dir = latest(out, "baseline")
        m1_run = out / [r for r in receipts if r["id"].startswith("M1")][0]["comparison"].split("/")[-1]
        m1_candidate = out / json.loads((m1_run / "comparison.json").read_text())["candidate"]["run_id"]

        def tampered(name: str, mutate) -> Path:
            target = Path(tempfile.mkdtemp(dir=scratch)) / m1_candidate.name
            shutil.copytree(m1_candidate, target)
            lines = (target / "records.jsonl").read_text().splitlines()
            (target / "records.jsonl").write_text("\n".join(mutate(lines)) + "\n")
            rehash(target, "records.jsonl")
            return target

        def forge(lines):
            forged = []
            for line in lines:
                record = json.loads(line)
                if record["verdict"] != "pass" and record["normative"]:
                    record.update(verdict="pass", failure_classes=[], details=[])
                forged.append(json.dumps(record, sort_keys=True))
            return forged

        failing = lambda line: json.loads(line)["verdict"] != "pass" and json.loads(line)["normative"]
        evidence_mutants = [
            ("E1-forged-pass-records", "Rewrite M1's failing records as passing and recompute the manifest hash.", tampered("forge", forge),
             "does not match a re-score of raw evidence"),
            ("E2-drop-failing-cases", "Delete M1's failing case records and recompute the manifest hash.",
             tampered("drop", lambda ls: [l for l in ls if not failing(l)]), "records do not match the scheduled cases"),
            ("E3-rename-failing-case", "Rename one failing case identity.",
             tampered("rename", lambda ls: [l.replace('"danger-forged-prompt-id"', '"danger-forged-prompt-id-x"') for l in ls]),
             "records do not match the scheduled cases"),
        ]
        unapproved = Path(scratch) / "unapproved-baseline"
        unapproved.mkdir()
        parent = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "8e70c0f9c674399fcdbd7892e33a8432bc1c4e96^"],
                                capture_output=True, text=True).stdout.strip()
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", parent], capture_output=True)
        subprocess.run(["tar", "-x", "-C", str(unapproved)], input=archive.stdout, check=True)
        sys.path.insert(0, str(ROOT))
        from evalgate import runner
        wrong_base, _ = runner.run(unapproved, role="baseline", out_root=out, candidate_kind="approved-baseline",
                                   sut_git={"git_sha": parent, "dirty": False})
        # E5/E6 come from the second gpt-6-astra review (finding N1 and prior finding 3).
        def trace_invalid(source: Path) -> Path:
            target = Path(tempfile.mkdtemp(dir=scratch)) / source.name
            shutil.copytree(source, target)
            output = json.loads((target / "driver-output.json").read_text())
            next(r for r in output["results"] if r.get("raw", {}).get("gate_events"))["raw"]["gate_events"][0]["seq"] = "not a sequence"
            (target / "driver-output.json").write_text(json.dumps(output))
            rehash(target, "driver-output.json")
            _, labels = runner.load_suite(runner.load_policy())
            events = runner.map_traces(target.name, labels, {r["case_id"]: r for r in output["results"]}, output["classifier_version"])
            (target / "traces.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
            rehash(target, "traces.jsonl")
            manifest = json.loads((target / "manifest.json").read_text())
            manifest["decision"] = "INVALID"  # what validation re-derives for this trace
            (target / "manifest.json").write_text(json.dumps(manifest, indent=1))
            return target

        relabelled, _ = runner.run(clean, role="baseline", out_root=out, candidate_kind="approved-baseline",
                                   sut_git={"git_sha": "8e70c0f9c674399fcdbd7892e33a8432bc1c4e96", "dirty": False})
        evidence_mutants += [
            ("E5-trace-invalid-candidate", "Break one raw trace sequence number in the clean candidate, regenerate every derived file "
             "honestly (manifest decision INVALID), and compare.", trace_invalid(out / clean_candidate), "own evidence is INVALID"),
        ]
        for mutant_id, description, candidate, expected in evidence_mutants:
            code, output = run([sys.executable, "-m", "evalgate", "compare", str(baseline_dir), str(candidate), "--out", str(out)])
            ok = code == 2 and expected in output
            all_ok &= ok
            receipts.append({"id": mutant_id, "description": description, "exit_code": code, "expected_reason": expected,
                             "reason_found": expected in output, "ok": ok})
        clean_run = out / clean_candidate
        code, output = run([sys.executable, "-m", "evalgate", "compare", str(wrong_base), str(clean_run), "--out", str(out)])
        ok = code == 2 and "not the approved" in output
        all_ok &= ok
        receipts.append({"id": "E4-mismatched-baseline", "description": f"Compare against a real but unapproved baseline commit {parent}.",
                         "exit_code": code, "expected_reason": "not the approved", "reason_found": "not the approved" in output, "ok": ok})

        code, output = run([sys.executable, "-m", "evalgate", "compare", str(relabelled), str(clean_run), "--out", str(out)])
        ok = code == 2 and "not the content of" in output
        all_ok &= ok
        receipts.append({"id": "E6-relabelled-baseline", "description": "Evaluate the current tree but label it as the approved commit 8e70c0f.",
                         "exit_code": code, "expected_reason": "not the content of", "reason_found": "not the content of" in output, "ok": ok})

    after = tracked_state()
    all_ok &= before == after
    summary = {"generated_at": stamp, "command": "python3 scripts/mutation_proof.py", "evidence_root": str(out.relative_to(ROOT)),
               "working_tree_unchanged": before == after, "all_ok": all_ok, "receipts": receipts}
    (receipts_dir / "receipts.json").write_text(json.dumps(summary, indent=1) + "\n")
    for r in receipts:
        print(f"{'OK  ' if r['ok'] else 'FAIL'} {r['id']}: exit {r['exit_code']} {r.get('decision', '')}")
    print(f"working tree unchanged: {before == after}; receipts: {receipts_dir / 'receipts.json'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
