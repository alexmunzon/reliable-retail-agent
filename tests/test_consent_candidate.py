"""Visible saved reply fixtures and adversarial whole-response boundaries."""
import unittest

from study.consent_candidate import CLASSIFIER_VERSION, classify_response
from study.consent import classify_response as classify_v3

ACTUAL_SIX = [
    ('Yes, please approve the operation.', 'AFFIRM'),
    ('Yes, I confirm to approve this entire operation with these exact details.', 'AFFIRM'),
    ('Yes, approve this entire operation with these details.', 'AFFIRM'),
    ("Why do you keep asking? I've confirmed multiple times to approve it. Just do it already!", 'AMBIGUOUS'),
    ('YES! I approve! How many times do I need to say it?', 'AMBIGUOUS'),
    ('YES! Do it now, please.', 'AFFIRM'),
]


class ConsentCandidateTests(unittest.TestCase):
    def test_conditions_negation_partial_scope_quotes_changes_first(self):
        for text in [
            'Yes, please approve the operation only if the price is lower.',
            'Yes, approve this entire operation with these details, but change the amount to 255.',
            'Yes, please do it now unless insurance costs money.',
            'Yes! Do it now, please, except the second passenger.',
            'Yes, I confirm to approve this operation with different details.',
            'Yes, do not approve the operation.',
            'Yes, I do not confirm to approve this entire operation.',
            'Not approved. Please approve the operation.',
            'Yes, approve only this item.', 'Yes, approve the address, not payment.',
            '"Yes, please approve the operation."', 'User said yes, approve the operation.',
            'Yes, please approve the operation. Ship to 45 Other Street.',
            'Yes, please approve the operation. Use gift_card_2.',
            'Yes, please approve the operation. Refund amount should be 999.',
            'Yes, please approve the operation. Reason is airline cancelled flight.',
            'Yes! Do it now, please. Also book another flight.',
            'Yes! Do it now, please?', 'Yes, please approve the operation? Thanks.',
            'Yes and no, please approve the operation.',
            'I approved it earlier.', 'I have already approved the operation.',
            'Why do you keep asking? I said yes earlier.',
        ]:
            with self.subTest(text=text): self.assertNotEqual('AFFIRM', classify_response(text))

    def test_all_six_actual_visible_forms_without_rescoring(self):
        self.assertEqual('whole-response-assent-offline-candidate-v1', CLASSIFIER_VERSION)
        for text, expected in ACTUAL_SIX:
            with self.subTest(text=text):
                self.assertEqual('AMBIGUOUS', classify_v3(text))
                self.assertEqual(expected, classify_response(text))

    def test_generic_principled_operation_grammar(self):
        for text in ['Please approve this entire operation.',
                     'Yes, please approve the whole operation with these exact details. Thank you!',
                     'I confirm to approve the operation with the exact details.',
                     'Yes! Approve the operation and these details.',
                     'Yes. Do it now, please.', 'Yes, please do it.',
                     'Yes, do it now. Thanks!',
                     'Yes, I approve this entire operation and these exact details. Thank you!',
                     'yes', 'i confirm']:
            with self.subTest(text=text): self.assertEqual('AFFIRM', classify_response(text))

    def test_negative_qualifier_permutations_never_match(self):
        qualifiers = ['if free', 'unless the price changes', 'but change ZIP 00124',
                      'except insurance', 'only the first item', 'not payment',
                      'maybe', 'subject to review', 'after you change the date',
                      'and change the payment allocation', 'and no', 'for example']
        accepted = [text for text, expected in ACTUAL_SIX if expected == 'AFFIRM']
        for core in accepted:
            for qualifier in qualifiers:
                for text in [f'{qualifier}, {core}', f'{core} {qualifier}.',
                             f'{core[:-1]}, {qualifier}.']:
                    with self.subTest(text=text): self.assertNotEqual('AFFIRM', classify_response(text))

    def test_terminal_protocol_is_single_final_explicit_opt_in(self):
        core = ACTUAL_SIX[0][0]
        self.assertEqual('AMBIGUOUS', classify_response(core + '###STOP###'))
        self.assertEqual('AFFIRM', classify_response(core + '###STOP###', allow_terminal_marker=True))
        for text in ['###STOP###', '###STOP###' + core, core + '###STOP######STOP###',
                     core + '###STOP### other content', '"' + core + '"###STOP###',
                     'Yes, please approve the operation if free.###STOP###']:
            with self.subTest(text=text): self.assertNotEqual('AFFIRM', classify_response(text, allow_terminal_marker=True))

    def test_arbitrary_yes_prefix_and_frustration_remain_unsupported(self):
        for text in ['Yes, anything you want.', 'Yes, approve it.', 'Yes, that thing.',
                     'YES! I approve!', 'Yes, please approve.', 'Just do it already!',
                     'YES! Do it now, please. How many times do I need to say it?',
                     'YES! I approve! Thank you! How many times do I need to say it?']:
            with self.subTest(text=text): self.assertNotEqual('AFFIRM', classify_response(text))

    def test_does_not_grant_prompt_or_payload_authority(self):
        # Pure parser API contains no dispatch, prompt state, stored facts or gate.
        import inspect
        self.assertEqual(['text', 'allow_terminal_marker'], list(inspect.signature(classify_response).parameters))
        for value in [None, 1, [], {}]:
            with self.assertRaises(TypeError): classify_response(value)
        with self.assertRaises(TypeError): classify_response('yes', allow_terminal_marker='true')


if __name__ == '__main__': unittest.main()
