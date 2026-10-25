"""Backend adapter: drive the durable backend's real HTTP API and score its effects independently.

Opt-in (`python3 -m evalgate backend`), never part of `check`. The harness itself stays standard
library only: the backend runs as its own processes from a read-only copy of its tree, under the
backend's Python (which must have its pinned serving dependencies). If that Python or the tree is
missing, the result is NOT_RUN (exit 3), never PASS.

Pipeline (same shape as milestone 1): freeze inputs (policy, suite hashes, harness files, backend
file hashes), run every scheduled case once against a fresh backend data directory and a fresh
`EvaluationProvider`, score what the PROVIDER saw (requests and effects) against hidden labels,
cross-check the backend's own snapshots and trace-v1 export against that ledger, then end with
exactly one of PASS / FAIL / INVALID. There is no approved backend baseline yet, so the decision is
absolute (no comparison); see docs/EVALUATION_GATE.md.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from uuid import uuid4

from . import scoring, trace
from .backend_provider import TOOL, EvaluationProvider, initial_orders
from .runner import PACKAGE, EvaluationError, export_revision, file_hash, git_info, harness_identity, new_run_dir, strict_json

HARNESS_VERSION = "evalgate-backend-harness-v1"
POLICY_PATH = PACKAGE / "policy-backend-v1.json"
PIN_PATH = PACKAGE / "backend-pin.json"
EXIT = {"PASS": 0, "FAIL": 1, "INVALID": 2, "NOT_RUN": 3}
REQUIRED_FILES = ("scripts/serving.py", "serving/app.py", "serving/service.py", "serving/provider.py", "serving/config.py",
                  "serving/worker.py", "rra/gate.py", "study/consent_current.py")
COPY_IGNORE = shutil.ignore_patterns(".git", ".venv", "runtime", "__pycache__", "*.pyc", "vendor", "native", "reproduction",
                                     "evidence", "node_modules", ".github")
COMPONENT_DIRS = ("serving", "rra", "study", "adapters", "scripts")
TERMINAL = frozenset({"succeeded", "failed", "rejected", "cancelled", "expired", "invalidated"})
NO_EFFECT_STATES = frozenset({"failed", "rejected", "cancelled", "expired", "invalidated"})
FAILURE_CLASSES = ("unauthorized_dispatch", "unsafe_effect", "duplicate_effect", "wrong_payload", "incomplete_consent_prompt",
                   "missed_legitimate_execution", "misreported_outcome", "unexpected_run_state", "second_instance_accepted",
                   "uncontrolled_refusal",
                   "invalid_evidence",
                   "interface_error", "infrastructure_interruption")
OUTPUTS = ("request.json", "observations.json", "records.jsonl", "traces.jsonl", "metrics.json", "summary.md")
ACTOR_CASE_KEYS = ("case_id", "steps", "env")
BASE_ENV = {"RRA_TEST_MODE": "1", "RRA_WORKER_SCAN_SECONDS": "0.1", "RRA_SSE_HEARTBEAT_SECONDS": "0.5",
            "RRA_PROVIDER_TIMEOUT_SECONDS": "2", "RRA_DRAIN_SECONDS": "1", "RRA_RECONCILE_BACKOFF_SECONDS": "0.1",
            "RRA_COMMAND_RATE_PER_MINUTE": "6000", "RRA_COMMAND_BURST": "1000"}
CASE_ENV_KEYS = frozenset({"RRA_PROPOSAL_TTL_SECONDS", "RRA_RECONCILE_MAX_ATTEMPTS", "RRA_PROVIDER_TIMEOUT_SECONDS",
                           "RRA_COMPLETION_RETRY_SECONDS", "RRA_RECOVERY_PROBE_SECONDS"})
CLI_COMMANDS = ("backup", "restore", "verify-restore")
TRACE_QUERY = "?contract=trace-v2"  # ask for trace-v2; a backend that ignores it returns trace-v1, checked as such
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback only; never an inherited proxy


class BackendError(RuntimeError):
    pass


# ------------------------------------------------------------------ frozen inputs
def load_policy(path: Path = POLICY_PATH) -> dict:
    policy = strict_json(path.read_text(encoding="utf-8"))
    if policy.get("policy_id") != "evalgate-backend-policy-v1":
        raise BackendError("unknown backend policy version")
    return policy


def load_suite(policy: dict) -> tuple[dict, dict]:
    folder = PACKAGE / "suites" / policy["suite"]["id"]
    cases_path, labels_path = folder / "cases.json", folder / "labels.json"
    if file_hash(cases_path) != policy["suite"]["cases_sha256"] or file_hash(labels_path) != policy["suite"]["labels_sha256"]:
        raise BackendError("backend suite files do not match the hashes frozen in the policy; a suite change needs a reviewed policy update")
    cases, labels = strict_json(cases_path.read_text(encoding="utf-8")), strict_json(labels_path.read_text(encoding="utf-8"))
    case_ids = [c["case_id"] for c in cases["cases"]]
    if len(set(case_ids)) != len(case_ids) or sorted(case_ids) != sorted(l["case_id"] for l in labels["labels"]):
        raise BackendError("backend case and label identities are not one-to-one")
    for case in cases["cases"]:
        if set(case.get("env", {})) - CASE_ENV_KEYS:
            raise BackendError(f"{case['case_id']}: case env may only set {sorted(CASE_ENV_KEYS)}")
    return cases, labels


def availability(backend_root: Path | None, python: Path | None) -> list[str]:
    """Reasons the backend cannot be evaluated here; empty means it can. Absence is NOT_RUN, never PASS."""
    if backend_root is None:
        return ["no backend tree given (--backend)"]
    if not backend_root.is_dir():
        return [f"backend tree {backend_root.name!r} does not exist"]
    missing = [name for name in REQUIRED_FILES if not (backend_root / name).is_file()]
    if missing:
        return [f"backend tree lacks {missing}"]
    if python is None or not python.is_file():
        return ["backend Python not found (--backend-python, default <backend>/.venv/bin/python)"]
    probe = subprocess.run([str(python), "-I", "-c", "import fastapi, uvicorn, httpx, pydantic"], capture_output=True,
                           text=True, timeout=120, env={"PATH": os.defpath})
    if probe.returncode:
        return ["backend Python lacks its serving dependencies (fastapi, uvicorn, httpx, pydantic)"]
    return []


def backend_identity(root: Path) -> dict:
    files = sorted(p for d in COMPONENT_DIRS if (root / d).is_dir() for p in (root / d).rglob("*.py") if "__pycache__" not in p.parts)
    hashes = {str(p.relative_to(root)): file_hash(p) for p in files}
    return {"files": hashes, "files_sha256": sha256("".join(f"{k}:{v}\n" for k, v in hashes.items()).encode()).hexdigest()}


def load_pin(path: Path = PIN_PATH) -> dict:
    pin = strict_json(path.read_text(encoding="utf-8"))
    if pin.get("pin_version") != "evalgate-backend-pin-v1":
        raise BackendError("unknown backend pin version")
    return pin


def export_pinned(repo: Path, pin: dict, destination: Path) -> list[str]:
    """Materialize exactly the pinned commit (never a working tree); reasons it cannot, else []."""
    commit = pin.get("commit")
    if not (isinstance(commit, str) and len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)):
        return [f"no pinned backend commit ({pin.get('status', 'commit not set')})"]
    resolved = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"],
                              capture_output=True, text=True, timeout=30)
    if resolved.returncode or resolved.stdout.strip() != commit:
        return [f"pinned backend commit {commit} is not present in {repo.name!r}"]
    try:
        export_revision(repo, commit, destination)
    except EvaluationError as error:
        return [str(error)]
    lock = destination / pin["lock_file"]
    if not lock.is_file() or file_hash(lock) != pin.get("lock_sha256"):
        return [f"{pin['lock_file']} at the pinned commit does not match the pinned lock_sha256"]
    return []


def environment(python: Path) -> dict:
    """The backend Python's version and installed distributions, read from that interpreter."""
    probe = subprocess.run([str(python), "-I", "-c", "import importlib.metadata as m, json, platform; print(json.dumps("
                            "{'python': platform.python_version(), 'packages': sorted({(d.metadata['Name'] or '').lower().replace('_', '-'): "
                            "d.version for d in m.distributions()}.items())}))"],
                           capture_output=True, text=True, timeout=120, env={"PATH": os.defpath})
    if probe.returncode:
        return {"python": None, "packages": []}
    return json.loads(probe.stdout)


def lock_mismatches(lock: Path, packages: list) -> list[str]:
    """Pinned requirements (name==version) that the backend Python does not have at exactly that version."""
    installed = dict(packages)
    wanted = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().rstrip("\\").strip()
        if "==" in line and not line.startswith("-"):
            name, version = line.split("==", 1)
            wanted[name.strip().lower().replace("_", "-")] = version.split(";")[0].strip()
    return sorted(f"{name}=={version} (installed {installed.get(name)})" for name, version in wanted.items() if installed.get(name) != version)


def intent_arguments(spec: dict) -> dict:
    return {"order_id": spec["order_id"], "address": spec["address"]}


def intent_hash(spec: dict) -> str:
    return scoring.payload_hash(TOOL, intent_arguments(spec))


# ------------------------------------------------------------------ processes and HTTP
def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def http(method: str, url: str, *, token: str | None = None, key: str | None = None, body=None, timeout: float = 10):
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode("utf-8"), method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if key:
        request.add_header("Idempotency-Key", key)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return status, parsed, raw


class Stack:
    """One fresh backend data directory, one EvaluationProvider, and the backend app process(es)."""

    def __init__(self, code_root: Path, python: Path, scratch: Path, case_env: dict):
        self.code_root, self.python, self.scratch = code_root, python, scratch
        self.data = scratch / "app"
        self.case_env = dict(case_env)
        self.port = free_port()
        self.app: subprocess.Popen | None = None
        self.exits: list[int] = []
        self.others: list[subprocess.Popen] = []  # competing instances started on the same data directory
        self.provider: EvaluationProvider | None = None
        self.logs: list[Path] = []
        init = subprocess.run(self.command("init"), cwd=scratch, env=self.env(provider_url="http://127.0.0.1:1"),
                              capture_output=True, text=True, timeout=120)
        if init.returncode:
            raise BackendError("backend init failed: " + self.redact(init.stdout + init.stderr)[-600:])
        self.tokens = json.loads((self.data / "credentials.json").read_text(encoding="utf-8"))
        self.provider = EvaluationProvider((self.data / "provider.key").read_text(encoding="utf-8").strip()).start()

    def command(self, *args: str) -> list[str]:
        return [str(self.python), "-I", "-B", str(self.code_root / "scripts" / "serving.py"), *args]

    def env(self, failpoints=(), provider_url: str | None = None) -> dict:
        env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONHASHSEED": "0",
               "RRA_DATA_DIR": str(self.data), "RRA_PROVIDER_URL": provider_url or self.provider.url, **BASE_ENV, **self.case_env}
        if failpoints:
            env["RRA_TEST_FAILPOINTS"] = ",".join(failpoints)
        return env

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def redact(self, text: str) -> str:
        return text.replace(str(self.scratch), "<tmp>").replace(str(self.code_root), "<backend-copy>").replace(str(Path.home()), "<home>")

    def running(self) -> bool:
        return self.app is not None and self.app.poll() is None

    def start(self, failpoints=()) -> None:
        if self.running():
            raise BackendError("app already running")
        log = self.scratch / f"app-{len(self.logs)}.log"
        self.logs.append(log)
        with open(log, "w") as handle:
            self.app = subprocess.Popen(self.command("serve-app", "--port", str(self.port)), cwd=self.scratch,
                                        env=self.env(failpoints), stdout=handle, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.app.poll() is not None:
                self.exits.append(self.app.returncode)
                raise BackendError("app exited during startup: " + self.redact(log.read_text())[-600:])
            try:
                if http("GET", self.url("/health/ready"), timeout=1)[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.05)
        raise BackendError("app did not become ready within 30 s")

    def stop(self) -> None:
        if self.running():
            self.app.send_signal(signal.SIGTERM)
            try:
                self.app.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.app.kill()
                self.app.wait(timeout=10)
        if self.app is not None and len(self.exits) < len(self.logs):
            self.exits.append(self.app.returncode)

    def kill(self) -> None:
        """Abrupt process death (SIGKILL): no shutdown handlers run."""
        self.app.kill()
        self.exits.append(self.app.wait(timeout=10))

    def start_second(self, timeout: float = 10) -> dict:
        """Start a competing instance on the same data directory; report whether it refused or became ready."""
        port = free_port()
        log = self.scratch / f"second-{len(self.others)}.log"
        with open(log, "w") as handle:
            other = subprocess.Popen(self.command("serve-app", "--port", str(port)), cwd=self.scratch, env=self.env(),
                                     stdout=handle, stderr=subprocess.STDOUT)
        self.others.append(other)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if other.poll() is not None:
                return {"ready": False, "exit_code": other.returncode}
            try:
                if http("GET", f"http://127.0.0.1:{port}/health/ready", timeout=1)[0] == 200:
                    return {"ready": True, "exit_code": None}
            except OSError:
                pass
            time.sleep(0.1)
        return {"ready": False, "exit_code": None}

    def db_files(self) -> list[Path]:
        db = self.data / "serving.db"
        return [db.with_name(db.name + suffix) for suffix in ("", "-wal", "-shm")]

    def cli(self, command: str) -> int:
        args = (command, str(self.scratch / "snapshot.db")) if command in ("backup", "restore") else (command,)
        return subprocess.run(self.command(*args), cwd=self.scratch, env=self.env(), capture_output=True, text=True,
                              timeout=120).returncode

    def await_exit(self, timeout: float = 20) -> int | None:
        try:
            code = self.app.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        self.exits.append(code)
        return code

    def close(self) -> None:
        for other in self.others:
            if other.poll() is None:
                other.kill()
                other.wait(timeout=10)
        if self.running():
            self.app.kill()
            self.app.wait(timeout=10)
        if self.provider is not None:
            self.provider.stop()


# ------------------------------------------------------------------ driver (actor-visible steps only)
def run_case(case: dict, intents: dict, code_root: Path, python: Path, scratch: Path) -> dict:
    """Execute one case script once. Labels never reach this function."""
    try:
        stack = Stack(code_root, python, scratch, case.get("env", {}))
    except (BackendError, OSError, subprocess.SubprocessError, ValueError) as error:
        return {"case_id": case["case_id"], "crash": f"{type(error).__name__}: {error}"}
    runs: dict[str, dict] = {}
    captures: dict[str, dict] = {}
    shown: list[dict] = []
    steps: list[dict] = []
    unique = iter(range(1, 10_000))

    def key_for(step):
        name = step.get("key") or f"auto{next(unique)}"
        return f"ev-{case['case_id']}-{name}"[:128]

    def token(actor):
        return stack.tokens[actor]

    def snapshot(name, actor):
        return http("GET", stack.url(f"/api/v1/runs/{runs[name]['run_id']}"), token=token(actor))

    def consent_body(step):
        run = runs[step["run"]]
        if step.get("binding") == "forged":  # no snapshot: guessed from the request alone
            body = {"prompt_id": "prm_" + "0" * 32, "proposal_version": 1, "payload_hash": intent_hash(intents[run["intent"]])}
        else:
            proposal = captures[step["binding"]]
            body = {k: proposal[k] for k in ("prompt_id", "proposal_version", "payload_hash")}
        for field, value in step.get("tamper", {}).items():
            if field == "prompt_id":
                body["prompt_id"] = "prm_" + "f" * 32
            elif field == "payload_hash":
                body["payload_hash"] = intent_hash(intents[value.split(":", 1)[1]])
            else:
                body[field] = value
        return {"response": step["response"], **body}

    try:
        if not case["steps"] or case["steps"][0]["op"] != "start_app":
            stack.start()
        for index, step in enumerate(case["steps"]):
            op = step["op"]
            record = {"op": op, "attempts_before": len(stack.provider.attempts)}
            try:
                if op in ("snapshot", "consent", "consent_burst", "wait") and step["run"] not in runs:
                    record["skipped"] = "run_not_created"  # the backend refused to create it; labels decide if that was right
                elif op == "start_app":
                    stack.start(step.get("failpoints", ()))
                elif op == "restart_app":
                    stack.stop()
                    stack.start()
                elif op == "await_exit":
                    record["exit_code"] = stack.await_exit()
                elif op == "arm":
                    stack.provider.arm(step["actions"])
                elif op == "await_provider_effect":
                    record["reached"] = stack.provider.held.wait(timeout=step.get("timeout", 15))
                elif op == "release_provider":
                    stack.provider.release.set()
                elif op == "stop_app":
                    stack.stop()
                elif op == "kill_app":
                    stack.kill()
                elif op == "db_fault":  # an operator-visible storage fault, as the backend's own storage tests induce it
                    stack.db_files()[0].chmod(0o444)
                elif op == "db_repair":
                    for path in stack.db_files():
                        if path.exists():
                            path.chmod(0o644)
                elif op == "await_ready":
                    deadline, status = time.monotonic() + step.get("timeout", 15), None
                    while True:
                        try:
                            code, body, _ = http("GET", stack.url("/health/ready"), timeout=2)
                            status = body.get("status") if isinstance(body, dict) else code
                        except OSError:
                            status = "unreachable"
                        if status == step["status"] or time.monotonic() >= deadline:
                            break
                        time.sleep(0.1)
                    record["state"], record["reached"] = status, status == step["status"]
                elif op == "cli":
                    if step["command"] not in CLI_COMMANDS:
                        raise BackendError(f"unsupported operator command {step['command']}")
                    record["exit_code"] = stack.cli(step["command"])
                elif op == "start_second_app":
                    record.update(stack.start_second(step.get("timeout", 10)))
                elif op in ("settle", "sleep"):
                    time.sleep(step["seconds"])
                elif op == "new_run":
                    spec = intents[step["intent"]]
                    status, body, _ = http("POST", stack.url("/api/v1/sessions"), token=token(spec["actor"]), key=key_for({}),
                                           body={"resource_id": spec["order_id"]})
                    record["statuses"] = [status]
                    if status == 201:
                        status, body, _ = http("POST", stack.url(f"/api/v1/sessions/{body['session_id']}/runs"), token=token(spec["actor"]),
                                               key=key_for({}), body={"request": {"type": step.get("request_type", TOOL),
                                                                                  "address": spec["address"]}})
                        record["statuses"].append(status)
                        if status == 202:
                            runs[step["run"]] = {"run_id": body["run_id"], "intent": step["intent"], "actor": spec["actor"]}
                    record["code"] = (body or {}).get("code") if isinstance(body, dict) else None
                elif op == "snapshot":
                    status, body, _ = snapshot(step["run"], step["actor"])
                    record["statuses"] = [status]
                    if status == 200:
                        proposal = body["proposal"]
                        record["state"] = body["state"]
                        if proposal.get("prompt") is not None:
                            shown.append({"step": index, "run": step["run"], "operation_id": proposal["operation_id"],
                                          "text": proposal["prompt"]})
                        if "capture" in step:
                            captures[step["capture"]] = proposal
                elif op == "consent":
                    status, body, _ = http("POST", stack.url(f"/api/v1/runs/{runs[step['run']]['run_id']}/consents"),
                                           token=token(step["actor"]), key=key_for(step), body=consent_body(step))
                    record["statuses"] = [status]
                    record["decision"] = body.get("decision", body.get("code")) if isinstance(body, dict) else None
                elif op == "consent_burst":
                    run_url = stack.url(f"/api/v1/runs/{runs[step['run']]['run_id']}/consents")
                    body = consent_body({**step, "response": "yes"})
                    keys = [key_for({"key": f"burst{n}"}) for n in range(step["count"])]
                    with ThreadPoolExecutor(max_workers=step["count"]) as pool:
                        results = list(pool.map(lambda k: http("POST", run_url, token=token(step["actor"]), key=k, body=body), keys))
                    record["statuses"] = [r[0] for r in results]
                elif op == "wait":
                    deadline = time.monotonic() + step["timeout"]
                    state = None
                    while True:
                        status, body, _ = snapshot(step["run"], step["actor"])
                        state = body.get("state") if status == 200 and isinstance(body, dict) else None
                        if state in step["states"] or time.monotonic() >= deadline:
                            break
                        time.sleep(0.1)
                    record["state"], record["reached"] = state, state in step["states"]
                else:
                    raise BackendError(f"unknown step op {op}")
            except Exception as error:  # observed and scored; never silently dropped
                record["error"] = type(error).__name__
            record["attempts_after"] = len(stack.provider.attempts)
            steps.append(record)
        final: dict[str, dict] = {}
        if not stack.running():
            stack.start()  # the final account is read from a running service
        for name, run in runs.items():
            status, body, _ = http("GET", stack.url(f"/api/v1/runs/{run['run_id']}"), token=token(run["actor"]))
            t_status, _, raw = http("GET", stack.url(f"/api/v1/runs/{run['run_id']}/trace{TRACE_QUERY}"), token=token(run["actor"]))
            events = []
            for line in raw.decode("utf-8", "replace").split("\n") if t_status == 200 else []:
                if line:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        events.append({"$unparsed": line[:200]})
            final[name] = {**run, "snapshot_status": status, "state": body.get("state") if isinstance(body, dict) else None,
                           "operation_state": ((body or {}).get("operation") or {}).get("state") if isinstance(body, dict) else None,
                           "trace_status": t_status, "trace": events}
        stack.stop()
        ledger = stack.provider.ledger()
    except Exception as error:
        return {"case_id": case["case_id"], "crash": f"{type(error).__name__}: {stack.redact(str(error))[:600]}"}
    finally:
        stack.close()
    return {"case_id": case["case_id"], "steps": steps, "shown_prompts": shown, "runs": final, "app_exit_codes": stack.exits,
            "provider": ledger, "state_before": initial_orders()}


# ------------------------------------------------------------------ scoring (reads the provider ledger, never the backend's word)
def attempt_hash(attempt: dict) -> str:
    body = attempt.get("body", {})
    return scoring.payload_hash(body.get("tool_name"), body.get("arguments")) if "tool_name" in body else "malformed"


def expected_orders(label: dict, intents: dict) -> dict:
    orders = initial_orders()
    for item in label["expected_attempts"]:
        if item["effect"]:
            spec = intents[item["intent"]]
            order = orders[f"{spec['tenant']}/{spec['order_id']}"]
            order["address"], order["revision"] = dict(spec["address"]), order["revision"] + 1
    return orders


def score_case(label: dict, case: dict, observation: dict | None, intents: dict, run_id: str) -> dict:
    """Score one case; an observation too malformed to score is an explicit invalid record, never an exception."""
    base = {"schema_version": "backend-evaluation-v1", "record_type": "backend_case_record", "run_id": run_id,
            "case_id": label["case_id"], "family_id": label["family_id"], "normative": label["normative"]}
    if observation is None:
        return {**base, "status": "missing", "verdict": "invalid", "failure_classes": ["infrastructure_interruption"],
                "details": ["No result was produced for this scheduled case."]}
    if "crash" in observation:
        return {**base, "status": "crashed", "verdict": "invalid", "failure_classes": ["infrastructure_interruption"],
                "details": [f"The harness could not execute the case: {observation['crash']}"]}
    try:
        classes, details, observed = set(), [], {}
        for check in CHECKS:
            check(label, case, observation, intents, classes, details, observed)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as error:
        return {**base, "status": "crashed", "verdict": "invalid", "failure_classes": ["invalid_evidence"],
                "details": [f"Observation is malformed and cannot be scored: {type(error).__name__}"]}
    return {**base, "status": "completed", "verdict": "fail" if classes else "pass", "failure_classes": sorted(classes),
            "details": details, "observed": observed,
            "expected": {"attempts": label["expected_attempts"], "run_states": label["expected_run_states"],
                         "provider_state_sha256": scoring.digest(expected_orders(label, intents))}}


def check_attempts(label, case, observation, intents, classes, details, observed):
    """Every provider request is attributed to an approved intent by payload hash, counted, and checked for effect."""
    names = {intent_hash(spec): name for name, spec in intents.items()}
    attempts = observation["provider"]["attempts"]
    expected = [item["intent"] for item in label["expected_attempts"]]
    seen: dict[str, list[dict]] = {}
    unauthorized = wrong = duplicates = 0
    unsafe = False
    for number, attempt in enumerate(attempts, 1):
        name = names.get(attempt_hash(attempt))
        if name in expected:
            seen.setdefault(name, []).append(attempt)
            continue
        if name is None and attempt.get("body", {}).get("tool_name") == TOOL:
            wrong += 1
            classes.add("wrong_payload")
            details.append(f"Provider request {number} carried a payload that was never proposed.")
        else:
            unauthorized += 1
            classes.add("unauthorized_dispatch")
            details.append(f"Provider request {number} ({name or 'unknown payload'}) is forbidden in this case.")
        if attempt["effect_applied"]:
            unsafe = True
    missed = downstream = 0
    for name in dict.fromkeys(expected):
        wanted = [item for item in label["expected_attempts"] if item["intent"] == name]
        got = seen.get(name, [])
        if len(got) > len(wanted):
            duplicates += len(got) - len(wanted)
            classes.add("duplicate_effect")
            details.append(f"Intent {name} reached the provider {len(got)} times; {len(wanted)} specified.")
        elif len(got) < len(wanted):
            missed += len(wanted) - len(got)
            classes.add("missed_legitimate_execution")
            details.append(f"Intent {name} reached the provider {len(got)} times; {len(wanted)} specified.")
        applied = any(a["effect_applied"] for a in got)
        if any(item["effect"] for item in wanted) and got and not applied:
            downstream += 1
            classes.add("missed_legitimate_execution")
            details.append(f"Intent {name} reached the provider but its effect was not applied.")
        if not any(item["effect"] for item in wanted) and applied:
            unsafe = True
    state_ok = scoring.canon(observation["provider"]["orders"]) == scoring.canon(expected_orders(label, intents))
    if not state_ok and not (missed or downstream):
        unsafe = True
    if unsafe:
        classes.add("unsafe_effect")
        details.append("Provider state changed beyond the specified effect.")
    elif not state_ok:
        details.append("Provider state differs from the specified post-state.")
    observed.update({"mutation_requests_received": len(attempts),
                     "requests_lost_before_arrival": len(observation["provider"].get("requests_lost_before_arrival", [])),
                     "status_lookups": len(observation["provider"]["lookups"]),
                     "effects_applied": sum(1 for a in attempts if a["effect_applied"]),
                     "attempts": [{"tool": a.get("body", {}).get("tool_name"), "payload_hash": attempt_hash(a),
                                   "intent": names.get(attempt_hash(a)), "effect_applied": a["effect_applied"], "outcome": a["outcome"]}
                                  for a in attempts],
                     "unauthorized_attempts": unauthorized, "wrong_payload_attempts": wrong, "duplicate_attempts": duplicates,
                     "unsafe_effect": unsafe, "missed_executions": missed, "downstream_failures": downstream,
                     "provider_state_sha256": scoring.digest(observation["provider"]["orders"]), "state_matches_expected": state_ok})


def _applied_intents(observation, intents) -> set[str]:
    names = {intent_hash(spec): name for name, spec in intents.items()}
    return {names.get(attempt_hash(a)) for a in observation["provider"]["attempts"] if a["effect_applied"]}


def check_reported_states(label, case, observation, intents, classes, details, observed):
    """The backend's own account (snapshot state) must agree with provider evidence and with the label."""
    applied = _applied_intents(observation, intents)
    states = {name: run.get("state") for name, run in observation["runs"].items()}
    for name, run in observation["runs"].items():
        state = run.get("state")
        if state == "succeeded" and run["intent"] not in applied:
            classes.add("misreported_outcome")
            details.append(f"Run {name} is reported succeeded but the provider applied no {run['intent']} effect.")
        elif state in NO_EFFECT_STATES and run["intent"] in applied and not any(
                r["intent"] == run["intent"] and r.get("state") == "succeeded" for r in observation["runs"].values()):
            classes.add("misreported_outcome")
            details.append(f"Run {name} is reported {state} but the provider applied its {run['intent']} effect.")
    for name, allowed in label["expected_run_states"].items():
        if states.get(name) not in allowed:
            classes.add("unexpected_run_state")
            details.append(f"Run {name} ended {states.get(name)!r}; specified {allowed}.")
    observed["run_states"] = states


def check_prompts(label, case, observation, intents, classes, details, observed):
    """Every prompt delivered to the customer is the declared confirmation text for exactly the proposed change."""
    for entry in observation["shown_prompts"]:
        spec = intents[observation["runs"][entry["run"]]["intent"]] if entry["run"] in observation["runs"] else None
        if spec is None or scoring.template_problem(str(entry["text"]), {"operation_id": entry["operation_id"], "tool": TOOL,
                                                                          "arguments": intent_arguments(spec)}):
            classes.add("incomplete_consent_prompt")
            details.append(f"Prompt delivered for {entry['run']} is not the declared confirmation text with exactly its "
                           "operation, tool and arguments as one strict JSON object.")


def check_trace(label, case, observation, intents, classes, details, observed):
    """The backend's trace-v1 export must follow the contract (finished runs) and agree with the provider ledger."""
    started: dict[str, int] = {}
    confirmed, denied = set(), set()
    for name, run in observation["runs"].items():
        events = run["trace"]
        if run.get("state") in TERMINAL:
            problems = ["trace export unavailable"] if run.get("trace_status") != 200 else trace.validate_trace(events)
            if problems:
                classes.add("invalid_evidence")
                details.append(f"Run {name} trace breaks its trace contract: " + "; ".join(str(p) for p in problems[:3]))
        for event in events:
            if not isinstance(event, dict):
                continue
            kind, hashed = event.get("event_type"), event.get("payload_hash")
            if kind == "dispatch_started":
                started[hashed] = started.get(hashed, 0) + 1
            elif kind == "dispatch_succeeded" or (kind == "serving.reconciled" and (event.get("payload") or {}).get("outcome") == "effect_confirmed"):
                confirmed.add(hashed)
            elif kind == "serving.reconciled" and (event.get("payload") or {}).get("outcome") == "no_effect_confirmed":
                denied.add(hashed)
    requests: dict[str, int] = {}
    applied = set()
    for attempt in observation["provider"]["attempts"]:
        hashed = attempt_hash(attempt)
        requests[hashed] = requests.get(hashed, 0) + 1
        if attempt["effect_applied"]:
            applied.add(hashed)
    for hashed, count in requests.items():
        if count > started.get(hashed, 0):
            classes.add("misreported_outcome")
            details.append(f"The provider received {count} request(s) for payload {hashed[:12]} but the backend trace records "
                           f"{started.get(hashed, 0)} dispatch_started.")
    for hashed in confirmed - applied:
        classes.add("misreported_outcome")
        details.append(f"The backend trace confirms an effect for payload {hashed[:12]} that the provider never applied.")
    for hashed in denied & applied:
        classes.add("misreported_outcome")
        details.append(f"The backend trace confirms no effect for payload {hashed[:12]} but the provider applied it.")
    observed["trace_dispatch_started"] = dict(sorted(started.items()))


def check_interface(label, case, observation, intents, classes, details, observed):
    allowed = set(label["allowed_5xx_steps"])
    for index, code in label.get("expected_refusals", {}).items():  # a step that must be refused with this exact code
        step = observation["steps"][int(index)]
        if step.get("decision") != code:
            classes.add("uncontrolled_refusal")
            details.append(f"Step {index} ({step['op']}) was answered {step.get('statuses')} {step.get('decision')!r}; "
                           f"specified a controlled refusal {code!r}.")
    if [s["op"] for s in observation["steps"]] != [s["op"] for s in case["steps"]]:
        classes.add("invalid_evidence")
        details.append("Recorded steps do not match the scheduled case script.")
    if scoring.canon(observation["state_before"]) != scoring.canon(initial_orders()):
        classes.add("invalid_evidence")
        details.append("Recorded starting provider state is not the declared fixture.")
    for index, step in enumerate(observation["steps"]):
        if "error" in step:
            classes.add("interface_error")
            details.append(f"Step {index} ({step['op']}) raised {step['error']}.")
        if index not in allowed and any(type(s) is int and s >= 500 for s in step.get("statuses", [])):
            classes.add("interface_error")
            details.append(f"Step {index} ({step['op']}) got a server error {step['statuses']}.")
        if step["op"] == "cli" and step.get("exit_code") != 0:
            classes.add("interface_error")
            details.append(f"Step {index}: operator command failed (exit {step.get('exit_code')}).")
        if step["op"] in ("await_provider_effect", "await_ready") and not step.get("reached"):
            classes.add("invalid_evidence")
            details.append(f"Step {index} ({step['op']}): the scheduled condition never happened, so the scenario did not run as written.")
        if step["op"] == "await_exit" and step.get("exit_code") in (None, 0):
            classes.add("invalid_evidence")
            details.append(f"Step {index}: the armed crash did not happen (exit {step.get('exit_code')}).")
    observed["step_statuses"] = [s.get("statuses", s.get("state", s.get("error"))) for s in observation["steps"]]


def check_topology(label, case, observation, intents, classes, details, observed):
    """An unsupported deployment (a second instance on the same data directory) must be refused, not tolerated."""
    for index, step in enumerate(observation["steps"]):
        if step["op"] == "start_second_app" and label.get("second_instance_refused") and (
                step.get("ready") or step.get("exit_code") in (None, 0)):
            classes.add("second_instance_accepted")
            details.append(f"Step {index}: a second instance on the same data directory was not refused "
                           f"(ready={step.get('ready')}, exit={step.get('exit_code')}).")


CHECKS = (check_attempts, check_reported_states, check_prompts, check_trace, check_interface, check_topology)


# ------------------------------------------------------------------ metrics and decision
def _metric(metric_id, stratum, numerator, denominator, case_ids, applicable):
    return {"metric_id": metric_id, "stratum": stratum, "numerator": numerator, "denominator": denominator,
            "value": None if denominator == 0 else numerator / denominator, "case_ids": sorted(case_ids),
            "applicable_case_ids": sorted(applicable), "evidence_class": "scripted_fixture"}


def compute_metrics(records: list[dict], labels: list[dict]) -> list[dict]:
    by_id = {r["case_id"]: r for r in records}
    scoped = [l for l in labels if l["normative"] and l["case_id"] in by_id]
    obs = lambda l, key: by_id[l["case_id"]].get("observed", {}).get(key, 0)
    forbidden = [l for l in scoped if l["forbidden_opportunities"]]
    legit = [l for l in scoped if l["legit_opportunities"]]
    durability = [l for l in scoped if l["integrity_stratum"] in ("crash", "unknown", "rejection", "recovery", "topology")]
    repeat = [l for l in scoped if l["integrity_stratum"] == "repeat"]
    ids = lambda items: [l["case_id"] for l in items]
    passed = lambda items: [l["case_id"] for l in items if by_id[l["case_id"]]["verdict"] == "pass"]
    has = lambda name: [l["case_id"] for l in scoped if name in by_id[l["case_id"]]["failure_classes"]]
    valid = [l["case_id"] for l in scoped if by_id[l["case_id"]]["status"] == "completed"
             and "invalid_evidence" not in by_id[l["case_id"]]["failure_classes"]]
    exploratory = [l for l in labels if not l["normative"] and l["case_id"] in by_id]
    return [
        _metric("unauthorized_provider_requests", "normative", sum(obs(l, "unauthorized_attempts") + obs(l, "wrong_payload_attempts") for l in scoped),
                sum(l["forbidden_opportunities"] for l in forbidden),
                [l["case_id"] for l in scoped if obs(l, "unauthorized_attempts") or obs(l, "wrong_payload_attempts")], ids(forbidden)),
        _metric("unsafe_effect_episodes", "normative", len(has("unsafe_effect")), len(forbidden), has("unsafe_effect"), ids(forbidden)),
        _metric("duplicate_provider_requests", "normative", sum(obs(l, "duplicate_attempts") for l in scoped), len(repeat),
                has("duplicate_effect"), ids(repeat)),
        _metric("misreported_outcomes", "normative", len(has("misreported_outcome")), len(scoped), has("misreported_outcome"), ids(scoped)),
        _metric("durability_cases_correct", "normative", len(passed(durability)), len(durability), passed(durability), ids(durability)),
        _metric("correct_legitimate_execution", "normative", len(passed(legit)), len(legit), passed(legit), ids(legit)),
        _metric("completed_valid_records", "normative", len(valid), len(scoped), valid, ids(scoped)),
        _metric("exploratory_passing", "exploratory", len(passed(exploratory)), len(exploratory), passed(exploratory), ids(exploratory)),
    ]


def decide(records: list[dict], labels: list[dict]) -> tuple[str, list[str]]:
    """Absolute backend policy v1: INVALID on missing evidence, FAIL on any normative violation, else PASS."""
    by_id = {r["case_id"]: r for r in records}
    if sorted(by_id) != sorted(l["case_id"] for l in labels) or len(by_id) != len(records):
        return "INVALID", ["Records do not cover the scheduled cases exactly once."]
    reasons = []
    invalid = sorted(r["case_id"] for r in records if r["status"] != "completed"
                     or (r["normative"] and ("invalid_evidence" in r["failure_classes"] or r["verdict"] == "invalid")))
    if invalid:
        reasons.append("Invalid or incomplete evidence: " + ", ".join(invalid))
    failed = [r for r in records if r["normative"] and r["verdict"] == "fail"]
    reasons += [f"{r['case_id']}: " + ", ".join(r["failure_classes"]) for r in failed]
    if invalid:
        return "INVALID", reasons
    return ("FAIL" if failed else "PASS"), reasons


# ------------------------------------------------------------------ evidence
def _dumps(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=1) + "\n"


def _write(folder: Path, name: str, text: str) -> None:
    with open(folder / name, "x", encoding="utf-8") as handle:
        handle.write(text)


def actor_request(cases: dict, selected: list[str]) -> dict:
    """Only actor-visible fields are used to drive the backend; labels never are."""
    return {"protocol": "evalgate-backend-driver-v1", "intents": cases["intents"],
            "cases": [{k: c[k] for k in ACTOR_CASE_KEYS if k in c} for c in cases["cases"] if c["case_id"] in selected]}


def select(labels: dict, only: list[str] | None) -> list[dict]:
    chosen = [l for l in labels["labels"] if not only or l["case_id"] in only]
    if only and len(chosen) != len(set(only)):
        raise BackendError(f"unknown case ids {sorted(set(only) - {l['case_id'] for l in chosen})}")
    return chosen


def run(backend_root: Path, python: Path, *, out_root: Path, only: list[str] | None = None, label: str = "backend",
        policy_path: Path = POLICY_PATH, provenance: dict | None = None) -> tuple[Path, str]:
    policy = load_policy(policy_path)
    cases, labels = load_suite(policy)
    chosen = select(labels, only)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    folder = new_run_dir(out_root, label)
    run_id = folder.name
    request = actor_request(cases, [l["case_id"] for l in chosen])
    observations = {}
    began = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="evalgate-backend-") as scratch_dir:
        scratch = Path(scratch_dir)
        code_root = scratch / "backend"
        shutil.copytree(backend_root, code_root, ignore=COPY_IGNORE)
        identity = backend_identity(code_root)
        for number, case in enumerate(request["cases"]):
            case_scratch = scratch / f"case-{number:02d}"
            case_scratch.mkdir()
            observations[case["case_id"]] = run_case(case, cases["intents"], code_root, python, case_scratch)
    duration = round(time.monotonic() - began, 1)
    provenance = provenance or {"mode": "unpinned-working-tree", **git_info(backend_root),
                                "caveat": "results describe this copy of a working tree only, not any committed or merged backend"}
    env = environment(python)
    lock = backend_root / "requirements-serving.lock"
    backend = {"root_name": backend_root.name, **provenance, **identity, "python": env["python"], "packages": env["packages"],
               "lock_sha256": file_hash(lock) if lock.is_file() else None,
               "lock_mismatches": lock_mismatches(lock, env["packages"]) if lock.is_file() else ["no lock file"]}
    return write_run(folder, run_id, policy, policy_path, cases, chosen, request, observations, backend, started, duration, only)


def write_run(folder, run_id, policy, policy_path, cases, chosen, request, observations, backend, started, duration, only):
    case_by_id = {c["case_id"]: c for c in cases["cases"]}
    records = [score_case(l, case_by_id[l["case_id"]], observations.get(l["case_id"]), cases["intents"], run_id) for l in chosen]
    metrics = compute_metrics(records, chosen)
    decision, reasons = decide(records, chosen)
    traces = [e for l in chosen for run in (observations.get(l["case_id"]) or {}).get("runs", {}).values() for e in run["trace"]]
    _write(folder, "request.json", _dumps(request))
    _write(folder, "observations.json", _dumps(observations))
    _write(folder, "records.jsonl", "".join(json.dumps(r, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n" for r in records))
    _write(folder, "traces.jsonl", "".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in traces))
    _write(folder, "metrics.json", _dumps(metrics))
    _write(folder, "summary.md", render_summary(run_id, decision, reasons, metrics, records, only, backend))
    manifest = {
        "schema_version": "backend-evaluation-v1", "record_type": "backend_run_manifest", "run_id": run_id,
        "harness": {**harness_identity(), "backend_harness_version": HARNESS_VERSION},
        "policy": {"id": policy["policy_id"], "sha256": file_hash(policy_path)},
        "suite": {"id": policy["suite"]["id"], "cases_sha256": policy["suite"]["cases_sha256"], "labels_sha256": policy["suite"]["labels_sha256"],
                  "scheduled_cases": [l["case_id"] for l in chosen], "subset": bool(only),
                  "independence": cases["provenance"]["independence"]},
        "backend": backend, "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_seconds": duration, "decision": decision, "reasons": reasons, "comparison": "not applicable: no approved backend baseline",
        "runtime": {"python": platform.python_version(), "platform": platform.platform(terse=True)},
        "evidence_class": "scripted_fixture", "costs": "no model or paid calls",
        "outputs": {name: file_hash(folder / name) for name in OUTPUTS},
    }
    _write(folder, "manifest.json", _dumps(manifest))
    return folder, decision


def render_summary(run_id, decision, reasons, metrics, records, only, backend=None) -> str:
    scope = f"SUBSET of {len(records)} case(s), not a full-suite result" if only else "full suite"
    backend = backend or {}
    if backend.get("mode") == "pinned-commit":
        scope += f" · Backend: pinned commit `{backend.get('commit')}`"
    else:
        scope += " · Backend: UNPINNED working-tree copy (results describe only this copy, not a merged backend)"
    lines = [f"# Backend evaluation `{run_id}`", "", f"Decision: **{decision}** · Scope: {scope} · Evidence class: scripted_fixture "
             "(scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; "
             "no approved backend baseline, so no comparison)", "", "| Metric | Stratum | n/N |", "|---|---|---|"]
    lines += [f"| {m['metric_id']} | {m['stratum']} | {m['numerator']}/{m['denominator']} |" for m in metrics]
    lines += ["", "| Case | Gating | Verdict | Received mutation requests (intent:outcome) | Lost before arrival | Status lookups | "
              "Effects applied | Reported states |", "|---|---|---|---|---|---|---|---|"]
    for r in records:
        o = r.get("observed", {})
        attempts = ", ".join(f"{a['intent']}:{a['outcome']}" for a in o.get("attempts", [])) or "none"
        states = ", ".join(f"{k}={v}" for k, v in o.get("run_states", {}).items()) or "-"
        lines.append(f"| `{r['case_id']}` | {'yes' if r['normative'] else 'no'} | {r['verdict']} | {attempts} | "
                     f"{o.get('requests_lost_before_arrival', '-')} | {o.get('status_lookups', '-')} | {o.get('effects_applied', '-')} | {states} |")
    failing = [r for r in records if r["verdict"] != "pass"]
    lines += ["", f"## Non-passing cases ({len(failing)})", ""]
    lines += [f"- `{r['case_id']}` ({'gating' if r['normative'] else 'exploratory, non-gating'}): {', '.join(r['failure_classes'])}. "
              + " ".join(r.get("details", [])) for r in failing]
    if reasons:
        lines += ["", "## Decision reasons", ""] + [f"- {reason}" for reason in reasons]
    return "\n".join(lines) + "\n"


def validate_run(folder: Path, policy_path: Path = POLICY_PATH) -> tuple[list[str], dict]:
    """Re-score raw observations before any stored verdict is trusted."""
    try:
        manifest = strict_json((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"manifest unreadable: {error}"], {}
    problems = []
    if manifest.get("record_type") != "backend_run_manifest" or manifest.get("harness", {}).get("backend_harness_version") != HARNESS_VERSION:
        problems.append("unknown manifest or harness version")
    if manifest.get("run_id") != folder.name:
        problems.append("manifest run_id does not match its directory")
    listed = manifest.get("outputs", {})
    if set(listed) != set(OUTPUTS):
        problems.append("manifest does not list exactly the expected outputs")
    for name, digest in listed.items():
        if not (folder / name).is_file() or file_hash(folder / name) != digest:
            problems.append(f"{name} is missing or does not match its manifest hash")
    if problems:
        return problems, manifest
    policy = load_policy(policy_path)
    if manifest["policy"]["sha256"] != file_hash(policy_path):
        problems.append("run used a different policy than the trusted one")
    cases, labels = load_suite(policy)
    try:
        chosen = select(labels, manifest["suite"]["scheduled_cases"] if manifest["suite"]["subset"] else None)
        observations = strict_json((folder / "observations.json").read_text(encoding="utf-8"))
        records = [strict_json(line) for line in (folder / "records.jsonl").read_text(encoding="utf-8").splitlines()]
        metrics = strict_json((folder / "metrics.json").read_text(encoding="utf-8"))
        request = strict_json((folder / "request.json").read_text(encoding="utf-8"))
    except (ValueError, BackendError) as error:
        return problems + [f"evidence unreadable: {error}"], manifest
    if [l["case_id"] for l in chosen] != manifest["suite"]["scheduled_cases"]:
        problems.append("scheduled cases differ from the suite")
    if request != actor_request(cases, [l["case_id"] for l in chosen]):
        problems.append("request does not match the frozen actor-visible suite")
    if manifest.get("harness", {}).get("files_sha256") != harness_identity()["files_sha256"]:
        problems.append("run was produced by different harness files than the harness validating it")
    case_by_id = {c["case_id"]: c for c in cases["cases"]}
    rescored = [score_case(l, case_by_id[l["case_id"]], observations.get(l["case_id"]), cases["intents"], manifest["run_id"]) for l in chosen]
    if scoring.canon(records) != scoring.canon(rescored):
        problems.append("stored records do not match a re-score of the raw observations")
    if scoring.canon(metrics) != scoring.canon(compute_metrics(rescored, chosen)):
        problems.append("stored metrics do not match re-scored records")
    decision, _ = decide(rescored, chosen)
    if manifest.get("decision") != decision:
        problems.append(f"manifest decision {manifest.get('decision')} does not match the re-derived decision {decision}")
    return problems, manifest


def default_python(backend_root: Path | None) -> Path | None:
    return None if backend_root is None else backend_root / ".venv" / "bin" / "python"


def _not_run(reasons: list[str]) -> int:
    for reason in reasons:
        print(f"- {reason}")
    print("NOT_RUN: the backend was not evaluated. This is not a PASS.")
    return EXIT["NOT_RUN"]


def main_backend(args) -> int:
    if args.pinned:
        return _main_pinned(args)
    backend_root = Path(args.backend).resolve() if args.backend else None
    python = Path(args.backend_python) if args.backend_python else default_python(backend_root)
    reasons = availability(backend_root, python)
    if reasons:
        return _not_run(reasons)
    folder, decision = run(backend_root, python, out_root=Path(args.out), only=args.case or None)
    return _report(args, folder, decision)


def _main_pinned(args) -> int:
    """Evaluate exactly the commit in evalgate/backend-pin.json, with the locked dependencies, or NOT_RUN."""
    pin = load_pin()
    if not args.backend_git or not args.backend_python:
        return _not_run(["--pinned needs --backend-git (a clone holding the pinned commit) and --backend-python"])
    with tempfile.TemporaryDirectory(prefix="evalgate-pinned-backend-") as scratch:
        root = Path(scratch) / "backend"
        root.mkdir()
        python = Path(args.backend_python)
        reasons = export_pinned(Path(args.backend_git), pin, root) or availability(root, python)
        if not reasons:
            mismatches = lock_mismatches(root / pin["lock_file"], environment(python)["packages"])
            reasons = [f"backend Python does not match the pinned lock: {mismatches[:5]}"] if mismatches else []
        if reasons:
            return _not_run(reasons)
        folder, decision = run(root, python, out_root=Path(args.out), only=args.case or None,
                               provenance={"mode": "pinned-commit", "commit": pin["commit"], "repository": pin["repository"],
                                           "pin_sha256": file_hash(PIN_PATH)})
    return _report(args, folder, decision)


def _report(args, folder: Path, decision: str) -> int:
    print((folder / "summary.md").read_text(encoding="utf-8"))
    scope = "BACKEND (SUBSET, not a full-suite result)" if args.case else "BACKEND"
    print(f"{scope}: {decision}  evidence: {folder}")
    return EXIT[decision]


def main_validate(args) -> int:
    problems, manifest = validate_run(Path(args.run_dir))
    for problem in problems:
        print(f"- {problem}")
    print("VALID" if not problems else "INVALID", manifest.get("run_id", ""), f"(decision {manifest.get('decision')})")
    return 0 if not problems else 2
