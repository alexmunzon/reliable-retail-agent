"""Real authenticated HTTP reads, independent effect counts, and optional capture faults."""
import asyncio
import json
import time
import unittest
from unittest import mock
import httpx
from observability.live import SpanRecorder
from serving.provider import Outcome
from serving.telemetry import Telemetry
from serving.worker import Worker
from tests_serving.harness import Stack, ADDRESS
from tests_serving.test_units import ServiceCase

BASE = '/api/v1/observability/'

class LiveHTTP(unittest.TestCase):
    def setUp(self):
        self.stack = Stack()
        self.stack.start_provider(); self.stack.start_app()
    def tearDown(self): self.stack.close()
    def data(self, path, actor='alice'):
        response=self.stack.get(BASE+path,actor)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_observing_never_delivers_prompt_or_dispatches(self):
        s=self.stack
        session=s.post('/api/v1/sessions','alice','observe-session',{'resource_id':'SYN-ORDER-1042'}).json()
        run=s.post('/api/v1/sessions/'+session['session_id']+'/runs','alice','observe-run-0001',
                   {'request':{'type':'change_order_address','address':ADDRESS}}).json()
        with s.app_db() as conn:
            before=[tuple(r) for r in conn.execute('SELECT event_type,envelope FROM events')]
        page=self.data('requests'); key=page['items'][0]['key']
        self.assertIn('/trc_',key)
        detail=self.data('requests/'+key)
        self.data('export/'+key); self.assertFalse(self.data('aggregates')['sample']['all_history']); self.data('meta')
        self.assertEqual(detail['data_origin'],'live_synthetic_provider')
        self.assertIsNone(detail['costs']['estimated_charge']['total'])
        with s.app_db() as conn:
            after=[tuple(r) for r in conn.execute('SELECT event_type,envelope FROM events')]
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM operations').fetchone()[0],0)
        self.assertEqual(before,after)
        self.assertFalse(any(kind=='prompt' for kind,_ in after))
        self.assertEqual(s.provider_effects(),[])
        self.assertNotIn('Example Lane',json.dumps(detail))
        for actor in ('bob','carol','dave'):
            self.assertEqual(self.data('requests',actor)['items'],[])
            for route in ('requests/','export/'):
                denied=s.get(BASE+route+key,actor)
                self.assertEqual(denied.status_code,404)
        for route in ('meta','requests','aggregates','requests/'+key,'export/'+key):
            self.assertEqual(httpx.get(s.url(BASE+route)).status_code,401)
            self.assertEqual(s.get(BASE+route,'operator').status_code,403)
        self.assertEqual(s.get(BASE+'requests?from=2026-99-01T00:00Z','alice').status_code,400)
        self.assertEqual(s.get(BASE+'requests?principal=bob','alice').status_code,400)

    def test_real_effect_and_measured_provider_duration_then_restart_missingness(self):
        s=self.stack; run=s.new_run(tag='obs-success'); s.approve(run); s.wait_state(run['run_id'],{'succeeded'})
        key=self.data('requests')['items'][0]['key']
        detail=self.data('requests/'+key)
        self.assertEqual(detail['status'],'completed')
        self.assertEqual(detail['evidence']['contract_verdict'],'CONSISTENT')
        self.assertEqual(len(s.provider_effects()),1)
        timing=detail['timing']['by_class']['dispatch_provider']
        self.assertEqual(timing['known'],1); self.assertGreater(timing['total_ns'],0)
        self.assertIsNone(detail['timing']['request_duration_ns'])
        self.assertEqual(detail['costs']['model_usage_applicability'],'not_applicable_no_model_calls')
        self.assertEqual(detail['costs']['confirmed_charge'],{})
        for _ in range(3):self.data('requests/'+key);self.data('export/'+key)
        self.assertEqual(len(s.provider_effects()),1)
        s.stop_app();s.start_app()
        later=self.data('requests/'+key)
        self.assertEqual(later['timing']['spans_accepted'],0)
        self.assertEqual(later['status'],'completed')
        self.assertEqual(len(s.provider_effects()),1)
        s.run_cli('revoke','alice')
        for route in ('meta','requests','aggregates','requests/'+key,'export/'+key):
            self.assertEqual(s.get(BASE+route,'alice').status_code,401)

    def test_live_refusal_and_cancel_before_claim(self):
        s=self.stack;s.arm('reject_invalid')
        run=s.new_run(tag='obs-refusal');s.approve(run);s.wait_state(run['run_id'],{'failed'})
        key=self.data('requests')['items'][0]['key']; detail=self.data('requests/'+key)
        self.assertEqual(detail['status'],'failed')
        self.assertIn('dispatch_rejected',[e['event_type'] for e in detail['events']])
        cancelled=s.new_run(tag='obs-cancel')
        s.post('/api/v1/runs/'+cancelled['run_id']+'/cancel','alice','observe-cancel')
        rows=self.data('requests')['items']
        self.assertEqual(next(r for r in rows if r['run_id']==cancelled['run_id'])['status'],'cancelled')
        self.assertEqual(s.provider_effects(),[])

class CaptureIsolation(ServiceCase):
    def test_failing_optional_recorder_cannot_change_dispatch(self):
        run=self.new_run();self.consent(run);claim=self.service.claim(1)[0]
        provider=mock.Mock();provider.mutate=mock.AsyncMock(return_value=Outcome('succeeded','applied',{'revision':2,'effect_id':'synthetic'}))
        recorder=mock.Mock();recorder.record.side_effect=OSError('synthetic metrics fault')
        worker=Worker(self.service,provider,self.settings,Telemetry(),lambda:None,recorder)
        asyncio.run(worker._dispatch(claim))
        provider.mutate.assert_awaited_once()
        self.assertEqual(self.sql('SELECT state,attempt_count FROM operations'),[('succeeded',1)])

    def test_span_memory_and_age_are_bounded(self):
        recorder=SpanRecorder(capacity=2)
        claim=mock.Mock(run_id='run-one',operation_id='op-one')
        for n in range(4):recorder.record(claim,n,n+1,'ok')
        self.assertEqual(len(recorder.records),2)
        self.assertEqual(recorder.stats()['evicted'],2)
        self.assertEqual(len(recorder.for_run('run-one','trace-one',10)),2)
        self.assertEqual(recorder.for_run('run-one','trace-one',3_600_000_000_010),[])
