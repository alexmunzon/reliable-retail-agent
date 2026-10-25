"""Real-process test stack: synthetic provider + uvicorn app on loopback, isolated temp dirs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "serving.py"
ADDRESS = {"street": "18 Example Lane", "apartment": "2A", "city": "Sampleton", "state": "CA",
           "postal_code": "00000", "country": "US"}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Stack:
    def __init__(self, *, provider_mode: str = "full", env: dict[str, str] | None = None):
        self.tmp = tempfile.TemporaryDirectory(prefix="rra-serving-")
        self.root = Path(self.tmp.name)
        self.app_port, self.provider_port = free_port(), free_port()
        self.provider_mode = provider_mode
        self.extra_env = dict(env or {})
        self.app: subprocess.Popen | None = None
        self.provider: subprocess.Popen | None = None
        self.app_logs: list[Path] = []
        self.run_cli("init")
        self.tokens = json.loads((self.root / "app" / "credentials.json").read_text())

    # ------------------------------------------------------------- processes
    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
               "RRA_DATA_DIR": str(self.root / "app"), "RRA_PROVIDER_URL": f"http://127.0.0.1:{self.provider_port}",
               "RRA_TEST_MODE": "1", "RRA_WORKER_SCAN_SECONDS": "0.2", "RRA_SSE_HEARTBEAT_SECONDS": "0.5",
               "RRA_PROVIDER_TIMEOUT_SECONDS": "2", "RRA_DRAIN_SECONDS": "1", "RRA_RECONCILE_BACKOFF_SECONDS": "0.2",
               "RRA_COMMAND_RATE_PER_MINUTE": "6000", "RRA_COMMAND_BURST": "1000"}
        env.update(self.extra_env)
        env.update(extra or {})
        return env

    def run_cli(self, *args: str, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        result = subprocess.run([sys.executable, str(CLI), *args], cwd=ROOT, env=self.env(extra),
                                capture_output=True, text=True, timeout=60)
        if result.returncode not in (0, None):
            raise AssertionError(f"CLI {args} failed: {result.stdout}\n{result.stderr}")
        return result

    def start_provider(self, mode: str | None = None) -> None:
        self.provider_mode = mode or self.provider_mode
        command = [sys.executable, "-m", "provider_sim.server", "--data-dir", str(self.root / "provider"),
                   "--key-file", str(self.root / "app" / "provider.key"), "--port", str(self.provider_port),
                   "--mode", self.provider_mode, "--test-mode"]
        self.provider = subprocess.Popen(command, cwd=ROOT, env=self.env(), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True)
        line = self.provider.stdout.readline()
        assert line.startswith("PROVIDER_READY"), line

    def start_app(self, extra: dict[str, str] | None = None, *, ready: bool = True) -> None:
        log = self.root / f"app-{len(self.app_logs)}.log"
        self.app_logs.append(log)
        self._log_handle = open(log, "w")
        self.app = subprocess.Popen([sys.executable, str(CLI), "serve-app", "--port", str(self.app_port)], cwd=ROOT,
                                    env=self.env(extra), stdout=self._log_handle, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.app.poll() is not None:
                raise AssertionError("app exited during startup:\n" + log.read_text())
            try:
                if httpx.get(self.url("/health/ready" if ready else "/health/live"), timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        raise AssertionError("app not ready:\n" + log.read_text())

    def wait_app_exit(self, timeout: float = 15) -> int:
        code = self.app.wait(timeout=timeout)
        self._log_handle.close()
        return code

    def stop_app(self, sig=signal.SIGTERM) -> int:
        if self.app and self.app.poll() is None:
            self.app.send_signal(sig)
        return self.wait_app_exit()

    def kill_provider(self) -> None:
        if self.provider and self.provider.poll() is None:
            self.provider.kill()
        if self.provider:
            self.provider.wait(timeout=10)

    def close(self) -> None:
        for process in (self.app, self.provider):
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        if self.provider and self.provider.stdout:
            self.provider.stdout.close()
        if getattr(self, "_log_handle", None):
            self._log_handle.close()
        self.tmp.cleanup()

    def logs(self) -> str:
        return "".join(path.read_text() for path in self.app_logs)

    # ------------------------------------------------------------- HTTP helpers
    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.app_port}{path}"

    def headers(self, actor: str, key: str | None = None, **extra) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.tokens[actor]}", **extra}
        if key:
            headers["Idempotency-Key"] = key
        return headers

    def post(self, path: str, actor: str, key: str, body=None, **kwargs) -> httpx.Response:
        return httpx.post(self.url(path), headers=self.headers(actor, key), json=body, timeout=10, **kwargs)

    def get(self, path: str, actor: str, **kwargs) -> httpx.Response:
        return httpx.get(self.url(path), headers=self.headers(actor), timeout=10, **kwargs)

    def new_run(self, actor: str = "alice", resource: str = "SYN-ORDER-1042", address: dict | None = None,
                tag: str = "") -> dict:
        session = self.post("/api/v1/sessions", actor, f"session-{actor}-{resource}-{tag}-{time.monotonic_ns()}",
                            {"resource_id": resource})
        assert session.status_code == 201, session.text
        run = self.post(f"/api/v1/sessions/{session.json()['session_id']}/runs", actor,
                        f"run-{actor}-{tag}-{time.monotonic_ns()}",
                        {"request": {"type": "change_order_address", "address": address or ADDRESS}})
        assert run.status_code == 202, run.text
        return self.snapshot(run.json()["run_id"], actor)

    def snapshot(self, run_id: str, actor: str = "alice") -> dict:
        response = self.get(f"/api/v1/runs/{run_id}", actor)
        assert response.status_code == 200, response.text
        return response.json()

    def consent_body(self, snapshot: dict, response: str = "yes") -> dict:
        proposal = snapshot["proposal"]
        return {"response": response, "prompt_id": proposal["prompt_id"],
                "proposal_version": proposal["proposal_version"], "payload_hash": proposal["payload_hash"]}

    def approve(self, snapshot: dict, actor: str = "alice", key: str | None = None) -> httpx.Response:
        return self.post(f"/api/v1/runs/{snapshot['run_id']}/consents", actor, key or f"consent-{time.monotonic_ns()}",
                         self.consent_body(snapshot))

    def wait_state(self, run_id: str, states: set[str], actor: str = "alice", timeout: float = 10) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = self.snapshot(run_id, actor)
            if snapshot["state"] in states:
                return snapshot
            time.sleep(0.05)
        raise AssertionError(f"run {run_id} never reached {states}: {snapshot['state']}")

    def arm(self, *actions: str) -> None:
        key = (self.root / "app" / "provider.key").read_text().strip()
        response = httpx.post(f"http://127.0.0.1:{self.provider_port}/v1/test/failpoints",
                              headers={"Authorization": f"Bearer {key}"}, json={"actions": list(actions)}, timeout=5)
        assert response.status_code == 200, response.text

    # ------------------------------------------------------------- independent ground truth
    def provider_effects(self, order_id: str | None = None) -> list[dict]:
        """Read the provider's own ledger directly: test-only ground truth the app cannot use."""
        conn = sqlite3.connect(str(self.root / "provider" / "provider.db"))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM effects" + (" WHERE order_id=?" if order_id else ""),
                                (order_id,) if order_id else ()).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def app_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.root / "app" / "serving.db"))
        conn.row_factory = sqlite3.Row
        return conn
