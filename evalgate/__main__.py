"""Command line: run, compare, check (both), validate, trace-check, coverage, backend, backend-validate.

Exit codes: 0 PASS, 1 FAIL (policy violation), 2 INVALID/INCOMPLETE evidence, 3 NOT_RUN (backend only).
trace-check exits 0 for CONSISTENT and 2 for INVALID; it never reports PASS.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

from . import backend, compare, runner, trace

DEFAULT_OUT = runner.REPO / "runtime" / "evalgate"


def _print_result(label: str, folder: Path, decision: str) -> None:
    print(f"{label}: {decision}  evidence: {folder}")


def cmd_run(args) -> int:
    folder, decision = runner.run(Path(args.sut), role=args.role, out_root=Path(args.out),
                                  candidate_kind=args.candidate_kind, harness_trust=args.harness_trust)
    _print_result(args.role, folder, decision)
    print((folder / "summary.md").read_text(encoding="utf-8"))
    return compare.EXIT[decision]


def cmd_compare(args) -> int:
    folder, decision = compare.compare(Path(args.baseline), Path(args.candidate), Path(args.out), expect_candidate_sha=args.expect_candidate_sha,
                                       repo=Path(args.repo))
    print((folder / "comparison.md").read_text(encoding="utf-8"))
    _print_result("comparison", folder, decision)
    return compare.EXIT[decision]


def cmd_check(args) -> int:
    """Evaluate the approved baseline and the candidate with this harness, then compare."""
    policy = runner.load_policy()
    out = Path(args.out)
    with tempfile.TemporaryDirectory(prefix="evalgate-baseline-") as scratch:
        baseline_root = Path(scratch)
        try:
            runner.export_revision(Path(args.repo), policy["approved_baseline_sha"], baseline_root)
        except runner.EvaluationError as error:
            print(f"INVALID: unsupported comparison: {error}", file=sys.stderr)
            return 2
        base_dir, base_decision = runner.run(baseline_root, role="baseline", out_root=out, candidate_kind="approved-baseline",
                                             harness_trust=args.harness_trust,
                                             sut_git={"git_sha": policy["approved_baseline_sha"], "dirty": False})
    _print_result("approved baseline", base_dir, base_decision)
    cand_dir, cand_decision = runner.run(Path(args.candidate), role="candidate", out_root=out, candidate_kind=args.candidate_kind,
                                         harness_trust=args.harness_trust)
    _print_result("candidate", cand_dir, cand_decision)
    folder, decision = compare.compare(base_dir, cand_dir, out, expect_candidate_sha=args.expect_candidate_sha, repo=Path(args.repo))
    print((cand_dir / "summary.md").read_text(encoding="utf-8"))
    print((folder / "comparison.md").read_text(encoding="utf-8"))
    _print_result("REGRESSION GATE", folder, decision)
    return compare.EXIT[decision]


def cmd_validate(args) -> int:
    problems, manifest, _ = runner.validate_run(Path(args.run_dir))
    if not problems and manifest.get("decision") == "INVALID":  # consistent files, but the re-derived decision is INVALID
        problems = [f"run decision is INVALID: {reason}" for reason in manifest.get("reasons", [])] or ["run decision is INVALID"]
    for problem in problems:
        print(f"- {problem}")
    print("VALID" if not problems else "INVALID", manifest.get("run_id", ""))
    return 0 if not problems else 2


def cmd_trace_check(args) -> int:
    """Check a service-generated trace-v1 file. Consistency is not a PASS: effects need independent evidence."""
    problems, events = trace.check_file(Path(args.trace_file))
    for problem in problems[:50]:
        print(f"- {problem}")
    if problems:
        print(f"INVALID: {len(problems)} problem(s) in {len(events)} event(s)")
        return 2
    streams = len({(e.get("run_id"), e.get("trace_id")) for e in events})
    versions = "/".join(sorted({e.get("schema_version") for e in events}))
    print(f"CONSISTENT: {len(events)} event(s) in {streams} trace(s) follow {versions} and its ordering rules. "
          "This is not a PASS: the trace is the system's own account, and effects need independent evidence (see contracts/HANDOFF.md).")
    return 0


def cmd_coverage(_args) -> int:
    policy = runner.load_policy()
    _, labels = runner.load_suite(policy)
    names = {1: "No unauthorized assent", 2: "Exact binding / fresh consent on change", 3: "Fail-closed tool classification",
             4: "Attempt before tool; no duplicate; halt on uncertain", 5: "Legitimate action executes exactly once"}
    print("| Invariant | Positive controls (must execute) | Adversarial controls (must not execute) |\n|---|---|---|")
    for number, name in names.items():
        scoped = [l for l in labels["labels"] if number in l["invariants"] and l["normative"]]
        positive = [l["case_id"] for l in scoped if l["legit_opportunities"] or l["expect_read_ok"]]
        adversarial = [l["case_id"] for l in scoped if l["forbidden_opportunities"]]
        print(f"| {number}. {name} | {len(positive)}: {', '.join(positive)} | {len(adversarial)}: {', '.join(adversarial)} |")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m evalgate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", default=str(DEFAULT_OUT), help="evidence root (default: runtime/evalgate, gitignored)")
    common.add_argument("--harness-trust", default="local", choices=("local", "base-revision", "bootstrap-candidate", "merged-main"))
    p = sub.add_parser("run", parents=[common], help="evaluate one SUT tree")
    p.add_argument("--sut", default=str(runner.REPO))
    p.add_argument("--role", default="candidate", choices=("baseline", "candidate"))
    p.add_argument("--candidate-kind", default="working-tree", choices=runner.CANDIDATE_KINDS)
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("compare", parents=[common], help="compare a baseline run with a candidate run")
    p.add_argument("baseline")
    p.add_argument("candidate")
    p.add_argument("--expect-candidate-sha", help="fail unless the candidate run is for exactly this commit (CI)")
    p.add_argument("--repo", default=str(runner.REPO), help="git repository used to verify the claimed commits' content")
    p.set_defaults(func=cmd_compare)
    p = sub.add_parser("check", parents=[common], help="the regression gate: approved baseline vs candidate")
    p.add_argument("--candidate", default=str(runner.REPO))
    p.add_argument("--candidate-kind", default="working-tree", choices=runner.CANDIDATE_KINDS)
    p.add_argument("--repo", default=str(runner.REPO), help="git repository holding the approved baseline commit")
    p.add_argument("--expect-candidate-sha", help="fail unless the candidate checkout is exactly this commit (CI)")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("validate", help="validate one run directory from raw evidence")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser("trace-check", help="check a service-generated trace-v1 JSON Lines file (never reports PASS)")
    p.add_argument("trace_file")
    p.set_defaults(func=cmd_trace_check)
    p = sub.add_parser("backend", parents=[common], help="opt-in: evaluate the durable backend's real effects (NOT_RUN if absent)")
    p.add_argument("--backend", help="backend source tree (copied read-only; never written)")
    p.add_argument("--backend-python", help="Python with the backend's serving dependencies (default <backend>/.venv/bin/python)")
    p.add_argument("--case", action="append", help="run only this case (repeatable); the result is labelled a subset")
    p.add_argument("--pinned", action="store_true", help="evaluate exactly the commit in evalgate/backend-pin.json (NOT_RUN if unset)")
    p.add_argument("--backend-git", help="with --pinned: a git clone that holds the pinned backend commit")
    p.set_defaults(func=backend.main_backend)
    p = sub.add_parser("backend-validate", help="validate one backend run directory from raw observations")
    p.add_argument("run_dir")
    p.set_defaults(func=backend.main_validate)
    p = sub.add_parser("coverage", help="print the invariant coverage matrix")
    p.set_defaults(func=cmd_coverage)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
