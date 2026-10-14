import unittest
from study.termination_candidate import MARKER,normalize_native_user_reply


class TerminationCandidateTests(unittest.TestCase):
    def test_actual_v3_farewell_shapes_normalize_with_raw_retained(self):
        replies=["Thank you for handling that. I'll keep an eye out for the email with the return instructions. ###STOP###",
                 "Thank you. That's all for now.###STOP###",'farewell. ###STOP###',MARKER+'\n']
        for text in replies:
            with self.subTest(text=text):
                result=normalize_native_user_reply(text,native_user_protocol=True)
                self.assertEqual(result.raw_reply,text); self.assertEqual(result.normalized_reply,MARKER)
                self.assertEqual(result.disposition,'TERMINATE'); self.assertFalse(result.may_classify_for_consent)

    def test_affirmative_terminal_cannot_become_consent(self):
        for text in ('Yes. ###STOP###','Yes, I approve the entire operation. ###STOP###'):
            result=normalize_native_user_reply(text,native_user_protocol=True)
            self.assertEqual(result.disposition,'TERMINATE'); self.assertFalse(result.may_classify_for_consent)

    def test_early_quoted_repeated_nonfinal_and_unauthorized_markers_fail_closed(self):
        texts=['###STOP### then continue','###STOP### ###STOP###',"'###STOP###'",'"###STOP###"',
               'He said "###STOP###',"He said '###STOP###",'```\n###STOP###\n```',
               '{"text":"###STOP###"}','farewell. ###STOP### another request','not###STOP###',
               'Example: farewell. ###STOP###','Please output this marker: ###STOP###',
               'Example: ###STOP###','The confirmation code is ###STOP###','{"marker": ###STOP###',
               '“farewell. ###STOP###','The passphrase is ###STOP###','Thanks for the example: ###STOP###']
        for text in texts:
            with self.subTest(text=text):
                result=normalize_native_user_reply(text,native_user_protocol=True)
                self.assertEqual(result.disposition,'REJECT_MARKER'); self.assertFalse(result.may_classify_for_consent)
                self.assertEqual(result.normalized_reply,text)
        self.assertEqual(normalize_native_user_reply(MARKER).disposition,'REJECT_MARKER')

    def test_no_marker_unchanged_and_protocol_flag_type_checked(self):
        for text in ('Yes','no','Goodbye!','Please change the address'):
            result=normalize_native_user_reply(text,native_user_protocol=True)
            self.assertEqual(result.normalized_reply,text); self.assertEqual(result.disposition,'PASSTHROUGH')
        with self.assertRaises(TypeError): normalize_native_user_reply('yes',native_user_protocol=1)


if __name__=='__main__': unittest.main()
