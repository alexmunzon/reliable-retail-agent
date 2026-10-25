#!/usr/bin/env python3
"""Bounded synthetic loopback diagnostic. Requires the pinned serving Python; never installs.

Use --source for a frozen baseline/candidate tree. Output is exclusive and includes
raw observations, source hashes, plan hash, errors and descriptive percentiles.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

sys.dont_write_bytecode = True
TERMINAL = {"succeeded", "failed", "outcome_unknown", "expired", "invalidated", "cancelled", "rejected"}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def stats(values):
    values = sorted(values)
    return {"n": len(values), "p50": values[math.ceil(len(values) * .5) - 1] if values else None,
            "p95": values[math.ceil(len(values) * .95) - 1] if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None}


def command(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=10)
    return p.stdout.strip() if p.returncode == 0 else None


def identity(source):
    files = {str(p.relative_to(source)): digest(p) for name in
             ("serving", "provider_sim", "rra", "study", "observability", "scripts", "tests_serving")
             for p in sorted((source / name).rglob("*.py"))}
    return {"source": str(source), "git_head": command(["git", "-C", str(source), "rev-parse", "HEAD"]),
            "git_dirty": command(["git", "-C", str(source), "status", "--porcelain"]), "files": files,
            "files_sha256": sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def db_sizes(stack):
    return {name: sum(p.stat().st_size for p in (stack.root / name).glob("*.db*")) for name in ("app", "provider")}


class Sampler:
    def __init__(self, stack):
        self.stack, self.rows, self.stop = stack, [], threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            row = {"at_s": time.monotonic(), "db_bytes": db_sizes(self.stack)}
            for name in ("app", "provider"):
                p = getattr(self.stack, name)
                raw = command(["ps", "-p", str(p.pid), "-o", "rss=,time="])
                if raw:
                    rss, cpu = raw.split()
                    parts = cpu.split(":")
                    row[name] = {"rss_kib": int(rss), "cpu_seconds": sum(float(x) * 60**i for i, x in enumerate(reversed(parts)))}
            self.rows.append(row)
            self.stop.wait(.1)

    def start(self):
        self.thread.start()

    def finish(self):
        self.stop.set()
        self.thread.join(5)


class Bench:
    def __init__(self, source, output, plan):
        sys.path.insert(0, str(source))
        from tests_serving.harness import Stack, ADDRESS
        from tests_serving.sse import SSEParser
        from scripts.ci import block_external_connections
        import httpx
        block_external_connections()
        self.Stack, self.address, self.Parser, self.httpx = Stack, ADDRESS, SSEParser, httpx
        self.output, self.plan = output, plan
        self.results, self.raw, self.errors = [], [], []

    def client(self):
        return self.httpx.Client(timeout=5, trust_env=False)

    @contextmanager
    def stack(self, label, env=None):
        start = time.monotonic()
        stack = self.Stack(env=env)
        init_ms = (time.monotonic() - start) * 1000
        sampler = None
        try:
            started = time.monotonic()
            stack.start_provider()
            provider_ms = (time.monotonic() - started) * 1000
            started = time.monotonic()
            stack.start_app()
            ready_ms = (time.monotonic() - started) * 1000
            row = {"phase": label, "initialization_ms": init_ms, "provider_start_ms": provider_ms,
                   "app_ready_ms": ready_ms, "db_before_bytes": db_sizes(stack), "environment": stack.extra_env}
            with stack.app_db() as conn:
                row["sqlite"] = {key: conn.execute("PRAGMA " + key).fetchone()[0]
                                 for key in ("journal_mode", "synchronous", "busy_timeout", "page_size")}
            sampler = Sampler(stack)
            sampler.start()
            yield stack
            row["provider_effects_total"] = len(stack.provider_effects())
            row["db_after_bytes"] = db_sizes(stack)
        finally:
            if sampler:
                sampler.finish()
                row["resource_samples"] = sampler.rows
                for name in ("app", "provider"):
                    samples = [r[name] for r in sampler.rows if name in r]
                    row[name + "_rss_peak_mib"] = max((r["rss_kib"] / 1024 for r in samples), default=None)
                    row[name + "_cpu_seconds"] = samples[-1]["cpu_seconds"] - samples[0]["cpu_seconds"] if samples else None
                self.raw.append(row)
            stack.close()

    def request(self, client, stack, method, path, actor="alice", key=None, body=None):
        start = time.monotonic()
        kwargs = {"headers": stack.headers(actor, key)}
        if body is not None:
            kwargs["json"] = body
        response = client.request(method, stack.url(path), **kwargs)
        return response, (time.monotonic() - start) * 1000

    def session(self, client, stack, actor, resource):
        response, _ = self.request(client, stack, "POST", "/api/v1/sessions", actor,
                                   "bench-session-" + actor, {"resource_id": resource})
        assert response.status_code == 201, (response.status_code, response.text)
        return response.json()["session_id"]

    def proposal(self, client, stack, actor, session, index):
        response, elapsed = self.request(client, stack, "POST", f"/api/v1/sessions/{session}/runs", actor,
                                         f"bench-run-{actor}-{index}",
                                         {"request": {"type": "change_order_address", "address": self.address}})
        assert response.status_code == 202, (response.status_code, response.text)
        run = response.json()["run_id"]
        response, _ = self.request(client, stack, "GET", f"/api/v1/runs/{run}", actor)
        assert response.status_code == 200
        return response.json(), elapsed

    def stream(self, stack, run, actor, cursor=None, first_seen=None):
        start = time.monotonic()
        data = {"events": [], "first_event_ms": None, "bytes": 0}
        headers = stack.headers(actor)
        if cursor:
            headers["Last-Event-ID"] = cursor
        with self.client() as client:
            with client.stream("GET", stack.url(f"/api/v1/runs/{run}/events"), headers=headers) as response:
                data["status"] = response.status_code
                assert response.status_code == 200, response.status_code
                parser = self.Parser()
                for chunk in response.iter_raw():
                    data["bytes"] += len(chunk)
                    for event in parser.feed(chunk):
                        event["received_monotonic_s"] = time.monotonic()
                        data["events"].append(event)
                        if data["first_event_ms"] is None:
                            data["first_event_ms"] = (time.monotonic() - start) * 1000
                            if first_seen:
                                first_seen.set()
                    if time.monotonic() - start > 8:
                        raise TimeoutError("stream exceeded total deadline")
        data["catchup_ms"] = (time.monotonic() - start) * 1000
        return data

    def operation(self, client, stack, actor, session, index, streams=False, row=None):
        row = {} if row is None else row
        snapshot, proposal_ms = self.proposal(client, stack, actor, session, index)
        run = snapshot["run_id"]
        row.update(run_id=run, actor=actor, proposal_ms=proposal_ms)
        executor = ThreadPoolExecutor(1) if streams else None
        try:
            if streams:
                seen = threading.Event()
                future = executor.submit(self.stream, stack, run, actor, first_seen=seen)
                if not seen.wait(5):
                    raise TimeoutError("first stream event missing")
            start = time.monotonic()
            response, row["consent_response_ms"] = self.request(client, stack, "POST", f"/api/v1/runs/{run}/consents",
                actor, f"bench-consent-{actor}-{index}", stack.consent_body(snapshot))
            row["consent_status"] = response.status_code
            assert response.status_code == 202, (response.status_code, response.text)
            while time.monotonic() - start < 5:
                response, _ = self.request(client, stack, "GET", f"/api/v1/runs/{run}", actor)
                state = response.json()["state"]
                if state in TERMINAL:
                    break
                time.sleep(.01)
            row.update(state=state, consent_to_completion_ms=(time.monotonic() - start) * 1000)
            assert state == "succeeded", state
            if streams:
                live = future.result(timeout=8)
                row["live"] = live
                dispatch = [e for e in live["events"] if e["event"] == "dispatch_started"]
                row["consent_to_dispatch_observed_ms"] = (dispatch[0]["received_monotonic_s"] - start) * 1000 if dispatch else None
                cursor = live["events"][0]["id"]
                replay = self.stream(stack, run, actor, cursor)
                row["replay"] = replay
                assert [e["id"] for e in replay["events"]] == [e["id"] for e in live["events"]][1:]
            return row
        finally:
            if executor:
                executor.shutdown(wait=True, cancel_futures=True)

    def pilot(self, stack, label, concurrency, count=40, streams=False):
        actors = [("alice", "SYN-ORDER-1042"), ("bob", "SYN-ORDER-2077")][:concurrency]
        health, stop = [], threading.Event()
        def probe():
            with self.client() as c:
                while not stop.is_set():
                    start = time.monotonic()
                    try:
                        r = c.get(stack.url("/health/live"))
                        health.append({"ms": (time.monotonic() - start) * 1000, "status": r.status_code})
                    except Exception as error:
                        health.append({"ms": (time.monotonic() - start) * 1000, "status": None,
                                       "error_type": type(error).__name__})
                    stop.wait(.05)
        thread = threading.Thread(target=probe, daemon=True)
        before = len(stack.provider_effects())
        warmup = []
        sessions = {}
        for actor, resource in actors:
            with self.client() as c:
                sessions[actor] = self.session(c, stack, actor, resource)
                for index in range(max(1, 2 // concurrency)):
                    warmup.append(self.operation(c, stack, actor, sessions[actor], f"{label}-warmup-{index}", streams))
        warmup_effects = len(stack.provider_effects()) - before
        def lane(actor, amount):
            rows = []
            with self.client() as c:
                for index in range(amount):
                    row = {}
                    try:
                        self.operation(c, stack, actor, sessions[actor], f"{label}-{index}", streams, row=row)
                    except Exception as error:
                        row.update(error_type=type(error).__name__, error=str(error)[:200])
                    rows.append(row)
            return rows
        start = time.monotonic()
        thread.start()
        try:
            with ThreadPoolExecutor(concurrency) as pool:
                futures = [pool.submit(lane, actor, count // concurrency) for actor, _ in actors]
                rows = [row for future in futures for row in future.result()]
            elapsed = time.monotonic() - start
        finally:
            stop.set()
            thread.join(5)
        effects = len(stack.provider_effects()) - before - warmup_effects
        accepted = sum(r.get("consent_status") == 202 for r in rows)
        completed = sum(r.get("state") == "succeeded" for r in rows)
        result = {"phase": label, "concurrency": concurrency, "scheduled": count, "warmup": warmup,
                  "warmup_effects": warmup_effects, "rows": rows, "elapsed_s": elapsed,
                  "accepted_consents": accepted, "completed_operations": completed, "independent_provider_effects": effects,
                  "accepted_consents_per_s": accepted / elapsed, "completed_operations_per_s": completed / elapsed,
                  "errors": sum("error" in r for r in rows), "health_ms": stats([r["ms"] for r in health]),
                  "health_errors": sum(r["status"] != 200 for r in health), "health_rows": health}
        for key in ("proposal_ms", "consent_response_ms", "consent_to_completion_ms", "consent_to_dispatch_observed_ms"):
            result[key] = stats([r[key] for r in rows if r.get(key) is not None])
        for field, source, key in (("first_event_ms", "live", "first_event_ms"), ("replay_ms", "replay", "catchup_ms")):
            result[field] = stats([r[source][key] for r in rows if source in r])
        result["correctness_ok"] = count == accepted == completed == effects and result["errors"] == 0
        self.results.append(result)
        print(json.dumps({k: result[k] for k in ("phase", "completed_operations", "independent_provider_effects", "errors", "elapsed_s")}), flush=True)

    def commands(self, stack, concurrency, label):
        with self.client() as c:
            self.session(c, stack, "alice", "SYN-ORDER-1042")
        def one(_):
            with self.client() as c:
                r, ms = self.request(c, stack, "POST", "/api/v1/sessions", "alice", "bench-session-alice",
                                     {"resource_id": "SYN-ORDER-1042"})
                return {"status": r.status_code, "ms": ms, "code": r.json().get("code")}
        started = time.monotonic()
        with ThreadPoolExecutor(concurrency) as pool:
            rows = list(pool.map(one, range(40)))
        self.results.append({"phase": label, "concurrency": concurrency, "rows": rows,
                             "response_ms": stats([r["ms"] for r in rows]), "elapsed_s": time.monotonic() - started,
                             "accepted_http_responses": sum(r["status"] == 201 for r in rows),
                             "rate_limited": sum(r["status"] == 429 for r in rows), "unique_provider_effects": len(stack.provider_effects())})

    def boundaries(self):
        with self.stack("slow_and_overload") as s, self.client() as c:
            session = self.session(c, s, "alice", "SYN-ORDER-1042")
            run, _ = self.proposal(c, s, "alice", session, "slow-pending")
            sockets, statuses = [], []
            try:
                # Raw clients read headers, then deliberately never read event bytes.
                for index in range(5):
                    sock = socket.create_connection(("127.0.0.1", s.app_port), timeout=5)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
                    request = f"GET /api/v1/runs/{run['run_id']}/events HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {s.tokens['alice']}\r\n\r\n"
                    sock.sendall(request.encode())
                    header = b""
                    while b"\r\n\r\n" not in header:
                        header += sock.recv(1)
                    statuses.append(int(header.split(b" ", 2)[1]))
                    if index < 2:
                        sockets.append(sock)
                    elif index < 4:
                        sockets.append(sock)
                    else:
                        sock.close()
                self.results.append({"phase": "stream_overload", "statuses_at_concurrency_1_to_5": statuses,
                    "correctness_ok": statuses == [200, 200, 200, 200, 429], "provider_effects": len(s.provider_effects())})
                # Keep two stalled sockets during unrelated legitimate provider work.
                for sock in sockets[2:]:
                    sock.close()
                sockets = sockets[:2]
                # Bob is the unrelated customer; own sequential loop avoids the pending Alice resource.
                bob_session = self.session(c, s, "bob", "SYN-ORDER-2077")
                before, started = len(s.provider_effects()), time.monotonic()
                rows = [self.operation(c, s, "bob", bob_session, f"slow-{i}") for i in range(12)]
                effects = len(s.provider_effects()) - before
                self.results.append({"phase": "slow_consumers", "rows": rows, "nonreading_sockets": 2,
                    "completed_operations": len(rows), "independent_provider_effects": effects,
                    "elapsed_s": time.monotonic() - started, "consent_to_completion_ms": stats([r["consent_to_completion_ms"] for r in rows]),
                    "correctness_ok": effects == 12,
                    "caveat": "Low-volume stream occupancy; kernel send-buffer saturation and send-timeout disconnect are not established by this benchmark."})
                codes = []
                for index in range(8):
                    r, _ = self.request(c, s, "POST", f"/api/v1/sessions/{session}/runs", "alice", f"bench-bound-{index}",
                        {"request": {"type": "change_order_address", "address": self.address}})
                    codes.append({"status": r.status_code, "code": r.json().get("code")})
                self.results.append({"phase": "admission_overload", "additional_run_responses": codes,
                    "correctness_ok": [r["status"] for r in codes] == [202] * 7 + [429]})
            finally:
                for sock in sockets:
                    sock.close()
        with self.stack("default_rate_overload", {"RRA_COMMAND_RATE_PER_MINUTE": "60", "RRA_COMMAND_BURST": "20"}) as s:
            self.commands(s, 8, "default_rate_overload")

    def recorder(self):
        try:
            from observability.live import SpanRecorder
        except ImportError:
            self.results.append({"phase": "span_recorder", "status": "NOT_AVAILABLE_IN_BASELINE"})
            return
        claim = SimpleNamespace(run_id="bench-run", operation_id="bench-operation")
        recorder = SpanRecorder()
        def broken(*_):
            raise OSError("synthetic_metrics_failure")
        rows = []
        for mode in ("off", "on", "failure"):
            fn = None if mode == "off" else recorder.record if mode == "on" else broken
            values = []
            errors = 0
            for _ in range(20000):
                start = time.perf_counter_ns()
                if fn:
                    try:
                        fn(claim, start, start + 1, "ok")
                    except Exception:
                        errors += 1
                values.append(time.perf_counter_ns() - start)
            rows.append({"mode": mode, "call_ns": stats(values), "caught_failures": errors})
        self.results.append({"phase": "span_recorder", "rows": rows, "retained_records": len(recorder.records),
                             "capacity": 512, "caveat": "isolated recorder-call overhead; no whole-service A/B or effect proof"})

    def dashboard(self, stack, label):
        """Bounded HTTP query overhead after an existing forty-operation phase."""
        with self.client() as client:
            probe, _ = self.request(client, stack, "GET", "/api/v1/observability/requests")
            if probe.status_code == 404:
                self.results.append({"phase": label, "status": "NOT_AVAILABLE_IN_BASELINE"})
                return
            for route in ("requests?page_size=20", "aggregates"):
                path = "/api/v1/observability/" + route
                warm, warm_ms = self.request(client, stack, "GET", path)
                rows = []
                for _ in range(20):
                    response, elapsed = self.request(client, stack, "GET", path)
                    rows.append({"status": response.status_code, "ms": elapsed, "bytes": len(response.content)})
                self.results.append({"phase": label, "route": route, "rows": rows, "warmup_ms": warm_ms,
                    "warmup_status": warm.status_code, "response_ms": stats([r["ms"] for r in rows]),
                    "errors": sum(r["status"] != 200 for r in rows), "expected_owned_runs": 21,
                    "caveat": "HTTP projection/query only; excludes browser rendering. Same available source is used; baseline has no live dashboard."})

    def run(self):
        with self.stack("idle_and_commands") as s, self.client() as c:
            rows = []
            for _ in range(40):
                start = time.monotonic()
                r = c.get(s.url("/health/live"))
                rows.append({"status": r.status_code, "ms": (time.monotonic() - start) * 1000})
            self.results.append({"phase": "idle", "rows": rows, "response_ms": stats([r["ms"] for r in rows])})
            for concurrency in (1, 2, 8):
                self.commands(s, concurrency, f"command_only_c{concurrency}")
        for repeat in range(self.plan["profile"]["repetitions"]):
            for concurrency in (1, 2):
                label = f"pilot_c{concurrency}_r{repeat + 1}"
                with self.stack(label) as s:
                    self.pilot(s, label, concurrency)
            label = f"mixed_streams_r{repeat + 1}"
            with self.stack(label) as s:
                self.pilot(s, label, 2, streams=True)
                if repeat == 0:
                    self.dashboard(s, "live_dashboard_queries")
        self.boundaries()
        self.recorder()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_text())
    record = {"label": args.label, "plan_sha256": digest(args.plan), "harness_sha256": digest(Path(__file__)),
              "started_at": datetime.now(timezone.utc).isoformat(), "source_before": identity(args.source),
              "command": sys.argv, "python": sys.version, "platform": platform.platform(), "machine": platform.machine(),
              "cpu_count": os.cpu_count(), "cpu_model": command(["sysctl", "-n", "machdep.cpu.brand_string"]),
              "physical_memory_bytes": command(["sysctl", "-n", "hw.memsize"]), "filesystem": command(["df", "-h", str(args.output)]),
              "packages": {p: importlib.metadata.version(p) for p in ("fastapi", "starlette", "uvicorn", "httpx", "pydantic", "anyio")},
              "background_load_caveat": "Shared developer host; other local work may run concurrently. ps is sampled, not continuous. No causal speedup claim."}
    bench = Bench(args.source.resolve(), args.output, plan)
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("benchmark wall budget exceeded")))
    signal.alarm(plan["profile"]["max_wall_seconds_per_invocation"])
    status = "COMPLETED"
    try:
        bench.run()
    except Exception as error:
        status = "INCOMPLETE"
        bench.errors.append({"type": type(error).__name__, "message": str(error)[:1000]})
    finally:
        signal.alarm(0)
        record.update(status=status, finished_at=datetime.now(timezone.utc).isoformat(), results=bench.results,
                      resources=bench.raw, errors=bench.errors, source_after=identity(args.source))
        record["source_unchanged"] = record["source_before"]["files_sha256"] == record["source_after"]["files_sha256"]
        record["correctness_ok"] = status == "COMPLETED" and all(r.get("correctness_ok", True) for r in bench.results)
        text = json.dumps(record, indent=2, sort_keys=True) + "\n"
        if len(text.encode()) > plan["profile"]["max_output_bytes"]:
            raise RuntimeError("evidence exceeds declared output budget")
        (args.output / "results.json").write_text(text)
        print(json.dumps({"status": status, "output": str(args.output), "errors": bench.errors}), flush=True)
    return 0 if status == "COMPLETED" and all(r.get("correctness_ok", True) for r in bench.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
