"""Compare a candidate run with the pinned approved baseline run."""
from __future__ import annotations

from pathlib import Path

from . import runner, scoring

EXIT = {"PASS": 0, "FAIL": 1, "INVALID": 2}


def compare(baseline_dir: Path, candidate_dir: Path, out_root: Path, policy_path: Path = runner.POLICY_PATH,
            expect_candidate_sha: str | None = None, repo: Path = runner.REPO) -> tuple[Path, str]:
    policy = runner.load_policy(policy_path)
    _, labels = runner.load_suite(policy)
    reasons: list[str] = []
    base_problems, base_manifest, base_records = runner.validate_run(baseline_dir, policy_path)
    cand_problems, cand_manifest, cand_records = runner.validate_run(candidate_dir, policy_path)
    reasons += [f"baseline evidence: {p}" for p in base_problems] + [f"candidate evidence: {p}" for p in cand_problems]
    if not base_problems and not cand_problems:
        if base_manifest["role"] != "baseline" or cand_manifest["role"] != "candidate":
            reasons.append("runs are not a baseline/candidate pair")
        if base_manifest["sut"].get("git_sha") != policy["approved_baseline_sha"]:
            reasons.append(f"baseline SUT is {base_manifest['sut'].get('git_sha')}, not the approved {policy['approved_baseline_sha']}")
        if base_manifest["harness"]["files_sha256"] != cand_manifest["harness"]["files_sha256"]:
            reasons.append("baseline and candidate were scored by different harness files")
        cand_sut = cand_manifest["sut"]
        if expect_candidate_sha and cand_sut.get("git_sha") != expect_candidate_sha:
            reasons.append(f"candidate run is for {cand_sut.get('git_sha')}, not the expected {expect_candidate_sha}")
        if cand_manifest["candidate_kind"] in ("merge-revision", "pr-head") and cand_sut.get("dirty") is not False:
            reasons.append("a PR candidate must be a clean checkout of a known commit")
        # A run manifest's decision has been re-derived by validation from records, trace contract and termination.
        if base_manifest["decision"] == "INVALID":
            reasons.append("the approved baseline's own evidence is INVALID, so nothing can be compared against it")
        claims = [("baseline", base_manifest["sut"], policy["approved_baseline_sha"])]
        if expect_candidate_sha:
            claims.append(("candidate", cand_sut, expect_candidate_sha))
        for role, sut, sha in claims:
            stored = runner.commit_components(repo, sha)
            if stored is None:
                reasons.append(f"cannot read commit {sha} from {repo}, so the {role}'s claimed identity cannot be verified")
            elif stored != sut.get("components"):
                reasons.append(f"the {role}'s evaluated files are not the content of commit {sha}")
    if reasons:
        decision = "INVALID"
        changed, candidate_reasons, base_decision = [], [], None
    else:
        decision, candidate_reasons = scoring.decide(cand_records, labels["labels"])
        if cand_manifest["decision"] == "INVALID" and decision != "INVALID":
            decision = "INVALID"
            candidate_reasons += ["the candidate's own evidence is INVALID (see its manifest reasons)"] + cand_manifest.get("reasons", [])[-5:]
        base_decision, _ = scoring.decide(base_records, labels["labels"])
        base = {r["case_id"]: r for r in base_records}
        changed = []
        for record in cand_records:
            before = base[record["case_id"]]
            if (before["verdict"], before["failure_classes"]) != (record["verdict"], record["failure_classes"]):
                changed.append({"case_id": record["case_id"], "normative": record["normative"],
                                "baseline": {"verdict": before["verdict"], "failure_classes": before["failure_classes"]},
                                "candidate": {"verdict": record["verdict"], "failure_classes": record["failure_classes"]},
                                "details": record.get("details", [])})
        regressions = [c for c in changed if not c["normative"] and c["baseline"]["verdict"] == "pass"]
        if regressions and decision == "PASS" and policy["exploratory_regression_fails"]:
            decision = "FAIL"
            candidate_reasons += [f"{c['case_id']}: exploratory case passed on the approved baseline and now fails" for c in regressions]
        reasons = candidate_reasons
    folder = runner.new_run_dir(out_root, "comparison")
    result = {"schema_version": "evaluation-v1", "record_type": "comparison", "decision": decision, "reasons": reasons,
              "policy_sha256": runner.file_hash(policy_path),
              "baseline": {"run_id": baseline_dir.name, "sut_git_sha": base_manifest.get("sut", {}).get("git_sha"),
                           "absolute_decision": base_decision,
                           "manifest_sha256": runner.file_hash(baseline_dir / "manifest.json") if (baseline_dir / "manifest.json").is_file() else None},
              "candidate": {"run_id": candidate_dir.name, "sut_git_sha": cand_manifest.get("sut", {}).get("git_sha"),
                            "sut_dirty": cand_manifest.get("sut", {}).get("dirty"), "candidate_kind": cand_manifest.get("candidate_kind"),
                            "harness_trust": cand_manifest.get("harness_trust"),
                            "manifest_sha256": runner.file_hash(candidate_dir / "manifest.json") if (candidate_dir / "manifest.json").is_file() else None},
              "changed_cases": changed}
    runner._write(folder, "comparison.json", runner._dumps(result))
    runner._write(folder, "comparison.md", render(result, cand_records))
    return folder, decision


def render(result: dict, records: list[dict]) -> str:
    base, cand = result["baseline"], result["candidate"]
    lines = [f"# Regression comparison: **{result['decision']}**", "",
             f"- Approved baseline: `{base['sut_git_sha']}` (run `{base['run_id']}`, absolute decision {base['absolute_decision']})",
             f"- Candidate: `{cand['sut_git_sha']}` dirty={cand['sut_dirty']} kind={cand['candidate_kind']} "
             f"harness={cand['harness_trust']} (run `{cand['run_id']}`)",
             "- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.", ""]
    if records:
        normative = [r for r in records if r["normative"]]
        exploratory = [r for r in records if not r["normative"]]
        lines.append(f"Normative cases passing: {sum(r['verdict'] == 'pass' for r in normative)}/{len(normative)}. "
                     f"Exploratory language cases passing (non-gating): {sum(r['verdict'] == 'pass' for r in exploratory)}/{len(exploratory)}.")
        lines.append("")
    lines += [f"## Changed outcomes ({len(result['changed_cases'])})", ""]
    for c in result["changed_cases"]:
        lines.append(f"- `{c['case_id']}`: {c['baseline']['verdict']} {c['baseline']['failure_classes']} -> "
                     f"{c['candidate']['verdict']} {c['candidate']['failure_classes']}. " + " ".join(c["details"]))
    if result["reasons"]:
        lines += ["", "## Why", ""] + [f"- {r}" for r in result["reasons"]]
    return "\n".join(lines) + "\n"
