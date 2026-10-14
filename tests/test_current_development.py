"""Unpaid synthetic contracts of the actual current development controller."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from study.consent_current import CLASSIFIER_VERSION,classify_response
from study.controller_current import StudyController,Limits
from study.planning import PlanningPolicy
from study.visible_current import VisibleStore
from study.evidence import AttemptJournal,EpisodeKey,read_events
from study.registry import ToolRegistry
from study.gate_current import StudyGate
from study.intents import build_intent

ADDRESS=dict(address1='18 Example Lane',address2='',city='Sampleton',state='CA',country='USA',zip='00000')
ADDRESS_WRITE={'name':'modify_user_address','arguments':{'user_id':'synthetic_user',**ADDRESS}}
STOP={'name':'respond','arguments':{'content':'Synthetic finished.'}}
RESERVATION={'reservation_id':'SYN001','user_id':'synthetic_user','cabin':'business',
    'flights':[{'flight_number':'SYN_A','date':'2030-01-01','origin':'AAA','destination':'BBB','price':300},
               {'flight_number':'SYN_B','date':'2030-01-01','origin':'BBB','destination':'CCC','price':200}],
    'passengers':[{'first_name':'Ada','last_name':'Example','dob':'2000-01-01'}]}
FLIGHT_WRITE={'name':'update_reservation_flights','arguments':{'reservation_id':'SYN001','cabin':'economy',
    'flights':[{'flight_number':'SYN_A','date':'2030-01-01'},{'flight_number':'SYN_B','date':'2030-01-01'}],
    'payment_id':'synthetic_card'}}
RES_READ={'name':'get_reservation_details','arguments':{'reservation_id':'SYN001'}}
SEARCH_A={'name':'search_direct_flight','arguments':{'origin':'AAA','destination':'BBB','date':'2030-01-01'}}
SEARCH_B={'name':'search_direct_flight','arguments':{'origin':'BBB','destination':'CCC','date':'2030-01-01'}}


class Actor:
    model='scripted-no-api'
    def reset(self): self.messages=[{'role':'system','content':'Synthetic public policy.'}]


class Env:
    def __init__(self,domain='retail',replies=('Yes, I approve these details as well.',),mutation_error=None):
        self.tools_dict=dict.fromkeys(ToolRegistry().names(domain))
        self.actions=[]; self.replies=iter(replies); self.mutation_error=mutation_error
    def reset(self,index=None):
        return 'Synthetic public request.',{'task':{'instruction':'HIDDEN_INSTRUCTION_CANARY','actions':['HIDDEN_REFERENCE_CANARY'],'outputs':['HIDDEN_OUTPUT_CANARY']}}
    def step(self,action):
        self.actions.append(deepcopy(action)); name=action['name']; args=action['arguments']
        if name=='respond':
            if args['content']=='Synthetic finished.': return '###STOP###',1,True,{}
            reply=next(self.replies)
            return reply,0,reply=='###STOP###',{}
        if name=='get_reservation_details': return json.dumps(RESERVATION),0,False,{}
        if name=='search_direct_flight':
            number='SYN_A' if args['origin']=='AAA' else 'SYN_B'
            return json.dumps([{'flight_number':number,'origin':args['origin'],'destination':args['destination'],
                'prices':{'economy':100},'available_seats':{'economy':4}}]),0,False,{}
        if name=='update_reservation_flights':
            return self.mutation_error or json.dumps({'reservation_id':'SYN001'}),0,False,{}
        if name in {'get_user_details','modify_user_address'}:
            return self.mutation_error or json.dumps({'user_id':'synthetic_user','address':ADDRESS}),0,False,{}
        raise AssertionError('Unexpected synthetic dispatch')


class CurrentDevelopmentTests(unittest.TestCase):
    def controller(self,actions,*,domain='retail',arm='G',env=None,**kwargs):
        root=Path(self.enterContext(TemporaryDirectory())); journal=AttemptJournal(root,EpisodeKey(
            'current_fixture',domain,'dev' if domain=='retail' else 'test',0,0,arm,'6f4b718037db619539b8b692060e6686f3f0dcc9'))
        self.requests=[]; script=iter(deepcopy(actions))
        def generate(messages,*args,**sampling):
            self.requests.append(deepcopy(messages)); return 'Synthetic proposal',next(script)
        env=env or Env(domain)
        return StudyController(domain=domain,arm=arm,agent=Actor(),actor_module=SimpleNamespace(get_message_action=generate),
            env=env,journal=journal,**kwargs),env,journal

    def test_additive_assent_exact_write_and_hidden_fields_separated(self):
        self.assertEqual(CLASSIFIER_VERSION,'whole-response-assent-current-v5')
        self.assertEqual(classify_response('Yes, I approve these details as well.'),'AFFIRM')
        for text in ('Yes, I approve these details as well, but change the amount.','Yes, I approve these details as well if cheaper.'):
            self.assertNotEqual(classify_response(text),'AFFIRM')
        controller,env,journal=self.controller([ADDRESS_WRITE,STOP])
        result=controller.run(0)
        self.assertTrue(result['native_done']); self.assertFalse(result['model_evaluated'])
        self.assertEqual(result['development_version'],'current-dev-v5')
        self.assertEqual([a['name'] for a in env.actions],['respond','modify_user_address','respond'])
        self.assertEqual(env.actions[1],ADDRESS_WRITE)
        for request in self.requests:
            self.assertNotIn('HIDDEN_',json.dumps(request))
        events=read_events(journal.directory/'events.jsonl')
        self.assertLess(next(i for i,e in enumerate(events) if e['event_type']=='gate_CUSTOMER_RESPONSE' and e['payload']['classification']=='AFFIRM'),
                        next(i for i,e in enumerate(events) if e['event_type']=='gate_ATTEMPTED'))

    def test_missing_price_produces_exact_visible_route_plan_then_fresh_consent(self):
        controller,env,journal=self.controller([RES_READ,FLIGHT_WRITE,SEARCH_A,SEARCH_B,FLIGHT_WRITE,STOP],domain='airline')
        result=controller.run(0)
        self.assertTrue(result['native_done'])
        plan=result['planning']['plans'][0]
        self.assertEqual(plan['missing_fields'],['price_difference'])
        self.assertEqual([s['action'] for s in plan['suggestions']],[SEARCH_A,SEARCH_B])
        self.assertEqual(plan['minimum_native_actions'],4)
        self.assertEqual(plan['minimum_actor_turns'],3)
        self.assertFalse(plan['authority_to_dispatch']); self.assertEqual(plan['dispatches_performed'],0)
        self.assertEqual([a['name'] for a in env.actions],['get_reservation_details','search_direct_flight',
            'search_direct_flight','respond','update_reservation_flights','respond'])
        self.assertEqual(sum(a['name']=='update_reservation_flights' for a in env.actions),1)
        prompt=next(a['arguments']['content'] for a in env.actions if a['name']=='respond' and 'Proposed' in a['arguments']['content'])
        self.assertIn('"-300"',prompt)
        events=read_events(journal.directory/'events.jsonl')
        self.assertEqual(sum(e['event_type']=='planning_dependencies' for e in events),1)

    def test_no_visible_route_never_guesses_hidden_search_and_budget_shortfall_stops(self):
        controller,env,_=self.controller([FLIGHT_WRITE],domain='airline',limits=Limits(native_actions=2,actor_turns=2))
        result=controller.run(0); plan=result['planning']['plans'][0]
        self.assertEqual(result['stop_reason'],'planning_budget'); self.assertFalse(result['native_done'])
        self.assertEqual(env.actions,[]); self.assertEqual(plan['suggestions'][0]['action'],RES_READ)
        self.assertEqual(plan['native_action_shortfall'],1); self.assertEqual(plan['actor_turn_shortfall'],1)
        self.assertFalse(plan['authority_to_dispatch'])

    def test_identical_reads_stop_explicitly_without_cached_native_response(self):
        read={'name':'get_user_details','arguments':{'user_id':'synthetic_user'}}
        for arm in ('B','G'):
            controller,env,journal=self.controller([read]*5,arm=arm)
            result=controller.run(0)
            self.assertEqual(result['stop_reason'],'planning_loop'); self.assertFalse(result['native_done'])
            self.assertEqual(len(env.actions),3)
            events=read_events(journal.directory/'events.jsonl')
            self.assertEqual(sum(e['event_type']=='native_dispatch_started' for e in events),3)
            self.assertEqual(sum(e['event_type']=='planning_loop_stopped' for e in events),1)

    def test_changed_read_observations_are_progress_not_a_static_loop(self):
        class ChangingEnv(Env):
            def step(self,action):
                if action['name']=='get_user_details':
                    self.actions.append(deepcopy(action))
                    return json.dumps({'user_id':'synthetic_user','address':ADDRESS,'revision':len(self.actions)}),0,False,{}
                return super().step(action)
        read={'name':'get_user_details','arguments':{'user_id':'synthetic_user'}}
        controller,env,_=self.controller([read]*4+[STOP],env=ChangingEnv())
        result=controller.run(0)
        self.assertTrue(result['native_done']); self.assertEqual(result['stop_reason'],'native_done')
        self.assertEqual(sum(a['name']=='get_user_details' for a in env.actions),4)

    def test_repeated_missing_proposals_without_evidence_stop_no_dispatch(self):
        controller,env,_=self.controller([FLIGHT_WRITE]*4,domain='airline')
        result=controller.run(0)
        self.assertEqual(result['stop_reason'],'planning_loop'); self.assertEqual(env.actions,[])

    def test_terminal_rejection_and_uncertain_state_still_precede_any_retry(self):
        for reply in ('no','###STOP###'):
            controller,env,_=self.controller([ADDRESS_WRITE,STOP],env=Env(replies=[reply]))
            result=controller.run(0)
            self.assertFalse(any(a['name']=='modify_user_address' for a in env.actions))
            self.assertTrue(result['native_done'])
        for arm in ('B','G'):
            controller,env,_=self.controller([ADDRESS_WRITE,ADDRESS_WRITE,STOP],arm=arm,
                env=Env(mutation_error='Error: unknown partial write'))
            result=controller.run(0)
            self.assertEqual(result['stop_reason'],'uncertain_mutation')
            self.assertEqual(sum(a['name']=='modify_user_address' for a in env.actions),1)

    def test_corrected_latest_search_remains_unresolved_not_old_price_resurrection(self):
        store=VisibleStore('airline')
        store.record('reservation',1,RES_READ,json.dumps(RESERVATION))
        store.record('price-a',2,SEARCH_A,json.dumps([{'flight_number':'SYN_A','prices':{'economy':100}}]))
        store.record('price-b',3,SEARCH_B,json.dumps([{'flight_number':'SYN_B','prices':{'economy':100}}]))
        self.assertIn('price_difference',[f.name for f in store.facts_for(FLIGHT_WRITE['name'],FLIGHT_WRITE['arguments'])])
        store.record('corrected-a',4,SEARCH_A,'Error: unavailable search')
        facts=store.facts_for(FLIGHT_WRITE['name'],FLIGHT_WRITE['arguments'])
        self.assertNotIn('price_difference',[f.name for f in facts])
        plan=store.dependency_plan(FLIGHT_WRITE,('price_difference',),remaining_native_actions=8,remaining_actor_turns=8).to_dict()
        self.assertEqual([s['action'] for s in plan['suggestions']],[SEARCH_A])

    def test_direct_gate_false_callback_blocks_authority_or_attempt_and_postcall_retry(self):
        for declined in ('PREPARED','CUSTOMER_RESPONSE','ATTEMPTED','SUCCEEDED'):
            with self.subTest(declined=declined):
                calls=[]
                def write(arguments):
                    calls.append(deepcopy(arguments))
                    return json.dumps({'user_id':'synthetic_user','address':ADDRESS})
                gate=StudyGate({('retail','modify_user_address'):write},
                    event_callback=lambda kind,payload:False if kind==declined else None)
                intent=build_intent('retail','modify_user_address',ADDRESS_WRITE['arguments'],intent_id='synthetic',revision=1).intent
                prompt=gate.propose(intent,prompt_event_id='prompt',prompt_sequence=1)
                if declined!='PREPARED':
                    gate.respond('Yes, I approve these details as well.',prompt_id=prompt.prompt_id,
                        event_id='actual-customer',sequence=2,source='user')
                    result=gate.dispatch(intent)
                self.assertTrue(gate.snapshot()['halted'])
                self.assertEqual(len(calls),1 if declined=='SUCCEEDED' else 0)
                gate.dispatch(intent)
                self.assertEqual(len(calls),1 if declined=='SUCCEEDED' else 0)


if __name__=='__main__': unittest.main()
