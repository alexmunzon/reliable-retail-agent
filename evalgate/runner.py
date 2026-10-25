"""Run one SUT through the frozen suite and write an immutable evidence directory."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from uuid import uuid4

from . import scoring, trace

HARNESS_VERSION = "evalgate-harness-v1"
PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parent
POLICY_PATH = PACKAGE / "policy-v1.json"
DRIVER = PACKAGE / "driver.py"
ACTOR_CASE_KEYS = ("case_id", "adapter", "provider", "registry", "steps")
SUT_MODULES = ("rra.gate", "study.consent_current", "adapters.tau_dispatch")  # must match driver.SUT_MODULES
DRIVER_PROTOCOL = "evalgate-driver-v1"
COMPONENTS = ("rra/gate.py", "study/consent_current.py", "study/consent_as_well_candidate.py", "study/consent_candidate.py",
              "study/consent.py", "adapters/tau_dispatch.py", "rra/version.py")
OUTPUTS = ("request.json", "driver-output.json", "records.jsonl", "traces.jsonl", "metrics.json", "summary.md")
PRIVATE_KEYS = {"api_key", "authorization", "access_token", "refresh_token", "cookie", "environment", "headers", "password", "secret"}
CANDIDATE_KINDS = ("working-tree", "pr-head", "merge-revision", "approved-baseline", "mutant")


class EvaluationError(RuntimeError):
    pass


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def strict_json(text: str):
    def reject(constant):
        raise ValueError(f"non-finite number {constant}")
    return json.loads(text, parse_constant=reject)


def load_policy(path: Path = POLICY_PATH) -> dict:
    policy = strict_json(path.read_text(encoding="utf-8"))
    if policy.get("policy_id") != "evalgate-policy-v1":
        raise EvaluationError("unknown policy version")
    return policy


def load_suite(policy: dict) -> tuple[dict, dict]:
    folder = PACKAGE / "suites" / policy["suite"]["id"]
    cases_path, labels_path = folder / "cases.json", folder / "labels.json"
    if file_hash(cases_path) != policy["suite"]["cases_sha256"] or file_hash(labels_path) != policy["suite"]["labels_sha256"]:
        raise EvaluationError("suite files do not match the hashes frozen in the policy; a suite change needs a reviewed policy update")
    cases, labels = strict_json(cases_path.read_text(encoding="utf-8")), strict_json(labels_path.read_text(encoding="utf-8"))
    case_ids = [c["case_id"] for c in cases["cases"]]
    label_ids = [l["case_id"] for l in labels["labels"]]
    if len(set(case_ids)) != len(case_ids) or sorted(case_ids) != sorted(label_ids):
        raise EvaluationError("case and label identities are not one-to-one")
    return cases, labels


def harness_identity() -> dict:
    files = sorted(p for p in PACKAGE.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    combined = sha256("".join(f"{p.relative_to(PACKAGE)}:{file_hash(p)}\n" for p in files).encode()).hexdigest()
    return {"version": HARNESS_VERSION, "files_sha256": combined, **git_info(REPO)}


def git_info(root: Path) -> dict:
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
        return result.stdout.strip() if result.returncode == 0 else None
    sha = git("rev-parse", "HEAD") if (root / ".git").exists() else None
    status = git("status", "--porcelain") if sha else None
    return {"git_sha": sha, "dirty": None if status is None else bool(status)}


def commit_components(repo: Path, sha: str) -> dict | None:
    """Hashes of the SUT components as stored in commit `sha`, read from git itself (None if git cannot show it)."""
    check = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"], capture_output=True, timeout=30)
    if check.returncode:
        return None
    hashes = {}
    for component in COMPONENTS:
        blob = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", f"{sha}:{component}"], capture_output=True, timeout=30)
        if blob.returncode == 0:
            hashes[component] = sha256(blob.stdout).hexdigest()
    return hashes


def export_revision(repo: Path, sha: str, destination: Path) -> None:
    """Materialize an exact commit without touching any working tree."""
    archive = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", sha], capture_output=True, timeout=60)
    if archive.returncode:
        raise EvaluationError(f"cannot export {sha}: {archive.stderr.decode(errors='replace').strip()}")
    unpack = subprocess.run(["tar", "-x", "-C", str(destination)], input=archive.stdout, capture_output=True, timeout=60)
    if unpack.returncode:
        raise EvaluationError("cannot unpack exported revision")


def actor_request(cases: dict, sut_root: Path) -> dict:
    """Only actor-visible fields leave the harness; labels are never included."""
    return {"protocol": "evalgate-driver-v1", "sut_root": str(sut_root), "intents": cases["intents"],
            "cases": [{key: case[key] for key in ACTOR_CASE_KEYS if key in case} for case in cases["cases"]]}


def execute(sut_root: Path, request: dict, timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="evalgate-driver-") as scratch:
        result = subprocess.run([sys.executable, "-I", str(DRIVER)], input=json.dumps(request), capture_output=True,
                                text=True, timeout=timeout, cwd=scratch,
                                env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"})
    if result.returncode:
        raise EvaluationError("driver failed: " + result.stderr.strip()[-800:])
    return strict_json(result.stdout)


def _write(folder: Path, name: str, text: str) -> None:
    with open(folder / name, "x", encoding="utf-8") as handle:
        handle.write(text)


def _dumps(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=1) + "\n"


def run_decision(records, labels, trace_problems, termination, driver_error=None) -> tuple[str, list[str]]:
    """The one decision rule, used both when writing and when validating a run."""
    decision, reasons = scoring.decide(records, labels)
    if termination != "complete":
        decision, reasons = "INVALID", [f"Driver did not complete: {driver_error or 'incomplete run'}"] + reasons
    if trace_problems:
        decision, reasons = "INVALID", reasons + [f"trace contract: {p}" for p in trace_problems[:20]]
    return decision, reasons


def map_traces(run_id: str, labels: dict, observations: dict, classifier_version) -> list[dict]:
    return [e for l in labels["labels"] if l["case_id"] in observations
            for e in trace.map_case(run_id, l["case_id"], observations[l["case_id"]], str(classifier_version))]


def new_run_dir(out_root: Path, role: str) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = out_root / f"{stamp}-{role}-{uuid4().hex[:8]}"
    folder.mkdir()  # exclusive: never reuse or overwrite evidence
    return folder


def run(sut_root: Path, *, role: str, out_root: Path, candidate_kind: str, harness_trust: str = "local",
        sut_git: dict | None = None, policy_path: Path = POLICY_PATH) -> tuple[Path, str]:
    if candidate_kind not in CANDIDATE_KINDS:
        raise EvaluationError("unknown candidate kind")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    policy = load_policy(policy_path)
    cases, labels = load_suite(policy)
    sut_root = sut_root.resolve()
    folder = new_run_dir(out_root, role)
    run_id = folder.name
    request = actor_request(cases, sut_root)
    termination, driver_error = "complete", None
    try:
        output = execute(sut_root, request, policy["driver_timeout_seconds"])
    except (EvaluationError, subprocess.TimeoutExpired, ValueError) as error:
        output, termination, driver_error = {"protocol": None, "loaded": {}, "results": []}, "incomplete", str(error)[:800]
    observations = {r["case_id"]: r for r in output["results"]}
    case_by_id = {c["case_id"]: c for c in cases["cases"]}
    records = [scoring.score_case(l, case_by_id[l["case_id"]], observations.get(l["case_id"]), cases["intents"], labels["patches"], run_id)
               for l in labels["labels"]]
    metrics = scoring.compute_metrics(records, labels["labels"])
    events = map_traces(run_id, labels, observations, output.get("classifier_version"))
    decision, reasons = run_decision(records, labels["labels"], trace.validate_trace(events), termination, driver_error)
    _write(folder, "request.json", _dumps(request))
    _write(folder, "driver-output.json", _dumps(output))
    _write(folder, "records.jsonl", "".join(json.dumps(r, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n" for r in records))
    _write(folder, "traces.jsonl", "".join(json.dumps(e, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n" for e in events))
    _write(folder, "metrics.json", _dumps(metrics))
    _write(folder, "summary.md", render_summary(run_id, role, decision, reasons, metrics, records))
    manifest = {
        "schema_version": "evaluation-v1", "record_type": "run_manifest", "run_id": run_id, "role": role,
        "candidate_kind": candidate_kind, "harness_trust": harness_trust, "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "termination": termination,
        "decision": decision, "reasons": reasons,
        "harness": harness_identity(), "policy": {"id": policy["policy_id"], "sha256": file_hash(policy_path)},
        "suite": {"id": policy["suite"]["id"], "cases_sha256": policy["suite"]["cases_sha256"],
                  "labels_sha256": policy["suite"]["labels_sha256"], "scheduled_cases": len(labels["labels"]),
                  "independence": cases["provenance"]["independence"]},
        "sut": {"root_name": sut_root.name, **(sut_git or git_info(sut_root)),
                "components": {c: file_hash(sut_root / c) for c in COMPONENTS if (sut_root / c).is_file()},
                "loaded_modules": output["loaded"]},
        "runtime": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                        "platform": platform.platform(terse=True)},
        "command": [Path(sys.argv[0]).name, *sys.argv[1:]], "evidence_class": "scripted_fixture",
        "seeds": "none: deterministic fixtures", "costs": "no model or paid calls; runtime measured by caller",
        "outputs": {name: file_hash(folder / name) for name in OUTPUTS},
    }
    _write(folder, "manifest.json", _dumps(manifest))
    return folder, decision


def render_summary(run_id, role, decision, reasons, metrics, records) -> str:
    lines = [f"# Evaluation run `{run_id}`", "", f"Role: **{role}** · Decision: **{decision}** · Evidence class: scripted_fixture "
             "(deterministic synthetic fixtures, no model calls)", "", "| Metric | Stratum | n/N | Value | Complete |", "|---|---|---|---|---|"]
    for m in metrics:
        value = "n/a" if m["value"] is None else f"{m['value']:.3f}"
        lines.append(f"| {m['metric_id']} | {m['stratum']} | {m['numerator']}/{m['denominator']} | {value} | {m['complete']} |")
    failing = [r for r in records if r["verdict"] != "pass"]
    lines += ["", f"## Non-passing cases ({len(failing)})", ""]
    for r in failing:
        gating = "gating" if r["normative"] else "exploratory, non-gating"
        lines.append(f"- `{r['case_id']}` ({gating}): {', '.join(r['failure_classes'])}. " + " ".join(r.get("details", [])))
    if reasons:
        lines += ["", "## Decision reasons", ""] + [f"- {reason}" for reason in reasons]
    return "\n".join(lines) + "\n"


def _scan_private(value, path="$") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.lower() in PRIVATE_KEYS:
                found.append(f"{path}.{key}")
            found += _scan_private(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found += _scan_private(child, f"{path}[{index}]")
    return found


def validate_run(folder: Path, policy_path: Path = POLICY_PATH) -> tuple[list[str], dict, list[dict]]:
    """Check linkage and re-score raw evidence BEFORE any stored verdict is trusted."""
    problems: list[str] = []
    try:
        manifest = strict_json((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"manifest unreadable: {error}"], {}, []
    if manifest.get("schema_version") != "evaluation-v1" or manifest.get("record_type") != "run_manifest":
        problems.append("unknown manifest version")
    if manifest.get("harness", {}).get("version") != HARNESS_VERSION:
        problems.append("run was produced by a different harness version")
    if manifest.get("run_id") != folder.name:
        problems.append("manifest run_id does not match its directory")
    listed = manifest.get("outputs", {})
    if set(listed) != set(OUTPUTS):
        problems.append("manifest does not list exactly the expected outputs")
    for name, digest in listed.items():
        path = folder / name
        if not path.is_file() or file_hash(path) != digest:
            problems.append(f"{name} is missing or does not match its manifest hash")
    if problems:
        return problems, manifest, []
    policy = load_policy(policy_path)
    if manifest["policy"]["sha256"] != file_hash(policy_path):
        problems.append("run used a different policy than the trusted one")
    if (manifest["suite"]["cases_sha256"], manifest["suite"]["labels_sha256"]) != (policy["suite"]["cases_sha256"], policy["suite"]["labels_sha256"]):
        problems.append("run used a different suite than the trusted policy")
    cases, labels = load_suite(policy)
    try:
        records = [strict_json(line) for line in (folder / "records.jsonl").read_text(encoding="utf-8").splitlines()]
        output = strict_json((folder / "driver-output.json").read_text(encoding="utf-8"))
        metrics = strict_json((folder / "metrics.json").read_text(encoding="utf-8"))
    except ValueError as error:
        return problems + [f"evidence is not strict JSON: {error}"], manifest, []
    ids = [r.get("case_id") for r in records]
    if len(ids) != len(set(ids)):
        problems.append("duplicate case records")
    if sorted(ids) != sorted(l["case_id"] for l in labels["labels"]):
        problems.append("records do not match the scheduled cases")
    if any(r.get("schema_version") != "evaluation-v1" or r.get("run_id") != manifest["run_id"] for r in records):
        problems.append("record with unknown version or foreign run_id")
    try:
        request = strict_json((folder / "request.json").read_text(encoding="utf-8"))
        exported = [strict_json(line) for line in (folder / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    except ValueError as error:
        return problems + [f"evidence is not strict JSON: {error}"], manifest, []
    if not isinstance(request, dict) or request != actor_request(cases, Path(str(request.get("sut_root", "")))):
        problems.append("request sent to the SUT does not match the frozen actor-visible suite")
    if manifest.get("termination") != "complete":
        problems.append("run did not complete; incomplete runs are never valid evidence")
    if output.get("protocol") != DRIVER_PROTOCOL:
        problems.append("unknown driver protocol")
    raw_ids = [r.get("case_id") for r in output.get("results", [])]
    if len(raw_ids) != len(set(raw_ids)) or sorted(raw_ids) != sorted(l["case_id"] for l in labels["labels"]):
        problems.append("raw results do not cover each scheduled case exactly once")
    if manifest.get("harness", {}).get("files_sha256") != harness_identity()["files_sha256"]:
        problems.append("run was produced by different harness files than the harness validating it")
    loaded = output.get("loaded", {})
    if loaded != manifest["sut"]["loaded_modules"]:
        problems.append("loaded-module evidence disagrees with the manifest")
    for name in SUT_MODULES:
        entry = loaded.get(name) or {}
        if not entry.get("sha256") or manifest["sut"]["components"].get(entry.get("file")) != entry["sha256"]:
            problems.append(f"{name}: imported module is missing or is not the file hashed in the manifest")
    observations = {r["case_id"]: r for r in output.get("results", [])}
    events = map_traces(manifest["run_id"], labels, observations, output.get("classifier_version"))
    if scoring.canon(events) != scoring.canon(exported):
        problems.append("exported traces do not regenerate from the raw driver output")
    trace_problems = trace.validate_trace(events)
    case_by_id = {c["case_id"]: c for c in cases["cases"]}
    rescored = [scoring.score_case(l, case_by_id[l["case_id"]], observations.get(l["case_id"]), cases["intents"], labels["patches"], manifest["run_id"])
                for l in labels["labels"]]
    stored = {r.get("case_id"): r for r in records}
    for fresh in rescored:
        if scoring.canon(stored.get(fresh["case_id"])) != scoring.canon(fresh):
            problems.append(f"{fresh['case_id']}: stored record does not match a re-score of raw evidence")
    if scoring.canon(metrics) != scoring.canon(scoring.compute_metrics(rescored, labels["labels"])):
        problems.append("stored metrics do not match re-scored records")
    decision, _ = run_decision(rescored, labels["labels"], trace_problems, manifest.get("termination"))
    if manifest.get("decision") != decision:
        problems.append(f"manifest decision {manifest.get('decision')} does not match the re-derived decision {decision}")
    private = _scan_private(output) + _scan_private(records) + _scan_private(manifest)
    if private:
        problems.append(f"disallowed private fields: {private[:5]}")
    for metric in metrics:
        if metric["value"] is not None and not math.isfinite(metric["value"]):
            problems.append("non-finite metric")
    return problems, manifest, rescored
