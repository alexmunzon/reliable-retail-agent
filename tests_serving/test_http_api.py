"""Real loopback HTTP/SSE tests against uvicorn + the synthetic provider (separate processes)."""
import json
import socket
import threading
import time
import unittest

import httpx

from tests_serving.harness import ADDRESS, Stack
from tests_serving.sse import SSEParser


def read_stream(stack, run_id, actor="alice", cursor=None, deadline=2.0):
    """Collect SSE events until the server closes the stream or `deadline` of silence passes."""
    headers = stack.headers(actor)
    if cursor:
        headers["Last-Event-ID"] = cursor
    parser, events = SSEParser(), []
    try:
        with httpx.stream("GET", stack.url(f"/api/v1/runs/{run_id}/events"), headers=headers,
                          timeout=httpx.Timeout(10, read=deadline)) as response:
            if response.status_code != 200:
                return response.status_code, json.loads(response.read())
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-store"
            for chunk in response.iter_raw():
                events.extend(parser.feed(chunk))
    except httpx.ReadTimeout:
        pass
    return 200, events


class HTTPAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = Stack(env={"RRA_EVENT_RETENTION_SECONDS": "1", "RRA_MAINTENANCE_SECONDS": "0.3"})
        cls.stack.start_provider()
        cls.stack.start_app()

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def effects(self):
        return self.stack.provider_effects()

    def test_health_endpoints_expose_no_customer_data(self):
        live = httpx.get(self.stack.url("/health/live")).json()
        ready = httpx.get(self.stack.url("/health/ready")).json()
        self.assertEqual((live, ready), ({"status": "live"}, {"status": "ready"}))

    def test_authentication_scope_and_ownership_matrix(self):
        s = self.stack
        run = s.new_run(tag="authz")
        path = f"/api/v1/runs/{run['run_id']}"
        missing = httpx.get(s.url(path))
        self.assertEqual((missing.status_code, missing.json()["code"]), (401, "unauthenticated"))
        self.assertIn("Bearer", missing.headers["www-authenticate"])
        for header in ("Bearer nope", "Basic abc", "Bearer rra_" + "A" * 43, s.tokens["alice"]):
            self.assertEqual(httpx.get(s.url(path), headers={"Authorization": header}).status_code, 401)
        self.assertEqual(s.post("/api/v1/sessions", "dave", "dave-session-01", {"resource_id": "SYN-ORDER-1042"}).json()["code"],
                         "insufficient_scope")
        self.assertEqual(s.get(path, "operator").status_code, 403)
        for actor in ("bob", "carol", "dave"):  # Other tenant, same tenant other actor, read-only scope.
            self.assertEqual(s.get(path, actor).status_code, 404)
            self.assertEqual(read_stream(s, run["run_id"], actor)[0], 404)
        # Another customer cannot approve by replaying the prompt identity they somehow learned.
        stolen = s.post(f"{path}/consents", "bob", "bob-steal-00001", s.consent_body(run))
        self.assertEqual(stolen.status_code, 404)
        foreign = s.post("/api/v1/sessions", "bob", "bob-foreign-001", {"resource_id": "SYN-ORDER-1042"})
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(s.snapshot(run["run_id"])["state"], "awaiting_consent")
        self.assertEqual([e for e in self.effects() if e["order_id"] == "SYN-ORDER-1042" and
                          e["idempotency_key"] == "rra-" + run["proposal"]["operation_id"]], [])

    def test_strict_request_parsing(self):
        s = self.stack
        url = s.url("/api/v1/sessions")
        auth = s.headers("alice", "strict-key-0001")

        def raw(body, content_type="application/json", **extra):
            return httpx.post(url, content=body, headers={**auth, "Content-Type": content_type, **extra})
        cases = [
            (raw(b'{"resource_id":"SYN-ORDER-1042","resource_id":"SYN-ORDER-1043"}'), 400, "invalid_json"),
            (raw(b'{"resource_id":NaN}'), 400, "invalid_json"),
            (raw(b'["SYN-ORDER-1042"]'), 400, "invalid_body"),
            (raw(b'{"resource_id":"SYN-ORDER-1042"}', "text/plain"), 415, "invalid_content_type"),
            (raw(b'{"resource_id":"' + b"x" * 20_000 + b'"}'), 413, "body_too_large"),
            (raw(b'{"resource_id":"SYN-ORDER-1042","actor_id":"bob"}'), 422, "validation_error"),
            (raw(b'{"resource_id":1042}'), 422, "validation_error"),
            (raw(b'{"resource_id":"../etc"}'), 422, "validation_error"),
        ]
        for response, status, code in cases:
            self.assertEqual((response.status_code, response.json()["code"]), (status, code), response.text)
            self.assertIn("request_id", response.json())
        no_key = httpx.post(url, json={"resource_id": "SYN-ORDER-1042"}, headers=s.headers("alice"))
        self.assertEqual((no_key.status_code, no_key.json()["code"]), (400, "invalid_idempotency_key"))
        # Validation errors report locations, never submitted values.
        sentinel = "SENTINEL-ADDRESS-77 Leak Street"
        session = s.post("/api/v1/sessions", "alice", "strict-session-1", {"resource_id": "SYN-ORDER-1042"}).json()
        bad = s.post(f"/api/v1/sessions/{session['session_id']}/runs", "alice", "strict-run-00001",
                     {"request": {"type": "change_order_address", "address": dict(ADDRESS, street=sentinel, country=7)}})
        self.assertEqual(bad.status_code, 422)
        self.assertNotIn(sentinel, bad.text)
        tool = s.post(f"/api/v1/sessions/{session['session_id']}/runs", "alice", "strict-run-00002",
                      {"request": {"type": "issue_refund", "address": ADDRESS}})
        self.assertEqual(tool.status_code, 422)
        self.assertEqual(s.post("/api/v1/execute_tool", "alice", "strict-exec-0001", {}).status_code, 404)

    def test_command_idempotency(self):
        s = self.stack
        first = s.post("/api/v1/sessions", "alice", "idem-session-01", {"resource_id": "SYN-ORDER-1042"})
        again = s.post("/api/v1/sessions", "alice", "idem-session-01", {"resource_id": "SYN-ORDER-1042"})
        self.assertEqual((first.status_code, first.json()), (again.status_code, again.json()))
        conflict = s.post("/api/v1/sessions", "alice", "idem-session-01", {"resource_id": "SYN-ORDER-9999"})
        self.assertEqual((conflict.status_code, conflict.json()["code"]), (409, "idempotency_conflict"))
        # Keys are scoped per principal: bob's identical key is an independent command.
        bob = s.post("/api/v1/sessions", "bob", "idem-session-01", {"resource_id": "SYN-ORDER-2077"})
        self.assertEqual(bob.status_code, 201)

    def test_origin_and_host_checks(self):
        s = self.stack
        cross = httpx.post(s.url("/api/v1/sessions"), json={"resource_id": "SYN-ORDER-1042"},
                           headers={**s.headers("alice", "origin-key-0001"), "Origin": "https://evil.example"})
        self.assertEqual((cross.status_code, cross.json()["code"]), (403, "origin_rejected"))
        same = httpx.post(s.url("/api/v1/sessions"), json={"resource_id": "SYN-ORDER-1042"},
                          headers={**s.headers("alice", "origin-key-0002"), "Origin": f"http://127.0.0.1:{s.app_port}"})
        self.assertEqual(same.status_code, 201)
        host = httpx.get(s.url("/health/live"), headers={"Host": "evil.example"})
        self.assertEqual(host.status_code, 400)

    def test_consent_refusals_cause_no_effect(self):
        s = self.stack
        other = s.new_run(tag="other-prompt")
        run = s.new_run(tag="refusals")
        path = f"/api/v1/runs/{run['run_id']}/consents"
        body = s.consent_body(run)
        cases = [
            (dict(body, prompt_id=other["proposal"]["prompt_id"]), 409, "stale_consent"),
            (dict(body, payload_hash=other["proposal"]["payload_hash"][::-1]), 409, "stale_consent"),
            (dict(body, proposal_version=2), 409, "stale_consent"),
            (dict(body, response="maybe"), 200, "ambiguous"),
            (dict(body, response="Yes, but change the apartment to 4B."), 200, "ambiguous"),
            (dict(body, response="yes please"), 200, "ambiguous"),
            (dict(body, proposal_version=True), 422, None),
            (dict(body, approved=True), 422, None),
        ]
        for index, (payload, status, decision) in enumerate(cases):
            response = s.post(path, "alice", f"refusal-key-{index:04d}", payload)
            self.assertEqual(response.status_code, status, response.text)
            if decision:
                self.assertEqual(response.json()["decision"], decision)
        self.assertEqual(s.snapshot(run["run_id"])["state"], "awaiting_consent")
        rejected = s.post(path, "alice", "refusal-key-reject", dict(body, response="no"))
        self.assertEqual(rejected.json()["decision"], "rejected")
        after = s.post(path, "alice", "refusal-key-after", body)
        self.assertEqual(after.json()["decision"], "consent_not_pending")
        time.sleep(0.5)
        keys = {e["idempotency_key"] for e in self.effects()}
        self.assertNotIn("rra-" + run["proposal"]["operation_id"], keys)
        types = [event["event"] for event in read_stream(s, run["run_id"])[1]]
        self.assertEqual(types.count("blocked"), 6)
        self.assertNotIn("dispatch_started", types)

    def test_sse_replay_resume_and_cursor_validation(self):
        s = self.stack
        run = s.new_run(tag="sse")
        s.approve(run)
        s.wait_state(run["run_id"], {"succeeded"})
        status, events = read_stream(s, run["run_id"])
        self.assertEqual(status, 200)
        sequences = [json.loads(event["data"])["sequence"] for event in events]
        self.assertEqual(sequences, list(range(1, len(events) + 1)))
        # A finished run's stream ends with exactly one completeness marker (trace-v1 serving rule).
        self.assertEqual([event["event"] for event in events],
                         ["serving.run_accepted", "prompt", "authorized", "dispatch_started", "dispatch_succeeded",
                          "serving.run_finished"])
        for event in events:
            data = json.loads(event["data"])
            self.assertEqual((event["id"], event["event"]), (data["event_id"], data["event_type"]))
            self.assertEqual(data["source"] in {"serving-api", "serving-worker"}, True)
            self.assertTrue(data["occurred_at"].endswith("Z"))
        _, resumed = read_stream(s, run["run_id"], cursor=events[1]["id"])
        self.assertEqual([event["id"] for event in resumed], [event["id"] for event in events[2:]])
        _, again = read_stream(s, run["run_id"], cursor=events[1]["id"])
        self.assertEqual(again, resumed)  # Same cursor, same replay: client dedupes by id.
        other = s.new_run(tag="sse-other")
        foreign_cursor = read_stream(s, other["run_id"])[1][0]["id"]
        for cursor in (foreign_cursor, "garbage", events[-1]["id"][:-8] + "99999999", "evt_" + "x" * 40):
            status, body = read_stream(s, run["run_id"], cursor=cursor)
            self.assertEqual((status, body["code"]), (400, "invalid_cursor"))
            self.assertNotIn(other["run_id"], json.dumps(body))

    def test_live_stream_delivers_incrementally_and_observing_never_dispatches(self):
        s = self.stack
        run = s.new_run(tag="live")
        for _ in range(5):
            self.assertEqual(len(read_stream(s, run["run_id"], deadline=0.3)[1]), 2)
        time.sleep(0.5)
        self.assertEqual(s.snapshot(run["run_id"])["state"], "awaiting_consent")
        received, arrival = [], {}

        def listen():
            parser = SSEParser()
            with httpx.stream("GET", s.url(f"/api/v1/runs/{run['run_id']}/events"), headers=s.headers("alice"),
                              timeout=httpx.Timeout(10, read=10)) as response:
                for chunk in response.iter_raw():
                    for event in parser.feed(chunk):
                        received.append(event["event"])
                        arrival.setdefault(event["event"], time.monotonic())
        listener = threading.Thread(target=listen)
        listener.start()
        time.sleep(0.5)
        self.assertEqual(received, ["serving.run_accepted", "prompt"])  # Open, waiting on live tail.
        approved_at = time.monotonic()
        s.approve(run)
        listener.join(timeout=10)
        self.assertFalse(listener.is_alive())  # The stream ends by itself once the run is terminal.
        # Ends with the completeness marker written in the same transaction as the final state.
        self.assertEqual(received[-4:], ["authorized", "dispatch_started", "dispatch_succeeded", "serving.run_finished"])
        self.assertGreater(arrival["authorized"], approved_at)

    def test_stream_subscriber_limit(self):
        s = self.stack
        run = s.new_run(tag="limit")
        streams = []
        try:
            for _ in range(4):
                context = httpx.stream("GET", s.url(f"/api/v1/runs/{run['run_id']}/events"), headers=s.headers("alice"),
                                       timeout=httpx.Timeout(10, read=5))
                response = context.__enter__()
                self.assertEqual(response.status_code, 200)
                streams.append(context)
            status, body = read_stream(s, run["run_id"])
            self.assertEqual((status, body["code"]), (429, "stream_limit"))
            self.assertEqual(read_stream(s, s.new_run("bob", "SYN-ORDER-2077", tag="limit")["run_id"], "bob",
                                         deadline=0.3)[0], 200)
        finally:
            for context in streams:
                context.__exit__(None, None, None)
        time.sleep(0.5)
        self.assertEqual(read_stream(s, run["run_id"], deadline=0.3)[0], 200)

    def test_expired_retention_cursor_returns_410(self):
        s = self.stack
        run = s.new_run(tag="retention")
        cursor = read_stream(s, run["run_id"], deadline=0.3)[1][0]["id"]
        s.post(f"/api/v1/runs/{run['run_id']}/cancel", "alice", "retention-cancel")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, body = read_stream(s, run["run_id"], cursor=cursor)
            if status == 410:
                break
            time.sleep(0.3)
        self.assertEqual((status, body["code"]), (410, "replay_expired"))
        self.assertEqual(read_stream(s, run["run_id"])[0], 410)  # Never silently restarts at zero.
        self.assertEqual(s.snapshot(run["run_id"])["state"], "cancelled")

    def test_revoked_credential_closes_stream_and_blocks_commands(self):
        s = self.stack
        run = s.new_run("carol", "SYN-ORDER-1043", tag="revoke")
        done = threading.Event()

        def listen():
            read_stream(s, run["run_id"], "carol", deadline=20)
            done.set()
        threading.Thread(target=listen, daemon=True).start()
        time.sleep(0.5)
        s.run_cli("revoke", "carol")
        self.assertTrue(done.wait(5), "stream stayed open after revocation")
        self.assertEqual(s.get(f"/api/v1/runs/{run['run_id']}", "carol").status_code, 401)

    def test_client_controlled_strings_never_reach_logs_or_errors(self):
        s = self.stack
        sentinel_token = "rra_" + "S" * 43
        echoed = httpx.get(s.url("/api/v1/runs/run_" + "0" * 32), headers={**s.headers("alice"), "X-Request-ID": sentinel_token})
        self.assertNotIn(sentinel_token, echoed.text)
        self.assertNotEqual(echoed.headers["x-request-id"], sentinel_token)
        sneaky_key = "SENTINEL-ADDRESS 99 Secret Road"
        response = s.post("/api/v1/sessions", "alice", "privacy-key-0001", {"resource_id": "SYN-ORDER-1042", sneaky_key: 1})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("Secret Road", response.text)
        time.sleep(0.2)
        self.assertNotIn(sentinel_token, s.logs())
        self.assertNotIn("Secret Road", s.logs())

    def test_demo_client_is_served_same_origin_under_the_csp(self):
        page = httpx.get(self.stack.url("/serving/"))
        self.assertEqual(page.status_code, 200)
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        self.assertIn("connect-src 'self'", page.headers["content-security-policy"])
        script = httpx.get(self.stack.url("/serving/client.js"))
        self.assertEqual(script.status_code, 200)
        self.assertIn("javascript", script.headers["content-type"])

    def test_walkthrough_arm_command_injects_a_provider_failure(self):
        s = self.stack
        s.run_cli("arm", "reject_invalid")
        run = s.new_run(tag="arm")
        self.assertEqual(s.approve(run).status_code, 202)
        final = s.wait_state(run["run_id"], {"failed"})
        self.assertEqual(final["operation"]["error_code"], "invalid_request")
        self.assertEqual([e for e in self.effects() if e["idempotency_key"] == "rra-" + run["proposal"]["operation_id"]], [])

    def test_zz_logs_contain_no_secrets_or_customer_data(self):
        logs = self.stack.logs()
        self.assertIn('"event":"http.request"', logs)
        for secret in list(self.stack.tokens.values()) + ["Bearer", "authorization", "18 Example Lane",
                                                          "Sampleton", "Please confirm", "SENTINEL-ADDRESS"]:
            self.assertNotIn(secret, logs)
        key = (self.stack.root / "app" / "provider.key").read_text().strip()
        self.assertNotIn(key, logs)


class LimitsTests(unittest.TestCase):
    def setUp(self):
        self.stack = Stack(env={"RRA_COMMAND_RATE_PER_MINUTE": "6", "RRA_COMMAND_BURST": "3",
                                "RRA_UNAUTHENTICATED_RATE_PER_MINUTE": "5", "RRA_SSE_SEND_TIMEOUT_SECONDS": "1",
                                "RRA_SSE_BATCH_SIZE": "20"})
        self.stack.start_provider()
        self.stack.start_app()

    def tearDown(self):
        self.stack.close()

    def test_rate_limits_return_429_with_retry_after(self):
        s = self.stack
        statuses = [s.post("/api/v1/sessions", "alice", f"rate-key-{i:05d}", {"resource_id": "SYN-ORDER-1042"}).status_code
                    for i in range(4)]
        self.assertEqual(statuses, [201, 201, 201, 429])
        limited = s.post("/api/v1/sessions", "alice", "rate-key-99999", {"resource_id": "SYN-ORDER-1042"})
        self.assertTrue(limited.json()["retryable"])
        self.assertGreaterEqual(int(limited.headers["retry-after"]), 1)
        self.assertEqual(s.post("/api/v1/sessions", "bob", "rate-bob-00001", {"resource_id": "SYN-ORDER-2077"}).status_code, 201)
        anonymous = [httpx.get(s.url("/api/v1/runs/run_" + "0" * 32)).status_code for _ in range(7)]
        self.assertEqual(anonymous[:5], [401] * 5)
        self.assertEqual(anonymous[-1], 429)

    def test_slow_consumer_is_disconnected_without_blocking_work(self):
        s = self.stack
        run = s.new_run(tag="slow")
        # Test-only bulk setup: append many large committed events to this run's outbox.
        import sys
        sys.path.insert(0, str(s.root))
        from serving import storage
        from serving.config import load_settings
        from serving.service import Service
        service = Service(load_settings(s.env()))
        conn = service.connect()
        with storage.write_tx(conn):
            for index in range(3000):
                service._event(conn, run["run_id"], "test.padding", {"index": index, "pad": "p" * 2000}, now=time.time())
        conn.close()
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.connect(("127.0.0.1", s.app_port))
        sock.sendall((f"GET /api/v1/runs/{run['run_id']}/events HTTP/1.1\r\nHost: 127.0.0.1:{s.app_port}\r\n"
                      f"Authorization: Bearer {s.tokens['alice']}\r\n\r\n").encode())
        self.assertIn(b"200", sock.recv(64))  # Then stop reading entirely.
        try:
            started = time.monotonic()
            other = s.new_run("bob", "SYN-ORDER-2077", tag="while-slow")
            s.approve(other, "bob")
            s.wait_state(other["run_id"], {"succeeded"}, "bob")
            self.assertLess(time.monotonic() - started, 5)  # Mutations are not blocked by the stalled reader.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                metrics = s.get("/api/v1/ops/metrics", "operator").json()
                if metrics["counters"].get("streams_slow_disconnected"):
                    break
                time.sleep(0.3)
            self.assertEqual(metrics["counters"]["streams_slow_disconnected"], 1)
            self.assertEqual(metrics["gauges"]["streams_open"], 0)
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()
