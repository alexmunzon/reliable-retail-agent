#!/usr/bin/env python3
"""Backend mutation proof: plant real bugs in throwaway copies of the backend, run the real adapter.

The backend tree is only read: each mutant is applied to a disposable copy. A mutant counts as
killed only if the adapter is non-green AND each named case fails for its named reason. The
unmutated backend must pass the same cases. A mutant whose target text is not found (the backend
changed) is reported NOT_APPLICABLE, never as killed. The check ablation (each scorer check proven
by a planted observation) runs first and needs no backend.

  python3 scripts/backend_mutation_proof.py --backend PATH [--backend-python PATH] [--receipts DIR]
"""
from __future__ import annotations

import argparse
import difflib
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evalgate import backend  # noqa: E402

SERVICE, WORKER, PROVIDER = "serving/service.py", "serving/worker.py", "serving/provider.py"
MUTANTS = [
    ("B1-retry-after-unknown", "Resend the mutation once (same provider key) when the outcome is unknown.", WORKER,
     [("        if self.settings.failpoint(\"crash_after_provider_response\"):\n",
       "        if outcome.state == \"unknown\":  # MUTANT: automatic retry\n"
       "            outcome = await self.provider.mutate(provider_key=claim.provider_key, tenant=claim.tenant_id,\n"
       "                                                 tool_name=claim.tool_name, arguments=claim.arguments,\n"
       "                                                 expected_revision=claim.expected_revision)\n"
       "        if self.settings.failpoint(\"crash_after_provider_response\"):\n")],
     {"bk-unknown-effect-reconciled": "duplicate_effect"}),
    ("B2-not-found-means-success", "Reconciliation treats a fenced not_found as applied.", PROVIDER,
     [("                return Outcome(\"failed_no_effect\", \"status_not_found_fenced\", {\"fenced\": True})\n",
       "                return Outcome(\"succeeded\", \"status_applied\", {\"effect_id\": 0, \"revision\": 2})  # MUTANT\n")],
     {"bk-crash-after-claim": "misreported_outcome", "bk-unknown-no-effect-then-new": "misreported_outcome"}),
    ("B3-accept-stale-binding", "Accept an approval that names a different prompt, version or payload.", SERVICE,
     [("        if (body[\"prompt_id\"], body[\"proposal_version\"], body[\"payload_hash\"]) != \\\n",
       "        if False and (body[\"prompt_id\"], body[\"proposal_version\"], body[\"payload_hash\"]) != \\\n")],
     {"bk-stale-binding": "unauthorized_dispatch"}),
    ("B4-accept-ambiguous", "Treat every non-refusal reply as approval.", SERVICE,
     [("        classification = classify_response(body[\"response\"])\n",
       "        classification = \"AFFIRM\" if classify_response(body[\"response\"]) != \"REJECT\" else \"REJECT\"  # MUTANT\n")],
     {"bk-ambiguous": "unauthorized_dispatch"}),
    ("B5-no-ownership-check", "Look runs up by id only, so any authenticated caller can act on them.", SERVICE,
     [("        row = conn.execute(\"SELECT * FROM runs WHERE run_id=? AND tenant_id=? AND actor_id=?\",\n"
       "                           (run_id, principal.tenant_id, principal.actor_id)).fetchone()\n",
       "        row = conn.execute(\"SELECT * FROM runs WHERE run_id=?\", (run_id,)).fetchone()  # MUTANT\n")],
     {"bk-other-principal": "unauthorized_dispatch"}),
    ("B6-ignore-superseded-revision", "Dispatch an approval made against an order revision that has since changed.", SERVICE,
     [("            elif row[\"known_revision\"] is not None and row[\"known_revision\"] != row[\"resource_revision\"]:\n",
       "            elif False:  # MUTANT: revision check removed\n")],
     {"bk-superseded-revision": "unauthorized_dispatch"}),
    ("B7-release-hold-on-unknown", "Release the order's hold when an outcome becomes unknown, so new changes are not blocked.", SERVICE,
     [("            self._run_state(conn, run_id, \"outcome_unknown\", from_states, now)\n",
       "            conn.execute(\"DELETE FROM resource_holds WHERE operation_id=?\", (operation_id,))  # MUTANT\n"
       "            self._run_state(conn, run_id, \"outcome_unknown\", from_states, now)\n")],
     {"bk-unknown-unresolved-hold": "unauthorized_dispatch"}),
    ("B8-never-dispatch", "The worker claims operations but never calls the provider.", WORKER,
     [("        started = time.monotonic()\n        self.telemetry.count(\"provider_attempts\")\n",
       "        return  # MUTANT: dispatch disabled\n        started = time.monotonic()\n        self.telemetry.count(\"provider_attempts\")\n")],
     {"bk-positive-once": "missed_legitimate_execution"}),
    ("B9-report-unknown-as-success", "Record an unknown provider outcome as succeeded.", PROVIDER,
     [("            return Outcome(\"unknown\", \"provider_timeout\")\n        except httpx.HTTPError as error:\n"
       "            return Outcome(\"unknown\", \"provider_transport_\" + type(error).__name__)\n",
       "            return Outcome(\"unknown\", \"provider_timeout\")\n        except httpx.HTTPError as error:\n"
       "            return Outcome(\"succeeded\", \"applied\", {\"effect_id\": 0, \"revision\": 2, \"duplicate\": False})  # MUTANT\n")],
     {"bk-unknown-no-effect-then-new": "misreported_outcome"}),
]
CONTROL_CASES = ["bk-positive-once"]


def ablation() -> dict:
    """Which planted-observation tests each scorer check is responsible for catching."""
    from tests import test_evalgate_backend as suite
    full = suite.planted_results(backend.CHECKS)
    result = {"all_checks": full, "no_checks": suite.planted_results(())}
    for check in backend.CHECKS:
        without = suite.planted_results(c for c in backend.CHECKS if c is not check)
        result[f"without {check.__name__}"] = sorted(name for name, ok in without.items() if not ok)
    return result


def apply(copy: Path, target: str, edits) -> str | None:
    path = copy / target
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in edits:
        if text.count(old) != 1:
            return None
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    return "".join(difflib.unified_diff(original.splitlines(True), text.splitlines(True), f"a/{target}", f"b/{target}"))


def killed(folder: Path, decision: str, expected: dict) -> tuple[bool, list[str]]:
    records = {r["case_id"]: r for r in map(json.loads, (folder / "records.jsonl").read_text().splitlines())}
    notes = []
    ok = decision != "PASS"
    for case_id, failure in expected.items():
        classes = records[case_id]["failure_classes"]
        hit = failure in classes
        ok = ok and hit
        notes.append(f"{case_id}: {classes} ({'named reason present' if hit else 'MISSING ' + failure})")
    return ok, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--backend-python")
    parser.add_argument("--receipts", help="directory for receipts (default: a fresh runtime/ directory)")
    parser.add_argument("--only", action="append", help="run only this mutant id (repeatable)")
    args = parser.parse_args()
    source = Path(args.backend).resolve()
    python = Path(args.backend_python) if args.backend_python else backend.default_python(source)
    reasons = backend.availability(source, python)
    if reasons:
        print("NOT_RUN: " + "; ".join(reasons))
        return backend.EXIT["NOT_RUN"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipts_dir = Path(args.receipts) if args.receipts else ROOT / "runtime" / "evalgate" / f"backend-mutation-proof-{stamp}"
    receipts_dir.mkdir(parents=True, exist_ok=False)
    out = receipts_dir / "runs"
    receipts = {"generated_at": stamp, "evidence_class": "scripted_fixture", "backend_root_name": source.name,
                "check_ablation": ablation(), "mutants": []}
    with tempfile.TemporaryDirectory(prefix="evalgate-backend-mutants-") as scratch:
        pristine = Path(scratch) / "pristine"
        shutil.copytree(source, pristine, ignore=backend.COPY_IGNORE)
        receipts["backend_files_sha256"] = backend.backend_identity(pristine)["files_sha256"]
        all_cases = sorted({c for *_, expected in MUTANTS for c in expected} | set(CONTROL_CASES))
        folder, decision = backend.run(pristine, python, out_root=out, only=all_cases, label="unmutated")
        receipts["unmutated"] = {"cases": all_cases, "decision": decision, "run": folder.name}
        print(f"unmutated backend on {len(all_cases)} cases: {decision}")
        for mutant_id, description, target, edits, expected in MUTANTS:
            if args.only and mutant_id not in args.only:
                continue
            copy = Path(scratch) / mutant_id
            shutil.copytree(pristine, copy)
            diff = apply(copy, target, edits)
            if diff is None:
                receipts["mutants"].append({"id": mutant_id, "description": description, "result": "NOT_APPLICABLE",
                                            "note": f"target text not found exactly once in {target}"})
                print(f"{mutant_id}: NOT_APPLICABLE (target text changed)")
                continue
            (receipts_dir / f"{mutant_id}.patch").write_text(diff, encoding="utf-8")
            cases = sorted(set(expected) | set(CONTROL_CASES))
            folder, decision = backend.run(copy, python, out_root=out, only=cases, label=mutant_id.lower())
            ok, notes = killed(folder, decision, expected)
            receipts["mutants"].append({"id": mutant_id, "description": description, "target": target, "cases": cases,
                                        "decision": decision, "result": "KILLED" if ok else "SURVIVED", "notes": notes, "run": folder.name})
            print(f"{mutant_id}: {decision} -> {'KILLED' if ok else 'SURVIVED'}; " + "; ".join(notes))
    (receipts_dir / "receipts.json").write_text(json.dumps(receipts, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    survived = [m["id"] for m in receipts["mutants"] if m["result"] != "KILLED"]
    green = receipts["unmutated"]["decision"] == "PASS" and all(receipts["check_ablation"]["all_checks"].values())
    print(f"receipts: {receipts_dir}")
    print("BACKEND MUTATION PROOF: " + ("OK" if green and not survived else f"NOT OK (unmutated {receipts['unmutated']['decision']}, not killed: {survived})"))
    return 0 if green and not survived else 1


if __name__ == "__main__":
    raise SystemExit(main())
