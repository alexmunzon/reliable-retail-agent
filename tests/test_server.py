"""HTTP tests use a real temporary loopback server and the actual engine."""
import concurrent.futures
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from server import DemoHTTPServer, MAX_BODY_BYTES


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = tempfile.TemporaryDirectory()
        Path(cls.web.name, "index.html").write_text("<!doctype html><title>Test host</title>")
        cls.server = DemoHTTPServer(("127.0.0.1", 0), web_root=cls.web.name)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.web.cleanup()

    def request(self, method, path, body=None, raw=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        if body is not None:
            raw = json.dumps(body)
        hdrs = {"Content-Type": "application/json"}
        hdrs.update(headers or {})
        connection.request(method, path, body=raw, headers=hdrs)
        response = connection.getresponse()
        payload = response.read()
        status, content_type = response.status, response.getheader("Content-Type", "")
        connection.close()
        return status, json.loads(payload) if "application/json" in content_type else payload.decode()

    def create(self, scenario="normal-confirmation", **extra):
        status, snapshot = self.request("POST", "/api/sessions", {"scenario_id": scenario, **extra})
        self.assertEqual(status, 201, snapshot)
        return snapshot

    def test_health_scenarios_and_static_host(self):
        status, body = self.request("GET", "/api/health")
        self.assertEqual((status, body["paid_calls"], body["mode"]), (200, 0, "scripted-synthetic"))
        status, body = self.request("GET", "/api/scenarios")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(body["scenarios"]), 5)
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/imaginary-spa-route")[0], 404)

    def test_concurrent_repeated_clicks_are_one_step(self):
        initial = self.create()
        path = "/api/sessions/" + initial["session_id"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.request("POST", path + "/step", {"request_id": "same-click"}), range(8)))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertTrue(all(snapshot == results[0][1] for _, snapshot in results))
        self.assertEqual(self.request("GET", path)[1]["step_index"], 1)

    def test_request_id_conflict_and_creation_replay(self):
        first = self.create(request_id="unique-create-replay")
        second = self.create(request_id="unique-create-replay")
        self.assertEqual(first, second)
        self.assertEqual(self.request("POST", "/api/sessions", {"scenario_id": "missing-confirmation", "request_id": "unique-create-replay"})[0], 409)
        path = "/api/sessions/" + first["session_id"]
        self.assertEqual(self.request("POST", path + "/step", {"request_id": "one"})[0], 200)
        self.assertEqual(self.request("POST", path + "/run", {"request_id": "one"})[0], 409)
        self.request("POST", path + "/respond", {"request_id": "answer", "text": "no", "prompt_id": "old-prompt"})
        self.assertEqual(self.request("POST", path + "/respond", {"request_id": "answer", "text": "yes", "prompt_id": "old-prompt"})[0], 409)

    def test_run_replay_reset_and_old_click_does_not_advance_reset(self):
        initial = self.create()
        path = "/api/sessions/" + initial["session_id"]
        first = self.request("POST", path + "/run", {"request_id": "run"})
        self.assertEqual(first, self.request("POST", path + "/run", {"request_id": "run"}))
        self.assertEqual(len(first[1]["lanes"]["gated"]["dispatches"]), 1)
        reset = self.request("POST", path + "/reset", {"request_id": "reset"})
        self.assertEqual(reset[1]["step_index"], 0)
        self.assertEqual(self.request("POST", path + "/run", {"request_id": "run"}), first)
        self.assertEqual(self.request("GET", path)[1]["step_index"], 0)
        self.assertEqual(self.request("POST", path + "/reset", {"request_id": "reset"}), reset)

    def test_scenario_and_session_isolation(self):
        a, b = self.create("changed-payload"), self.create("airline-transfer")
        pa, pb = "/api/sessions/" + a["session_id"], "/api/sessions/" + b["session_id"]
        done = self.request("POST", pa + "/run", {})[1]
        self.assertEqual(len(done["lanes"]["gated"]["dispatches"]), 0)
        self.assertEqual(self.request("GET", pb)[1], b)
        accepted = self.request("POST", pa + "/respond", {"text": "yes", "prompt_id": done["lanes"]["gated"]["confirmation"]["prompt_id"]})[1]
        self.assertEqual(len(accepted["lanes"]["gated"]["dispatches"]), 1)
        self.assertEqual(self.request("GET", pb)[1], b)

    def test_invalid_json_fields_and_bounds(self):
        initial = self.create()
        path = "/api/sessions/" + initial["session_id"] + "/respond"
        for raw in ("{", "[]", '{"text":"yes","text":"no"}', '{"text":NaN}'):
            with self.subTest(raw=raw):
                self.assertEqual(self.request("POST", path, raw=raw)[0], 400)
        for body in ({}, {"text": 1}, {"text": " "}, {"text": "x" * 4001}, {"text": "yes", "extra": True}, {"text": "yes", "request_id": None}):
            with self.subTest(body=str(body)[:80]):
                self.assertEqual(self.request("POST", path, body)[0], 400)
        self.assertEqual(self.request("POST", path, raw="x" * (MAX_BODY_BYTES + 1))[0], 413)
        self.assertEqual(self.request("POST", path, raw='{"text":"yes"}', headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/sessions", {"scenario_id": "unknown"})[0], 400)

    def test_stale_tab_yes_cannot_authorize_prompt_after_reset(self):
        initial = self.create("missing-confirmation")
        path = "/api/sessions/" + initial["session_id"]
        old = self.request("POST", path + "/run", {})[1]
        old_prompt = old["lanes"]["gated"]["confirmation"]["prompt_id"]
        self.request("POST", path + "/reset", {})
        new = self.request("POST", path + "/run", {})[1]
        new_prompt = new["lanes"]["gated"]["confirmation"]["prompt_id"]
        self.assertNotEqual(old_prompt, new_prompt)
        stale = self.request("POST", path + "/respond", {"text": "yes", "prompt_id": old_prompt})[1]
        self.assertEqual(stale["lanes"]["gated"]["dispatches"], [])
        fresh = self.request("POST", path + "/respond", {"text": "yes", "prompt_id": new_prompt})[1]
        self.assertEqual(len(fresh["lanes"]["gated"]["dispatches"]), 1)

    def test_rejection_cannot_be_cleared_by_stale_then_fresh_reply(self):
        initial = self.create("rejection")
        path = "/api/sessions/" + initial["session_id"]
        rejected = self.request("POST", path + "/run", {})[1]
        prompt_id = rejected["lanes"]["gated"]["confirmation"]["prompt_id"]
        self.request("POST", path + "/respond", {"text": "yes", "prompt_id": "stale"})
        result = self.request("POST", path + "/respond", {"text": "yes", "prompt_id": prompt_id})[1]
        self.assertEqual(result["lanes"]["gated"]["dispatches"], [])

    def test_unknown_routes_and_traversal_are_json_errors(self):
        for path in ("/api/nope", "/api/sessions/" + "0" * 32, "/../server.py", "/%2e%2e/server.py", "/%2e%2e%2fserver.py", "/%00", "/%5c..%5cserver.py"):
            with self.subTest(path=path):
                status, body = self.request("GET", path)
                self.assertEqual(status, 404)
                self.assertIn("error", body)
        self.assertEqual(self.request("POST", "/api/nope", {})[0], 404)
        self.assertEqual(self.request("PUT", "/api/health", {})[0], 501)


if __name__ == "__main__":
    unittest.main()
