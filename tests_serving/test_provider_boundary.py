"""Exposed provider-boundary regressions: malformed evidence is never completion authority."""
import asyncio
import io
import json
import threading
import time
import unittest
from dataclasses import replace

import httpx

from provider_sim.server import Handler, ProviderServer, init_db
from serving.config import load_settings
from serving.provider import Outcome, ProviderClient, classify_mutation
from serving.telemetry import Telemetry
from serving.worker import Worker
from tests_serving.harness import Stack
from tests_serving.test_units import ServiceCase


class ProviderClassificationTests(unittest.TestCase):
    def test_malformed_refusal_fields_stay_unknown(self):
        for value in (None, [], {}, 1, True, ['invalid_request']):
            with self.subTest(code=value):
                result = classify_mutation(httpx.Response(409, json={'effect': 'none', 'code': value}), 'rra-key')
                self.assertEqual(result.state, 'unknown')

    def test_malformed_json_and_duplicate_keys_stay_unknown(self):
        for raw in (b'[' * 2000 + b']' * 2000,
                    b'{"status":"applied","idempotency_key":"rra-key","effect_id":1,"revision":2,"extra":NaN}',
                    b'{"status":"not_applied","status":"applied","idempotency_key":"rra-key","effect_id":1,"revision":2}'):
            with self.subTest(size=len(raw)):
                self.assertEqual(classify_mutation(httpx.Response(200, content=raw), 'rra-key').state, 'unknown')

    def test_dispatch_and_status_require_complete_positive_receipt(self):
        async def check():
            client = ProviderClient('http://127.0.0.1:8766', 'synthetic-key', 1)
            await client.aclose()
            bodies = []
            valid = {'status': 'applied', 'idempotency_key': 'rra-key', 'effect_id': 1, 'revision': 2}
            for field in ('effect_id', 'revision'):
                bodies.append({key: value for key, value in valid.items() if key != field})
                bodies.extend(dict(valid, **{field: value}) for value in (None, False, 0, -1, [], {}, '1'))
            for body in bodies:
                with self.subTest(body=body):
                    response = httpx.Response(200, json=body)
                    self.assertEqual(classify_mutation(response, 'rra-key').state, 'unknown')
                    client._client = httpx.AsyncClient(base_url='http://127.0.0.1:8766', transport=httpx.MockTransport(lambda r: response))
                    try:
                        self.assertEqual((await client.status('rra-key')).state, 'unknown')
                    finally:
                        await client.aclose()
            self.assertEqual(classify_mutation(httpx.Response(200, json=valid), 'rra-key').state, 'succeeded')
            for code in ('revision_conflict', 'key_fenced', 'invalid_request', 'rate_limited'):
                self.assertEqual(classify_mutation(httpx.Response(409, json={'effect': 'none', 'code': code}), 'rra-key').state,
                                 'failed_no_effect')
        asyncio.run(check())


class ProviderWorkerTests(ServiceCase):
    def test_status_client_exceptions_exhaust_lookup_budget_and_keep_hold(self):
        class FailingStatus:
            calls = 0
            async def status(self, key):
                self.calls += 1
                raise RuntimeError('synthetic status exception')
        async def check():
            provider = FailingStatus()
            service = self.service
            service.settings = replace(self.settings, reconcile_max_attempts=3)
            worker = Worker(service, provider, service.settings, Telemetry(io.StringIO()), lambda: None)
            run = self.new_run()
            self.consent(run)
            claim, = service.claim(1)
            service.complete(claim, Outcome('unknown', 'provider_timeout'))
            for _ in range(3):
                self.sql("UPDATE operations SET next_reconcile_at=0 WHERE state='unknown'")
                await worker._reconcile_batch()
            await worker._reconcile_batch()
            self.assertEqual(provider.calls, 3)
            self.assertEqual(self.sql('SELECT state, reconcile_attempts, next_reconcile_at FROM operations'), [('unknown', 3, None)])
            self.assertEqual(self.sql('SELECT COUNT(*) FROM resource_holds'), [(1,)])
            self.assertFalse(worker.degraded)
        asyncio.run(check())

    def test_unexpected_dispatch_exception_is_durably_unknown_without_redispatch(self):
        class FailingProvider:
            calls = 0
            async def mutate(self, **kwargs):
                self.calls += 1
                raise RuntimeError('synthetic adapter exception')
            async def status(self, key):
                return Outcome('unknown', 'status_unsupported')
        async def check():
            provider = FailingProvider()
            worker = Worker(self.service, provider, self.settings, Telemetry(io.StringIO()), lambda: None)
            run = self.new_run()
            self.consent(run)
            claim, = self.service.claim(1)
            await worker._dispatch(claim)
            self.assertEqual(self.sql('SELECT state, attempt_count FROM operations'), [('unknown', 1)])
            self.assertEqual(self.sql('SELECT COUNT(*) FROM resource_holds'), [(1,)])
            self.assertEqual(len(self.service.due_reconciliations(10)), 1)
            await worker._reconcile_batch()
            self.assertEqual(self.service.due_reconciliations(10), [])
            self.assertEqual(self.service.claim(1), [])
            self.assertEqual(provider.calls, 1)
            self.assertEqual(self.sql('SELECT state FROM runs'), [('outcome_unknown',)])
        asyncio.run(check())

    def test_cancellation_is_not_swallowed_by_provider_boundary(self):
        class CancelProvider:
            async def mutate(self, **kwargs):
                raise asyncio.CancelledError()
        async def check():
            worker = Worker(self.service, CancelProvider(), self.settings, Telemetry(io.StringIO()), lambda: None)
            run = self.new_run()
            self.consent(run)
            claim, = self.service.claim(1)
            with self.assertRaises(asyncio.CancelledError):
                await worker._dispatch(claim)
            await worker._abandon(claim)
            self.assertEqual(self.sql('SELECT state, attempt_count FROM operations'), [('unknown', 1)])
        asyncio.run(check())


class LoopbackConfigTests(unittest.TestCase):
    def test_only_unambiguous_loopback_origins_are_accepted(self):
        for url in ('http://127.0.0.1:8766@external.invalid', 'http://localhost:8766@external.invalid',
                    'http://127.0.0.1:8766/', 'http://localhost:8766/'):
            if url.endswith('/'):
                self.assertEqual(load_settings({'RRA_PROVIDER_URL': url}).provider_url, url)
            else:
                with self.assertRaises(ValueError, msg=url):
                    load_settings({'RRA_PROVIDER_URL': url})
        for url in ('http://127.0.0.1:bad', 'http://127.0.0.1:0', 'http://127.0.0.1:65536',
                    'http://localhost:', 'http://127.0.0.1:8766/path', 'http://localhost:8766?x=1',
                    'http://localhost:8766#fragment', 'http://external.invalid:8766', 'https://localhost:8766'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                load_settings({'RRA_PROVIDER_URL': url})
        for url in ('http://127.0.0.1:8766', 'http://localhost:8766'):
            self.assertEqual(load_settings({'RRA_PROVIDER_URL': url}).provider_url, url)


class MalformedProviderHTTPTests(unittest.TestCase):
    def test_applied_effect_with_malformed_reply_reconciles_only_from_complete_evidence(self):
        # Test-only HTTP corruption around the real provider's durable effect transaction.
        # No runtime simulator failpoint or provider-ledger oracle is changed.
        class CorruptReply(Handler):
            def do_POST(self):
                if self.path == '/v1/mutations':
                    self.server.mutation_requests += 1
                return super().do_POST()
            def _send(self, status, body, *, raw=None):
                if self.path == '/v1/mutations' and status == 200:
                    return super()._send(409, {'effect': 'none', 'code': []})
                if self.path.startswith('/v1/mutations/'):
                    self.server.status_requests += 1
                    if isinstance(body, dict) and body.get('status') == 'applied' and not self.server.complete_evidence.is_set():
                        body = {key: value for key, value in body.items() if key != 'effect_id'}
                return super()._send(status, body, raw=raw)

        stack = Stack(env={'RRA_RECONCILE_BACKOFF_SECONDS': '0.1', 'RRA_RECONCILE_BACKOFF_MAX_SECONDS': '0.2'})
        server = None
        thread = None
        run = None
        observations = {}
        try:
            db = stack.root / 'provider' / 'provider.db'
            init_db(db)
            server = ProviderServer(('127.0.0.1', stack.provider_port), db_path=db,
                                    key=(stack.root / 'app' / 'provider.key').read_text().strip(), mode='full', test_mode=False)
            server.RequestHandlerClass = CorruptReply
            server.mutation_requests = server.status_requests = 0
            server.complete_evidence = threading.Event()
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
            thread.start()
            stack.start_app()
            run = stack.new_run()
            self.assertEqual(stack.approve(run).status_code, 202)
            unknown = stack.wait_state(run['run_id'], {'outcome_unknown'}, timeout=2)
            self.assertTrue(unknown['resource_hold']['held'])
            deadline = time.monotonic() + 2
            while server.status_requests < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(server.status_requests, 2)
            self.assertEqual(stack.snapshot(run['run_id'])['state'], 'outcome_unknown')
            observations['after_malformed_status'] = {'state': 'outcome_unknown', 'held': True,
                                                      'status_requests': server.status_requests}
            self.assertEqual(len(stack.provider_effects()), 1)
            self.assertEqual(server.mutation_requests, 1)
            server.complete_evidence.set()
            final = stack.wait_state(run['run_id'], {'succeeded'})
            self.assertFalse(final['resource_hold']['held'])
            self.assertEqual(final['operation']['provider_result']['effect_id'], stack.provider_effects()[0]['effect_id'])
            self.assertEqual(server.mutation_requests, 1)
            self.assertEqual(len(stack.provider_effects()), 1)
            conn = stack.app_db()
            try:
                kinds = [row[0] for row in conn.execute('SELECT event_type FROM events WHERE run_id=?', (run['run_id'],))]
            finally:
                conn.close()
            self.assertEqual(kinds.count('dispatch_started'), 1)
            self.assertIn('dispatch_uncertain', kinds)
            self.assertIn('serving.reconciled', kinds)
        finally:
            if run is not None:
                # Receipt fields contain only synthetic states/counts, never tokens or payloads.
                conn = stack.app_db()
                try:
                    row = conn.execute('SELECT state, attempt_count FROM operations WHERE run_id=?', (run['run_id'],)).fetchone()
                    observations['final_operation'] = dict(row) if row else None
                    observations['final_holds'] = conn.execute('SELECT COUNT(*) FROM resource_holds').fetchone()[0]
                finally:
                    conn.close()
                observations.update(provider_effects=len(stack.provider_effects()), mutation_requests=server.mutation_requests,
                                    status_requests=server.status_requests)
                print('Provider boundary receipt: ' + json.dumps(observations, sort_keys=True), flush=True)
            stack.close()
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=2)
