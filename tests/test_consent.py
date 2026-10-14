"""Offline consent grammar and exact current gate binding; handcrafted facts."""
from copy import deepcopy
import json
import unittest

from study import MaterialFact, ObservationRef, StudyGate, ToolRegistry, build_intent
from study.consent import CLASSIFIER_VERSION, TERMINAL_MARKER, classify_response
from study.gate import CLASSIFIER_VERSION as GATE_CLASSIFIER_VERSION

FULL = 'Yes, I approve this entire operation and these exact details. Thank you!'
ADDRESS = dict(address1='123 Main St', address2='', city='Example', state='CA', country='USA', zip='00123')


def intent(*, revision=1, address=None, evidence_sequence=1, user_id='synthetic'):
    args = dict(user_id=user_id, **(address or ADDRESS))
    return build_intent('retail', 'modify_user_address', args, intent_id='operation', revision=revision,
        facts=[], registry=ToolRegistry()).intent


class ConsentTests(unittest.TestCase):
    def test_prospective_development_version(self):
        self.assertEqual('whole-response-assent-v3-development', CLASSIFIER_VERSION)
        self.assertEqual(CLASSIFIER_VERSION, GATE_CLASSIFIER_VERSION)

    def test_eight_observed_development_reply_shapes(self):
        # Three ordinary and five terminal-marked variants in the visible audit.
        for reply in [FULL] * 3 + [FULL + TERMINAL_MARKER] * 5:
            self.assertEqual('AFFIRM', classify_response(reply, allow_terminal_marker=True))
        self.assertEqual('AFFIRM', classify_response('Yes, I approve these details.'))

    def test_clear_whole_operation_equivalents(self):
        for text in [FULL, 'Yes, I approve these details. Thank you!',
                     'I confirm this entire operation and these exact details.',
                     'Yes, I authorize the whole operation. Thanks.',
                     'I approve all of these exact details. Thank you very much!',
                     'Yes, please proceed exactly as shown. Thank you.',
                     'Please proceed with this entire operation.',
                     'I confirm everything exactly as described.',
                     'Yes, proceed with these exact details.', 'Yes, thank you!',
                     ' YES, I APPROVE THESE EXACT DETAILS.\nTHANK YOU! ',
                     'yes', 'yes.', 'confirm', 'i confirm.', 'approved', 'proceed']:
            with self.subTest(text=text): self.assertEqual('AFFIRM', classify_response(text))

    def test_negation_never_affirmative(self):
        for text in ['not yes', 'Yes, I do not approve these details.', 'I do not approve this operation.',
                     'Not approved. Thank you!', 'Yes, no thanks.', 'No, I approve these details.',
                     'I confirm this entire operation is not approved.', 'Yes, do not proceed.']:
            with self.subTest(text=text): self.assertNotEqual('AFFIRM', classify_response(text))
        self.assertEqual('REJECT', classify_response('no'))

    def test_conditional_mixed_or_partial_scope_never_affirmative(self):
        for text in ['yes if the price is lower', 'yes, but change the date',
                     'Yes, I approve these details only if the price stays the same.',
                     'Yes, I approve only the first item.', 'Yes, I approve this operation except insurance.',
                     'Yes, I approve these details, unless there is a charge.',
                     'Yes and no', 'Yes, maybe.', 'I approve the address, not payment.',
                     'Yes, I approve these details. Also cancel my other order.',
                     'Yes, I approve these details. Thank you, but change nothing except payment.',
                     'Yes, I approve these details, subject to my later approval.',
                     'Yes, I approve this entire operation and these exact details? Thank you!']:
            with self.subTest(text=text): self.assertNotEqual('AFFIRM', classify_response(text))
        self.assertEqual('CHANGE', classify_response('yes, but change the date'))

    def test_changed_nested_values_never_affirmative(self):
        changes = ['amount is 305', 'refund to gift_card_2', 'ship to 45 Other St',
                   'use ZIP 00124', 'passenger name is Alex', 'date is 2024-06-02',
                   'reason is ordered by mistake', 'replace the second item', 'insurance should be no']
        for change in changes:
            for text in [f'Yes, {change}.', f'{FULL} {change}.', f'Yes, I approve these details, {change}.']:
                with self.subTest(text=text): self.assertNotEqual('AFFIRM', classify_response(text))

    def test_quotes_examples_questions_and_ambiguity_never_affirmative(self):
        for text in ['"yes"', f'"{FULL}"', f'Customer said: {FULL}', 'For example, yes.',
                     'The word yes means approval.', 'sure', 'yesterday', 'yes?', 'probably yes',
                     'Okay, yes.', 'Yes because I want a refund.', 'Yes, I approve it.',
                     'I think these details are correct.', 'Thank you!', '']:
            with self.subTest(text=text): self.assertNotEqual('AFFIRM', classify_response(text))

    def test_terminal_sentinel_requires_protocol_opt_in(self):
        self.assertEqual('AMBIGUOUS', classify_response(FULL + TERMINAL_MARKER))
        for text in [FULL + TERMINAL_MARKER, FULL + '\n' + TERMINAL_MARKER + '\n']:
            self.assertEqual('AFFIRM', classify_response(text, allow_terminal_marker=True))
        for text in [TERMINAL_MARKER, TERMINAL_MARKER + FULL, FULL + TERMINAL_MARKER + ' extra',
                     FULL + TERMINAL_MARKER * 2, FULL + ' ###stop###',
                     'Yes, but use another address.' + TERMINAL_MARKER,
                     f'"{FULL}"' + TERMINAL_MARKER, FULL + ' Thank you ###STOP### again']:
            with self.subTest(text=text): self.assertNotEqual('AFFIRM', classify_response(text, allow_terminal_marker=True))

    def test_wrong_input_types(self):
        for value in [None, 1, {}, []]:
            with self.assertRaises(TypeError): classify_response(value)
        with self.assertRaises(TypeError): classify_response('yes', allow_terminal_marker=1)

    def gate(self):
        calls = []
        def native(args):
            calls.append(deepcopy(args))
            return json.dumps(dict(user_id=args['user_id'], address={k: args[k] for k in ADDRESS}))
        return StudyGate({('retail', 'modify_user_address'): native}), calls

    def test_actual_fresh_user_reply_and_terminal_marker_dispatch_once(self):
        for reply in [FULL, FULL + TERMINAL_MARKER]:
            gate, calls = self.gate(); current = intent()
            prompt = gate.propose(current, prompt_event_id='prompt', prompt_sequence=10)
            self.assertEqual('AUTHORIZED', gate.respond(reply, prompt_id=prompt.prompt_id,
                event_id='user', sequence=11, source='user').status)
            self.assertEqual('SUCCEEDED', gate.dispatch(current).status)
            self.assertEqual([current.arguments], calls)
            self.assertEqual('ALREADY_ATTEMPTED', gate.dispatch(current).status)
            event = next(e for e in gate.snapshot()['events'] if e['event_type'] == 'PREPARED')
            self.assertEqual(CLASSIFIER_VERSION, event['classifier_version'])

    def test_stale_prompt_sequence_or_nonuser_source_never_authorizes(self):
        for bad in [dict(prompt_id='old'), dict(sequence=12), dict(source='actor'), dict(source='tool')]:
            gate, calls = self.gate(); current = intent()
            prompt = gate.propose(current, prompt_event_id='prompt', prompt_sequence=10)
            kwargs = dict(prompt_id=prompt.prompt_id, event_id='reply', sequence=11, source='user'); kwargs.update(bad)
            self.assertEqual('STALE_RESPONSE', gate.respond(FULL + TERMINAL_MARKER, **kwargs).status)
            self.assertFalse(gate.dispatch(current).dispatched)
            self.assertEqual([], calls)

    def test_exact_payload_change_invalidates_approved_reply(self):
        gate, calls = self.gate(); current = intent()
        prompt = gate.propose(current, prompt_event_id='prompt', prompt_sequence=10)
        gate.respond(FULL, prompt_id=prompt.prompt_id, event_id='reply', sequence=11, source='user')
        changed = intent(revision=2, address={**ADDRESS, 'zip': '00124'})
        self.assertEqual('INVALIDATED', gate.dispatch(changed).status)
        self.assertEqual([], calls)

    def test_material_evidence_change_invalidates_approved_reply(self):
        registry = ToolRegistry()
        def make(sequence):
            return build_intent('retail', 'cancel_pending_order', dict(order_id='#SYN', reason='ordered by mistake'),
                intent_id='cancel', revision=1, facts=[MaterialFact(name, ObservationRef('read-' + str(sequence),
                'tool:retail/get_order_details', '$.' + name, value, sequence)) for name, value in
                [('user_id', 'synthetic'), ('refund_payments', [{'payment_method_id': 'card', 'amount': 100}])]], registry=registry).intent
        gate = StudyGate({('retail', 'cancel_pending_order'): lambda args: self.fail('Must not dispatch')})
        current = make(1); self.assertIsNotNone(current)
        prompt = gate.propose(current, prompt_event_id='prompt', prompt_sequence=10)
        gate.respond(FULL, prompt_id=prompt.prompt_id, event_id='reply', sequence=11, source='user')
        self.assertEqual('INVALIDATED', gate.dispatch(make(2)).status)

    def test_restart_and_consumed_reply_slot_cannot_use_polite_assent(self):
        gate, calls = self.gate(); current = intent()
        prompt = gate.propose(current, prompt_event_id='prompt', prompt_sequence=10)
        self.assertEqual('AMBIGUOUS', gate.respond('maybe', prompt_id=prompt.prompt_id, event_id='first', sequence=11, source='user').status)
        self.assertEqual('STALE_RESPONSE', gate.respond(FULL, prompt_id=prompt.prompt_id, event_id='second', sequence=11, source='user').status)
        gate.restart()
        self.assertFalse(gate.dispatch(current).dispatched)
        self.assertEqual([], calls)

    def test_native_terminal_assent_never_dispatches_later_mutation(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from study.controller import StudyController
        from study.evidence import AttemptJournal, EpisodeKey
        class TerminalEnv:
            tools_dict = dict.fromkeys(ToolRegistry().names('retail'))
            def __init__(self): self.actions = []
            def reset(self, index=None): return 'Please update my address.', {}
            def step(self, action):
                self.actions.append(deepcopy(action))
                return FULL + TERMINAL_MARKER, 0, True, {}
        class Actor:
            model = 'offline-fixture'
            def reset(self): self.messages = []
        current = intent()
        action = dict(name=current.tool_name, arguments=current.arguments)
        def generate(*args, **kwargs): return 'Synthetic proposal', deepcopy(action)
        with TemporaryDirectory() as directory:
            key = EpisodeKey('consent-fixture', 'retail', 'dev', 0, 0, 'G', '6f4b718037db619539b8b692060e6686f3f0dcc9')
            journal = AttemptJournal(Path(directory), key)
            env = TerminalEnv()
            controller = StudyController(domain='retail', arm='G', agent=Actor(),
                actor_module=SimpleNamespace(get_message_action=generate), env=env, journal=journal)
            result = controller.run(0)
            self.assertTrue(result['native_done'])
            self.assertEqual('native_done', result['stop_reason'])
            self.assertEqual(['respond'], [row['name'] for row in env.actions])
            self.assertEqual([], result['gate']['attempts'])


if __name__ == '__main__': unittest.main()
