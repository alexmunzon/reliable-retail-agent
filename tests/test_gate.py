"""No-key demo uses the reviewed pure assent grammar; its binding is unchanged."""
import subprocess
import sys
import unittest

from rra.gate import ConfirmationGate, MutationIntent

FULL = 'Yes, I approve this entire operation and these exact details. Thank you!'


class DemoConsentTests(unittest.TestCase):
    def gate(self):
        calls = []
        gate = ConfirmationGate({'modify_address': lambda args: calls.append(args) or {'ok': True}})
        current = MutationIntent('modify_address', {'address': '123 Main St', 'zip': '00123'}, 'synthetic-op')
        return gate, calls, current

    def test_polite_whole_operation_approval_dispatches_once(self):
        gate, calls, current = self.gate(); prompt = gate.propose(current)
        self.assertEqual('authorized', gate.respond(FULL, prompt_id=prompt.prompt_id).status)
        self.assertTrue(gate.dispatch(current).dispatched)
        self.assertEqual([current.arguments], calls)
        self.assertEqual('already_attempted', gate.dispatch(current).status)

    def test_negation_conditions_quotes_changes_and_markers_do_not_dispatch(self):
        for reply in ['Yes, but use ZIP 99999', 'Yes if free', 'I do not approve these details.',
                      'Yes and no', '"yes"', FULL + ' Also change the refund amount.', FULL + '###STOP###']:
            with self.subTest(reply=reply):
                gate, calls, current = self.gate(); prompt = gate.propose(current)
                self.assertNotEqual('authorized', gate.respond(reply, prompt_id=prompt.prompt_id).status)
                self.assertFalse(gate.dispatch(current).dispatched)
                self.assertEqual([], calls)

    def test_stale_polite_reply_and_changed_payload_remain_blocked(self):
        gate, calls, current = self.gate(); prompt = gate.propose(current)
        self.assertEqual('stale_consent', gate.respond(FULL, prompt_id='old').status)
        self.assertFalse(gate.dispatch(current).dispatched)
        gate.respond(FULL, prompt_id=prompt.prompt_id)
        changed = MutationIntent(current.tool_name, {**current.arguments, 'zip': '99999'}, current.operation_id)
        self.assertFalse(gate.dispatch(changed).dispatched)
        self.assertEqual([], calls)

    def test_import_does_not_load_provider_or_native_runtime(self):
        script = ('import sys; import rra.gate; '
                  'assert not any(n == "openai" or n.startswith(("openai.", "tau_bench", "native.")) for n in sys.modules)')
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == '__main__': unittest.main()
